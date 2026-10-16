"""Run isolated legolization diagnostics against the exact exported assembly.

Invoke with a Python environment containing legolization >= 0.6. The source
Studio library is read-only; geometry, indexes and diagnostics stay in output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def scoped_library(model, source, output):
    source, output = source.resolve(), output.resolve()
    if output.is_relative_to(source):
        raise ValueError("Analysis must not modify the source library")
    root = output / "ldraw"
    pending = [line.split(maxsplit=14)[14] for line in model.read_text(encoding="utf-8").splitlines()
               if line.startswith("1 ") and len(line.split(maxsplit=14)) == 15]
    files, missing = {}, set()
    while pending:
        name = pending.pop().replace("\\", "/")
        if name in files or name in missing:
            continue
        folders = ("parts", "p", "UnOfficial/parts", "UnOfficial/p")
        found = next(((source/folder/name,folder) for folder in folders
                      if (source/folder/name).is_file() and (source/folder/name).resolve().is_relative_to(source)), None)
        if found is None:
            missing.add(name)
            continue
        part,folder = found
        relative = Path(folder.split("/")[-1]) / name
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(part,destination)
        files[name] = dict(source=str(part),sha256=hashlib.sha256(part.read_bytes()).hexdigest())
        for line in part.read_text(encoding="utf-8",errors="replace").splitlines():
            row=line.split(maxsplit=14)
            if len(row)==15 and row[0]=="1":
                pending.append(row[14])
    if missing:
        raise ValueError(f"Missing geometry dependencies: {sorted(missing)}")
    shutil.copyfile(source / "LDConfig.ldr",root / "LDConfig.ldr")
    from ldraw.generate import generate_parts_lst
    generate_parts_lst("number",output)
    (output / "source-manifest.json").write_text(json.dumps(dict(
        model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(), files=files),indent=2),encoding="utf-8")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--input",type=Path,required=True)
    parser.add_argument("--library",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--shadow",type=Path)
    parser.add_argument("--native-audit",type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    scoped_library(args.input,args.library,args.output / "library")
    # pyldraw has a process-wide config default. Redirect it only in this worker.
    import ldraw.config
    ldraw.config.CONFIG_FILE=args.output.resolve() / "pyldraw.yml"
    ldraw.config.Config(str((args.output / "library").resolve()),
                       str((args.output / "generated").resolve())).write()
    from legolization.main import main as analyze
    arguments=["analyze",str(args.input.resolve()),"--no-repair","--json",
               "--artifact-dir",str((args.output / "diagnostics").resolve())]
    if args.shadow:
        arguments += ["--ldcad-metadata",str(args.shadow.resolve())]
    if args.native_audit:
        audit=json.loads(args.native_audit.read_text(encoding="utf-8"))
        assembly=args.input.parent / "assembly.json"
        if not assembly.is_file() or audit.get("assembly_sha256") != hashlib.sha256(assembly.read_bytes()).hexdigest():
            raise ValueError("Native connector metadata does not match the assembly")
        from collections import Counter
        rows=json.loads(assembly.read_text(encoding="utf-8"))["parts"]
        expected=Counter((p["part"]+".dat",p["color"],
            *(round(float(v),4) for v in (*p["position"],*p["rotation"]))) for p in rows)
        actual=Counter((r[14],int(r[1]),*(round(float(v),4) for v in r[2:14]))
            for line in args.input.read_text(encoding="utf-8").splitlines()
            if len(r:=line.split())==15 and r[0]=="1")
        if actual != expected:
            raise ValueError("LDraw geometry does not match the audited assembly")
        parts=[]
        for name,part in audit["catalog"].items():
            connections=[]
            for index,port in enumerate(part.get("connectors",[])):
                if port["kind"] not in {"stud","hole"} or port["area"]<4 or port["altitude"] not in (10,20,30):
                    continue
                male=port["kind"]=="stud"
                connections.append(dict(id=f"native-{index}",type="stud",gender="male" if male else "female",
                    position=port["position"],axis=[v*(-1 if male else 1) for v in port["direction"]],
                    radius=6,length=4))
            if connections:
                parts.append(dict(part_id=name,connections=connections))
        metadata=args.output / "studio-connectors.json"
        metadata.write_text(json.dumps(dict(parts=parts),indent=2),encoding="utf-8")
        (args.output / "connector-source.json").write_text(json.dumps(dict(
            source=str(args.native_audit),sha256=hashlib.sha256(args.native_audit.read_bytes()).hexdigest(),
            mapping="Standard full-area Studio stud/hole ports only; axes point outward; 6 LDU stud radius, 4 LDU stud length",
            excluded="Nonstandard studs, shafts, hinges and connector capacities are not inferred."),indent=2),encoding="utf-8")
        arguments += ["--studio-metadata",str(metadata.resolve())]
    return analyze(arguments)


if __name__=="__main__":
    raise SystemExit(main())
