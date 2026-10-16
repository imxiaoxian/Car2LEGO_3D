"""Pinned Hunyuan3D-2mv reproduction with labelled, jointly conditioned views."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from .common import ROOT, device_name, digest, model_lock, stage, write_json
from .reference_plan import REFERENCE_GROUPS

VIEWS = ("front", "left", "back", "right")


def download_weights(workers=8):
    """Resumable HTTP range download with a full upstream LFS SHA256 check."""
    spec = model_lock()["hunyuan3d_weights"]
    directory = ROOT / "models/Hunyuan3D-2mv" / spec["subfolder"]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "model.fp16.safetensors"
    if target.exists() and digest(target) == spec["sha256"]:
        return target
    endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    base = f"{endpoint}/{spec['repo']}/resolve/{spec['revision']}/{spec['subfolder']}"
    config = directory / "config.yaml"
    if not config.exists():
        subprocess.run(["curl", "--fail", "-sS", "-L", "--retry", "3", f"{base}/config.yaml",
                        "-o", str(config)], check=True)
    parts = directory / "download-parts"
    parts.mkdir(exist_ok=True)
    span = 64 * 2**20
    ranges = [(i, start, min(start+span, spec["size_bytes"])-1)
              for i, start in enumerate(range(0, spec["size_bytes"], span))]
    prefix = directory / "model.fp16.safetensors.downloading"
    if prefix.exists():
        with prefix.open("rb") as stream:
            for i in range(prefix.stat().st_size // span):
                part = parts / f"{i:04}.part"
                data = stream.read(span)
                if not part.exists():
                    part.write_bytes(data)

    def fetch(item):
        i, start, end = item
        part = parts / f"{i:04}.part"
        if part.exists() and part.stat().st_size == end-start+1:
            return
        result = subprocess.run(["curl", "--fail", "-sS", "-L", "--retry", "3", "--max-time", "600",
                    "--range", f"{start}-{end}", f"{base}/model.fp16.safetensors", "-o", str(part)],
                    capture_output=True)
        if result.returncode or part.stat().st_size != end-start+1:
            raise RuntimeError(f"Checkpoint range {i} failed or returned an incorrect size.")
        print(f"Downloaded checkpoint range {i+1}/{len(ranges)}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(fetch, ranges))
    assembled = directory / "model.fp16.safetensors.verified-download"
    import shutil
    with assembled.open("wb") as output:
        for i, _, _ in ranges:
            with (parts / f"{i:04}.part").open("rb") as stream:
                shutil.copyfileobj(stream, output, length=4*2**20)
    if digest(assembled) != spec["sha256"]:
        raise ValueError("Hunyuan3D-2mv checkpoint SHA256 mismatch.")
    os.replace(assembled, target)
    write_json(directory / "checkpoint.json", {**spec, "verified_sha256": digest(target)})
    return target


def github_json(url):
    result = subprocess.run(["curl", "--fail", "-sS", "-L", "--retry", "2", "--max-time", "60", url],
                            capture_output=True, check=True)
    return json.loads(result.stdout)


def git_blob_digest(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def setup_source():
    """Fetch only shape code and its notices; validate Git blob hashes at a pinned commit."""
    spec = model_lock()["hunyuan3d_code"]
    target = ROOT / "external/Hunyuan3D-2"
    api = f"https://api.github.com/repos/{spec['repo']}"
    tree = github_json(f"{api}/git/trees/{spec['commit']}?recursive=1")
    if tree.get("truncated"):
        raise RuntimeError("Incomplete upstream source tree.")
    entries = [item for item in tree["tree"] if item["type"] == "blob" and
               (item["path"].startswith("hy3dgen/shapegen/") or item["path"] in
                ("hy3dgen/__init__.py", "LICENSE", "NOTICE", "requirements.txt",
                 "examples/shape_gen_multiview.py"))]

    def fetch(item):
        destination = target / item["path"]
        if destination.is_file() and git_blob_digest(destination.read_bytes()) == item["sha"]:
            return
        blob = github_json(f"{api}/git/blobs/{item['sha']}")
        data = base64.b64decode(blob["content"])
        if git_blob_digest(data) != item["sha"]:
            raise ValueError(f"Upstream source checksum mismatch: {item['path']}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, entries))
    write_json(target / "source.json", {"commit": spec["commit"], "url": spec["url"],
               "files": {item["path"]: item["sha"] for item in entries}})
    return target


def upstream_pipeline():
    source = ROOT / "external/Hunyuan3D-2"
    snapshot = source / "source.json"
    if not snapshot.is_file():
        raise RuntimeError("Hunyuan3D-2mv source missing. Run car2lego3d setup --backend hunyuan-mv.")
    metadata = json.loads(snapshot.read_text(encoding="utf-8"))
    if metadata["commit"] != model_lock()["hunyuan3d_code"]["commit"]:
        raise ValueError("Unexpected Hunyuan3D source version.")
    for name, expected in metadata["files"].items():
        if git_blob_digest((source / name).read_bytes()) != expected:
            raise ValueError(f"Modified upstream source: {name}")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline
    return Hunyuan3DDiTFlowMatchingPipeline


def load_views(path):
    from PIL import Image
    path = Path(path).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or set(manifest.get("views", {})) != set(VIEWS):
        raise ValueError("Multiview reconstruction requires front, left, back and right views.json.")
    paths, hashes = {}, set()
    for view in VIEWS:
        item = manifest["views"][view]
        source = (path.parent / item["file"]).resolve()
        if not source.is_relative_to(path.parent):
            raise ValueError("View files must stay inside the manifest directory.")
        actual = digest(source)
        if actual != item["sha256"]:
            raise ValueError(f"View checksum mismatch: {view}")
        if actual in hashes:
            raise ValueError("Duplicate view images cannot be used as multiple angles.")
        hashes.add(actual)
        with Image.open(source) as image:
            image.verify()
        paths[view] = source
    return manifest, paths


def snapshot_views(source, directory):
    import shutil
    manifest, groups = load_reference_images(source)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    copied = {}
    for group, paths in groups.items():
        copied[group] = {}
        for name, origin in paths.items():
            destination = directory / f"{name}.png"
            shutil.copyfile(origin, destination)
            copied[group][name] = {**manifest[group][name], "file": destination.name}
    anchor = Path(source).parent / "reference.png"
    if anchor.is_file():
        shutil.copyfile(anchor, directory / "reference.png")
    output = directory / "views.json"
    write_json(output, {**manifest, **copied})
    write_json(directory / "import.json", {"source_manifest": str(Path(source).resolve()),
               "source_sha256": digest(source), "imported": True, "new_image_api_calls": 0})
    return output


def load_reference_images(path):
    """Validate optional evidence independently of the model's four-camera input."""
    import re
    from PIL import Image
    path = Path(path).resolve()
    manifest, primary = load_views(path)
    groups = {"views": primary}
    names = set(primary)
    for group in REFERENCE_GROUPS[1:]:
        groups[group] = {}
        for name, item in manifest.get(group, {}).items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name in names:
                raise ValueError("Reference labels must be unique safe identifiers")
            names.add(name)
            source = (path.parent / item["file"]).resolve()
            if not source.is_relative_to(path.parent):
                raise ValueError("Reference files must stay inside the manifest directory")
            if digest(source) != item["sha256"]:
                raise ValueError(f"Reference checksum mismatch: {name}")
            with Image.open(source) as image:
                image.verify()
            groups[group][name] = source
    return manifest, groups


def reuse_reconstruction(source, directory, manifest_path, vehicle_spec, resolution, steps, seed):
    import shutil
    source, directory = Path(source).resolve(), Path(directory).resolve()
    manifest, _ = load_views(manifest_path)
    record = json.loads((source / "reconstruction.json").read_text(encoding="utf-8"))
    expected = {"backend": "hunyuan-mv", "resolution": resolution,
                "steps": steps, "seed": seed}
    cached_views = record.get("inputs", {}).get("views", {})
    matching_images = (set(cached_views) == set(VIEWS) and
                       all(cached_views[view].get("sha256") == manifest["views"][view]["sha256"]
                           for view in VIEWS))
    if (record["status"] != "completed" or not matching_images or
            any(record["inputs"].get(k) != v for k, v in expected.items())):
        raise ValueError("Reconstruction cache does not match the requested views or inference settings.")
    for key in ("hunyuan3d_code", "hunyuan3d_weights"):
        if record["models"].get(key) != model_lock()[key]:
            raise ValueError("Reconstruction cache model version mismatch.")
    if record.get("vehicle_spec", {}).get("sha256") != (digest(vehicle_spec) if vehicle_spec else None):
        raise ValueError("Reconstruction cache physical specification mismatch.")
    required = {"mesh.glb", "mesh.obj"} | ({"calibrated.glb"} if vehicle_spec else set())
    if not required <= record.get("outputs", {}).keys():
        raise ValueError("Reconstruction cache is missing required outputs.")
    for name, expected_hash in record["outputs"].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or digest(path) != expected_hash:
            raise ValueError("Reconstruction cache output hash mismatch.")
    shutil.copytree(source, directory)


def prepare_views(paths, directory, spec=None):
    import numpy as np
    import rembg
    from PIL import Image, ImageOps
    from .vehicle import measurement
    session = None
    images, measurements, errors = {}, {}, []
    for view, source in paths.items():
        image = ImageOps.exif_transpose(Image.open(source)).convert("RGBA")
        image.thumbnail((2048, 2048))
        if image.getextrema()[3][0] == 255:
            session = session or rembg.new_session("u2netp", providers=["CPUExecutionProvider"])
            image = rembg.remove(image, session=session)
        mask = np.asarray(image.getchannel("A")) > 128
        bbox = image.getchannel("A").point(lambda a: 255 if a > 128 else 0).getbbox()
        if bbox is None:
            raise ValueError(f"Empty foreground: {view}")
        x0, y0, x1, y1 = bbox
        ratio = (x1-x0) / (y1-y0)
        edge = min(x0, y0, image.width-x1, image.height-y1)
        if edge < 2:
            errors.append(f"{view}: foreground touches the image boundary")
        if spec:
            dimension = "length" if view in ("left", "right") else "width"
            expected = measurement(spec, dimension) / measurement(spec, "height")
            # Mirrors and shadows are outside the sourced body envelope.
            if abs(ratio / expected - 1) > .30:
                errors.append(f"{view}: silhouette aspect differs from the physical prior by more than 30 percent")
        measurements[view] = {"bbox": list(bbox), "aspect": ratio, "edge_margin_px": edge,
                              "foreground_pixels": int(mask.sum()), "size": list(image.size)}
        image.save(directory / f"{view}-foreground.png")
        images[view] = image
    for first, second in (("front", "back"), ("left", "right")):
        a, b = measurements[first]["aspect"], measurements[second]["aspect"]
        if max(a, b) / min(a, b) > 1.20:
            errors.append(f"{first}/{second}: opposing silhouette aspect differs by more than 20 percent")
    write_json(directory / "view-quality.json", {"views": measurements, "blockers": errors,
               "geometric_input_passed": not errors, "identity_and_camera_verified": False,
               "limitations": ["Silhouette checks cannot prove camera pose or matching details."]})
    if errors:
        raise ValueError("Inconsistent reconstruction views: " + "; ".join(errors))
    return images


def reconstruct(manifest_path, directory, resolution=256, chunk_size=8000, device="auto",
                vehicle_spec=None, steps=50, seed=42):
    import torch
    from .vehicle import load_spec, spec_record, calibrate_mesh
    from .lego import orient
    manifest, paths = load_views(manifest_path)
    directory, device = Path(directory), device_name(device)
    spec = load_spec(vehicle_spec) if vehicle_spec else None
    weights_spec = model_lock()["hunyuan3d_weights"]
    if not 1 <= steps <= 100:
        raise ValueError("Reconstruction steps must be between 1 and 100.")
    with stage(directory, "reconstruction", {"backend": "hunyuan-mv", "views": manifest["views"],
                    "manifest_sha256": digest(manifest_path), "resolution": resolution,
                    "steps": steps, "seed": seed, "device": device, "cpu_offload": device.startswith("cuda")}) as record:
        images = prepare_views(paths, directory, spec)
        weights = ROOT / "models/Hunyuan3D-2mv"
        checkpoint = weights / weights_spec["subfolder"] / "model.fp16.safetensors"
        if not checkpoint.is_file():
            download_weights()
        if digest(checkpoint) != weights_spec["sha256"]:
            raise ValueError("Unverified Hunyuan3D-2mv checkpoint.")
        pipeline_class = upstream_pipeline()
        pipeline = pipeline_class.from_pretrained(str(weights), subfolder=weights_spec["subfolder"],
                    variant="fp16", device="cpu", dtype=torch.float16 if device.startswith("cuda") else torch.float32)
        if device.startswith("cuda"):
            # This upstream release provides offload methods but not the Diffusers components property.
            pipeline.components = {name: getattr(pipeline, name) for name in ("conditioner", "model", "vae")}
            pipeline.enable_model_cpu_offload(device=device)
            # Upstream offload installs hooks but leaves the pipeline's device on CPU.
            pipeline.device = torch.device(device)
            torch.cuda.reset_peak_memory_stats()
        generator = torch.Generator(device=device).manual_seed(seed)
        with torch.inference_mode():
            mesh = pipeline(image=images, num_inference_steps=steps, octree_resolution=resolution,
                            num_chunks=chunk_size, generator=generator, output_type="trimesh")[0]
        if mesh is None or not len(mesh.vertices):
            raise RuntimeError("Hunyuan3D-2mv returned an empty mesh.")
        # Upstream shape coordinates are Y-up. Store the project's X-long/Z-up frame.
        mesh, alignment = orient(mesh, up_axis="y")
        mesh.fix_normals()
        mesh.export(directory / "mesh.glb")
        mesh.export(directory / "mesh.obj")
        record.update(vertices=len(mesh.vertices), faces=len(mesh.faces), watertight=bool(mesh.is_watertight),
                      bounds=mesh.bounds.tolist(), backend="Hunyuan3D-2mv; four jointly conditioned views",
                      frame="X-long/Z-up; front direction unverified", alignment_degrees=alignment,
                      texture_generated=False, outputs={name: digest(directory / name) for name in ("mesh.glb", "mesh.obj")},
                      checkpoint_sha256=digest(checkpoint), view_count=len(images))
        if spec:
            calibrated, calibration = calibrate_mesh(mesh, spec)
            calibrated.export(directory / "calibrated-mm.obj")
            calibrated.convert_units("m")
            calibrated.export(directory / "calibrated.glb")
            write_json(directory / "physical-calibration.json", {**calibration, "vehicle_spec": spec_record(vehicle_spec),
                       "glb_units": "metres", "obj_units": "mm"})
            record["vehicle_spec"] = spec_record(vehicle_spec)
            record["outputs"].update({name: digest(directory / name) for name in
                                      ("calibrated.glb", "calibrated-mm.obj", "physical-calibration.json")})
        if device.startswith("cuda"):
            record["peak_gpu_allocated_mb"] = round(torch.cuda.max_memory_allocated() / 2**20, 2)
        from .preview import render
        render(mesh, directory / "mesh-preview.png")
        del pipeline
        if device.startswith("cuda"):
            torch.cuda.empty_cache()
    return directory / "mesh.glb"
