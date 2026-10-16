"""Verify the delivered wedge assembly and compare its cabin junctions."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from car2lego3d.common import digest, write_json
from car2lego3d.studio import verify_archive


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    candidate, baseline = args.candidate, args.baseline
    directory = candidate / "03-lego"
    assembly = directory / "assembly.json"
    rows = read(assembly)["parts"]
    audit = read(directory / "native-audit.json")
    junctions = read(directory / "junction-report.json")
    sequence = read(directory / "build-sequence.json")
    presentation = read(candidate / "05-preview/presentation.json")
    for record in (audit, junctions, sequence, presentation):
        if record["assembly_sha256"] != digest(assembly):
            raise ValueError("Inspection evidence must match the delivered assembly")
    if (audit["collision_count"] or audit["collision_coverage"] != "complete"
            or audit["connectivity"]["status"] != "passed" or junctions["status"] != "passed"
            or not sequence["connected_prefixes"]):
        raise ValueError("The candidate failed native assembly checks")
    archive = verify_archive(candidate / "04-studio/model.io", directory / "model.ldr")

    def bounds(module):
        return [audit["per_part_bounds_ldu"][i] for i, row in enumerate(rows) if row["module"] == module]

    cowl = bounds("bonnet to cabin cowl beam")
    bonnet = bounds("planar wedge bonnet")
    roof = bounds("proportioned cabin roof")
    ramp = bounds("cabin to engine deck ramp")
    sill_offsets = []
    for low, high in bounds("continuous door skins"):
        sills = [(lo, hi) for lo, hi in bounds("smooth lower sills")
                 if (lo[2]+hi[2])*(low[2]+high[2]) > 0 and lo[0] < high[0] and hi[0] > low[0]]
        if not sills:
            raise ValueError("Door skin has no adjacent lower sill")
        sill_offsets.append(min(lo[1] for lo, _ in sills)-high[1])
    report = dict(assembly_sha256=digest(assembly), studio_sha256=digest(candidate / "04-studio/model.io"),
        baseline_part_count=len(read(baseline / "03-lego/assembly.json")["parts"]),
        part_count=len(rows), step_count=sequence["step_count"], studio_archive=archive,
        collision_count=audit["collision_count"], collision_coverage=audit["collision_coverage"],
        connectivity=audit["connectivity"], junction_checks=junctions["checks"],
        bounding_edge_offsets_ldu=dict(
            bonnet_to_cowl=min(lo[0] for lo, _ in cowl)-max(hi[0] for _, hi in bonnet),
            roof_to_rear_ramp=min(lo[0] for lo, _ in ramp)-max(hi[0] for _, hi in roof),
            door_bottom_to_sill_top=sill_offsets),
        native_seats=sum(p["part"] == "4079" for p in rows), appearance_accepted=False,
        studio_gui_verified=False, physical_verified=False,
        limitations=["Bounding edge offsets measure alignment, not complete surface contact or appearance fidelity.",
                     "Native connector and collision checks do not verify insertion access or clutch strength."])
    write_json(candidate / "connection-inspection.json", report)

    comparison = Image.new("RGB", (1600, 1100), "#f5f5f5")
    draw = ImageDraw.Draw(comparison)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 26)
    for row, view in enumerate(("front", "rear")):
        for col, (title, run) in enumerate((("Before", baseline), ("After", candidate))):
            with Image.open(run / f"05-preview/render_{view}.png") as original:
                image = original.convert("RGB")
            image.thumbnail((800, 500))
            comparison.paste(image, (col*800+(800-image.width)//2, row*550+50))
            draw.text((col*800+24, row*550+12), f"{title} - {view}", font=font, fill="#222222")
    comparison.save(candidate / "connection-comparison.png")
    print(json.dumps({key: report[key] for key in ("part_count", "step_count", "collision_count",
        "native_seats", "bounding_edge_offsets_ldu", "studio_archive")}, indent=2))


if __name__ == "__main__":
    main()
