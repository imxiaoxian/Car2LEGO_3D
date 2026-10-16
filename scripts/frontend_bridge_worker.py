"""Reuse original UI services without starting its API or opening its database."""
import argparse
import asyncio
import csv
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["catalog", "geometry", "recognize", "presentation"])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--renders-only", action="store_true", help="Skip the PDF during geometry refinement")
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if output.is_relative_to(source):
        raise ValueError("Original workspace is read-only")
    sys.dont_write_bytecode = True
    for key, value in dotenv_values(source / "backend/.env").items():
        if key.startswith(("OPENAI_", "AI_")) and value:
            os.environ.setdefault(key, value)
    os.environ["STORAGE_PATH"] = str(output.parent / "service-storage")
    os.environ["STUDIO_LDRAW_PATH"] = str(source / "Studio 2.0/ldraw")
    os.environ["NUMBA_CACHE_DIR"] = str(output.parent / "numba-cache")
    sys.path.insert(0, str(source / "backend"))
    # Fill the upstream service's small colour subset in this isolated process.
    from app.services.ldraw_service import LDrawService
    color_path = source / "Studio 2.0/data/StudioColorDefinition.txt"
    with color_path.open(encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            code, rgb = row["LDraw Color Code"], row["RGB value"].lstrip("#")
            if code.isdigit() and re.fullmatch(r"[0-9a-fA-F]{6}", rgb):
                LDrawService.COLOR_MAP[int(code)] = (row["LDraw Color Name"], "#"+rgb)
    if args.operation == "catalog":
        from app.services.vehicle_catalog import vehicle_catalog
        from app.services.mod_parts_catalog import MOD_CATALOG
        from app.services.assembly_v3.requirements import requirements_for_scale, SCALES
        result = {"vehicles": vehicle_catalog(), "mods": {
            category: [dict(id=m.id, name=m.name, category=m.category,
                real_world_ref=m.real_world_ref, description=m.description,
                difficulty=m.difficulty, estimated_parts=m.estimated_parts,
                visual_change=m.visual_change, lego_parts_count=len(m.lego_parts)) for m in mods]
            for category, mods in MOD_CATALOG.items()},
            "policies": {scale: requirements_for_scale(scale) for scale in SCALES}}
        result["source_sha256"] = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in ("backend/app/services/vehicle_catalog.py", "backend/app/services/mod_parts_catalog.py")}
        color_path = source / "Studio 2.0/data/StudioColorDefinition.txt"
        with color_path.open(encoding="utf-8-sig") as stream:
            result["colors"] = {row["LDraw Color Code"]: dict(bricklink=int(row["BL Color Code"]),
                name=row["LDraw Color Name"], hex=row["RGB value"])
                for row in csv.DictReader(stream, delimiter="\t")
                if row["BL Color Code"].isdigit() and row["LDraw Color Code"].isdigit()}
        result["source_sha256"]["Studio 2.0/data/StudioColorDefinition.txt"] = hashlib.sha256(color_path.read_bytes()).hexdigest()
    elif args.operation == "recognize":
        from app.services.vision_analyzer import VisionAnalyzer
        features = asyncio.run(VisionAnalyzer().analyze_image(str(args.input)))
        if not features or not features.make or not features.model:
            raise ValueError("Unable to identify vehicle from photograph")
        result = asdict(features)
    elif args.operation == "presentation":
        from app.services.assembly_v3.scene import AssemblyScene
        from app.services.assembly_v3.exports import ldraw, instructions_pdf
        from app.services.assembly_v3.render import render_scene
        request = json.loads(args.request.read_text(encoding="utf-8"))
        rows = json.loads(args.input.read_text(encoding="utf-8"))["parts"]
        module_ids = {name: re.sub(r"[^a-z0-9_]", "_", name.lower()) for name in {p["module"] for p in rows}}
        parts = [dict(id=f"p{i}", part_num=p["part"]+".dat", color_id=p["color"], module=module_ids[p["module"]],
            transform=dict(position=p["position"], rotation=p["rotation"])) for i,p in enumerate(rows)]
        steps = []
        limit = {"1:38":12, "1:24":12, "1:16":10, "1:12":8}.get(request.get("scale"), 12)
        for number in sorted({p["step"] for p in rows}):
            for module in dict.fromkeys(parts[i]["module"] for i,p in enumerate(rows) if p["step"] == number):
                ids = [p["id"] for i,p in enumerate(parts) if rows[i]["step"] == number and p["module"] == module]
                for start in range(0, len(ids), limit):
                    steps.append(dict(index=len(steps)+1, module=module, parts=ids[start:start+limit]))
        scene = AssemblyScene(design_id=output.parent.name, vehicle=dict(make=request["brand"], model=request["model"]),
            scale=request.get("scale") if request.get("scale") in {"1:38", "1:24", "1:16", "1:12"} else "1:24",
            specifications={}, target_ldu={}, parts=parts,
            modules=[dict(id=m, recipe_version="multiview_v1") for m in sorted(module_ids.values())], steps=steps)
        output.parent.mkdir(parents=True, exist_ok=True)
        (output.parent / "scene.json").write_text(scene.model_dump_json(), encoding="utf-8")
        (output.parent / "model.mpd").write_text(ldraw(scene, True), encoding="utf-8")
        for view in ("front", "rear", "left", "right", "top", "bottom"):
            camera = {"front": "rear", "rear": "front"}.get(view, view)
            rendered=scene
            if view in {"left","right"}:
                import numpy as np
                rendered=scene.model_copy(deep=True)
                yaw=np.array([[0,0,1],[0,1,0],[-1,0,0]])*(-1 if view=="left" else 1)
                yaw[1,1]=1
                for p in rendered.parts:
                    p.transform.position=(yaw@np.array(p.transform.position)).tolist()
                    p.transform.rotation=(yaw@np.array(p.transform.rotation).reshape(3,3)).ravel().tolist()
                camera="left"
            render_scene(rendered, output.parent / f"render_{view}.png", camera)
        render_scene(scene, output.parent / "render_front_elevation.png", "left")
        render_scene(scene, output.parent / "render_rear_elevation.png", "right")
        cabin = scene.model_copy(deep=True)
        hidden = {module_ids[name] for name in module_ids if any(word in name for word in ("roof", "windscreen", "glazing"))}
        cabin.parts = [p for p in cabin.parts if p.module not in hidden]
        render_scene(cabin, output.parent / "render_cabin.png", "rear")
        if not args.renders_only:
            instructions_pdf(scene, output.parent / "instructions.partial.pdf", {})
            (output.parent / "instructions.partial.pdf").replace(output.parent / "instructions.pdf")
        result = dict(assembly_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
            part_count=len(parts), step_count=len(steps), insertion_paths_verified=False,
            files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.parent.iterdir()
                   if p.suffix in {".mpd", ".pdf", ".png"}})
    else:
        from app.services.assembly_v3.exports import colored_geometry
        rows = json.loads(args.input.read_text(encoding="utf-8"))["parts"]
        import numpy as np
        result = {name: [{"color_id": c, "positions": mesh.astype(np.float32).ravel().tolist()}
                        for c, mesh in colored_geometry(name)]
                  for name in sorted({p["part"] + ".dat" for p in rows})}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(str(output))


if __name__ == "__main__":
    main()
