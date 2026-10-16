"""Evidence-labelled vehicle architecture selection and bounded mesh adaptation."""
from copy import deepcopy
import json
import re

import numpy as np

from .common import ROOT, digest


def catalog():
    return json.loads((ROOT / "data/recipes/families.json").read_text(encoding="utf-8"))


def classify(spec, brief=None, explicit=None):
    families = catalog()["families"]
    if explicit:
        if explicit not in families:
            raise ValueError(f"Unknown body style {explicit}; choose from {', '.join(families)}")
        return {"family": explicit, "source": "explicit selection", "confidence": 1.0}
    identity = " ".join(str((spec or {}).get(k, "")) for k in ("model", "variant")).lower()
    rules = [
        (r"\bf1\b|formula.?1|方程式", "f1_race"),
        (r"hybrid v8|\blmdh\b|\blmp[12]\b|prototype", "prototype_race"),
        (r"convertible|cabrio|roadster|敞篷", "convertible"),
        (r"pickup|pick-up|f-?150|皮卡", "pickup"),
        (r"\bmpv\b|minivan|多用途", "mpv"),
        (r"\bbus\b|coach|巴士|客车", "bus"),
        (r"\btruck\b|卡车", "truck"),
        (r"\bvan\b|transporter|厢式", "van"),
        (r"wagon|estate|touring|avant|shooting.brake|旅行", "wagon"),
        (r"defender|g.?63|wrangler|越野", "offroad"),
        (r"crossover|跨界", "crossover"),
        (r"\bsuv\b|lotus.*(for\s*me|eletre)|运动型多用途", "suv"),
        (r"hatchback|fiesta|mini cooper|掀背|两厢", "hatchback"),
        (r"sedan|saloon|轿车|三厢", "sedan"),
        (r"coupe|\bm4\b|轿跑", "coupe"),
        (r"ferrari.*f40|\bcountach\b|sports.car|supercar|跑车", "sports_car"),
    ]
    for pattern, family in rules:
        if re.search(pattern, identity):
            return {"family": family, "source": "resolved vehicle identity", "matched_pattern": pattern, "confidence": .9}
    body_style = (brief or {}).get("body_style") or (spec or {}).get("body_style")
    if body_style in families and body_style != "other":
        return {"family": body_style, "source": "GPT appearance brief" if brief else "vehicle metadata",
                "confidence": .75, "requires_identity_review": True}
    return {"family": "other", "source": "insufficient class evidence", "confidence": 0.0}


def selection_record(selection):
    family = deepcopy(catalog()["families"][selection["family"]])
    source_path = ROOT / "data/recipes/official-sources.json"
    sources = json.loads(source_path.read_text(encoding="utf-8"))["sets"]
    review_path = ROOT / "data/recipes/reviewed-modules.json"
    reviews = json.loads(review_path.read_text(encoding="utf-8"))["modules"]
    family.update(selection=selection, config_sha256=digest(ROOT / "data/recipes/families.json"),
                  source_catalog_sha256=digest(source_path), module_review_sha256=digest(review_path),
                  reviewed_modules=[item for item in reviews if item["set"] in family["official_sets"]],
                  official_references=[{"set": number, "name": sources[number]["metadata"]["name"],
                   "url": sources[number]["url"], "instruction_pdfs": sources[number]["instruction_pdfs"]}
                  for number in family["official_sets"]], official_replica=False, buildability_verified=False)
    return family


def fit_family(fit, spec, brief=None, explicit=None):
    from .coupe import fit_coupe
    from .suv import fit_suv
    selection = classify(spec, brief, explicit)
    family = selection["family"]
    config = selection_record(selection)
    if config["builder"] is None:
        raise ValueError(f"Vehicle family {family} has no implemented recipe. Classification: {selection['source']}.")
    fit["recipe_selection"] = config
    if family == "sports_car":
        from .wedge import fit_wedge, supports
        if supports(spec):
            return fit_wedge(fit, spec)
        fit["recipe"] = "sports-v1"
        return fit
    fit_suv(fit, spec) if family in ("suv", "crossover", "offroad") else fit_coupe(fit, spec)
    length = fit["length_studs"]
    start = int(np.clip(fit["roof_start_stud"], max(7, round(length*config["roof_start_fraction"][0])),
                        min(length-10, round(length*config["roof_start_fraction"][1]))))
    minimum_span = 8 if config["doors"] == 4 else 2
    end = int(np.clip(fit["roof_end_stud"], max(start+minimum_span, round(length*config["roof_end_fraction"][0])),
                      min(length-5, max(start+minimum_span, round(length*config["roof_end_fraction"][1])))))
    fit.update(recipe="suv-v1" if family in ("suv", "crossover", "offroad") else
               "coupe-v1" if family == "coupe" else f"{family}-v1",
               family_builder=config["builder"], roof_start_stud=start, roof_end_stud=end,
               door_count=config["doors"], body_family=family,
               design_prior=f"Official LEGO architecture references: {', '.join(config['official_sets'])}; {family} adaptation")
    fit["layout"].update(door_count=config["doors"], roof_interval_studs=[start-1, end+1],
                         bonnet_interval_studs=[0, start-4],
                         front_glass_interval_studs=[start-3, start-1],
                         rear_glass_interval_studs=[end+1, end+3])
    fit["inferred_parameters"] += ["body family from vehicle information", "bounded mesh roof profile"]
    fit["fixed_parameters"] += ["official-reference module topology", "native connection grid"]
    if config["builder"] == "open_wheel":
        fit.update(cabin_width_studs=2, roof_top_plates=13, deck_top_plates=6,
                   fitted_windscreen_angle_degrees=None)
    # Bound native geometry choices; images provide visual priors, not measured dimensions.
    description = " ".join(str((brief or {}).get(k, "")) for k in ("paint", "visual_description")).lower()
    fit["roof_color"] = 0 if "black roof" in description else fit["body_color"]
    fit["reference_appearance"] = {"body_style": (brief or {}).get("body_style"),
                "distinctive_features": (brief or {}).get("distinctive_features", []),
                "source": "synthetic GPT reference brief", "details_implemented": ["roof paint"] if description else []}
    return fit


def design_open_wheel(fit):
    from .carfit import Car, QUARTER, IDENTITY, HALF
    car = Car(fit)
    length, width, paint = (fit[k] for k in ("length_studs", "width_studs", "body_color"))
    center = (width-1)/2
    spine = range(width//2-1, width//2+1)
    start, end = round(length*.43), round(length*.64)
    car.fill(range(1, length-1), spine, range(2, 5), 0)
    for axle, wheel in zip(fit["axles_studs"], fit["wheel_families"]):
        x = int(axle-.5)
        car.fill(range(x, x+2), range(1, width-1), [3], 0)
        for side in (-1, 1):
            y = center+side*(width-1)/2
            car.place("3700", 0, axle, center+side*(width-3)/2, 6, "open wheel axle mounts", 6)
            car.place(wheel["tyre"], 0, axle, y, 4.75, "tyres", 18)
            car.place(wheel["rim"], 72, axle, y, 4.75, "wheel rims", 18, IDENTITY if side == 1 else HALF)
        car.place({8: "3707", 10: "3737", 12: "3708"}[width], 0, axle, center, 4.75, "axles", 7, QUARTER)
    car.fill(range(2, start), spine, range(4, 7), paint)
    for y in (width//2-2, width//2+1):
        car.fill(range(start-1, end+2), [y], range(3, 9), paint)
    car.fill(range(end+1, length-3), spine, range(4, 11), paint)
    car.fill(range(start, end+1), spine, [4], 0)
    car.fill([start-1], spine, range(4, 7), paint)
    car.fill([length-3], spine, range(4, 12), 0)
    car.pack()
    car.tiles(range(2, start), spine, 7, paint, "tapered nose", 13)
    for y in (width//2-2, width//2+1):
        car.tiles(range(start-1, end+2), [y], 9, paint, "sidepod shoulders", 14)
    car.place("4079", 0, start+1.5, center, 7, "single cockpit seat", 15, QUARTER)
    car.place("3829c01", 0, start-.5, center, 5, "steering wheel", 15, QUARTER)
    for x, height, name in ((1, 5, "front wing"), (length-3, 12, "rear wing")):
        for y in range(0, width, 2):
            car.place("3022", paint, x+.5, y+.5, height, name, 19)
        car.tiles([x, x+1], range(width), height+1, paint, name+" tiles", 20)
    return car
