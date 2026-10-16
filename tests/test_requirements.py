from collections import Counter
from copy import deepcopy
import json
import sys

import numpy as np
import pytest
import trimesh

from car2lego3d import cli
from car2lego3d.carfit import convert_car, design, fit_mesh, split_steps
from car2lego3d.common import ROOT
from car2lego3d.requirements import (SCALES, assess_requirements, plan_design,
                                    requirements_for_scale, validate_plan)
from car2lego3d.studio import export_studio, verify_archive
from car2lego3d.vehicle import constraint_targets, load_spec


SPEC = ROOT / "data/vehicles/ferrari_f40_1987.json"


@pytest.mark.parametrize("scale,length,width,fields,views,step_limit", [
    ("1:38", 15, 6, 6, 6, 12), ("1:24", 23, 10, 11, 14, 12),
    ("1:16", 35, 16, 17, 18, 10), ("1:12", 46, 20, 22, 20, 8)])
def test_each_scale_has_its_own_grid_data_and_reference_requirements(scale, length, width, fields, views, step_limit):
    plan = plan_design(load_spec(SPEC), scale)
    policy = plan["policy"]
    assert (plan["grid"]["length_studs"], plan["grid"]["width_studs"]) == (length, width)
    assert len(policy["required_measurements"]) == fields
    assert len(policy["reference_views"]) == views
    assert policy["max_parts_per_step"] == step_limit
    assert plan["generation"]["draft_recipe_available"]
    assert plan["policy"]["required_mechanisms"] == {}
    assert not plan["generation"]["delivery_ready"]


def test_mechanisms_and_tolerances_increase_with_scale_without_mutable_policy_drift():
    compact, standard, detail, large = [requirements_for_scale(scale) for scale in SCALES]
    assert compact["required_mechanisms"] == {"rolling_wheels": 4}
    assert standard["required_mechanisms"]["front_hood"] == 1
    assert detail["required_mechanisms"]["opening_doors"] == 2
    assert detail["required_mechanisms"]["steering"] == 1
    assert large["required_mechanisms"]["independent_suspension"] == 4
    assert compact["dimension_tolerances_percent"]["front_tyre_diameter"] == 15
    assert standard["dimension_tolerances_percent"]["front_tyre_diameter"] == 5
    assert large["dimension_tolerances_percent"]["front_tyre_diameter"] == 3
    assert large["angle_tolerances_degrees"]["windscreen_angle_to_horizontal"] == 2
    compact["required_measurements"].clear()
    assert len(requirements_for_scale("1:38")["required_measurements"]) == 6
    with pytest.raises(ValueError, match="reserved"):
        requirements_for_scale("1:8")


def test_scale_hint_is_preserved_but_wheels_use_actual_selected_body_size():
    spec = load_spec(SPEC)
    fit = fit_mesh(trimesh.creation.box((16, 4, 2)), specifications=spec, scale="1:24")
    assert fit["length_studs"] == 23
    assert fit["nominal_scale_denominator"] == 24
    assert fit["target_scale_denominator"] == pytest.approx(4430/184)
    dimensions = fit["physical_constraints"]
    assert dimensions["scale_denominator"] == pytest.approx(4430/184)
    assert dimensions["dimensions"]["length"]["target_mm"] == pytest.approx(184)
    assert dimensions["dimensions"]["length"]["fitted_mm"] == 184
    assert dimensions["dimensions"]["length"]["error_percent"] == pytest.approx(0)
    assert dimensions["target_model_mm"]["wheelbase"] == pytest.approx(2450*184/4430)
    # Angles are never divided by the model scale.
    spec["measurements"]["windscreen_angle_to_horizontal"] = {
        "value": 35, "unit": "deg", "status": "estimated", "source": "ferrari_brochure"}
    assert constraint_targets(spec, 23, 24)["windscreen_angle_degrees"] == 35


def test_custom_length_selects_a_policy_but_does_not_claim_an_exact_named_scale():
    plan = plan_design(load_spec(SPEC), length_studs=24)
    assert plan["selection"] == "nearest_policy_for_custom_length"
    assert plan["requested_scale"] is None
    assert plan["policy"]["scale"] == "1:24"
    assert plan["target_scale_denominator"] == pytest.approx(4430/192)
    with pytest.raises(ValueError, match="not both"):
        plan_design(load_spec(SPEC), "1:24", 24)


def test_missing_estimated_unmeasured_and_excessive_errors_cannot_pass():
    spec = load_spec(SPEC)
    spec["measurements"]["windscreen_angle_to_horizontal"] = {
        "value": 35, "unit": "deg", "status": "estimated", "source": "ferrari_brochure"}
    fit = fit_mesh(trimesh.creation.box((16, 4, 2)), specifications=spec, scale="1:24")
    report = assess_requirements(fit["requirements_plan"], fit["physical_constraints"], [13])
    checks = {check["field"]: check for check in report["dimension_checks"]}
    assert checks["length"]["status"] == "within_nominal_tolerance"
    assert checks["height"]["status"] == "not_measured"
    assert checks["rear_tyre_diameter"]["status"] == "out_of_tolerance"
    assert checks["windscreen_bottom_width"]["status"] == "missing_data"
    assert checks["windscreen_angle_to_horizontal"]["status"] == "estimated_data"
    assert report["step_check"]["status"] == "failed"
    assert all(check["verified_instances"] == 0 for check in report["mechanism_checks"])
    assert not report["delivery_accepted"]


def test_snapshot_rejects_changed_vehicle_grid_or_policy():
    spec = load_spec(SPEC)
    plan = plan_design(spec, "1:24")
    validate_plan(plan, spec, 23, 10, "1:24")
    altered = deepcopy(plan)
    altered["policy"]["max_parts_per_step"] = 100
    with pytest.raises(ValueError, match="snapshot"):
        validate_plan(altered, spec, 23, 10, "1:24")
    spec["measurements"]["length"]["value"] += 1
    with pytest.raises(ValueError, match="snapshot"):
        validate_plan(plan, spec, 23, 10, "1:24")


@pytest.mark.parametrize("scale", ["1:38", "1:16", "1:12"])
def test_legacy_scale_hints_adjust_to_native_envelope_instead_of_rejecting(scale):
    fit = fit_mesh(trimesh.creation.box((16, 6, 2)), specifications=load_spec(SPEC), scale=scale)
    assert 23 <= fit["length_studs"] <= 32
    assert fit["width_studs"] in (8, 10, 12)
    assert fit["visual_grid_adjustments"]["dimension_gate"] == "advisory"
    assert design(fit).parts


def test_scale_aware_steps_and_studio_preserve_geometry_and_report_draft(tmp_path):
    mesh = tmp_path / "vehicle.glb"
    trimesh.creation.box((16, 4, 2)).export(mesh)
    fit = fit_mesh(trimesh.creation.box((16, 4, 2)), specifications=load_spec(SPEC), scale="1:24")
    car = design(fit)
    geometry = Counter((p.part, p.color, p.position, p.rotation) for p in car.parts)
    for limit in (12, 10, 8):
        split_steps(car, limit)
        assert max(Counter(p.step for p in car.parts).values()) <= limit
        assert Counter((p.part, p.color, p.position, p.rotation) for p in car.parts) == geometry
    source = convert_car(mesh, tmp_path / "03-lego", vehicle_spec=SPEC, scale="1:24")
    assembly = json.loads((source.parent / "assembly.json").read_text())["parts"]
    assert max(Counter(p["step"] for p in assembly).values()) <= 12
    for part in assembly:
        np.testing.assert_allclose(np.array(part["rotation"]).reshape(3, 3).T @
                                   np.array(part["rotation"]).reshape(3, 3), np.eye(3))
    archive = export_studio(source, tmp_path / "04-studio")
    result = verify_archive(archive, source)
    assert result["part_count"] == len(assembly)
    assert result["step_count"] == len({p["step"] for p in assembly})
    report = json.loads((source.parent / "requirements-report.json").read_text())
    assert report["step_check"]["status"] == "passed"
    assert not report["delivery_accepted"]
