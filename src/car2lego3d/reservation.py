"""Use upstream native colliders to reserve body cells, never delete styled parts."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from .common import ROOT,digest,write_json
from .gpt_reference import configuration


def reserve(car):
    source,python=configuration()
    cells=list(car.cells)
    with TemporaryDirectory(prefix="native-reservation-",dir=ROOT/"runs") as temporary:
        directory=Path(temporary)
        request=directory/"request.json"
        write_json(request,dict(length=car.fit["length_studs"],width=car.fit["width_studs"],
            body_color=car.fit["body_color"],
            cells=cells,parts=[asdict(p) for p in car.parts]))
        result=subprocess.run([python,"-B",str(ROOT/"scripts/reserve_cells_worker.py"),"--source",str(source),
            "--input",str(request),"--output",str(directory/"result.json")],capture_output=True,
            env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"},timeout=180)
        if result.returncode:
            raise RuntimeError("Native collider reservation failed: "+result.stderr.decode("utf-8",errors="replace")[-2000:])
        result=json.loads((directory/"result.json").read_text())
        blocked=result["blocked_cells"]
    for x,y,h,color in result["support_cells"]:
        car.cells[x,y,h]=color
    for cell in blocked:
        car.cells.pop(tuple(cell),None)
    car.fit["native_reserved_cells"]=len(blocked)
    car.fit["reservation_triangle_bounds_fallback"]=result["conservative_triangle_bounds"]


def audit_assembly(directory):
    """Fail before Studio export when a complete native geometry check fails."""
    directory=Path(directory)
    source,python=configuration()
    assembly=directory / "assembly.json"
    target=directory / "native-audit.json"
    result=subprocess.run([python,"-B",str(ROOT/"scripts/native_audit_worker.py"),
        "--source",str(source),"--assembly",str(assembly),"--output",str(target)],
        capture_output=True,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"},timeout=180)
    if result.returncode:
        raise RuntimeError("Native assembly audit failed: "+result.stderr.decode("utf-8",errors="replace")[-1500:])
    audit=json.loads(target.read_text(encoding="utf-8"))
    if audit.get("assembly_sha256") != digest(assembly):
        raise RuntimeError("Native assembly audit does not match the exported assembly")
    if (audit.get("collision_coverage") != "complete" or audit.get("collision_count") != 0
            or audit.get("connectivity",{}).get("status") != "passed"):
        raise RuntimeError("Native geometry audit blocked Studio export; inspect native-audit.json")
    return audit
