"""Soft visual sizing preferences, legacy policies and evidence checks."""
from copy import deepcopy
import hashlib
import json
import math

from .vehicle import measurement, tyre_diameter, validate_spec


POLICY_VERSION = "car2lego3d-visual-v3"
VISUAL_SIZE_HINT = 12
SCALES = ("1:38", "1:24", "1:16", "1:12")
RECIPE_LENGTH_RANGE = (23, 32)
RECIPE_WIDTHS = (8, 10, 12)
OVERALL = ["length", "width", "height", "wheelbase"]
TYRES = ["front_tyre_diameter", "rear_tyre_diameter"]
GLASS = ["windscreen_bottom_width", "windscreen_slant_height", "windscreen_angle_to_horizontal"]
EXTERIOR = ["front_three_quarter", "rear_three_quarter", "front", "rear", "side", "top"]
EVIDENCE = ["native_dimensions", "native_collision", "connectivity", "appearance_comparison",
            "studio_open", "physical_build"]


def _policy(scale, label, detail, fields, mechanisms, views, tyre_error, tyre_width_error, step_parts):
    compact = scale == "1:38"
    tolerances = {"length": 5 if compact else 3, "width": 10 if compact else 5,
                  "height": 8 if compact else 5, "wheelbase": 5 if compact else 3,
                  "front_track": 10 if compact else 5, "rear_track": 10 if compact else 5,
                  "door_length": 5, "door_panel_height": 5, "seat_width": 5,
                  "windscreen_bottom_width": 8 if compact else 5, "windscreen_slant_height": 8 if compact else 5,
                  "engine_length": 5, "engine_width": 5, "engine_height": 5}
    for axle in ("front", "rear"):
        tolerances.update({f"{axle}_tyre_diameter": tyre_error, f"{axle}_tyre_width": tyre_width_error,
                           f"{axle}_rim_diameter": 3})
    return {"version": POLICY_VERSION, "scale": scale, "label": label,
            "visual_requirements": detail, "required_measurements": fields,
            "required_mechanisms": mechanisms, "reference_views": EXTERIOR + views,
            "dimension_tolerances_percent": tolerances,
            "angle_tolerances_degrees": {"windscreen_angle_to_horizontal": 8 if compact else
                                          {"1:24": 5, "1:16": 3, "1:12": 2}[scale],
                                          "door_opening_angle": 5},
            "max_parts_per_step": step_parts, "motion_sample_count": 9,
            "required_evidence": list(EVIDENCE), "standard_part_scaling_allowed": False,
            "applicability": "Engine, intakes and wing follow the exact vehicle variant; exceptions require evidence.",
            "excluded_mechanisms": ["gearbox", "powered_drivetrain"]}


_compact = OVERALL + TYRES
_standard = _compact + ["front_tyre_width", "rear_tyre_width"] + GLASS
_detailed = _standard + ["front_track", "rear_track", "door_length", "door_panel_height",
                         "door_opening_angle", "seat_width"]
_precision = _detailed + ["engine_length", "engine_width", "engine_height",
                         "front_rim_diameter", "rear_rim_diameter"]
_rolling = {"rolling_wheels": 4}
_covers = {**_rolling, "front_hood": 1, "engine_cover": 1}
_doors = {**_covers, "opening_doors": 2, "steering": 1}
_exterior = ["recognizable silhouette and stance", "wheel arches", "front and rear lights",
             "transparent glazing", "vehicle-specific intakes and rear treatment"]
_interior = ["continuous body surfaces", "dimensioned windscreen", "simplified seats and dashboard",
             "simplified engine bay"]
_detail = ["separate seats and headrests", "dashboard and console", "door panels and opening clearances"]
_views = ["cockpit", "engine_bay", "hood_open", "engine_cover_open", "hinges", "intakes", "lights", "seats"]
_POLICIES = {
    "1:38": _policy("1:38", "compact", _exterior, _compact, _rolling, [], 15, 20, 12),
    "1:24": _policy("1:24", "exterior", _exterior + _interior, _standard, _covers, _views, 5, 10, 12),
    "1:16": _policy("1:16", "interior", _exterior + _interior + _detail, _detailed, _doors,
                      _views + ["doors_open", "door_hinges", "dashboard", "steering"], 5, 7.5, 10),
    "1:12": _policy("1:12", "structure", _exterior + _interior + _detail +
                      ["engine plumbing", "layered body panels", "underbody load paths", "suspension clearances"],
                      _precision, {**_doors, "independent_suspension": 4},
                      _views + ["doors_open", "door_hinges", "dashboard", "steering", "suspension", "underbody"],
                      3, 5, 8),
}


def requirements_for_scale(scale):
    if scale not in SCALES:
        raise ValueError(f"Available scale policies: {', '.join(SCALES)}; 1:8 and 1:10 are reserved.")
    return deepcopy(_POLICIES[scale])


def spec_fingerprint(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def measurement_source(spec, field):
    if field in TYRES:
        axle = field.split("_")[0]
        names = [f"{axle}_{suffix}" for suffix in ("tyre_width", "tyre_aspect", "rim_diameter")]
        value = tyre_diameter(spec, axle)
    else:
        names, value = [field], measurement(spec, field)
    statuses = [spec["measurements"].get(name, {}).get("status", "unknown") for name in names]
    status = "missing" if value is None else "estimated" if "estimated" in statuses else "sourced"
    return {"field": field, "value": value, "status": status,
            "source_fields": names, "source_statuses": statuses}


def plan_design(spec, scale=None, length_studs=None, width_studs=None):
    validate_spec(spec)
    if scale == "auto":
        scale = None
    if scale is not None and length_studs is not None:
        raise ValueError("Choose --scale or --length-studs, not both.")
    preferred_grid = {"length_studs": round(measurement(spec, "length")/(VISUAL_SIZE_HINT*8)),
                      "width_studs": 2*round(measurement(spec, "width")/(VISUAL_SIZE_HINT*16))}
    if scale is not None:
        policy = requirements_for_scale(scale)
        denominator = int(scale.split(":")[1])
        length_studs = math.floor(measurement(spec, "length") / denominator / 8 + .5)
        selection = "explicit_scale"
    else:
        automatic = length_studs is None
        if automatic:
            # The 1:12 size preference is soft; current native modules still bound candidates.
            ratio = measurement(spec, "length")/measurement(spec, "width")
            candidates = [(length, width) for length in range(22, 33) for width in RECIPE_WIDTHS]
            length_studs, auto_width = min(candidates, key=lambda pair:
                3*(pair[0]/pair[1]-ratio)**2
                + .015*(pair[0]-preferred_grid["length_studs"])**2
                + .08*(pair[1]-preferred_grid["width_studs"])**2)
            width_studs = auto_width if width_studs is None else width_studs
        if isinstance(length_studs, bool) or not isinstance(length_studs, int) or length_studs <= 0:
            raise ValueError("length_studs must be a positive integer.")
        denominator = measurement(spec, "length") / (length_studs * 8)
        nearest = "1:12" if automatic else min(SCALES, key=lambda candidate: abs(math.log(denominator / int(candidate.split(":")[1]))))
        policy = requirements_for_scale(nearest)
        selection = "automatic_visual_envelope" if automatic else "nearest_policy_for_custom_length"
    target_width = measurement(spec, "width") / denominator / 8
    if width_studs is None:
        width_studs = max(4, 2 * math.floor(target_width / 2 + .5))
    if isinstance(width_studs, bool) or not isinstance(width_studs, int) or width_studs <= 0:
        raise ValueError("width_studs must be a positive integer.")
    soft_constraints = []
    from .wedge import LENGTH_RANGE, supports
    recipe_range = LENGTH_RANGE if supports(spec) else RECIPE_LENGTH_RANGE
    if not recipe_range[0] <= length_studs <= recipe_range[1]:
        soft_constraints.append({"kind": "length", "requested": length_studs,
                                 "supported_range": list(recipe_range),
                                 "message": f"Visual fit may adjust the body length into {recipe_range[0]}..{recipe_range[1]} studs."})
    if width_studs not in RECIPE_WIDTHS:
        soft_constraints.append({"kind": "width", "requested": width_studs,
                                 "supported_values": list(RECIPE_WIDTHS),
                                 "message": "Visual fit may select the nearest supported body width."})
    if scale is None and automatic and (length_studs != preferred_grid["length_studs"] or width_studs != preferred_grid["width_studs"]):
        soft_constraints.append(dict(kind="native_module_capacity", preferred_grid=preferred_grid,
            selected_grid=dict(length_studs=length_studs, width_studs=width_studs),
            message="Current native module family cannot achieve the preferred 1:12 envelope; larger glass, tyres and structure need validation."))
    fields = [measurement_source(spec, field) for field in policy["required_measurements"]]
    policy["required_mechanisms"] = {}
    policy["required_evidence"] = [e for e in policy["required_evidence"] if e != "physical_build"]
    policy["function_profile"] = "static"
    policy["dimension_enforcement"] = "advisory"
    scale_architecture = {
        "1:38": {"body": "compact silhouette shell", "detail_budget": "outline, window band, lights",
                 "interior": "front seats optional", "surface_priority": "large panels"},
        "1:24": {"body": "integrated smooth shell", "detail_budget": "door/window relation, embedded lamps, intakes",
                 "interior": "two seats, dashboard, steering wheel, console", "surface_priority": "large panels and wedge slopes"},
        "1:16": {"body": "layered body modules", "detail_budget": "independent pillars, lamp layers, door inner faces",
                 "interior": "two seats and full cockpit", "surface_priority": "curved and wedge panels"},
        "1:12": {"body": "high detail structural shell", "detail_budget": "lamp cavities, glazing frames, underbody",
                 "interior": "cockpit, seats, dashboard and console", "surface_priority": "large smooth panels with local custom parts"},
    }[policy["scale"]]
    from .config import options
    generation_config = options()
    return {"policy": policy, "selection": selection, "requested_scale": scale,
            "sizing_mode": "automatic_visual" if scale is None and automatic else "legacy_visual_hint",
            "visual_size_preference": dict(denominator=VISUAL_SIZE_HINT, exact=False,
                detail_standard="1:12", preferred_grid=preferred_grid,
                native_capacity_limited=any(c["kind"] == "native_module_capacity" for c in soft_constraints)),
            "target_scale_denominator": denominator, "vehicle_spec_fingerprint": spec_fingerprint(spec),
            "grid": {"length_studs": length_studs, "width_studs": width_studs,
                     "target_length_mm": measurement(spec, "length") / denominator,
                     "target_width_mm": target_width * 8,
                     "effective_core_length_scale_denominator": measurement(spec, "length") / (length_studs * 8)},
            "physical_data": fields,
            "missing_measurements": [item["field"] for item in fields if item["status"] == "missing"],
            "estimated_measurements": [item["field"] for item in fields if item["status"] == "estimated"],
            "visual_priority": ["silhouette", "lamp_and_glazing_identity", "surface_continuity", "interior", "nominal_proportion"],
            "scale_architecture": scale_architecture,
            "soft_constraints": soft_constraints,
            "generation": {"draft_recipe_available": True, "blockers": [],
                           "capability_status": "experimental_visual_envelope",
                           "delivery_ready": False, "config": generation_config},
            "notes": ["Grid dimensions are nominal core dimensions, not native exterior bounds.",
                      "Reference views are an evidence plan; reconstruction consumes the four exterior views.",
                      "Sourced specifications are not independent physical verification.",
                      "Scale is a visual starting point; native part selection may adjust width, wheelbase and cabin proportions."]}


def validate_plan(plan, spec, length, width, scale):
    automatic = plan.get("selection") == "automatic_visual_envelope"
    expected = plan_design(spec, scale, None if scale is not None or automatic else length,
                           None if automatic else width)
    if plan != expected or plan["grid"]["length_studs"] != length:
        raise ValueError("Requirements snapshot does not match the vehicle, scale, grid or policy version.")


def assess_requirements(plan, dimensions, step_counts):
    policy = plan["policy"]
    checks = []
    for item in plan["physical_data"]:
        field = item["field"]
        result = dimensions["dimensions"].get(field) if dimensions else None
        if field in policy["angle_tolerances_degrees"]:
            error = dimensions.get("windscreen_angle_error_degrees") if dimensions and field.startswith("windscreen") else None
            tolerance = policy["angle_tolerances_degrees"][field]
            basis = "native_part_angle" if error is not None else "not_measured"
        else:
            error = result["error_percent"] if result else None
            tolerance = policy["dimension_tolerances_percent"].get(field)
            basis = "nominal_core" if field in ("length", "width") else "nominal_part_family"
        if item["status"] != "sourced":
            status = "missing_data" if item["status"] == "missing" else "estimated_data"
        elif error is None or tolerance is None:
            status = "not_measured"
        elif abs(error) > tolerance:
            status = "out_of_tolerance"
        else:
            status = "within_nominal_tolerance"
        checks.append({"field": field, "status": status, "error": error, "tolerance": tolerance,
                       "unit": "deg" if field in policy["angle_tolerances_degrees"] else "%",
                       "basis": basis, "enforcement": "advisory", "native_measurement_verified": False})
    max_parts = max(step_counts, default=0)
    return {"policy_version": policy["version"], "scale": policy["scale"],
            "dimension_checks": checks,
            "mechanism_checks": [{"mechanism": key, "required_instances": count,
                                  "verified_instances": 0, "status": "unverified"}
                                 for key, count in policy["required_mechanisms"].items()],
            "reference_checks": [{"view": view, "status": "unverified"} for view in policy["reference_views"]],
            "evidence_checks": [{"evidence": evidence, "status": "unverified"}
                                for evidence in policy["required_evidence"]],
            "dimension_enforcement": "advisory", "physical_build_required": False,
            "visual_size_preference": deepcopy(plan["visual_size_preference"]),
            "step_check": {"maximum_parts": max_parts, "limit": policy["max_parts_per_step"],
                           "status": "passed" if max_parts <= policy["max_parts_per_step"] else "failed",
                           "assembly_order_verified": False},
            "status": "draft", "delivery_accepted": False,
            "limitations": ["Nominal dimensions do not certify native geometry, motion, appearance or physical assembly."]}
