"""Task-scoped geometry and LEGO styling targets, with explicit provenance."""
import hashlib
import json
from pathlib import Path

import numpy as np

from .common import digest, write_json
from .config import VERSION, options


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    allow_nan=False).encode()).hexdigest()


def file_record(path):
    path = Path(path)
    return dict(file=str(path.resolve()), sha256=digest(path))


def build_target(spec, fit, mesh_path, reference_views=None, style_reference=None, config=None):
    from .multiview import load_reference_images
    from .reference_plan import REFERENCE_GROUPS
    references = {group: {} for group in REFERENCE_GROUPS}
    if reference_views:
        _, groups = load_reference_images(reference_views)
        references = {group: {name: file_record(path) for name, path in images.items()}
                      for group, images in groups.items()}
    measurements = {}
    for name, item in (spec or {}).get("measurements", {}).items():
        status = item.get("status", "unknown")
        measurements[name] = dict(value=item.get("value"), unit=item.get("unit"), status=status,
            source=item.get("source"), confidence=item.get("confidence",
                None if item.get("value") is None else .5 if status == "estimated" else .75))
    target = dict(schema_version=1, pipeline_version=VERSION, config=options(config),
        identity=dict(model=(spec or {}).get("model"), variant=(spec or {}).get("variant")),
        sizing=dict(mode="visual", exact_scale_required=False,
            preference=fit.get("requirements_plan", {}).get("visual_size_preference"),
            selected_grid={k: fit.get(k) for k in ("length_studs", "width_studs")},
            detail_standard="1:12", dimensions_are_acceptance_gate=False),
        measurements=measurements, geometry=dict(mesh=file_record(mesh_path), views=references["views"],
            supplemental_views=references["supplemental_views"], details=references["details"],
            coordinate_frame="X front to rear; Y left to right; Z up", registration="bbox_orthographic_approximation",
            calibration=fit.get("physical_calibration"), front_flipped=fit.get("front_orientation_flipped", False)),
        lego_style=dict(reference=file_record(style_reference) if style_reference else None,
            scope="this_task_only", priority="explicit reference appearance overrides inferred defaults",
            supplied=bool(style_reference)),
        landmarks=fit.get("layout", {}).get("proportion_report"), features=[],
        module_targets=fit.get("detail_program", {}).get("module_targets", []),
        limitations=["Orthographic image registration is approximate, not calibrated camera recovery.",
                     "Transparent surfaces may require an independent fitted patch."])
    target["input_fingerprint"] = fingerprint(target)
    return target


def project_features(mesh, program, target, flipped=False):
    """Raycast image boundaries; retain missing transparent surfaces as independent patches."""
    low, high = np.asarray(mesh.bounds)
    span = high-low
    records = []
    for view in (program or {}).get("views", []):
        name = view["view"]
        for feature in view["features"]:
            # The observation contract already expresses polygons inside the vehicle bbox.
            uv = np.clip(np.asarray(feature["polygon"]), 0, 1)
            origins, directions = [], []
            for u, v in uv:
                z = high[2]-v*span[2]
                if name in {"left", "right"}:
                    x = low[0]+(1-u if name == "right" else u)*span[0]
                    if flipped:
                        x = low[0]+high[0]-x
                    side = -1 if name == "left" else 1
                    origins.append([x, low[1]-span[1] if side == -1 else high[1]+span[1], z])
                    directions.append([0, -side, 0])
                else:
                    side = -1 if name == "front" else 1
                    if flipped:
                        side *= -1
                    y = low[1]+(1-u if name == "front" else u)*span[1]
                    origins.append([low[0]-span[0] if side == -1 else high[0]+span[0], y, z])
                    directions.append([-side, 0, 0])
            origins, directions = np.asarray(origins), np.asarray(directions)
            hits, rays, _ = mesh.ray.intersects_location(origins, directions, multiple_hits=True)
            boundary, found = [], 0
            for i, (origin, direction) in enumerate(zip(origins, directions)):
                points = hits[rays == i]
                if len(points):
                    point = points[np.argmin(np.linalg.norm(points-origin, axis=1))]
                    found += 1
                else:
                    axis = int(np.argmax(abs(direction)))
                    point = origin.copy()
                    point[axis] = (low[axis]+high[axis])/2
                boundary.append(point.tolist())
            transparent = feature["kind"] in {"side_window", "windscreen", "headlamp", "taillamp"}
            records.append(dict(view=name, id=feature["id"], kind=feature["kind"],
                description=feature["description"], source=target["geometry"]["views"].get(name),
                detail_evidence=feature.get("detail_evidence", []),
                image_confidence=feature["confidence"], registration_confidence=.5,
                boundary_mesh_coordinates=boundary, ray_hit_fraction=found/len(origins),
                surface_type="independent_patch" if transparent else "mesh_boundary",
                reconstruction_verified=False, patch_fit="image_boundary_plane" if transparent else None))
    target["features"] = records
    target["target_fingerprint"] = fingerprint(target)
    return target


def save_target(directory, target):
    write_json(Path(directory)/"vehicle-target.json", target)
