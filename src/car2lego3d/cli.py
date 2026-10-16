import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

from .common import model_lock, stage, write_json
from .requirements import SCALES, plan_design, requirements_for_scale
from .project import PROJECT_NAME, DEFAULT_BACKEND, API_PORT, LEGACY_WEB_PORT


def main():
    parser = argparse.ArgumentParser(description=f"{PROJECT_NAME}: GPT images -> open-source reconstruction -> LEGO -> Studio .io")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="Download and pin an upstream reconstruction source")
    setup.add_argument("--backend", choices=["hunyuan-mv", "triposr"], default=DEFAULT_BACKEND)
    commands.add_parser("recipes", help="Show vehicle families and their official LEGO references")
    commands.add_parser("doctor", help="Show environment and CUDA availability")
    web = commands.add_parser("web", help="Serve the local generation page and brand selector")
    web.add_argument("--port", type=int, default=LEGACY_WEB_PORT)
    api = commands.add_parser("desktop-api", help="API for the replicated Car2LEGO desktop frontend")
    api.add_argument("--port", type=int, default=API_PORT)
    research = commands.add_parser("research", help="Get physical specification estimates through model dialogue")
    research.add_argument("--brand", required=True)
    research.add_argument("--model", required=True)
    research.add_argument("--variant", default="")
    research.add_argument("--output", type=Path, required=True)
    requirements = commands.add_parser("requirements", help="Export per-scale policies and vehicle-specific plans")
    requirements.add_argument("--scale", choices=SCALES)
    requirements.add_argument("--vehicle-specs", type=Path)
    requirements.add_argument("--output", type=Path, required=True)
    studio = commands.add_parser("studio", help="Package a flat LDraw assembly as Studio .io")
    studio.add_argument("--ldraw", type=Path, required=True)
    studio.add_argument("--output", type=Path, required=True)
    studio.add_argument("--thumbnail", type=Path)
    optimize = commands.add_parser("optimize", help="Native module search and multi-view visual quality feedback")
    optimize.add_argument("--assembly-directory", type=Path, required=True)
    optimize.add_argument("--style-reference", type=Path)
    optimize.add_argument("--iterations", type=int, default=16)
    measure = commands.add_parser("measure", help="Convert orthographic side-view annotations to physical priors")
    measure.add_argument("--vehicle-specs", type=Path, required=True)
    measure.add_argument("--annotations", type=Path, required=True)
    measure.add_argument("--reference-image", type=Path, required=True)
    measure.add_argument("--output", type=Path, required=True)
    for name in ("image", "reconstruct", "lego", "run"):
        child = commands.add_parser(name)
        child.add_argument("--output", type=Path, required=True)
        child.add_argument("--vehicle-specs", type=Path, help="Sourced physical vehicle dimensions JSON")
        if name in ("image", "run"):
            child.add_argument("--prompt", required=name == "image")
            child.add_argument("--image-provider", choices=["gpt", "sd-turbo"], default="gpt")
            child.add_argument("--reference-image", type=Path, help="Real vehicle appearance anchor for GPT generation")
            child.add_argument("--seed", type=int, default=42)
            child.add_argument("--steps", type=int, choices=range(1, 5), default=4)
        if name in ("reconstruct", "run"):
            if name == "reconstruct":
                child.add_argument("--seed", type=int, default=42)
            else:
                child.add_argument("--reconstruction-cache", type=Path,
                                   help="Reuse a completed multiview mesh only after verifying inputs and output hashes")
            child.add_argument("--image", type=Path, help="Legacy TripoSR input; requires --backend triposr")
            child.add_argument("--views", type=Path, help="Four labelled GPT views.json")
            child.add_argument("--backend", choices=["hunyuan-mv", "triposr"], default=DEFAULT_BACKEND)
            child.add_argument("--reconstruction-steps", type=int, default=50)
            child.add_argument("--resolution", type=int, choices=[64, 128, 192, 256], default=128)
            child.add_argument("--chunk-size", type=int, default=4096)
            child.add_argument("--no-remove-bg", action="store_true")
        if name != "lego":
            child.add_argument("--device", default="auto")
        if name in ("lego", "run"):
            child.add_argument("--body-style", help="Explicit vehicle family; otherwise classified from identity and GPT brief")
            child.add_argument("--reference-views", type=Path, help="GPT views.json for classification and appearance priors")
            if name == "lego":
                child.add_argument("--mesh", type=Path, required=True)
            size = child.add_mutually_exclusive_group()
            size.add_argument("--length-studs", type=int, help="Legacy core length hint; otherwise select automatically from appearance and parts")
            size.add_argument("--scale", choices=SCALES, help="Legacy approximate size hint; visual fit may adjust dimensions")
            child.add_argument("--style-reference", type=Path, help="Task-specific LEGO styling target")
            for key, choices, default in (("proportion-mode", ["visual", "nominal"], "visual"),
                ("function-profile", ["static"], "static"),
                ("part-policy", ["library_first_custom_if_needed", "library_only"], "library_first_custom_if_needed"),
                ("optimization-profile", ["quality", "draft"], "quality")):
                child.add_argument("--"+key, choices=choices, default=default)
            child.add_argument("--strategy", choices=["plates", "car"], default="car")
            child.add_argument("--width-studs", type=int, choices=[8, 10, 12],
                               help="Body core width; spec mode selects the nearest family if omitted")
            child.add_argument("--no-rear-wing", action="store_true")
            child.add_argument("--up-axis", choices=["x", "y", "z"], default="z")
            child.add_argument("--yaw", type=float, default=0)
            child.add_argument("--color", type=int, help="Body color for car strategy; uniform color for plates")
            if name == "lego":
                child.add_argument("--requirements-snapshot", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    vehicle_spec = getattr(args, "vehicle_specs", None)
    if vehicle_spec is not None:
        from .vehicle import load_spec
        vehicle_spec = vehicle_spec.resolve()
        try:
            load_spec(vehicle_spec)
        except (ValueError, OSError) as error:
            parser.error(str(error))
    if getattr(args, "strategy", None) == "plates" and (vehicle_spec is not None or args.width_studs is not None):
        parser.error("--vehicle-specs and --width-studs currently require --strategy car")
    if args.command in ("run", "lego") and args.scale is not None and (vehicle_spec is None or args.strategy != "car"):
        parser.error("--scale requires --vehicle-specs and --strategy car")
    if args.command in ("run", "lego") and args.length_studs is not None and args.length_studs <= 0:
        parser.error("--length-studs must be positive")
    if args.command == "run" and vehicle_spec is not None and (args.up_axis != "z" or args.yaw != 0):
        parser.error("Spec-based run uses the calibrated X-long/Z-up frame; omit --up-axis and --yaw")
    if args.command == "run" and args.reference_image and not args.prompt:
        parser.error("--reference-image requires --prompt")
    if args.command == "optimize":
        from .optimization import optimize_directory
        print(json.dumps(optimize_directory(args.assembly_directory, args.style_reference,
                         dict(max_quality_iterations=args.iterations)), ensure_ascii=False))
    elif args.command == "research":
        from .research import research_vehicle
        result = research_vehicle({"brand": args.brand, "model": args.model, "variant": args.variant}, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "web":
        from .web import serve
        serve(args.port)
    elif args.command == "desktop-api":
        from .web import serve_desktop
        serve_desktop(args.port)
    elif args.command == "setup":
        if args.backend == "hunyuan-mv":
            from .multiview import setup_source
        else:
            from .reconstruction import setup_source
        print(setup_source())
    elif args.command == "recipes":
        from .recipes import catalog
        print(json.dumps(catalog(), ensure_ascii=False, indent=2))
    elif args.command == "requirements":
        scales = [args.scale] if args.scale is not None else SCALES
        plans = {scale: plan_design(load_spec(vehicle_spec), scale) for scale in scales} if vehicle_spec else {}
        output = args.output / "requirements.json"
        write_json(output, {"policies": {scale: requirements_for_scale(scale) for scale in scales}, "plans": plans})
        print(output)
    elif args.command == "doctor":
        import torch
        info = {"python": sys.version, "platform": platform.platform(), "torch": torch.__version__,
                "cuda_available": torch.cuda.is_available(), "torch_cuda": torch.version.cuda, "models": model_lock()}
        if torch.cuda.is_available():
            properties = torch.cuda.get_device_properties(0)
            info.update(gpu=properties.name, gpu_total_mb=round(properties.total_memory / 2**20))
        print(json.dumps(info, indent=2))
    elif args.command == "run":
        if sum(bool(item) for item in (args.prompt, args.image, args.views)) != 1:
            parser.error("run requires exactly one of --prompt, --views or --image")
        if args.backend == "hunyuan-mv" and (args.image or (args.prompt and args.image_provider != "gpt")):
            parser.error("hunyuan-mv requires four --views or GPT multiview --prompt")
        if args.backend == "triposr" and (args.views or (args.prompt and args.image_provider == "gpt")):
            parser.error("TripoSR is a legacy single-image experiment; GPT runs require hunyuan-mv")
        if args.backend == "hunyuan-mv" and vehicle_spec is None:
            parser.error("Vehicle multiview run requires --vehicle-specs for classification and physical constraints")
        if args.output.exists() and any(args.output.iterdir()):
            parser.error("run --output must be empty to preserve prior artifacts")
        plan = None
        if vehicle_spec is not None:
            plan = plan_design(load_spec(vehicle_spec), args.scale, args.length_studs, args.width_studs)
            from .recipes import classify, selection_record
            early = classify(load_spec(vehicle_spec), explicit=args.body_style)
            if early["family"] != "other" and selection_record(early)["builder"] is None:
                parser.error(f"No implemented recipe for {early['family']}; see car2lego3d recipes")
        args.output.mkdir(parents=True, exist_ok=True)
        requirements_snapshot = None
        if plan is not None:
            requirements_snapshot = args.output / "00-requirements" / "requirements.json"
            write_json(requirements_snapshot, plan)
            if not plan["generation"]["draft_recipe_available"]:
                parser.error(" ".join(plan["generation"]["blockers"]) + f" Plan saved to {requirements_snapshot}.")
        if vehicle_spec is not None:
            snapshot = args.output / "00-vehicle" / "spec.json"
            write_json(snapshot, load_spec(vehicle_spec))
            vehicle_spec = snapshot.resolve()
        spec_args = ["--vehicle-specs", vehicle_spec] if vehicle_spec is not None else []
        core_length = args.length_studs if args.length_studs is not None else 24
        size_args = (["--scale", args.scale] if args.scale else
                     ["--length-studs", args.length_studs] if args.length_studs is not None else [])
        with stage(args.output, "pipeline", {"mode": "prompt" if args.prompt else "views" if args.views else "image", "seed": args.seed,
                                             "backend": args.backend,
                                             "resolution": args.resolution,
                                             "length_studs": plan["grid"]["length_studs"] if plan else core_length,
                                             "requested_scale": args.scale,
                                             "strategy": args.strategy}) as record:
            if requirements_snapshot is not None:
                from .common import digest
                record["requirements"] = {"file": "00-requirements/requirements.json",
                                          "sha256": digest(requirements_snapshot)}
            if vehicle_spec is not None:
                from .vehicle import spec_record
                record["vehicle_spec"] = spec_record(vehicle_spec)
            if args.prompt:
                invoke("image", "--prompt", args.prompt, "--output", args.output / "01-image",
                       "--seed", args.seed, "--steps", args.steps, "--device", args.device,
                       "--image-provider", args.image_provider,
                       *(["--reference-image", args.reference_image] if args.reference_image else []), *spec_args)
                source = args.output / "01-image" / ("views.json" if args.backend == "hunyuan-mv" else "reference.png")
            else:
                source = (args.views or args.image).resolve()
                if args.views:
                    from .multiview import snapshot_views
                    source = snapshot_views(source, args.output / "01-image")
            if args.backend == "hunyuan-mv":
                from .multiview import load_views
                from .recipes import classify, selection_record
                manifest, _ = load_views(source)
                selection = selection_record(classify(load_spec(vehicle_spec), manifest.get("brief"), args.body_style))
                write_json(args.output / "00-recipe/recipe.json", selection)
                if selection["builder"] is None:
                    raise ValueError(f"No recipe for {selection['selection']['family']}; reconstruction was not started")
            extra = ["--no-remove-bg"] if args.no_remove_bg else []
            if args.reconstruction_cache:
                if args.backend != "hunyuan-mv":
                    raise ValueError("Reconstruction cache requires hunyuan-mv")
                from .multiview import reuse_reconstruction
                reuse_reconstruction(args.reconstruction_cache, args.output / "02-mesh", source,
                                     vehicle_spec, args.resolution, args.reconstruction_steps, args.seed)
                record["reconstruction_reused_from"] = str(args.reconstruction_cache.resolve())
            else:
                invoke("reconstruct", "--views" if args.backend == "hunyuan-mv" else "--image", source,
                       "--backend", args.backend, "--reconstruction-steps", args.reconstruction_steps,
                       "--seed", args.seed,
                       "--output", args.output / "02-mesh", "--resolution", args.resolution,
                       "--chunk-size", args.chunk_size, "--device", args.device, *extra, *spec_args)
            mesh = args.output / "02-mesh" / ("calibrated.glb" if vehicle_spec is not None else "mesh.glb")
            color_args = ["--color", args.color] if args.color is not None else []
            invoke("lego", "--mesh", mesh, "--output", args.output / "03-lego",
                   *size_args, "--up-axis", args.up_axis, "--yaw", args.yaw,
                   "--strategy", args.strategy, *(["--no-rear-wing"] if args.no_rear_wing else []), *color_args,
                   *(["--width-studs", args.width_studs] if args.width_studs is not None else []),
                   *(["--body-style", args.body_style] if args.body_style else []),
                   *(["--reference-views", args.reference_views or source] if args.backend == "hunyuan-mv" or args.reference_views else []),
                   *(["--requirements-snapshot", requirements_snapshot] if requirements_snapshot else []),
                   *(["--style-reference", args.style_reference] if args.style_reference else []),
                   "--proportion-mode", args.proportion_mode, "--function-profile", args.function_profile,
                   "--part-policy", args.part_policy, "--optimization-profile", args.optimization_profile, *spec_args)
            if args.optimization_profile == "quality":
                invoke("optimize", "--assembly-directory", args.output/"03-lego",
                       *(["--style-reference", args.style_reference] if args.style_reference else []))
            from .native import configured_preview
            native = configured_preview(args.output / "03-lego" / "model.ldr", args.output / "03-lego")
            record["native_preview"] = "03-lego/native-ldraw.png" if native else None
            thumbnail = native or args.output / "03-lego" / "lego-preview.png"
            thumbnail_args = ["--thumbnail", thumbnail] if thumbnail.exists() else []
            invoke("studio", "--ldraw", args.output / "03-lego" / "model.ldr",
                   "--output", args.output / "04-studio", *thumbnail_args)
            record["studio_file"] = "04-studio/model.io"
            record["buildability"] = "unverified"
            record["appearance_accepted"] = False
            record["delivery_accepted"] = False
    elif args.command == "image":
        from .image import generate
        print(generate(args.prompt, args.output, args.seed, args.steps, args.device, vehicle_spec,
                       args.image_provider, args.reference_image))
    elif args.command == "reconstruct":
        if args.backend == "hunyuan-mv":
            if not args.views or args.image or args.no_remove_bg:
                parser.error("hunyuan-mv requires --views and foreground masks; single-image input is unsupported")
            from .multiview import reconstruct
            print(reconstruct(args.views, args.output, args.resolution, args.chunk_size, args.device,
                              vehicle_spec, args.reconstruction_steps, args.seed))
        else:
            if not args.image or args.views:
                parser.error("triposr requires --image")
            from .reconstruction import reconstruct
            print(reconstruct(args.image, args.output, args.resolution, args.chunk_size, args.device,
                              not args.no_remove_bg, vehicle_spec))
    elif args.command == "lego":
        if args.strategy == "car":
            from .carfit import convert_car
            print(convert_car(args.mesh, args.output, args.length_studs, args.up_axis, args.yaw,
                              args.color, not args.no_rear_wing, vehicle_spec, args.width_studs, args.scale,
                              json.loads(args.requirements_snapshot.read_text(encoding="utf-8"))
                              if args.requirements_snapshot else None, args.body_style, args.reference_views,
                              generation_config=dict(proportion_mode=args.proportion_mode, function_profile=args.function_profile,
                                  part_policy=args.part_policy, optimization_profile=args.optimization_profile),
                              style_reference=args.style_reference))
        else:
            from .lego import convert
            print(convert(args.mesh, args.output, args.length_studs if args.length_studs is not None else 24,
                          args.up_axis, args.yaw, args.color))
    elif args.command == "studio":
        from .studio import export_studio
        print(export_studio(args.ldraw, args.output, args.thumbnail))
    elif args.command == "measure":
        from .measurement import measure_side_view
        from .common import digest
        with stage(args.output, "measurement", {"image_sha256": digest(args.reference_image),
                    "annotation_sha256": digest(args.annotations), "vehicle_spec_sha256": digest(vehicle_spec)}) as record:
            spec = measure_side_view(load_spec(vehicle_spec),
                                     json.loads(args.annotations.read_text(encoding="utf-8")), args.reference_image)
            output = args.output / "spec.json"
            write_json(output, spec)
            record.update(physical_measurement_verified=False, outputs={output.name: digest(output)})
        print(output)


def invoke(*arguments):
    # A process per model stage guarantees GPU memory is released before the next stage.
    subprocess.run([sys.executable, "-m", "car2lego3d", *map(str, arguments)], check=True)
