"""Constructed side-view observations; no model calls or appearance claims."""
from copy import deepcopy
import json
from threading import Barrier

from PIL import Image
import pytest
import trimesh

from car2lego3d.carfit import design, fit_mesh
from car2lego3d.common import ROOT, digest, write_json
from car2lego3d.layout import normalize_landmarks, observe_layout, validate_layout
from car2lego3d.vehicle import load_spec


def raw():
    return dict(nose_x=.05, tail_x=.95, cowl_x=.32, roof_front_x=.45, roof_rear_x=.61,
                cabin_rear_x=.58, front_axle_x=.25, rear_axle_x=.79,
                roof_y=.25, sill_y=.6, tyre_bottom_y=.8)


def views(directory):
    images = {}
    for i, name in enumerate(("front", "back", "left", "right")):
        path = directory / f"{name}.png"
        Image.new("RGB", (32, 32), (100+i*30, 50, 20)).save(path)
        images[name] = dict(file=path.name, sha256=digest(path))
    target = directory / "views.json"
    write_json(target, dict(schema_version=1, views=images))
    return target


def specification():
    spec = deepcopy(load_spec(ROOT / "data/vehicles/ferrari_f40_1987.json"))
    spec.update(model="Lamborghini Countach", variant="5000 QV test fixture")
    for key, value in dict(length=4140, width=2000, height=1070, wheelbase=2450).items():
        spec["measurements"][key]["value"] = value
    return spec


def fitted(fractions):
    body = trimesh.creation.box((16, 6, 2))
    # The high rear wing must not set the cabin roof interval.
    wing = trimesh.creation.box((1, 6, 3))
    wing.apply_translation((6, 0, 3))
    return fit_mesh(trimesh.util.concatenate((body, wing)), specifications=specification(),
                    scale="1:24", color=15, layout_observations=dict(fractions=fractions))


def test_mirrored_coordinates_keep_the_same_vehicle_landmarks():
    left = raw()
    right = {k: 1-v if k.endswith("_x") else v for k, v in left.items()}
    assert normalize_landmarks(right) == pytest.approx(normalize_landmarks(left))
    for key, value in (("tail_x", .05), ("roof_front_x", .2), ("roof_y", float("nan"))):
        invalid = {**left, key: value}
        with pytest.raises(ValueError):
            normalize_landmarks(invalid)


def test_layout_parallel_cache_and_source_integrity(tmp_path, monkeypatch):
    from car2lego3d import dialogue
    manifest = views(tmp_path)
    barrier, calls = Barrier(2), []
    def request(prompt, schema, directory, images, **kwargs):
        name, = images
        calls.append(name)
        barrier.wait(timeout=5)
        answer = raw()
        if name == "right":
            answer = {k: 1-v if k.endswith("_x") else v for k, v in answer.items()}
        return answer, dict(model="fixture")
    monkeypatch.setattr(dialogue, "request_json", request)
    result = observe_layout(manifest, tmp_path/"observations")
    assert sorted(calls) == ["left", "right"]
    assert observe_layout(manifest, tmp_path/"observations") == result
    assert len(calls) == 2
    tampered = deepcopy(result)
    tampered["fractions"]["roof_rear_x"] += .1
    with pytest.raises(ValueError, match="averages"):
        validate_layout(tampered, manifest)
    tampered = deepcopy(result)
    tampered["views"]["left"]["image_sha256"] = "changed"
    with pytest.raises(ValueError, match="different side"):
        validate_layout(tampered, manifest)


def test_compact_scale_uses_observed_cabin_boundaries_not_rear_wing(monkeypatch):
    fractions = dict(cowl_x=.2862, roof_front_x=.4368, roof_rear_x=.6194, cabin_rear_x=.5871)
    fit = fitted(fractions)
    assert (fit["length_studs"], fit["width_studs"]) == (22, 10)
    report = fit["layout"]["proportion_report"]
    assert report["max_landmark_error_studs"] < .5
    assert report["roof_length_fraction"] == pytest.approx(4/22)
    assert not report["appearance_accepted"]
    shifted = fitted({k: v+.045 for k, v in fractions.items()})
    assert shifted["layout"]["glass_anchor_stud"] > fit["layout"]["glass_anchor_stud"]
    assert shifted["roof_end_stud"] > fit["roof_end_stud"]
    monkeypatch.setattr("car2lego3d.reservation.reserve", lambda car: None)
    car = design(fit, rear_wing=False)
    assert not any("wing" in p.module for p in car.parts)
    assert sum(p.module == "red bucket seats" for p in car.parts) == 2
    roof, = [p for p in car.parts if p.module == "proportioned cabin roof"]
    assert roof.part == "6180"
    # Side-stud bricks replace packed belt cells at the door panel mounts.
    first, last = fit["layout"]["door_interval_studs"]
    mounts = {(round(p.position[0]/20+(fit["length_studs"]-1)/2),
               round(p.position[2]/20+(fit["width_studs"]-1)/2))
              for p in car.parts if p.module == "integrated door panel mounts"}
    for y in (2, 7):
        assert all(car.cells.get((x,y,7)) == 15 or (x,y) in mounts for x in range(first,last+1))


def test_cowl_rear_pillars_and_door_sills_share_native_interfaces(monkeypatch):
    fit = fitted(dict(cowl_x=.2862, roof_front_x=.4368, roof_rear_x=.6194, cabin_rear_x=.5871))
    monkeypatch.setattr("car2lego3d.reservation.reserve", lambda car: None)
    car = design(fit)
    def module(name):
        return [p for p in car.parts if p.module == name]
    def stud_x(part):
        return part.position[0]/20+(fit["length_studs"]-1)/2
    cowl, = module("bonnet to cabin cowl beam")
    assert stud_x(cowl) == pytest.approx(fit["layout"]["front_glass_interval_studs"][0]+.5)
    assert len(module("windscreen half-stud anchors")) == 2
    pillars = module("shared cabin rear pillars")
    assert len(pillars) == 2
    assert all(stud_x(p) == fit["layout"]["glass_anchor_stud"]+1 for p in pillars)
    for skin in module("continuous door skins"):
        sill, = [p for p in module("smooth lower sills") if p.position[2]*skin.position[2] > 0]
        # Native 2-wide tiles extend 20 LDU either side of their vertical centre.
        assert sill.position[1] == pytest.approx(skin.position[1]+20)
    assert len(module("continuous fender crowns")) == 8
    assert len(module("cabin to rear shoulder buttresses")) == 2
    assert len(module("rear deck half-stud anchors")) == 4
    assert all(np_rotation_is_rigid(p.rotation) for p in car.parts)


def np_rotation_is_rigid(rotation):
    import numpy as np
    matrix = np.array(rotation).reshape(3,3)
    return np.allclose(matrix.T@matrix, np.eye(3)) and np.linalg.det(matrix) == pytest.approx(1)


def test_junction_gate_detects_one_missing_side_despite_chassis_connectivity():
    from types import SimpleNamespace
    from car2lego3d.wedge import junction_report
    modules = ["door glazing and front windscreen", "windscreen half-stud anchors",
               "proportioned cabin roof", "shared cabin rear pillars", "shared cabin rear pillars",
               "continuous door skins", "integrated door panel mounts", "cabin to engine deck ramp",
               "rear deck half-stud anchors", "cabin to rear shoulder buttresses", "rear buttress half-stud anchors"]
    car = SimpleNamespace(parts=[SimpleNamespace(module=m) for m in modules])
    pairs = [(0,1), (0,2), (2,3), (2,4), (5,6), (7,8), (9,10)]
    audit = dict(part_count=len(modules), connectivity=dict(status="passed"),
                 connections=[dict(a=f"p{a}", b=f"p{b}", kind="stud:hole", evidence="constructed fixture")
                              for a,b in pairs])
    assert junction_report(car, audit)["status"] == "passed"
    audit["connections"].pop(3)
    report = junction_report(car, audit)
    assert report["status"] == "failed"
    assert report["checks"][2]["missing_attachments"] == ["p4"]
    assert not report["appearance_accepted"]


def test_feature_requests_overlap_and_partial_success_is_reused(tmp_path, monkeypatch):
    from car2lego3d import details, dialogue
    manifest = views(tmp_path)
    barrier, calls = Barrier(4), []
    monkeypatch.setattr(dialogue, "settings", lambda: ("unused", "unused", "fixture"))
    def request(prompt, schema, directory, images, **kwargs):
        name, = images
        calls.append(name)
        barrier.wait(timeout=5)
        return dict(views=[dict(view=name, vehicle_bbox=[0,0,1,1], features=[] if name == "front" else [
            dict(id="window", kind="side_window", description="fixture", confidence=.9,
                 polygon=[[.2,.2],[.8,.2],[.8,.5],[.2,.5]])])]), dict(model="fixture")
    monkeypatch.setattr(dialogue, "request_json", request)
    with pytest.raises(ValueError, match="No visible"):
        details.extract_features(manifest, tmp_path/"features")
    assert sorted(calls) == ["back", "front", "left", "right"]
    assert not (tmp_path/"features/dialogue/front/cached.json").exists()
    def retry(prompt, schema, directory, images, **kwargs):
        name, = images
        calls.append(name)
        assert name == "front"
        return dict(views=[dict(view=name, vehicle_bbox=[0,0,1,1], features=[
            dict(id="lamp", kind="headlamp", description="fixture", confidence=.9,
                 polygon=[[.2,.2],[.8,.2],[.8,.5],[.2,.5]])])]), dict(model="fixture")
    monkeypatch.setattr(dialogue, "request_json", retry)
    result = details.extract_features(manifest, tmp_path/"features")
    assert calls[-1] == "front" and len(calls) == 5
    assert details.extract_features(manifest, tmp_path/"features") == result
    assert len(calls) == 5
