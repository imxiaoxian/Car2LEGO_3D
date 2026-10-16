from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh

from car2lego3d.carfit import Car, export, fit_mesh, design
from car2lego3d.common import ROOT, digest, write_json
from car2lego3d.config import options
from car2lego3d.quality import hard_checks, iterate, score
from car2lego3d.requirements import assess_requirements, plan_design, validate_plan
from car2lego3d.search import Candidate, beam_search, snap_connector
from car2lego3d.studio import assembly_sequence
from car2lego3d.target import build_target, fingerprint, project_features
from car2lego3d.vehicle import load_spec


def test_automatic_envelope_needs_no_scale_and_stays_static():
    spec = load_spec(ROOT/"data/vehicles/ferrari_f40_1987.json")
    plan = plan_design(spec)
    assert plan["selection"] == "automatic_visual_envelope"
    assert plan["requested_scale"] is None
    assert plan["policy"]["scale"] == "1:12"
    preference = plan["visual_size_preference"]
    assert preference["denominator"] == 12 and not preference["exact"]
    assert preference["preferred_grid"] == dict(length_studs=46, width_studs=20)
    assert preference["native_capacity_limited"]
    assert any(c["kind"] == "native_module_capacity" for c in plan["soft_constraints"])
    assert plan["policy"]["required_mechanisms"] == {}
    assert "physical_build" not in plan["policy"]["required_evidence"]
    grid = plan["grid"]
    validate_plan(plan, spec, grid["length_studs"], grid["width_studs"], None)
    fit = fit_mesh(trimesh.creation.box((16, 6, 2)), specifications=spec, requirements_plan=plan)
    assert design(fit).parts
    report = assess_requirements(plan, fit["physical_constraints"], [8])
    assert report["dimension_enforcement"] == "advisory"
    assert not report["physical_build_required"]
    assert "physical_build" not in {c["evidence"] for c in report["evidence_checks"]}
    assert options(dict(proportion_mode="nominal"))["proportion_mode"] == "visual"


def test_beam_search_retains_compatible_non_greedy_solution():
    groups = dict(glass=[Candidate("small", "glass", 0, interfaces={"roof": 6}),
                         Candidate("wide", "glass", 1, interfaces={"roof": 8})],
                  roof=[Candidate("expensive", "roof", 10, requires={"roof": 6}),
                        Candidate("smooth", "roof", 0, requires={"roof": 8}),
                        Candidate("illegal", "roof", -100, legal=False, rejection="collision")])
    beam, rejected = beam_search(groups)
    assert [c.id for c in beam[0][1]] == ["wide", "smooth"]
    assert rejected == [dict(id="illegal", reason="collision")]
    assert snap_connector([10, 5, 2], [0, 1, 0], np.eye(3)) == [10, 4, 2]
    with pytest.raises(ValueError, match="rigid"):
        snap_connector([0]*3, [0]*3, np.eye(3)*2)


def test_iteration_keeps_best_legal_and_stops_after_three_small_improvements(tmp_path):
    def evaluate(candidate, iteration):
        components = dict(silhouette=.7, identity_features=.6, surface_interface=.5, interior=.8, proportion=.9)
        return dict(**score(components, ["window outline"]), hard_checks=dict(passed=candidate != "bad"))
    best, report = iterate("good", lambda *args: ["bad", "equal"], evaluate, tmp_path)
    assert best == "good"
    assert report["stop_reason"] == "stagnation_three_rounds"
    assert len(report["iterations"]) == 4
    assert not report["delivery_accepted"]
    assert score({"silhouette": 1})["weighted_score"] == .35
    with pytest.raises(ValueError):
        options(dict(max_quality_iterations=17))


def test_native_gate_rejects_stale_colliders_and_reflections():
    row = dict(part="3001", position=[0, 0, 0], rotation=np.eye(3).ravel().tolist())
    audit = dict(assembly_sha256="current", part_count=1, collision_count=0, collision_coverage="complete",
        connectivity=dict(status="passed"), catalog={"3001.dat": dict(validity=dict(geometry=True, connections=True, collider=True))})
    assert hard_checks([row], audit, "current")["passed"]
    assert not hard_checks([row], audit, "old")["passed"]
    audit["collision_coverage"] = "partial"
    assert not hard_checks([row], audit, "current")["passed"]
    with pytest.raises(ValueError, match="rigid"):
        assembly_sequence("1 4 0 0 0 2 0 0 0 1 0 0 0 1 3001.dat")


def test_geometry_and_style_targets_are_separate_and_unknown_stays_unknown(tmp_path):
    mesh = trimesh.creation.box((16, 6, 2))
    path = tmp_path/"mesh.glb"
    mesh.export(path)
    target = build_target(dict(model="fixture", measurements=dict(door=dict(value=None, status="unknown"))), {}, path)
    assert target["measurements"]["door"]["confidence"] is None
    assert target["lego_style"]["scope"] == "this_task_only"
    assert target["sizing"]["detail_standard"] == "1:12"
    assert not target["sizing"]["exact_scale_required"]
    program = dict(views=[dict(view="left", vehicle_bbox=[0, 0, 1, 1], features=[dict(id="glass", kind="side_window",
        description="fixture", confidence=.7, polygon=[[.3,.2],[.7,.2],[.7,.5],[.3,.5]])])])
    projected = project_features(mesh, program, deepcopy(target))
    feature, = projected["features"]
    assert feature["surface_type"] == "independent_patch"
    assert len(feature["boundary_mesh_coordinates"]) == 4
    assert projected["target_fingerprint"] != fingerprint(target)


@pytest.mark.parametrize("view", ["left", "right", "front", "back"])
@pytest.mark.parametrize("flipped", [False, True])
def test_bbox_relative_features_project_independently_of_image_margins(view, flipped):
    mesh = trimesh.creation.box((16, 6, 2))
    feature = dict(id="lamp", kind="headlamp", description="fixture", confidence=.8,
                   polygon=[[.2,.3],[.4,.3],[.4,.6],[.2,.6]])
    target = dict(geometry=dict(views={view: {}}))
    outputs = []
    for bbox in ([0,0,1,1], [.15,.2,.85,.8]):
        program = dict(views=[dict(view=view, vehicle_bbox=bbox, features=[feature])])
        result = project_features(mesh, program, deepcopy(target), flipped=flipped)
        outputs.append(result["features"][0]["boundary_mesh_coordinates"])
    np.testing.assert_allclose(outputs[0], outputs[1], atol=1e-8)
    np.testing.assert_allclose(np.array(outputs[1])[:,2], [.4,.4,-.2,-.2], atol=1e-8)


def test_repeated_optimization_uses_source_parts_and_detects_external_rebuild(tmp_path):
    from car2lego3d.optimization import search_source
    work = tmp_path/"optimization"
    work.mkdir()
    fit = dict(length_studs=24, width_studs=10)
    car = Car(fit)
    car.place("3024", 15, 0, 0, 1, "source", 1)
    export(car, tmp_path)
    write_json(tmp_path/"fit.json", fit)
    base, record = search_source(tmp_path, work)
    assert len(base.parts) == 1
    car.place("3070b", 15, 0, 0, 2, "finish", 2)
    export(car, tmp_path)
    record["selected_files"] = {name: digest(tmp_path/name) for name in ("assembly.json", "fit.json")}
    write_json(work/"source.json", record)
    resumed, same = search_source(tmp_path, work)
    assert len(resumed.parts) == 1
    assert same["snapshot"] == record["snapshot"]
    car.place("3024", 0, 2, 0, 1, "external rebuild", 1)
    export(car, tmp_path)
    rebuilt, different = search_source(tmp_path, work)
    assert len(rebuilt.parts) == 3
    assert different["snapshot"] != record["snapshot"]
    snapshot = work/different["snapshot"]
    (snapshot/"assembly.json").write_text("corrupt", encoding="ascii")
    with pytest.raises(ValueError, match="Immutable"):
        search_source(tmp_path, work)
