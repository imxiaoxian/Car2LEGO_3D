"""Auditable side-view landmarks, normalized from the nose to the body tail."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path

from .common import digest, write_json


FIELDS = ("nose_x", "tail_x", "cowl_x", "roof_front_x", "roof_rear_x", "cabin_rear_x",
          "front_axle_x", "rear_axle_x", "roof_y", "sill_y", "tyre_bottom_y")


def normalize_landmarks(raw):
    if any(isinstance(raw[k], bool) or not math.isfinite(raw[k]) or not 0 <= raw[k] <= 1 for k in FIELDS):
        raise ValueError("Invalid side-view coordinates")
    span = raw["tail_x"] - raw["nose_x"]
    if abs(span) < .2:
        raise ValueError("Side-view body span is too small")
    fractions = {k: (raw[k]-raw["nose_x"])/span for k in FIELDS if k.endswith("_x")}
    if not (0 < fractions["cowl_x"] < fractions["roof_front_x"] < fractions["roof_rear_x"] < 1
            and fractions["cowl_x"] < fractions["cabin_rear_x"] < .9
            and 0 < fractions["front_axle_x"] < fractions["rear_axle_x"] < 1):
        raise ValueError("Inconsistent observed cabin or axle landmarks")
    return fractions


def validate_layout(record, views):
    from .multiview import load_views
    _, paths = load_views(views)
    if record.get("views_sha256") != digest(views):
        raise ValueError("Layout observations belong to a different view manifest")
    normalized = {}
    for name in ("left", "right"):
        observed = record["views"][name]
        if observed["image_sha256"] != digest(paths[name]):
            raise ValueError("Layout observations belong to a different side image")
        normalized[name] = normalize_landmarks(observed["raw"])
    fractions = {k: sum(v[k] for v in normalized.values())/2 for k in normalized["left"]}
    if any(abs(record["fractions"].get(k, -1)-v) > 1e-6 for k, v in fractions.items()):
        raise ValueError("Layout averages do not match the recorded observations")
    disagreement = {k: abs(normalized["left"][k]-normalized["right"][k]) for k in fractions}
    if max(disagreement.values()) > .12:
        raise ValueError("Opposing side views disagree on the vehicle layout")
    return {**record, "opposing_view_disagreement": disagreement}


def observe_layout(views, directory):
    from .dialogue import request_json
    from .multiview import load_views
    from .research import object_schema
    directory = Path(directory)
    target = directory / "layout.json"
    if target.is_file():
        return validate_layout(json.loads(target.read_text(encoding="utf-8")), views)
    _, paths = load_views(views)
    schema = object_schema({k: {"type": "number", "minimum": 0, "maximum": 1} for k in FIELDS})
    prompt = ("Locate these landmarks on this exact side-view production car. Return normalized FULL IMAGE coordinates. "
        "x increases image-right, y increases down. nose_x and tail_x are BODY endpoints excluding the rear wing. "
        "cowl_x is where the FRONT WINDSCREEN meets the bonnet. roof_front_x is the front upper corner of the "
        "windscreen. roof_rear_x is the back edge of the cabin roof, excluding engine cover and wing. "
        "cabin_rear_x is the lower rear corner of the SIDE WINDOW (B pillar), excluding rear intakes. "
        "front_axle_x and rear_axle_x are wheel centers. roof_y excludes the wing. sill_y is door bottom. "
        "tyre_bottom_y is road contact. Use only visible geometry, no written explanation.")

    def observe(name):
        raw, audit = request_json(prompt, schema, directory/name, {name: paths[name]}, timeout=180)
        return name, dict(image_sha256=digest(paths[name]), raw=raw,
                          fractions=normalize_landmarks(raw), audit=audit)

    with ThreadPoolExecutor(max_workers=2) as executor:
        records = dict(executor.map(observe, ("left", "right")))
    fractions = {k: sum(v["fractions"][k] for v in records.values())/2 for k in records["left"]["fractions"]}
    record = validate_layout(dict(views_sha256=digest(views), views=records, fractions=fractions,
        source="Direct model observation of two GPT side views", physical_verified=False), views)
    write_json(target, record)
    return record
