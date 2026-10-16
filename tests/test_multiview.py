import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw
import pytest

from car2lego3d import cli
from car2lego3d.common import digest, write_json
from car2lego3d.multiview import load_views, prepare_views, VIEWS


def view_set(directory):
    views = {}
    for index, name in enumerate(VIEWS):
        path = directory / f"{name}.png"
        image = Image.new("RGBA", (256, 256))
        ImageDraw.Draw(image).rectangle((35, 70, 220, 180), fill=(180+index*10, 50, 20, 255))
        image.save(path)
        views[name] = {"file": path.name, "sha256": digest(path)}
    path = directory / "views.json"
    write_json(path, {"schema_version": 1, "views": views})
    return path


def test_labels_integrity_and_duplicate_rejection(tmp_path):
    path = view_set(tmp_path)
    data, paths = load_views(path)
    assert set(paths) == set(VIEWS)
    data["views"]["right"] = data["views"]["left"]
    write_json(path, data)
    with pytest.raises(ValueError, match="Duplicate"):
        load_views(path)
    data["views"].pop("right")
    write_json(path, data)
    with pytest.raises(ValueError, match="requires front"):
        load_views(path)


def test_foreground_gate_rejects_inconsistent_opposing_views(tmp_path):
    path = view_set(tmp_path)
    data = json.loads(path.read_text())
    image = Image.new("RGBA", (256, 256))
    ImageDraw.Draw(image).rectangle((90, 40, 150, 210), fill=(200, 70, 20, 255))
    image.save(tmp_path / "back.png")
    data["views"]["back"]["sha256"] = digest(tmp_path / "back.png")
    write_json(path, data)
    _, paths = load_views(path)
    with pytest.raises(ValueError, match="opposing silhouette"):
        prepare_views(paths, tmp_path)
    assert json.loads((tmp_path / "view-quality.json").read_text())["blockers"]


def test_default_reconstruction_rejects_single_image_before_loading_model(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["car2lego3d", "reconstruct", "--image", "car.png", "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert not list(tmp_path.iterdir())


def test_reconstruction_routes_entire_manifest(monkeypatch, tmp_path):
    import car2lego3d.multiview as module
    path = view_set(tmp_path)
    calls = []
    monkeypatch.setattr(module, "reconstruct", lambda *args: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["car2lego3d", "reconstruct", "--views", str(path), "--output", str(tmp_path / "out")])
    cli.main()
    assert calls[0][0] == path
    assert len(load_views(calls[0][0])[1]) == 4


def test_gpt_failure_does_not_call_local_image_model(monkeypatch, tmp_path):
    import car2lego3d.image as module
    import car2lego3d.gpt_reference as gpt
    def fail(*args):
        raise RuntimeError("provider unavailable")
    monkeypatch.setattr(gpt, "generate", fail)
    monkeypatch.setattr(module, "generate_sd_turbo", lambda *a: pytest.fail("Unexpected SD fallback"))
    with pytest.raises(RuntimeError, match="provider unavailable"):
        module.generate("Lotus For Me", tmp_path)


def test_cache_rejects_tampered_mesh_and_changed_view_inputs(tmp_path):
    from car2lego3d.common import model_lock
    from car2lego3d.multiview import reuse_reconstruction
    path = view_set(tmp_path)
    manifest, _ = load_views(path)
    cache = tmp_path / "cache"
    cache.mkdir()
    for name in ("mesh.glb", "mesh.obj"):
        (cache / name).write_bytes(b"recorded mesh fixture")
    record = {"status": "completed", "inputs": {"backend": "hunyuan-mv", "views": manifest["views"],
              "resolution": 256, "steps": 50, "seed": 42}, "models": model_lock(),
              "outputs": {name: digest(cache / name) for name in ("mesh.glb", "mesh.obj")}}
    write_json(cache / "reconstruction.json", record)
    (cache / "mesh.glb").write_bytes(b"modified mesh")
    with pytest.raises(ValueError, match="hash mismatch"):
        reuse_reconstruction(cache, tmp_path / "out", path, None, 256, 50, 42)
    with pytest.raises(ValueError, match="settings"):
        reuse_reconstruction(cache, tmp_path / "out", path, None, 128, 50, 42)
    assert not (tmp_path / "out").exists()


def test_cache_reuses_same_cameras_with_new_metadata_and_details(tmp_path):
    from car2lego3d.common import model_lock
    from car2lego3d.multiview import reuse_reconstruction
    path = view_set(tmp_path)
    manifest, _ = load_views(path)
    cache = tmp_path / "cache"
    cache.mkdir()
    for name in ("mesh.glb", "mesh.obj"):
        (cache / name).write_bytes(b"recorded mesh fixture")
    write_json(cache / "reconstruction.json", {
        "status": "completed", "inputs": {"backend": "hunyuan-mv", "views": manifest["views"],
            "resolution": 256, "steps": 50, "seed": 42}, "models": model_lock(),
        "outputs": {name: digest(cache / name) for name in ("mesh.glb", "mesh.obj")}})
    for name, item in manifest["views"].items():
        item.update(modules=["glazing"], purpose="New appearance evidence")
        renamed = tmp_path / (name + "-renamed.png")
        renamed.write_bytes((tmp_path / item["file"]).read_bytes())
        item["file"] = renamed.name
    manifest["details"] = {"cockpit": {"file": "cockpit.png", "sha256": "new evidence"}}
    write_json(path, manifest)
    reuse_reconstruction(cache, tmp_path / "out", path, None, 256, 50, 42)
    assert (tmp_path / "out/mesh.glb").read_bytes() == (cache / "mesh.glb").read_bytes()
    # Changed camera pixels still invalidate the reconstruction, even if metadata agrees.
    image = tmp_path / manifest["views"]["front"]["file"]
    Image.new("RGB", (256, 256), "white").save(image)
    manifest["views"]["front"]["sha256"] = digest(image)
    write_json(path, manifest)
    with pytest.raises(ValueError, match="settings"):
        reuse_reconstruction(cache, tmp_path / "changed", path, None, 256, 50, 42)
