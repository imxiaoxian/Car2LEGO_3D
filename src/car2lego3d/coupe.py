"""Mesh-conditioned coupe layout and native-part construction.

Model identity supplies styling priors, never claimed as recovered CAD geometry.
"""
import re

import numpy as np

from .vehicle import constraint_targets, measurement


WHEELS = (
    {"tyre": "18977", "rim": "18976", "diameter_mm": 24.0, "width_mm": 11.2,
     "arch": "18974", "arch_span_studs": 4},
    {"tyre": "89201", "rim": "55982", "diameter_mm": 25.6, "width_mm": 14.0,
     "arch": "62361", "arch_span_studs": 6},
    {"tyre": "58090", "rim": "55982", "diameter_mm": 30.4, "width_mm": 14.0,
     "arch": "62361", "arch_span_studs": 6},
)


def styling(spec):
    identity = " ".join(str(spec.get(key, "")) for key in ("model", "variant")).lower() if spec else ""
    return "bmw-m4-g82" if "bmw" in identity and re.search(r"\bm4\b", identity) and "g82" in identity else None


def is_coupe(spec, profile, length):
    if styling(spec):
        return True
    if spec is None:
        return False
    ratio = measurement(spec, "height") / measurement(spec, "length")
    broad_roof = np.count_nonzero(profile > profile.max() * .94) / length
    return .275 <= ratio <= .36 and .16 <= broad_roof <= .55 and profile.min() < .8 * profile.max()


def fit_coupe(fit, spec):
    length, width = fit["length_studs"], fit["width_studs"]
    profile = np.asarray(fit["height_profile_studs"])
    peak = int(profile.argmax())
    lo = hi = peak
    # Keep the connected high-roof interval; disjoint artefacts must not extend it.
    while lo > 0 and profile[lo-1] >= profile.max() * .99:
        lo -= 1
    while hi < length-1 and profile[hi+1] >= profile.max() * .99:
        hi += 1
    start = int(np.clip(lo, 8, length-10))
    end = int(np.clip(hi, start+2, length-6))
    targets = constraint_targets(spec, length, fit.get("target_scale_denominator"))["target_model_mm"]
    wheels = []
    for axle in ("front", "rear"):
        diameter = targets.get(f"{axle}_tyre_diameter", 28)
        tyre_width = targets.get(f"{axle}_tyre_width", 12)
        choices = []
        for item in WHEELS:
            score = 4 * (item["diameter_mm"] / diameter - 1) ** 2 + .25 * (item["width_mm"] / tyre_width - 1) ** 2
            choices.append((score, item))
        wheel = dict(min(choices, key=lambda item: item[0])[1])
        wheel["target_diameter_mm"] = diameter
        wheel["target_width_mm"] = tyre_width
        wheel["center_height_plates"] = 4.75
        track = targets.get(f"{axle}_track", (width-1)*8)
        wheel["track_studs"] = float(np.clip(round(track / 4) / 2, width-2, width-1))
        wheels.append(wheel)
    ground = min(item["center_height_plates"] * 3.2 - item["diameter_mm"] / 2 for item in wheels)
    roof_top = int(np.clip(round((targets["height"] + ground) / 3.2), 16, 23))
    deck = roof_top - 8
    fit.update(recipe="coupe-v1", styling_prior=styling(spec), roof_start_stud=start, roof_end_stud=end,
               roof_top_plates=roof_top, deck_top_plates=deck, ground_y_mm=ground,
               wheel_families=wheels, windscreen_part="92583", fitted_windscreen_angle_degrees=45.0,
               design_prior="front-engine coupe; separate six-stud glazing; mesh roof interval; native wheel selection",
               inferred_parameters=["connected roof interval", "paint", "native wheel family", "body height"],
               fixed_parameters=["six-stud glazing width", "six-plate native windscreen height", "symmetric body"],
               layout={"bonnet_interval_studs": [0, start-4], "front_glass_interval_studs": [start-3, start-1],
                       "roof_interval_studs": [start-1, end+1], "rear_glass_interval_studs": [end+1, end+3],
                       "trunk_interval_studs": [end+4, length-1], "engine_position": "front",
                       "rear_wing_default": False})
    fit["limitations"] += ["Coupe classification and G82 grille are explicit design priors, not mesh semantics.",
                           "Native tyre diameter and width may not simultaneously meet the scale tolerances."]
    return fit


def design_coupe(fit):
    from .carfit import Car, IDENTITY, QUARTER, HALF, BACK_QUARTER
    car = Car(fit)
    length, width, paint = (fit[key] for key in ("length_studs", "width_studs", "body_color"))
    center = (width-1)/2
    start, end = fit["roof_start_stud"], fit["roof_end_stud"]
    roof, deck = fit["roof_top_plates"], fit["deck_top_plates"]
    suv = fit.get("recipe") == "suv-v1"
    open_cabin = fit.get("family_builder") == "open_cabin"
    pickup = fit.get("family_builder") == "pickup"
    cabin_width = fit.get("cabin_width_studs", 6)
    windscreen = fit.get("windscreen_part", "92583")
    glass_top = roof-2
    fit["glass_top_plates"] = glass_top
    left = (width-cabin_width)//2
    sides = [left, left+cabin_width-1]
    cabin = range(left, left+cabin_width)
    inner = range(left+1, left+cabin_width-1)
    edges = [0, 1, width-2, width-1]
    car.fill(range(1, length-1), range(2, width-2), range(2, 4), 0)
    car.fill(range(length), range(width), range(4, deck-1), paint)
    car.fill(range(length), range(width), [deck-1], paint)
    car.fill(range(1, length-1), [0, width-1], [3], 0)
    for axle, wheel in zip(fit["axles_studs"], fit["wheel_families"]):
        span = wheel["arch_span_studs"]
        first = int(axle-(span-1)/2)
        car.clear(range(first, first+span), edges, range(2, deck))
        # Arch feet are on integer stud centres, outside the tyre swept envelope.
        if span == 6:
            car.fill([first, first+5], edges, range(4, deck-2), paint)
        x0 = int(axle-.5)
        car.clear(range(x0, x0+2), [2, width-3], range(3, 6))
        car.clear(range(x0, x0+2), range(2, width-2), [4])
        for side in (-1, 1):
            facing = IDENTITY if side == -1 else HALF
            car.place("3700", 0, axle, center+side*(center-2), 6, "axle mounting", 6)
            arch_y = center+side*center if span == 6 else center+side*(width/2-1)
            car.place(wheel["arch"], paint, axle, arch_y, deck+1 if span == 6 else deck, "wheel arches", 12,
                      QUARTER if span == 6 else facing)
            wheel_y = center+side*wheel["track_studs"]/2
            car.place(wheel["tyre"], 0, axle, wheel_y, wheel["center_height_plates"], "tyres", 18)
            car.place(wheel["rim"], 72, axle, wheel_y, wheel["center_height_plates"], "wheel rims", 18,
                      IDENTITY if side == 1 else HALF)
        car.place({8: "3707", 10: "3737", 12: "3708"}[width], 0, axle, center, 4.75, "axles", 7, QUARTER)

    car.clear(range(start-2, end+3), inner, range(5, deck))
    car.fill(range(start-2, end+3), inner, [4], 0)
    car.place(windscreen, 40, start-1, center, glass_top, "front windscreen", 16, QUARTER)
    if not open_cabin:
        car.place(windscreen, 40, end+1, center, glass_top, "rear windscreen", 16, BACK_QUARTER)
    for side, y in zip((-1, 1), sides):
        if open_cabin:
            continue
        car.fill(range(start, end+1), [y], range(deck, glass_top-6), paint)
        if suv:
            x = start
            pillar = start+4
            while x <= end:
                if x == pillar or x == end:
                    car.fill([x], [y], range(glass_top-6, glass_top), 0)
                    x += 1
                else:
                    car.place("87552", 40, x+.5, y, glass_top, "continuous side glazing", 16,
                              HALF if side == -1 else IDENTITY)
                    x += 2
            car.fill(range(start-1, end+2), [y], [glass_top], 0)
            continue
        for x in range(start, end, 2):
            if x+1 > end:
                continue
            car.place("60592", 0, x+.5, y, glass_top, "side window frames", 16,
                      IDENTITY if side == -1 else HALF)
            car.place("60601", 40, x+.5, y, glass_top, "side window glass", 16,
                      IDENTITY if side == -1 else HALF)
        if (end-start+1) % 2:
            car.fill([end], [y], range(deck, glass_top), paint)
        car.fill(range(start-1, end+2), [y], [glass_top], paint)
    if not open_cabin:
        car.fill(range(start-1, end+2), inner, [glass_top], 0)

    # Bonnet and boot are opaque, independently bounded surfaces for a front-engine car.
    car.clear([0], range(width), [deck-1])
    lamp_rows = {0, 1, width-2, width-1} if width == 8 else {1, 2, width-3, width-2}
    for y in (center-1, center+1):
        car.place("15068", paint, .5, y, deck-1, "bonnet nose curves", 14, QUARTER)
    for y in (0, width-1):
        if y not in lamp_rows:
            car.place("11477", paint, .5, y, deck-1, "bonnet corner curves", 14, QUARTER)
    car.clear([length-1], range(width), [deck-1])
    for y in range(width):
        if y not in lamp_rows:
            car.place("11477", paint, length-1.5, y, deck-1, "trunk edge curves", 14, BACK_QUARTER)

    for y in (left-1, left+cabin_width):
        if not open_cabin:
            car.fill(range(end+1, end+4), [y], range(deck, glass_top-3), paint)
            car.place("4286", paint, end+1, y, glass_top, "raked C pillars", 16, BACK_QUARTER)

    front = (0, 1, 0, 0, 0, 1, 1, 0, 0)
    rear = (0, -1, 0, 0, 0, -1, 1, 0, 0)
    vertical_grille = (0, 1, 0, 1, 0, 0, 0, 0, -1)
    light_h = deck-2
    for y in sorted(set(range(1, width-1)) | lamp_rows):
        for x, rotation, name in ((0, QUARTER, "front fascia mounts"),
                                  (length-1, BACK_QUARTER, "rear fascia mounts")):
            lamp = y in lamp_rows
            mount_height = deck if lamp else light_h
            car.clear([x], [y], range(mount_height-3, mount_height))
            car.place("4070" if lamp else "87087", 0, x, y, mount_height, name, 9, rotation)
    for y in ((.5, width-1.5) if width == 8 else (1.5, width-2.5)):
        car.place("3069b", 47, -.7, y, deck-1.25, "slim headlamps", 18, front)
        if not suv:
            car.place("3069b", 36, length-.3, y, deck-1.25, "bar tail lights", 18, rear)
    if suv:
        for y in np.arange(.5, width-1, 2):
            car.place("3069b", 36, length-.3, y, deck-1.25, "continuous SUV tail lights", 18, rear)
    for y in (center-1, center+1):
        if fit["styling_prior"] == "bmw-m4-g82":
            car.place("3068b", 0, -.9, y, light_h, "G82 vertical kidney grilles", 18, vertical_grille)
        else:
            car.place("3069b", 0, -.9, y, light_h-1.25,
                      "Lotus lower air intake" if fit.get("styling_prior") == "lotus-for-me" else "front grille", 18, front)
        car.place("3069b", 0, length-.1, y, light_h-1.25, "rear fascia finish", 18, rear)
    for y in np.arange(1.5, width-1, 2):
        car.place("2412b", 0, length-.1, y, light_h-4.25, "rear diffuser", 18, rear)
        car.clear([length-1], [int(y-.5), int(y+.5)], range(light_h-6, light_h-3))
        for yy in (int(y-.5), int(y+.5)):
            car.place("87087", 0, length-1, yy, light_h-3, "diffuser mounts", 8, BACK_QUARTER)

    for side in (-1, 1):
        outside = 0 if side == -1 else width-1
        facing = IDENTITY if side == -1 else HALF
        panel = (1, 0, 0, 0, 0, side, 0, -side, 0)
        # Door skins use the same native side-stud socket spacing as the sports recipe.
        door_starts = [start-2, start+3] if fit.get("door_count", 4 if suv else 2) == 4 else [start-2]
        for door_start in door_starts:
            for x in (door_start, door_start+3):
                car.clear([x], [outside], range(deck-5, deck-2))
                car.place("87087", paint, x, outside, deck-2, "door mounts", 9, facing)
            car.place("87079", paint, door_start+1.5, center+side*(center+.9), deck-3.25,
                      "door skin", 18, panel)
        car.clear([start-3], [outside], range(deck-2, deck))
        car.place("4070", paint, start-3, outside, deck+1, "mirror mounts", 13, facing)
        car.place("24246", paint, start-3, center+side*(center+.7), deck-.25, "mirrors", 18, panel)
        car.place("3070b", paint, start-3, outside, deck+2, "mirror caps", 18)

    if pickup:
        car.clear(range(end+4, length-1), range(1, width-1), range(5, deck))
        car.fill(range(end+4, length-1), range(1, width-1), [4], 0)
    car.pack()
    car.tiles([0, 1, length-2, length-1], lamp_rows, deck+1, paint, "lamp caps", 14)
    car.tiles(range(2, start-3), range(2, width-2), deck+1, paint, "bonnet surface", 14)
    car.tiles(range(end+4, length-2), range(1, width-1), 5 if pickup else deck+1,
              0 if pickup else paint, "cargo bed" if pickup else "opaque trunk", 14)
    if not open_cabin:
        car.tiles(range(start-1, end+2), cabin, roof, fit.get("roof_color", 0),
                  "SUV roof" if suv else "coupe roof", 18)
    for x in range(length):
        for y in (0, 1, width-2, width-1):
            if ((x, y, deck-1) in car.cells and 2 <= x < length-2 and x != start-3
                    and not (end+1 <= x <= end+3 and y in (left-1, left+cabin_width))):
                car.tiles([x], [y], deck+1, paint, "beltline finish", 14)
    for axle, wheel in zip(fit["axles_studs"], fit["wheel_families"]):
        if wheel["arch_span_studs"] == 6:
            for y in (0, width-1):
                car.tiles([int(axle-.5), int(axle+.5)], [y], deck+2, paint, "arch crown finish", 14)
                car.tiles([int(axle-1.5), int(axle+1.5)], [y], deck+1, paint, "arch shoulder finish", 14)
                car.tiles([int(axle-2.5), int(axle+2.5)], [y], deck, paint, "arch foot finish", 14)
    for y in (center-1, center+1):
        car.place("4079", 0, start+1.5, y, 6, "front seats", 15, QUARTER)
        if not pickup:
            car.place("4079", 0, end+.5, y, 6, "rear seats", 15, QUARTER)
    car.place("3829c01", 0, start-2, center-1, 5, "steering wheel", 15, QUARTER)
    if suv:
        car.tiles([end+2], cabin, roof, 0, "SUV roof spoiler", 19)
    elif not pickup:
        car.tiles([length-3], range(1, width-1), deck+2, 0, "trunk lip spoiler", 19)
    return car
