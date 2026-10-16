"""Orthographic boundaries plus unregistered detail evidence drive module targets."""
import json
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.optimize import linear_sum_assignment
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

from .common import digest, write_json
from .research import object_schema


VERSION = "vehicle-details-v2"
KINDS = ("headlamp", "taillamp", "grille", "intake", "side_window", "windscreen",
         "pillar", "door_seam", "spoiler", "badge", "mirror", "wheel", "other")
COLORS = {"headlamp": 47, "taillamp": 36, "grille": 0, "intake": 0,
          "side_window": 40, "windscreen": 40, "pillar": 0, "door_seam": 0}


def feature_schema():
    number = {"type": "number", "minimum": 0, "maximum": 1}
    point = {"type": "array", "items": number, "minItems": 2, "maxItems": 2}
    polygon = {"type": "array", "items": point, "minItems": 3, "maxItems": 32}
    return object_schema({"views": {"type": "array", "minItems": 4, "maxItems": 4,
        "items": object_schema({"view": {"enum": ["front", "back", "left", "right"]},
            "vehicle_bbox": {"type": "array", "items": number, "minItems": 4, "maxItems": 4},
            "features": {"type": "array", "maxItems": 80, "items": object_schema({
                "id": {"type": "string"}, "kind": {"enum": list(KINDS)},
                "description": {"type": "string"}, "confidence": number, "polygon": polygon})}})}})


def extract_features(views, directory):
    from .dialogue import request_json, settings
    from .multiview import load_reference_images
    manifest, groups = load_reference_images(views)
    paths = groups["views"]
    directory = Path(directory)
    signature = {"version": VERSION, "model": settings()[2], "views_sha256": digest(views),
                 "images": {k: digest(p) for k, p in paths.items()}}
    target = directory / "features.json"
    if target.is_file():
        cached = json.loads(target.read_text(encoding="utf-8"))
        if cached.get("signature") == signature:
            validate_features(cached)
            return cached
    prompt = ("Inspect all four labelled orthographic views of the SAME vehicle. Segment all visible identity details: "
        "each separate headlamp/DRL, rear light segments and connecting bar, grille openings, intakes, "
        "side window panes, windscreen, pillars, door seams, spoilers, mirrors, wheels and badges. "
        "Do not replace observations with typical brand styling. Preserve separate kidney grilles, thin light "
        "outlines and split lamps as separate polygons. An absent grille must not be invented. "
        "vehicle_bbox is [left,top,right,bottom], normalized in full image, covering car body and tyres, "
        "excluding mirrors and shadows. Feature polygon coordinates are normalized RELATIVE TO that bbox, "
        "origin upper left, x rightwards, y downwards. Clip polygons to [0,1]. Each feature must have a unique "
        "nonempty id within its view, confidence and accurate polygon boundary, not a generic rectangle. "
        "Left side front points image LEFT; right side front points image RIGHT. Return every view exactly once. "
        "Include uncertain visible features at low confidence; do not claim physical dimensions or hidden details.")
    schema = feature_schema()
    schema["properties"]["views"]["minItems"] = 1
    schema["properties"]["views"]["maxItems"] = 1
    def observe(item):
        name, path = item
        per_view = directory / "dialogue" / name
        cache = per_view / "cached.json"
        key = dict(model=signature["model"], image_sha256=digest(path), version=VERSION,
                   prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
        per_prompt = prompt.replace("all four labelled orthographic views", "the labelled orthographic view")
        per_prompt = per_prompt.replace("Return every view exactly once.", f"Return ONLY the {name} view, once.")
        per_prompt += " Limit to 24 significant visible features; use concise descriptions."
        evidence = {name: path}
        for group in ("supplemental_views", "details"):
            for shot, file in groups[group].items():
                if name in evidence_views(shot):
                    evidence["unregistered_"+shot] = file
        key["evidence"] = {label: digest(file) for label, file in evidence.items()}
        if cache.is_file():
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if saved.get("signature") == key:
                validate_view(saved["answer"]["views"][0])
                return saved["answer"], saved["audit"]
        per_prompt += (" Additional images labelled unregistered are appearance evidence only. "
                       "Use them to distinguish lamp segments, glass frames and panel interfaces, "
                       "but locate every polygon ONLY on the labelled orthographic image. Never "
                       "copy close-up coordinates or invent hidden parts in the primary image.")
        value, audit = request_json(per_prompt, schema, per_view, evidence)
        if len(value["views"]) != 1 or value["views"][0]["view"] != name:
            raise ValueError("Feature answer does not match its labelled view")
        validate_view(value["views"][0])
        write_json(cache,dict(signature=key,answer=value,audit=audit))
        return value, audit
    with ThreadPoolExecutor(max_workers=4) as executor:
        observations = list(executor.map(observe, paths.items()))
    answer = {"views":[view for value,_ in observations for view in value["views"]]}
    audit = dict(mode="parallel_per_view_llm_dialogue", views=[a for _,a in observations])
    result = {**answer, "signature": signature, "audit": audit, "version": VERSION,
              "appearance_accepted": False}
    validate_features(result)
    result["reference_evidence"] = {
        group: {name: dict(file=str(path), sha256=digest(path),
                           modules=manifest.get(group, {}).get(name, {}).get("modules", []))
                for name, path in images.items()} for group, images in groups.items()}
    result["module_targets"] = extract_detail_evidence(groups, result, directory / "detail-dialogue")
    for view in result["views"]:
        for feature in view["features"]:
            feature["detail_evidence"] = [item for item in result["module_targets"]
                if dict(view=view["view"], id=feature["id"]) in item["primary_features"]]
    write_json(target, result)
    for view in result["views"]:
        with Image.open(paths[view["view"]]) as image:
            image = image.convert("RGB")
        draw = ImageDraw.Draw(image)
        box = view["vehicle_bbox"]
        for f in view["features"]:
            points = [((box[0]+x*(box[2]-box[0]))*image.width,
                       (box[1]+y*(box[3]-box[1]))*image.height) for x, y in f["polygon"]]
            draw.line(points+[points[0]], fill="cyan", width=3)
            draw.text(points[0], f["id"], fill="black", stroke_width=1, stroke_fill="white")
        image.save(directory / (view["view"]+"-features.png"))
    return result


def evidence_views(shot):
    return {
        "top": ("front", "back", "left", "right"),
        "front_right": ("front", "right"), "rear_left": ("back", "left"), "rear_right": ("back", "right"),
        "front_lights": ("front",), "rear_lights": ("back",), "door_windows": ("left", "right"),
        "windscreen_cowl": ("front", "left", "right"), "rear_transition": ("back", "left", "right"),
        "side_intakes": ("left", "right"), "wheel_arches": ("front", "left"),
    }.get(shot, ())


def extract_detail_evidence(groups, program, directory):
    """Keep inferred module requirements distinct from calibrated 3D boundaries."""
    from .dialogue import request_json, settings
    from .reference_plan import DETAIL_SHOTS
    from .target import fingerprint
    directory = Path(directory)
    link = object_schema({"view": {"enum": ["front", "back", "left", "right"]}, "id": {"type": "string"}})
    observation = object_schema({"module": {"enum": sorted({m for _, modules in DETAIL_SHOTS.values() for m in modules})},
        "description": {"type": "string", "minLength": 1}, "construction_guidance": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "primary_features": {"type": "array", "items": link, "maxItems": 12},
        "contradicts_primary": {"type": "boolean"}})
    schema = object_schema({"observations": {"type": "array", "items": observation, "maxItems": 16},
                            "unknowns": {"type": "array", "items": {"type": "string"}}})
    features = {v["view"]: [dict(id=f["id"], kind=f["kind"], description=f["description"])
                            for f in v["features"]] for v in program["views"]}
    valid_links = {(view, f["id"]) for view, items in features.items() for f in items}

    def observe(item):
        shot, path = item
        images = {"unregistered_detail_"+shot: path,
                  **{name: groups["views"][name] for name in evidence_views(shot)}}
        shot_features = {name: features[name] for name in evidence_views(shot)}
        prompt = ("Inspect the labelled synthetic vehicle close-up for module construction. Describe visible "
            "lamp layers, lens recess, window/frame/door integration, adjoining surfaces and interior components "
            "where actually visible. Give concise LEGO part-selection guidance without inventing part IDs or "
            "certifying connections. Preserve contours and nesting rather than filling them with small cubes. "
            "This camera is UNREGISTERED: do not output 3D coordinates, physical dimensions or fabricated "
            "projection matrices. Link to existing primary features only when clearly corresponding; use an "
            "empty list for seats, dashboard or anything absent from exterior views. Flag contradictions to the "
            "primary images rather than silently replacing vehicle identity or panel state. Record unknowns. "
            f"Shot: {shot}. Allowed primary feature IDs: " + json.dumps(shot_features))
        signature = dict(version=VERSION, model=settings()[2], primary_features=shot_features,
                         images={name: digest(file) for name, file in images.items()},
                         prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
        folder = directory / shot
        cache = folder / "cached.json"
        if cache.is_file() and (saved := json.loads(cache.read_text(encoding="utf-8"))).get("signature") == signature:
            answer, audit = saved["answer"], saved["audit"]
        else:
            answer, audit = request_json(prompt, schema, folder, images)
            import jsonschema
            jsonschema.validate(answer, schema)
            if any(f["view"] not in shot_features or (f["view"], f["id"]) not in valid_links
                   for obs in answer["observations"] for f in obs["primary_features"]):
                raise ValueError(f"Unknown primary feature link in {shot}")
            write_json(cache, dict(signature=signature, answer=answer, audit=audit))
        source = dict(shot=shot, file=str(path), sha256=digest(path), camera_registration="unregistered")
        return [dict(**obs, source=source, unknowns=answer["unknowns"], audit=audit,
                     target_id=shot+"/"+fingerprint(obs)[:16], geometry_verified=False)
                for obs in answer["observations"]]

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(observe, groups["details"].items()))
    return [item for result in results for item in result]


def validate_features(program):
    import jsonschema
    jsonschema.validate({"views": [observation_view(view) for view in program["views"]]}, feature_schema())
    if {v["view"] for v in program["views"]} != {"front", "back", "left", "right"}:
        raise ValueError("特征提取必须包含四个不同视角。")
    for view in program["views"]:
        validate_view(view)


def validate_view(view):
    import jsonschema
    jsonschema.validate(observation_view(view), feature_schema()["properties"]["views"]["items"])
    if not view["features"]:
        raise ValueError("No visible vehicle features were extracted")
    l, t, r, b = view["vehicle_bbox"]
    if r-l < .1 or b-t < .1:
        raise ValueError("Invalid vehicle bounding box")
    ids = [f["id"] for f in view["features"]]
    if any(not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("Feature IDs must be nonempty and unique")
    for f in view["features"]:
        points = np.array(f["polygon"])
        area = abs(np.dot(points[:, 0], np.roll(points[:, 1], 1)) -
                   np.dot(points[:, 1], np.roll(points[:, 0], 1))) / 2
        if area < 1e-6:
            raise ValueError("Degenerate vehicle feature polygon")


def observation_view(view):
    # Links are added after model-response validation and persist in the combined cache.
    return {**view, "features": [{key: value for key, value in feature.items() if key != "detail_evidence"}
                                 for feature in view["features"]]}


def mask(feature, size=384):
    image = Image.new("1", (size, size))
    ImageDraw.Draw(image).polygon([(x*(size-1), y*(size-1)) for x, y in feature["polygon"]], fill=1)
    return np.asarray(image, dtype=bool)


def assign_cells(features, boxes, minimum_coverage=.035):
    """Area voting plus unique representatives prevents a thin light being erased by an intake."""
    if not features or not boxes:
        return [None]*len(boxes), np.empty((0, len(boxes)))
    scores = np.zeros((len(features), len(boxes)))
    for i, f in enumerate(features):
        pixels = mask(f)
        for j, (u0, v0, u1, v1) in enumerate(boxes):
            x0, y0 = np.floor(np.clip([u0, v0], 0, 1)*383).astype(int)
            x1, y1 = np.ceil(np.clip([u1, v1], 0, 1)*383).astype(int)
            region = pixels[y0:y1+1, x0:x1+1]
            scores[i, j] = region.mean() if region.size else 0
    weighted = scores * np.array([3 if f["kind"] in {"headlamp", "taillamp"} else 1 for f in features])[:, None]
    labels = [int(np.argmax(weighted[:, j])) if weighted[:, j].max() > minimum_coverage else None for j in range(len(boxes))]
    if boxes:
        rows, columns = linear_sum_assignment(-weighted)
        for i, j in zip(rows, columns):
            if scores[i, j] > .005:
                labels[j] = int(i)
    return [None if i is None else features[i] for i in labels], scores


def cell_box(fit, view, x, height, horizontal_span=1, vertical_mm=3.2):
    length, width = fit["length_studs"], fit["width_studs"]
    extent = width if view in {"front", "back"} else length
    u = (x+.5)/extent
    if view in {"front", "right"}:
        u = 1-u
    ground = fit.get("ground_y_mm", 3.2)
    total = fit.get("roof_top_plates", 14)*3.2-ground
    v = 1-((height-.5)*3.2-ground)/total
    return [u-horizontal_span/(2*extent), v-vertical_mm/(2*total),
            u+horizontal_span/(2*extent), v+vertical_mm/(2*total)]


def fascia_features(features, boxes):
    """Fit the observed fascia band to the native mounting band, retaining its internal ordering."""
    if not features or not boxes:
        return features
    top = min(y for f in features for _, y in f["polygon"])
    bottom = max(y for f in features for _, y in f["polygon"])
    low, high = min(b[1] for b in boxes), max(b[3] for b in boxes)
    transformed = deepcopy(features)
    for f in transformed:
        f["polygon"] = [[x, low+(y-top)/(bottom-top)*(high-low)] for x, y in f["polygon"]]
    return transformed


def glazing_features(fit, view, features):
    """Preserve pane/pillar ordering inside the recipe's supported cabin envelope."""
    if not features:
        return []
    points = np.array([p for f in features for p in f["polygon"]])
    low, high = points.min(axis=0), points.max(axis=0)
    if np.any(high-low < 1e-6):
        return []
    start, end = fit["roof_start_stud"], fit["roof_end_stud"]
    deck, glass = fit["deck_top_plates"], fit["glass_top_plates"]
    boxes = [cell_box(fit, view, x, h+.5) for x in (start,end) for h in (deck,glass-1)]
    target_low = np.array([min(b[0] for b in boxes), min(b[1] for b in boxes)])
    target_high = np.array([max(b[2] for b in boxes), max(b[3] for b in boxes)])
    result = deepcopy(features)
    for f in result:
        f["polygon"] = (target_low+(np.array(f["polygon"])-low)/(high-low)*(target_high-target_low)).tolist()
    return result


def prepare_structure(car):
    """Reserve real mounting volumes before packing; glaze side walls at one-plate resolution."""
    program = car.fit.get("detail_program")
    if not program:
        return
    fit = car.fit
    if fit.get("detail_surface_mode") == "wedge":
        return
    if fit.get("detail_surface_mode") == "envelope":
        from .envelope_suv import prepare_fascia
        prepare_fascia(car)
        return
    length, width = fit["length_studs"], fit["width_studs"]
    deck = fit.get("deck_top_plates", 8)
    from .carfit import QUARTER, BACK_QUARTER
    # Reuse the recipe's fascia heights, while replacing its styling and mounts.
    tops = {view: max((-p.position[1]/8 for p in car.parts if p.module in modules), default=deck-2)
            for view, modules in (("front", {"front fascia mounts"}), ("back", {"rear fascia mounts"}))}
    for p in car.parts:
        if p.module == "fascia mounts":
            tops["front" if p.position[0] < 0 else "back"] = -p.position[1]/8
    car.parts = [p for p in car.parts if p.module not in DEFAULT_FASCIA]
    fit["detail_fascia_rows"] = {}
    for view, x, rotation in (("front", 0, QUARTER), ("back", length-1, BACK_QUARTER)):
        top = int(round(tops[view]))
        rows = sorted(range(top, 5, -3))
        fit["detail_fascia_rows"][view] = rows
        if rows:
            car.fill([0, 1] if view == "front" else [length-2, length-1],
                     range(width), [rows[0]-4], fit["body_color"])
        for h in rows:
            for y in range(width):
                car.clear([x], [y], range(h-3, h))
                car.place("87087", fit["body_color"], x, y, h, "detail/"+view+" mounts", 12, rotation)
    if fit.get("family_builder") in {"closed", "pickup"} and fit.get("detail_surface_mode") != "envelope":
        glass_top = fit.get("glass_top_plates", fit.get("roof_top_plates", 16)-2)
        start, end = fit["roof_start_stud"], fit["roof_end_stud"]
        for view, side in (("left", 0), ("right", width-1)):
            y = (width-fit.get("cabin_width_studs", 6))//2 if side == 0 else (width+fit.get("cabin_width_studs", 6))//2-1
            record = next(v for v in program["views"] if v["view"] == view)
            features = [f for f in record["features"] if f["kind"] in {"side_window", "pillar"} and f["confidence"] >= .55]
            if not any(f["kind"] == "side_window" for f in features):
                continue
            car.parts = [p for p in car.parts if p.module not in {
                "side window frames", "side window glass", "continuous side glazing"} or
                abs(p.position[2] - (y-(width-1)/2)*20) > 11]
            cells = [(x, h) for x in range(start, end+1) for h in range(deck, glass_top)]
            labels, _ = assign_cells(glazing_features(fit, view, features), [cell_box(fit, view, x, h+.5) for x, h in cells])
            for (x, h), f in zip(cells, labels):
                car.cells[x, y, h] = COLORS[f["kind"]] if f else fit["body_color"]
                if f:
                    fit.setdefault("detail_glazing_cells", {}).setdefault(view, []).append(
                        dict(x=x,y=y,h=h,id=f["id"],color=COLORS[f["kind"]]))


DEFAULT_FASCIA = {"slim headlamps", "bar tail lights", "continuous SUV tail lights", "G82 vertical kidney grilles",
                 "front grille", "Lotus lower air intake", "rear fascia finish", "front intake", "slim split headlamps",
                 "front fascia mounts", "rear fascia mounts", "fascia mounts", "grille mounts", "rear panel mounts",
                 "tail light mounts", "headlamp bases", "headlamp recesses", "headlamps", "tail lights", "rear grille",
                 "rear diffuser", "diffuser mounts"}


def finish_structure(car):
    program = car.fit.get("detail_program")
    if not program:
        return None
    fit = car.fit
    if fit.get("detail_surface_mode") in {"envelope", "wedge"}:
        return car
    width, length, paint = fit["width_studs"], fit["length_studs"], fit["body_color"]
    car.parts = [p for p in car.parts if p.module not in DEFAULT_FASCIA]
    for view in program["views"]:
        name = view["view"]
        features = [f for f in view["features"] if f["kind"] in COLORS and f["confidence"] >= .55]
        if name in {"front", "back"}:
            supported = [f for f in features if f["kind"] in {"headlamp", "taillamp", "grille", "intake"}]
            cells = [(y, h) for h in fit.get("detail_fascia_rows", {}).get(name, []) for y in range(width)]
            boxes = [cell_box(fit, name, y, h-1.25, vertical_mm=8) for y, h in cells]
            observed = supported if fit.get("detail_surface_mode") == "envelope" else fascia_features(supported, boxes)
            labels, _ = assign_cells(observed, boxes)
            rotation = (0, 1, 0, 0, 0, 1, 1, 0, 0) if name == "front" else (0, -1, 0, 0, 0, -1, 1, 0, 0)
            for (y, h), f in zip(cells, labels):
                module = f"detail/{name}/" + (f["id"] if f else "body")
                car.place("3070b", COLORS[f["kind"]] if f else paint, -.9 if name == "front" else length-.1,
                          y, h-1.25, module, 19, rotation)
    return car


def retention_report(car):
    """Read the final, step-sorted assembly; IDs refer to the exported part order."""
    program = car.fit.get("detail_program")
    if not program:
        return None
    fit = car.fit
    length = fit["length_studs"]
    coverage = []
    for view in program["views"]:
        name = view["view"]
        hits = {f["id"]: [] for f in view["features"]}
        for i, p in enumerate(car.parts):
            prefix = f"detail/{name}/"
            if p.module.startswith(prefix) and p.module[len(prefix):] in hits:
                hits[p.module[len(prefix):]].append(i)
        from .carfit import PLATES, BRICKS, TILES
        native_cells = [c for c in fit.get("detail_native_cells", []) if c["view"] == name]
        if name in {"left", "right"}:
            native_cells = fit.get("detail_glazing_cells", {}).get(name, [])
        for i, part in enumerate(car.parts):
            dims = (PLATES | BRICKS | TILES).get(part.part)
            thickness = 1 if part.part in PLATES or part.part in TILES else 3
            from .glazing import PANELS
            if part.part in PANELS and part.module == "side window glass":
                panel_width, thickness = PANELS[part.part]
                dims = (panel_width, 1)
            if not dims or part.module not in {"body and chassis", "side window glass", "lamp lenses"}:
                continue
            matrix = np.array(part.rotation).reshape(3, 3)
            if not np.allclose(matrix[:, 1], [0, 1, 0]):
                continue
            x = part.position[0]/20+(length-1)/2
            y = part.position[2]/20+(fit["width_studs"]-1)/2
            h = -part.position[1]/8
            span_x = abs(matrix[0, 0])*dims[0] + abs(matrix[0, 2])*dims[1]
            span_y = abs(matrix[2, 0])*dims[0] + abs(matrix[2, 2])*dims[1]
            for cell in native_cells:
                if (part.color == cell["color"] and abs(cell["x"]-x) <= (span_x-1)/2+.01
                        and abs(cell["y"]-y) <= (span_y-1)/2+.01
                        and h-thickness <= cell["h"] < h and cell["id"] in hits):
                    if i not in hits[cell["id"]]:
                        hits[cell["id"]].append(i)
        if name in {"left", "right"}:
            # Read back actual native glazing/opaque cells, rather than counting the desired polygons.
            side = -1 if name == "left" else 1
            for i, part in enumerate(car.parts):
                cabin_width = fit.get("cabin_width_studs", 6)
                target_z = side*(cabin_width-1)*10
                if part.module != "body and chassis" or abs(part.position[2]-target_z) > .01:
                    continue
                x = part.position[0]/20+(length-1)/2
                h = -part.position[1]/8
                from .carfit import PLATES, BRICKS
                dims = (PLATES if part.part in PLATES else BRICKS).get(part.part)
                if not dims:
                    continue
                local_x, local_z = dims
                matrix = np.array(part.rotation).reshape(3, 3)
                span = abs(matrix[0, 0])*local_x + abs(matrix[0, 2])*local_z
                thickness = 1 if part.part in PLATES else 3
                for cell in fit.get("detail_glazing_cells", {}).get(name, []):
                    if (part.color == cell["color"] and abs(cell["x"]-x) <= (span-1)/2+.01
                            and h-thickness <= cell["h"] < h and cell["id"] in hits):
                        if i not in hits[cell["id"]]:
                            hits[cell["id"]].append(i)
        for f in view["features"]:
            ids = hits[f["id"]]
            coverage.append({"view": name, "id": f["id"], "kind": f["kind"], "description": f["description"],
                "confidence": f["confidence"], "status": "represented" if ids else "unresolved",
                "part_indices": ids, "reason": "Native cells overlap observed polygon; shape fidelity unverified." if ids else
                "Below resolution, low confidence, outside supported surface, or no matching native structure."})
    unresolved = [f for f in coverage if f["status"] != "represented"]
    return {"version": VERSION, "features": coverage, "feature_count": len(coverage),
            "represented_count": len(coverage)-len(unresolved), "unresolved_count": len(unresolved),
            "critical_unresolved_count": sum(f["kind"] in {"headlamp", "taillamp", "grille", "intake", "side_window", "pillar"} for f in unresolved),
            "status": "incomplete" if unresolved else "represented_unverified", "appearance_accepted": False,
            "assembly_sha256": None,
            "full_detail_preservation_verified": False,
            "detail_module_targets": [dict(target_id=item["target_id"], module=item["module"],
                source=item["source"], status="requires_render_review", contradicts_primary=item["contradicts_primary"])
                for item in program.get("module_targets", [])],
            "limitations": ["Finite native-part resolution cannot encode every mesh or image detail.",
                            "Lamps are quantized to plate height; exact optical shape is not retained.",
                            "Glazing polygons are fitted to the supported cabin envelope, retaining pane/pillar ordering.",
                            "Polygon coverage does not verify rendered shape, insertion paths or physical buildability."]}
