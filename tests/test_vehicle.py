import copy
import numpy as np
import pytest
import trimesh

from car2lego3d.carfit import design, fit_mesh
from car2lego3d.common import ROOT
from car2lego3d.vehicle import (calibrate_mesh, constrained_axles, constraint_targets,
                              load_spec, tyre_diameter, validate_spec)


def specification():
    return load_spec(ROOT / "data/vehicles/ferrari_f40_1987.json")


def test_sourced_tyres_and_uniform_scale_conversion():
    spec = specification()
    assert tyre_diameter(spec, "front") == pytest.approx(627.8)
    assert tyre_diameter(spec, "rear") == pytest.approx(666.3)
    targets = constraint_targets(spec, 24)
    assert targets["scale_denominator"] == pytest.approx(4430/192)
    assert targets["target_model_mm"]["wheelbase"] == pytest.approx(2450 * 192/4430)
    assert targets["windscreen_angle_degrees"] is None
    assert "door_length" in targets["unknown_fields"]


def test_calibration_preserves_topology_and_color_and_exports_metre_glb(tmp_path):
    mesh = trimesh.creation.box((16, 4, 2))
    mesh.visual.vertex_colors = (201, 26, 9, 255)
    calibrated, report = calibrate_mesh(mesh, specification())
    np.testing.assert_allclose(calibrated.extents, [4430, 1980, 1130])
    np.testing.assert_array_equal(calibrated.faces, mesh.faces)
    np.testing.assert_array_equal(calibrated.visual.vertex_colors, mesh.visual.vertex_colors)
    np.testing.assert_allclose(mesh.extents, [16, 4, 2])
    assert not report["physical_measurement_verified"]
    calibrated.convert_units("m")
    calibrated.export(tmp_path / "calibrated.glb")
    loaded = trimesh.load(tmp_path / "calibrated.glb", force="mesh")
    np.testing.assert_allclose(loaded.extents, [4.43, 1.98, 1.13], rtol=1e-6)


def test_wheelbase_constraint_overrides_bad_mesh_wheel_hypotheses():
    spec = specification()
    axles, source = constrained_axles(spec, 24, [4.5, 16.5])
    assert axles[1] - axles[0] == 13
    assert "overhang split from mesh" in source
    mesh = trimesh.creation.box((16, 4, 2))
    fit = fit_mesh(mesh, color=4, specifications=spec)
    expected_axles, _ = constrained_axles(spec, fit["length_studs"], fit["source_axles_studs"],
                                         fit["effective_scale_denominator"])
    assert fit["axles_studs"] == expected_axles
    assert fit["requirements_plan"]["visual_size_preference"]["denominator"] == 12
    assert fit["effective_scale_denominator"] == pytest.approx(4430/(fit["length_studs"]*8))
    errors = fit["physical_constraints"]["dimensions"]
    assert errors["width"]["fitted_mm"] == fit["width_studs"]*8
    assert errors["rear_tyre_width"]["error_percent"] < -20
    assert not fit["physical_constraints"]["appearance_accepted"]


def test_known_overhangs_anchor_the_axle_positions():
    spec = specification()
    for field, value in (("front_overhang", 923), ("rear_overhang", 1057)):
        spec["measurements"][field] = {"value": value, "unit": "mm", "status": "estimated",
                                       "source": "ferrari_brochure"}
    validate_spec(spec)
    axles, source = constrained_axles(spec, 24, [7.5, 20.5])
    assert axles == [4.5, 17.5]
    assert source == "physical front overhang and wheelbase"


def test_zero_target_keeps_absolute_error_without_dividing_by_zero():
    spec = specification()
    spec["measurements"]["front_overhang"] = {"value": 0, "unit": "mm", "status": "estimated",
                                               "source": "ferrari_brochure"}
    fit = fit_mesh(trimesh.creation.box((16, 4, 2)), color=4, specifications=spec)
    error = fit["physical_constraints"]["dimensions"]["front_overhang"]
    assert error["error_percent"] is None
    assert error["error_mm"] == error["fitted_mm"]


@pytest.mark.parametrize("width", [8, 10, 12])
def test_wider_body_keeps_native_transforms_and_side_stud_connections(width):
    fit = fit_mesh(trimesh.creation.box((16, 4, 2)), color=4, width=width)
    car = design(fit)
    assert sum(p.module == "tyres" for p in car.parts) == 4
    assert sum(p.module == "integrated windscreen" for p in car.parts) == 1
    for skin in [p for p in car.parts if p.module == "door skin"]:
        sockets = [np.array(skin.position) + np.array(skin.rotation).reshape(3, 3) @ np.array((x, 8, 0))
                   for x in (-30, 30)]
        mounts = [p for p in car.parts if p.module == "door mounts"]
        for socket in sockets:
            assert any(np.allclose(socket, np.array(m.position) +
                       np.array(m.rotation).reshape(3, 3) @ np.array((0, 10, -10))) for m in mounts)
    for part in car.parts:
        rotation = np.array(part.rotation).reshape(3, 3)
        np.testing.assert_allclose(rotation.T @ rotation, np.eye(3))
        assert np.linalg.det(rotation) == pytest.approx(1)


@pytest.mark.parametrize("field,change", [
    ("length", {"value": float("nan")}), ("width", {"unit": "cm"}),
    ("wheelbase", {"value": 5000}), ("height", {"source": "missing"}),
    ("door_length", {"value": 1000, "status": "unknown"}),
    ("windscreen_angle_to_horizontal", {"value": 95, "status": "estimated", "source": "ferrari_brochure"})])
def test_specs_reject_wrong_units_missing_sources_and_impossible_values(field, change):
    spec = copy.deepcopy(specification())
    spec["measurements"][field].update(change)
    with pytest.raises(ValueError):
        validate_spec(spec)


def test_side_view_annotation_converts_size_and_angle_and_keeps_unknowns(tmp_path):
    from PIL import Image
    from car2lego3d.measurement import measure_side_view
    reference = tmp_path / "constructed-reference.png"
    Image.new("RGB", (1000, 600), "white").save(reference)
    annotations = {"view": "orthographic_side", "reference_measurement": "wheelbase",
                   "reference_points": [[100, 400], [600, 400]], "endpoint_uncertainty_px": 2,
                   "segments": {"door_length": [[300, 350], [500, 350]],
                                "windscreen_angle_to_horizontal": [[300, 250], [400, 175]]}}
    measured = measure_side_view(specification(), annotations, reference)
    assert measured["measurements"]["door_length"]["value"] == pytest.approx(980)
    angle = measured["measurements"]["windscreen_angle_to_horizontal"]
    assert angle["value"] == pytest.approx(36.86989765)
    assert angle["status"] == "estimated"
    assert angle["estimated_annotation_error"] > 0
    assert measured["measurements"]["windscreen_bottom_width"]["value"] is None
    annotations["view"] = "perspective"
    with pytest.raises(ValueError, match="orthographic"):
        measure_side_view(specification(), annotations, reference)
