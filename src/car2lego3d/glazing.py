"""Fit hollow native panes and opaque supports to the observed window belt."""
import numpy as np


PANELS = {"4865b": (2, 3), "30413": (4, 3), "4864b": (2, 6)}


def side_layout(fit, start, end):
    first, last = start+1, end
    if last-first < 2:
        raise ValueError("Side glazing needs room for a pane and a pillar")
    observations = []
    for view in fit.get("detail_program", {}).get("views", []):
        if view["view"] not in {"left", "right"}:
            continue
        features = [f for f in view["features"] if f["kind"] in {"side_window", "pillar"}
                    and f["confidence"] >= .55]
        panes = [f for f in features if f["kind"] == "side_window"]
        pillars = [f for f in features if f["kind"] == "pillar"]
        if len(panes) < 2 or not pillars:
            continue
        middle = np.mean([np.mean(np.array(f["polygon"])[:, 0]) for f in panes[:2]])
        chosen = min(pillars, key=lambda f: abs(np.mean(np.array(f["polygon"])[:, 0])-middle))
        points = np.array([p for f in features for p in f["polygon"]])[:, 0]
        low, high = float(points.min()), float(points.max())
        if high-low < 1e-6:
            continue
        u = (float(np.mean(np.array(chosen["polygon"])[:, 0]))-low)/(high-low)
        # Some vision outputs describe right-side panes in front-first order.
        # Use labelled front/rear panes when present before assuming camera direction.
        fronts = [f for f in panes if "front" in f["id"].lower()]
        rears = [f for f in panes if "rear" in f["id"].lower()]
        reversed_view = view["view"] == "right"
        if fronts and rears:
            reversed_view = np.mean(np.array(fronts[0]["polygon"])[:, 0]) > np.mean(np.array(rears[0]["polygon"])[:, 0])
        observations.append(first+(1-u if reversed_view else u)*(last-first))
    observed = float(np.median(observations)) if observations else (first+last)/2
    belt = np.array(fit["appearance_envelope"]["paint_plates"])
    available = float(fit["glass_top_plates"]-np.median(belt[first:last+1]))
    height = min((3, 6), key=lambda h: abs(h-available)+max(0, h-available)*.5)
    widths = (4, 2) if height == 3 else (2,)

    def span(lo, hi):
        panels, opaque = [], []
        cursor = lo
        while cursor <= hi:
            width = next((w for w in widths if cursor+w-1 <= hi), None)
            if width is None:
                opaque.append(cursor)
                cursor += 1
                continue
            part = next(p for p, dims in PANELS.items() if dims == (width, height))
            panels.append(dict(part=part, first=cursor, width=width))
            cursor += width
        return panels, opaque

    choices = []
    candidates = range(first+2, last-1) if last-first >= 4 else range(first, last+1)
    for pillar in candidates:
        front, a = span(first, pillar-1)
        rear, b = span(pillar+1, last)
        panes, opaque = front+rear, [pillar, *a, *b]
        if not panes:
            continue
        cost = .6*abs(pillar-observed)+.7*len(panes)+20*(len(opaque)-1)
        choices.append((cost, pillar, panes, opaque))
    _, pillar, panes, opaque = min(choices, key=lambda row: (row[0], row[1]))
    return dict(pillar=pillar, pane_height_plates=height,
                pane_part="4865b" if height == 3 else "4864b", panels=panes,
                opaque_columns=sorted(opaque), observed_pillar_studs=observations,
                observed_glass_height_plates=available,
                height_error_plates=abs(height-available),
                method="window-belt fit with native hollow panes, seam cost and supported pillars",
                roof_support="opaque pillars and connected roof plates; no clutch assumed on short panes",
                transparent_brick_fallback=False)


def place_sides(car, layout, start, end, sides):
    from .carfit import HALF, IDENTITY
    from .details import assign_cells, cell_box
    fit = car.fit
    top = fit["glass_top_plates"]
    bottom = top-layout["pane_height_plates"]
    belt = fit["appearance_envelope"]["paint_plates"]
    fit["detail_glazing_cells"] = {}
    for name, y in zip(("left", "right"), sides):
        car.clear(range(start+1, end+1), [y], range(bottom, top))
        for x in range(start+1, end+1):
            car.fill([x], [y], range(min(int(round(belt[x]))-1, bottom-1), bottom-1), fit["body_color"])
        # A dark sill hides the pane's base flange and supports every lower socket.
        car.fill(range(start+1, end+1), [y], [bottom-1], 0)
        for pane in layout["panels"]:
            car.place(pane["part"], 40, pane["first"]+(pane["width"]-1)/2, y, top,
                      "side window glass", 16, HALF if name == "left" else IDENTITY)
        for x in layout["opaque_columns"]:
            car.fill([x], [y], range(bottom, top), 0)
        view = next(v for v in fit["detail_program"]["views"] if v["view"] == name)
        features = [f for f in view["features"] if f["kind"] in {"side_window", "pillar"} and f["confidence"] >= .55]
        cells = [(x, h) for x in range(start+1, end+1) for h in range(bottom, top)]
        labels, _ = assign_cells(features, [cell_box(fit, name, x, h+1) for x, h in cells])
        fit["detail_glazing_cells"][name] = [dict(x=x, y=y, h=h, id=f["id"],
            color=0 if x in layout["opaque_columns"] else 40) for (x,h), f in zip(cells, labels) if f]


def support_roof(car, layout, start, end, sides):
    """Short panels have no upper studs: carry the roof on a B-pillar crossbeam."""
    from .carfit import QUARTER
    top = car.fit["glass_top_plates"]
    width = sides[1]-sides[0]+1
    crossbars = {6:"3666", 8:"3460"}
    rails = {3:"3623",4:"3710",6:"3666",8:"3460",10:"4477",12:"60479"}
    span = max(length for length in rails if length <= end-start)
    car.place(crossbars[width], 0, layout["pillar"], (sides[0]+sides[1])/2, top-1,
              "window pillar crossbeam", 17, QUARTER)
    for y in (sides[0]+1,sides[1]-1):
        car.place(rails[span], 0, end-(span-1)/2, y, top, "concealed roof rails", 17)
