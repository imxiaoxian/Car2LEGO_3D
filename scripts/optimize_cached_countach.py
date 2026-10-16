"""Reproduce quality search from verified existing image, mesh and assembly artifacts."""
import argparse
import json
from pathlib import Path
import shutil

from car2lego3d.common import digest, write_json
from car2lego3d.lego import load_mesh
from car2lego3d.optimization import optimize_directory
from car2lego3d.studio import export_studio
from car2lego3d.target import build_target, project_features, save_target
from car2lego3d.vehicle import load_spec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--auto-rebuild", action="store_true")
    parser.add_argument("--quality-rounds", type=int, choices=range(1, 17), default=16)
    args = parser.parse_args()
    root, baseline, output = args.run.resolve(), args.baseline.resolve(), args.output.resolve()
    if output == baseline or output.is_relative_to(Path("D:/lego")):
        raise ValueError("Outputs must be isolated from the baseline and original project")
    lego = output/"03-lego"
    if not lego.exists():
        shutil.copytree(baseline/"03-lego", lego)
    mesh = root/"02-mesh/calibrated.glb"
    spec = root/"specification/spec.json"
    views = root/"01-image/views.json"
    style = root/"reference/user-reference.png"
    if args.auto_rebuild:
        from car2lego3d.carfit import convert_car
        convert_car(mesh, lego, vehicle_spec=spec, color=15, reference_views=views, style_reference=style)
    fit = json.loads((lego/"fit.json").read_text(encoding="utf-8"))
    target = build_target(load_spec(spec), fit, mesh, views, style)
    target = project_features(load_mesh(mesh), fit.get("detail_program"), target,
                              fit.get("front_orientation_flipped", False))
    save_target(lego, target)
    write_json(output/"source-reuse.json", dict(mesh_sha256=digest(mesh), views_sha256=digest(views),
        baseline_assembly_sha256=digest(baseline/"03-lego/assembly.json"),
        image_generation_reused=True, reconstruction_reused=True, reference_scope="this_task_only"))
    report = optimize_directory(lego, style, dict(max_quality_iterations=args.quality_rounds))
    thumbnail = lego/"quality-preview/render_front.png"
    export_studio(lego/"model.ldr", output/"04-studio", thumbnail if thumbnail.exists() else None)
    print(json.dumps(dict(status=report["status"], selected=report.get("selected_candidate"),
        studio=str(output/"04-studio/model.io"), delivery_accepted=False), indent=2))


if __name__ == "__main__":
    main()
