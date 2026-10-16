"""Compare a GUI-saved Studio archive with its exact flat source assembly."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from zipfile import ZipFile

from car2lego3d.common import digest, write_json


def root_steps(text, colors=None, aliases=None):
    steps, current, root_seen = [], [], False
    for line in text.lstrip("\ufeff").splitlines():
        row=line.split()
        if row[:2] == ["0","FILE"]:
            if root_seen:
                break
            root_seen=True
        elif row[:2] == ["0","NOFILE"]:
            break
        elif row == ["0","STEP"]:
            steps.append(Counter(current))
            current=[]
        elif row and row[0] == "1":
            if len(row) != 15:
                raise ValueError("Invalid saved part reference")
            color=int(row[1])
            current.append(((aliases or {}).get(row[14],row[14]),colors[color] if colors else color,
                *(round(float(v),4) for v in row[2:14])))
    if current:
        steps.append(Counter(current))
    return steps


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--saved",type=Path,required=True)
    parser.add_argument("--colors",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    # Several names alias the same BrickLink colour. Match against source colour codes.
    with args.colors.open(encoding="utf-8-sig") as stream:
        to_bl={int(row["LDraw Color Code"]):int(row["BL Color Code"])
            for row in csv.DictReader(stream,delimiter="\t")
            if row["BL Color Code"].isdigit() and row["LDraw Color Code"].isdigit()}
    source=args.source.read_text(encoding="utf-8-sig")
    expected=root_steps(source)
    with ZipFile(args.saved) as archive:
        if archive.testzip():
            raise ValueError("Corrupt Studio save")
        main_steps=root_steps(archive.read("model.ldr").decode("utf-8-sig"))
        # Studio's model2 stores BrickLink colour numbers and inline geometry files.
        secondary_text=archive.read("model2.ldr").decode("utf-8-sig")
        aliases={}
        current=None
        for line in secondary_text.splitlines():
            fields=line.split()
            if fields[:2]==["0","FILE"]:
                current=fields[2]
            elif fields[:2]==["0","BL_Item_No"] and current and current.startswith("rename_"):
                aliases[current]=fields[2]+".dat"
        secondary=root_steps(secondary_text,aliases=aliases)
        if main_steps != expected:
            raise ValueError("Studio saved parts, transforms, colours or step membership differ")
        secondary_matches=secondary == root_steps(source,to_bl)
        errors=json.loads(archive.read("errorPartList.err").decode("utf-8-sig"))
        if errors:
            raise ValueError(f"Studio reported invalid parts: {errors}")
        info=json.loads(archive.read(".info").decode("utf-8-sig"))
    count=sum(sum(step.values()) for step in expected)
    if info["total_parts"] != count:
        raise ValueError("Studio saved part count mismatch")
    write_json(args.output,dict(source_sha256=digest(args.source),saved_sha256=digest(args.saved),
        studio_version=info["version"],part_count=count,step_count=len(expected),
        saved_roundtrip="main_model_passed",main_model="passed",
        secondary_model_references="passed" if secondary_matches else "unverified_part_aliases",
        inline_geometry_verified=False,aliases=aliases,
        invalid_parts=errors,physical_verified=False))
    print(str(args.output))


if __name__=="__main__":
    main()
