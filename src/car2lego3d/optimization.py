"""Countach module search with native checks, cached renders and visual feedback."""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import shutil
import subprocess

from PIL import Image, ImageDraw

from .carfit import Car, Placement, export
from .common import ROOT, digest, write_json
from .config import VERSION, options
from .gpt_reference import configuration
from .part_index import build_index
from .quality import hard_checks, iterate, score
from .search import Candidate, beam_search
from .target import fingerprint


def read(path, fallback=None):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).is_file() else fallback


def search_source(directory, work):
    """Resume from immutable source parts, never from a previously added finish."""
    names = ("assembly.json", "fit.json")
    current = {name: digest(directory/name) for name in names}
    record = read(work/"source.json", {})
    if current == record.get("selected_files"):
        snapshot = (work/record["snapshot"]).resolve()
        if not snapshot.is_relative_to(work.resolve()):
            raise ValueError("Optimization source must stay inside its work directory")
        if any(not (snapshot/name).is_file() or digest(snapshot/name) != record["source_files"][name]
               for name in names):
            raise ValueError("Immutable optimization source is missing or changed")
    else:
        source_id = fingerprint(current)
        snapshot = work/"sources"/source_id
        snapshot.mkdir(parents=True, exist_ok=True)
        for name in names:
            if (snapshot/name).is_file():
                if digest(snapshot/name) != current[name]:
                    raise ValueError("Immutable optimization source has changed")
            else:
                shutil.copyfile(directory/name, snapshot/name)
        record = dict(snapshot=str(snapshot.relative_to(work)), source_files=current,
                      selected_files=current)
        write_json(work/"source.json", record)
    base = Car(read(snapshot/"fit.json"))
    base.parts = [Placement(**{**row, "position": tuple(row["position"]), "rotation": tuple(row["rotation"])})
                  for row in read(snapshot/"assembly.json")["parts"]]
    return base, record


def module_groups(car, index):
    """Alternatives preserve the existing native mount locations and door interfaces."""
    catalog = index["catalog"]
    def available(*parts):
        return all(all(catalog.get(p+".dat", {}).get("validity", {}).get(k)
                       for k in ("geometry", "connections", "collider")) for p in parts)
    roof = next((p for p in car.parts if p.module == "proportioned cabin roof"), None)
    if roof is None:
        raise ValueError("Countach roof module is missing")
    roof_candidates = [Candidate("roof-original", "roof", .4, dict(finish="original"),
                                interfaces={"door_roof": "native-baseline"})]
    if roof.part == "6180":
        roof_candidates += [Candidate("roof-large-tiles", "roof", 0., dict(finish="large_tiles"),
            interfaces={"door_roof": "native-baseline"}, legal=available("87079")),
            Candidate("roof-long-tiles", "roof", .02, dict(finish="long_tiles"),
            interfaces={"door_roof": "native-baseline"}, legal=available("69729"))]
    lamps = [Candidate("lamps-original", "lamps", .3, dict(lenses="original")),
             Candidate("lamps-segmented", "lamps", 0., dict(lenses="segmented"), legal=available("3070b"))]
    interior = [Candidate("interior-original", "interior", .1, dict(console=False)),
                Candidate("interior-console", "interior", 0., dict(console=True), legal=available("3069b", "3023"))]
    return dict(roof=roof_candidates, lamps=lamps, interior=interior)


def apply_modules(base, selection):
    from .carfit import IDENTITY, QUARTER
    car = Car(deepcopy(base.fit))
    car.parts = list(base.parts)
    for candidate in selection:
        if candidate.module == "roof" and candidate.parameters["finish"] != "original":
            roof = next(p for p in car.parts if p.module == "proportioned cabin roof")
            x, h, z = roof.position
            if candidate.parameters["finish"] == "large_tiles":
                for offset in (-40, 0, 40):
                    car.parts.append(Placement("87079", roof.color, (x, h-8, z+offset), IDENTITY,
                                               "smooth cabin roof finish", roof.step+1))
            else:
                for offset in (-20, 20):
                    car.parts.append(Placement("69729", roof.color, (x+offset, h-8, z), QUARTER,
                                               "smooth cabin roof finish", roof.step+1))
        elif candidate.module == "lamps" and candidate.parameters["lenses"] == "segmented":
            parts = []
            for p in car.parts:
                if p.module not in {"rectangular tail lamps", "recessed rectangular headlamps"}:
                    parts.append(p)
                    continue
                for offset in (-10, 10):
                    position = (p.position[0], p.position[1], p.position[2]+offset)
                    outer = abs(position[2]) > abs(p.position[2])
                    color = 57 if outer else p.color
                    parts.append(replace(p, part="3070b", position=position, color=color))
            car.parts = parts
        elif candidate.module == "interior" and candidate.parameters["console"]:
            layout = car.fit["layout"]
            seat_x = layout["door_interval_studs"][1]-1.5
            front = round(layout["front_glass_interval_studs"][0])
            console_x = min(front+3.5, seat_x-2)
            if console_x >= front+2.5:
                center = (car.fit["width_studs"]-1)/2
                car.place("3023", 0, console_x, center, 5, "cabin central tunnel", 15)
                car.place("3069b", 72, console_x, center, 6, "cabin console finish", 16)
    car.fit["selected_module_candidates"] = [dict(id=c.id, module=c.module, parameters=c.parameters) for c in selection]
    return car


def render_candidate(assembly, directory, request, cabin=True):
    source, python = configuration()
    request_path = directory/"render-request.json"
    write_json(request_path, request)
    subprocess.run([python, "-B", str(ROOT/"scripts/frontend_bridge_worker.py"), "presentation",
        "--source", str(source), "--input", str(assembly), "--request", str(request_path),
        "--output", str(directory/"presentation.json"), "--renders-only"], check=True,
        capture_output=True, timeout=300, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return directory/"presentation.json"


def visual_review(preview, reference, directory, target=None):
    from .dialogue import request_json
    images = {name: preview/f"render_{name}.png" for name in ("front", "rear", "left", "right", "top", "cabin")
              if (preview/f"render_{name}.png").exists()}
    if reference:
        images["task_LEGO_style_reference"] = Path(reference)
    if target:
        from .reference_plan import REFERENCE_GROUPS
        for group in REFERENCE_GROUPS:
            for name, record in target.get("geometry", {}).get(group, {}).items():
                path = Path(record["file"])
                if not path.is_file() or digest(path) != record["sha256"]:
                    raise ValueError(f"Missing or changed visual evidence: {name}")
                images[("geometry_target_" if group == "views" else "unregistered_"+group+"_")+name] = path
    schema = dict(type="object", required=["scores", "critical_defects", "repair_modules"], additionalProperties=False,
        properties=dict(scores=dict(type="object", required=list(score({})["components"]), additionalProperties=False,
            properties={k: dict(type="number", minimum=0, maximum=1) for k in score({})["components"]}),
            critical_defects=dict(type="array", items=dict(type="string")),
            repair_modules=dict(type="array", items=dict(type="string"))))
    prompt = ("Evaluate this native LEGO car against the task reference and geometry views. "
        "Score silhouette .35, identity_features .30, surface_interface .20, interior .10, proportion .05. "
        "Scores are 0..1. Exact scale is a weak preference. Inspect cowl/windscreen/roof/rear transitions, "
        "door and side-window integration, lamp placement and segmentation, smooth surface panels, seats, dashboard. "
        "A high score requires actual shape resemblance, not merely presence of named modules. "
        "List all critical defects and the affected modules. Do not certify Studio, physical construction or acceptance. "
        "Geometry targets are synthetic views, LEGO style reference applies to this task only. "
        "Supplemental views and close-ups are UNREGISTERED semantic evidence, not calibrated projections. "
        "Inspect the visible layering and integration in lamp/window/transition close-ups and compare cockpit, "
        "seats and dashboard to the cabin cutaway. Record conflicting synthetic evidence as a defect. "
        "A cabin image is a cutaway with roof/glazing deliberately hidden. No custom parts should be assumed.")
    if target and target.get("module_targets"):
        prompt += " Module observations: " + json.dumps([{k: item[k] for k in
            ("module", "description", "construction_guidance", "confidence", "contradicts_primary")}
            for item in target["module_targets"]], ensure_ascii=True)
    answer, audit = request_json(prompt, schema, directory, images, timeout=240)
    return dict(**score(answer["scores"], answer["critical_defects"]),
                repair_modules=answer["repair_modules"], visual_audit=audit)


def optimize_directory(directory, style_reference=None, config=None):
    directory = Path(directory).resolve()
    config = options(config)
    fit = read(directory/"fit.json")
    if fit.get("family_builder") != "wedge":
        report = dict(status="unsupported_architecture", pipeline_version=VERSION,
                      family=fit.get("body_family"), delivery_accepted=False)
        write_json(directory/"quality-report.json", report)
        return report
    target = read(directory/"vehicle-target.json", {})
    reference = Path(style_reference).resolve() if style_reference else None
    if reference and not reference.is_file():
        raise ValueError("LEGO style reference is missing")
    request = dict(brand="Lamborghini", model=fit.get("styling_prior", "Countach"),
                   scale=fit.get("requirements_plan", {}).get("policy", {}).get("scale", "1:24"))
    work = directory/"optimization"
    work.mkdir(exist_ok=True)
    base, source_record = search_source(directory, work)
    fit = base.fit
    parts = {p.part for p in base.parts} | {"87079", "69729", "3070b", "3023", "3069b"}
    index = build_index(work, parts)
    groups = module_groups(base, index)
    beams, rejected = beam_search(groups, config["beam_width"], config["max_module_candidates"])
    choices = {"candidate-"+str(i+1): selected for i, (_, selected, _) in enumerate(beams)}
    choices["baseline"] = [c[0] for c in groups.values()]
    write_json(work/"candidate-selection.json", dict(version=VERSION, beam_width=config["beam_width"],
        max_module_candidates=config["max_module_candidates"], groups={k: [asdict(c) for c in v] for k, v in groups.items()},
        beam=[dict(id=name, modules=[c.id for c in selected]) for name, selected in choices.items()], rejected=rejected))
    evaluated = set()
    def evaluate(name, iteration):
        from .reservation import audit_assembly
        from .sequence import resequence
        from .wedge import junction_report
        evaluated.add(name)
        candidate_dir = work/name
        candidate_dir.mkdir(exist_ok=True)
        car = apply_modules(base, choices[name])
        car.fit["generation_config"] = config
        export(car, candidate_dir)
        assembly_hash = digest(candidate_dir/"assembly.json")
        cache_key = fingerprint(dict(version=VERSION, assembly=assembly_hash,
            target=target, style=digest(reference) if reference else None,
            index=digest(work/"part-index.json"), reviewer=reviewer_model(),
            implementation=digest(Path(__file__))))
        cached = read(candidate_dir/"evaluation.json", {})
        if cached.get("cache_key") == cache_key and cached.get("result", {}).get("status") == "visual_reviewed":
            return cached["result"]
        try:
            audit = audit_assembly(candidate_dir)
            junctions = junction_report(car, audit)
            junctions["assembly_sha256"] = assembly_hash
            write_json(candidate_dir/"junction-report.json", junctions)
            hard = hard_checks([asdict(p) for p in car.parts], audit, assembly_hash, junctions)
            if not hard["passed"]:
                raise RuntimeError("Native module interface checks failed")
            sequence = resequence(car, audit, fit.get("requirements_plan", {}).get("policy", {}).get("max_parts_per_step", 12))
            export(car, candidate_dir)
            audit = audit_assembly(candidate_dir)
            assembly_hash = digest(candidate_dir/"assembly.json")
            sequence["assembly_sha256"] = assembly_hash
            junctions = junction_report(car, audit)
            junctions["assembly_sha256"] = assembly_hash
            write_json(candidate_dir/"junction-report.json", junctions)
            write_json(candidate_dir/"build-sequence.json", sequence)
            write_json(candidate_dir/"fit.json", car.fit)
            hard = hard_checks([asdict(p) for p in car.parts], audit, assembly_hash, junctions)
            render_candidate(candidate_dir/"assembly.json", candidate_dir/"preview", request)
            result = visual_review(candidate_dir/"preview", reference, candidate_dir/"review", target)
            result.update(hard_checks=hard, assembly_sha256=assembly_hash, status="visual_reviewed")
        except (RuntimeError, subprocess.SubprocessError, ValueError) as error:
            audit = read(candidate_dir/"native-audit.json", {})
            hard = hard_checks([asdict(p) for p in car.parts], audit, digest(candidate_dir/"assembly.json"),
                               read(candidate_dir/"junction-report.json", {"status": "unverified"}))
            result = dict(**score({}, [str(error)]), hard_checks=hard, status="evaluation_incomplete")
            write_json(candidate_dir/"fit.json", car.fit)
        write_json(candidate_dir/"evaluation.json", dict(cache_key=cache_key, result=result))
        return result
    def propose(best, result, iteration):
        remaining = [name for name in choices if name not in evaluated]
        modules = (result or {}).get("repair_modules", [])
        if modules:
            remaining.sort(key=lambda name: -sum(any(c.module in m.lower() for m in modules) for c in choices[name]))
        return remaining[:1]
    best, report = iterate("baseline", propose, evaluate, work, config)
    if best:
        chosen = work/best
        for name in ("model.ldr", "assembly.json", "inventory.csv", "fit.json", "native-audit.json", "junction-report.json", "build-sequence.json"):
            shutil.copyfile(chosen/name, directory/name)
        render_candidate(directory/"assembly.json", directory/"quality-preview", request)
        if (work/"baseline/preview/render_front.png").exists():
            comparison(work/"baseline/preview", directory/"quality-preview", directory/"before-after.png")
        report.update(assembly_sha256=digest(directory/"assembly.json"), selected_candidate=best,
            unresolved_defects=report["best_quality"]["critical_defects"],
            studio_gui_verified=False, delivery_accepted=False)
        validation = read(directory/"validation.json", {})
        validation.update(part_count=len(read(directory/"assembly.json")["parts"]), appearance_accepted=False,
                          delivery_accepted=False, quality_status=report["status"])
        write_json(directory/"validation.json", validation)
        source_record["selected_files"] = {name: digest(directory/name) for name in ("assembly.json", "fit.json")}
        write_json(work/"source.json", source_record)
    report.update(pipeline_version=VERSION, style_reference_sha256=digest(reference) if reference else None,
        limitations=["Native connection and collision checks do not prove clutch strength or insertion access.",
                     "Current search covers roof finish, segmented lamps and central console; other module topology remains bounded.",
                     "Actual Studio GUI save/reopen is a separate delivery gate."])
    write_json(directory/"quality-report.json", report)
    return report


def reviewer_model():
    from .dialogue import settings
    return settings()[2]


def comparison(before, after, output):
    canvas = Image.new("RGB", (1600, 1100), "white")
    draw = ImageDraw.Draw(canvas)
    for row, view in enumerate(("front", "rear")):
        for col, (label, directory) in enumerate((("Before", before), ("After", after))):
            with Image.open(directory/f"render_{view}.png") as image:
                image = image.convert("RGB")
                image.thumbnail((800, 500))
                canvas.paste(image, (col*800+(800-image.width)//2, row*550+40))
            draw.text((col*800+20, row*550+12), label+" / "+view, fill="black")
    canvas.save(output)
