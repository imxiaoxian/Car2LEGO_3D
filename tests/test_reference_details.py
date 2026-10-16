"""Reference evidence routing, incremental observation, and cache boundaries."""
import json

from PIL import Image
import pytest

from car2lego3d.common import digest, write_json
from car2lego3d.multiview import load_reference_images, load_views, snapshot_views
from car2lego3d.checkpoints import view_files
from car2lego3d.target import build_target


def references(directory):
    directory.mkdir(exist_ok=True)
    groups = {"views": {}, "supplemental_views": {}, "details": {}}
    for index, (group, name) in enumerate([
        ("views", "front"), ("views", "back"), ("views", "left"), ("views", "right"),
        ("supplemental_views", "top"), ("details", "front_lights"), ("details", "cockpit")]):
        file = directory / (name+".png")
        Image.new("RGB", (64, 48), (index*30, 40, 60)).save(file)
        groups[group][name] = dict(file=file.name, sha256=digest(file), model="gpt-image-test")
    manifest = directory / "views.json"
    write_json(manifest, dict(schema_version=1, **groups))
    return manifest


def test_auxiliary_images_survive_snapshot_but_are_not_mesh_inputs(tmp_path):
    source = references(tmp_path/"source")
    copied = snapshot_views(source, tmp_path/"copy")
    _, primary = load_views(copied)
    _, groups = load_reference_images(copied)
    assert len(primary) == 4
    assert set(groups["supplemental_views"]) == {"top"}
    assert set(groups["details"]) == {"front_lights", "cockpit"}
    target = build_target({}, {}, source, copied)
    assert target["geometry"]["details"]["cockpit"]["sha256"] == digest(tmp_path/"copy/cockpit.png")
    (tmp_path/"copy/cockpit.png").write_bytes(b"changed")
    assert len(load_views(copied)[1]) == 4
    with pytest.raises(ValueError, match="checksum"):
        load_reference_images(copied)


def test_detail_edits_invalidate_assembly_but_keep_geometry_checkpoint(tmp_path):
    manifest = references(tmp_path)
    geometry = view_files(manifest, geometry_only=True)
    assembly = view_files(manifest)
    Image.new("RGB", (64, 48), "blue").save(tmp_path/"cockpit.png")
    data = json.loads(manifest.read_text())
    data["details"]["cockpit"]["sha256"] = digest(tmp_path/"cockpit.png")
    write_json(manifest, data)
    assert view_files(manifest, geometry_only=True) == geometry
    assert view_files(manifest) != assembly


def test_optional_evidence_rejects_escape_and_duplicate_labels(tmp_path):
    manifest = references(tmp_path/"source")
    data = json.loads(manifest.read_text())
    data["details"]["cockpit"]["file"] = "../outside.png"
    write_json(manifest, data)
    with pytest.raises(ValueError, match="inside"):
        load_reference_images(manifest)
    data["details"] = {"front": data["views"]["front"]}
    write_json(manifest, data)
    with pytest.raises(ValueError, match="unique"):
        load_reference_images(manifest)


def test_detail_change_reobserves_only_affected_views_and_module(tmp_path, monkeypatch):
    from car2lego3d import details, dialogue
    manifest = references(tmp_path/"images")
    calls = []
    monkeypatch.setattr(dialogue, "settings", lambda: ("test", "https://unused", "vision-test"))

    def request(prompt, schema, directory, images):
        calls.append((directory.name, set(images)))
        if "observations" in schema["properties"]:
            name = directory.name
            return dict(observations=[dict(module="lights" if name == "front_lights" else "interior",
                description="recessed lens" if name == "front_lights" else "two seats",
                construction_guidance="use lens backing" if name == "front_lights" else "reserve seat space",
                confidence=.8, primary_features=[dict(view="front", id="lamp")] if name == "front_lights" else [],
                contradicts_primary=False)], unknowns=["hidden mounts"]), dict(model="test")
        name = directory.name
        if name == "front":
            assert "unregistered_front_lights" in images
        assert "unregistered_cockpit" not in images
        return dict(views=[dict(view=name, vehicle_bbox=[.1,.1,.9,.9], features=[dict(
            id="lamp", kind="headlamp", description="lens", confidence=.9,
            polygon=[[.1,.2],[.3,.2],[.3,.3],[.1,.3]])])]), dict(model="test")

    monkeypatch.setattr(dialogue, "request_json", request)
    result = details.extract_features(manifest, tmp_path/"features")
    assert len(calls) == 6
    assert len(result["module_targets"]) == 2
    assert next(v for v in result["views"] if v["view"] == "front")["features"][0]["detail_evidence"]
    calls.clear()
    details.extract_features(manifest, tmp_path/"features")
    assert calls == []
    data = json.loads(manifest.read_text())
    Image.new("RGB", (64, 48), "pink").save(manifest.parent/"front_lights.png")
    data["details"]["front_lights"]["sha256"] = digest(manifest.parent/"front_lights.png")
    write_json(manifest, data)
    details.extract_features(manifest, tmp_path/"features")
    assert {name for name, _ in calls} == {"front", "front_lights"}


def test_visual_review_receives_local_detail_and_cabin_targets(tmp_path, monkeypatch):
    from car2lego3d import dialogue
    from car2lego3d.optimization import visual_review
    manifest = references(tmp_path/"images")
    target = build_target({}, {}, manifest, manifest)

    def review(prompt, schema, directory, images, **kwargs):
        assert "unregistered_details_cockpit" in images
        assert "unregistered_details_front_lights" in images
        assert "UNREGISTERED" in prompt
        return dict(scores={key:.5 for key in schema["properties"]["scores"]["properties"]},
                    critical_defects=["seat absent"], repair_modules=["interior"]), dict(model="fixture")

    monkeypatch.setattr(dialogue, "request_json", review)
    report = visual_review(tmp_path/"preview", None, tmp_path/"review", target)
    assert report["repair_modules"] == ["interior"]
