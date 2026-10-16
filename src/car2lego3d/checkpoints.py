"""Content-based stage checkpoints that survive isolated assembly revisions."""
import json
from pathlib import Path

from .common import ROOT, digest
from .target import fingerprint
from .reference_plan import REFERENCE_GROUPS


def files_snapshot(paths):
    return {str(name): digest(path) if path.is_file() else None for name, path in paths.items()}


def view_files(manifest, geometry_only=False):
    paths = {} if geometry_only else {"manifest": manifest, "anchor": manifest.parent/"reference.png"}
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for group in ("views",) if geometry_only else REFERENCE_GROUPS:
            for view, item in data.get(group, {}).items():
                path = (manifest.parent/item["file"]).resolve()
                if not path.is_relative_to(manifest.parent.resolve()):
                    raise ValueError("View files must stay inside the manifest directory")
                paths[group+"/"+view] = path
    return files_snapshot(paths)


def output_snapshot(stage, output):
    if stage == "reference_images":
        return view_files(output)
    paths = {"output": output}
    if stage == "assembly":
        paths.update(assembly=output.parent/"assembly.json", fit=output.parent/"fit.json")
    return files_snapshot(paths)


def stage_signature(stage, command, directory, artifacts, spec_path, request, source):
    def normalized(value):
        value = str(value).replace("\\", "/")
        for path, token in ((artifacts, "<artifacts>"), (directory, "<job>")):
            value = value.replace(str(path).replace("\\", "/"), token)
        return value

    inputs = dict(command=[normalized(a) for a in command])
    modules = {
        "reference_images": ["gpt_reference.py", "reference_plan.py", "vehicle.py"],
        "reconstruction": ["multiview.py", "vehicle.py"],
        "assembly": [p.name for p in (ROOT/"src/car2lego3d").glob("*.py")
                     if p.name not in {"desktop.py", "web.py", "optimization.py", "checkpoints.py"}],
        "optimization": ["optimization.py", "search.py", "quality.py", "part_index.py", "config.py"],
        "exporting": ["studio.py"],
    }[stage]
    paths = {name: ROOT/"src/car2lego3d"/name for name in modules}
    if stage == "reference_images":
        from .gpt_reference import configuration
        upstream, _ = configuration()
        paths.update(worker=ROOT/"scripts/car2lego_reference_worker.py",
                     service=upstream/"backend/app/services/reference_images.py")
        inputs.update(specification=digest(spec_path), source=digest(source) if source.is_file() else None)
    if stage == "reconstruction":
        inputs.update(specification=digest(spec_path), views=view_files(artifacts/"01-image/views.json", geometry_only=True))
        paths["models"] = ROOT/"models.lock.json"
    if stage == "assembly":
        inputs.update(specification=digest(spec_path), mesh=digest(artifacts/"02-mesh/calibrated.glb"),
                      views=view_files(artifacts/"01-image/views.json"), config=request.get("generation_config"))
    if stage == "optimization":
        inputs.update(assembly=digest(artifacts/"03-lego/assembly.json"),
                      target=digest(artifacts/"03-lego/vehicle-target.json") if (artifacts/"03-lego/vehicle-target.json").exists() else None,
                      config=request.get("generation_config"))
    if stage == "exporting":
        inputs["ldraw"] = digest(artifacts/"03-lego/model.ldr")
    inputs["implementation"] = files_snapshot(paths)
    return fingerprint(inputs)
