"""Validated, versioned generation options shared by CLI and desktop jobs."""
from copy import deepcopy


VERSION = "feature-modules-v2"
WEIGHTS = dict(silhouette=.35, identity_features=.30, surface_interface=.20,
               interior=.10, proportion=.05)
DEFAULTS = dict(proportion_mode="visual", function_profile="static",
                part_policy="library_first_custom_if_needed", optimization_profile="quality",
                beam_width=8, max_module_candidates=16, max_quality_iterations=16,
                appearance_weights=WEIGHTS)


def options(overrides=None):
    result = deepcopy(DEFAULTS)
    overrides = overrides or {}
    unknown = set(overrides)-set(result)
    if unknown:
        raise ValueError(f"Unknown generation options: {sorted(unknown)}")
    result.update(overrides)
    # Older callers can still send nominal, but proportions no longer block a visual design.
    if result["proportion_mode"] == "nominal":
        result["proportion_mode"] = "visual"
    choices = dict(proportion_mode={"visual", "nominal"}, function_profile={"static"},
                   part_policy={"library_first_custom_if_needed", "library_only"},
                   optimization_profile={"quality", "draft"})
    for key, allowed in choices.items():
        if result[key] not in allowed:
            raise ValueError(f"Unsupported {key}: {result[key]}")
    for key, maximum in (("beam_width", 8), ("max_module_candidates", 16), ("max_quality_iterations", 16)):
        if type(result[key]) is not int or not 1 <= result[key] <= maximum:
            raise ValueError(f"{key} must be an integer in 1..{maximum}")
    if result["appearance_weights"] != WEIGHTS:
        raise ValueError("Appearance weights are versioned; arbitrary weights are unsupported")
    return result
