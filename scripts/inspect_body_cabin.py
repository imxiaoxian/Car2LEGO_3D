"""Compare exported body modules and prepare a roof-hidden cabin inspection."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


SURFACES = {"fitted bonnet", "fitted tailgate", "continuous shoulder line", "curved front fenders",
            "rising rear shoulders", "profiled roof center", "rounded roof shoulders", "curved lower doors"}
ROOF = {"profiled roof center", "rounded roof shoulders", "roof substrate", "window pillar crossbeam",
        "concealed roof rails", "windscreen header trim", "rear glass header trim",
        "front windscreen", "rear windscreen", "side window glass"}
SMALL_TILES = {"3070b", "3069b"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def counts(rows):
    surfaces = [p for p in rows if p["module"] in SURFACES]
    return dict(parts=len(rows), steps=len({p["step"] for p in rows}),
        surface_parts=len(surfaces), small_surface_tiles=sum(p["part"] in SMALL_TILES for p in surfaces),
        surface_modules=dict(Counter(p["module"] for p in surfaces)),
        native_seats=sum(p["part"]=="4079" for p in rows))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--candidate",type=Path,required=True)
    parser.add_argument("--baseline",type=Path,required=True)
    args=parser.parse_args()
    candidate=args.candidate
    assembly=candidate/"03-lego/assembly.json"
    rows=read(assembly)["parts"]
    fit=read(candidate/"03-lego/fit.json")
    audit=read(candidate/"03-lego/native-audit.json")
    presentation=read(candidate/"preview/presentation.json")
    sequence=read(candidate/"03-lego/build-sequence.json")
    for record in (audit,presentation,sequence):
        if record["assembly_sha256"] != sha(assembly):
            raise ValueError("Audit, sequence and preview must match the final assembly")
    if audit["collision_count"] or audit["collision_coverage"]!="complete" or audit["connectivity"]["status"]!="passed":
        raise ValueError("Candidate failed native geometry checks")
    seat_indices=[i for i,p in enumerate(rows) if p["part"]=="4079"]
    if len(seat_indices)!=fit["interior_fitting"]["seat_count"]:
        raise ValueError("Reported seats differ from exported native seats")
    report=dict(assembly_sha256=sha(assembly),baseline=counts(read(args.baseline/"03-lego/assembly.json")["parts"]),
        candidate=counts(rows),interior=fit["interior_fitting"], seat_part_indices=seat_indices,
        native_collision_count=audit["collision_count"],native_connectivity=audit["connectivity"],
        appearance_accepted=False,studio_gui_verified=False,physical_verified=False)
    studio=candidate/"04-studio/model.io"
    if studio.exists():
        from car2lego3d.studio import verify_archive
        report["studio_archive"]=dict(sha256=sha(studio),
            **verify_archive(studio,candidate/"03-lego/model.ldr"))
    write(candidate/"change-report.json",report)
    removed=[]
    visible=[]
    for i,p in enumerate(rows):
        roof_support = p["module"]=="body and chassis" and -p["position"][1]/8 >= fit["glass_top_plates"]-1
        (removed if p["module"] in ROOF or roof_support else visible).append(i)
    write(candidate/"inspection/assembly.json",dict(units="LDraw LDU",parts=[rows[i] for i in visible]))
    write(candidate/"inspection/cutaway.json",dict(source_assembly_sha256=sha(assembly),
        description="Diagnostic view with roof and glass hidden; the delivered model retains all parts",
        removed_part_indices=removed,seat_source_indices=seat_indices,
        filtered_assembly_sha256=sha(candidate/"inspection/assembly.json")))
    font_path=Path("C:/Windows/Fonts/arial.ttf")
    font=ImageFont.truetype(str(font_path),26) if font_path.exists() else ImageFont.load_default()
    before=Image.open(args.baseline/"preview/render_front.png").convert("RGB")
    after=Image.open(candidate/"preview/render_front.png").convert("RGB")
    comparison=Image.new("RGB",(1600,550),"#f5f5f5")
    draw=ImageDraw.Draw(comparison)
    for x,title,img in ((0,"Before",before),(800,"After",after)):
        img.thumbnail((800,500))
        comparison.paste(img,(x+(800-img.width)//2,50))
        draw.text((x+28,15),title,fill="#222222",font=font)
    comparison.save(candidate/"comparison.png")
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
