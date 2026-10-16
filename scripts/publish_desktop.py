"""Publish a completed CLI candidate as a new draft without changing older jobs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from uuid import uuid4

from car2lego3d.common import ROOT, digest, write_json
from car2lego3d.multiview import load_views
from car2lego3d.studio import export_studio, verify_archive
from car2lego3d.vehicle import load_spec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--mesh", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    load_views(args.views / "views.json")
    spec = load_spec(args.spec)
    request = json.loads(args.request.read_text(encoding="utf-8"))
    if not (args.candidate / "03-lego/model.ldr").is_file() or not (args.mesh / "calibrated.glb").is_file():
        raise ValueError("Candidate assembly and calibrated reconstruction are required")
    assembly=args.candidate / "03-lego/assembly.json"
    validation=json.loads((args.candidate / "03-lego/validation.json").read_text(encoding="utf-8"))
    if validation.get("recipe", "").startswith("section-envelope"):
        audit=json.loads((args.candidate / "03-lego/native-audit.json").read_text(encoding="utf-8"))
        if (audit.get("assembly_sha256") != digest(assembly) or audit.get("collision_coverage") != "complete"
                or audit.get("collision_count") != 0 or audit.get("connectivity",{}).get("status") != "passed"):
            raise ValueError("Cannot publish a candidate with stale or failed native geometry checks")
    presentation=args.candidate / "05-presentation/presentation.json"
    if presentation.exists() and json.loads(presentation.read_text(encoding="utf-8")).get("assembly_sha256") != digest(assembly):
        raise ValueError("Candidate instructions and renders belong to a different assembly")
    id = uuid4().hex
    directory = ROOT / "runs/web" / id
    artifacts = directory / "artifacts"
    shutil.copytree(args.candidate / "03-lego", artifacts / "03-lego")
    shutil.copytree(args.views, artifacts / "01-image")
    shutil.copytree(args.mesh, artifacts / "02-mesh")
    if (args.candidate / "05-presentation").exists():
        shutil.copytree(args.candidate / "05-presentation", artifacts / "05-presentation")
    preview = artifacts / "05-presentation/render_front.png"
    studio = export_studio(artifacts / "03-lego/model.ldr", artifacts / "04-studio", preview if preview.exists() else None)
    verify_archive(studio, artifacts / "03-lego/model.ldr")
    write_json(directory / "vehicle-specs.json", spec)
    write_json(directory / "request.json", request | dict(created_at=datetime.now(timezone.utc).isoformat(),
               origin="Imported CLI candidate; appearance and physical build not accepted"))
    write_json(directory / "desktop.json", dict(artifacts="artifacts", workflow="multiview_v1"))
    write_json(directory / "checkpoints.json", {stage: digest(artifacts / file) for stage,file in {
        "reference_images":"01-image/views.json", "reconstruction":"02-mesh/calibrated.glb",
        "assembly":"03-lego/model.ldr", "exporting":"04-studio/model.io"}.items()})
    write_json(directory / "status.json", dict(status="draft", stage="draft", progress=100, error=None))
    print(json.dumps(dict(id=id, url=f"http://127.0.0.1:8768/designs/{id}", studio=str(studio))))


if __name__ == "__main__":
    main()
