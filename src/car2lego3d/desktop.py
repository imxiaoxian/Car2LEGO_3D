"""Original Car2LEGO frontend contract over the isolated multiview pipeline."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from threading import Condition, RLock, Thread
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from xml.etree.ElementTree import Element, SubElement, tostring

from PIL import Image, ImageOps

from .common import ROOT, digest, write_json
from .gpt_reference import configuration
from .recipes import classify, selection_record
from .requirements import SCALES, VISUAL_SIZE_HINT, plan_design, requirements_for_scale
from .vehicle import tyre_diameter
from .config import VERSION, options
from .checkpoints import output_snapshot, stage_signature


def read(path, fallback=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else deepcopy(fallback)


def bridge(operation, output, input_path=None):
    source, python = configuration()
    command = [python, "-B", str(ROOT / "scripts/frontend_bridge_worker.py"), operation,
               "--source", str(source), "--output", str(output)]
    if input_path:
        command += ["--input", str(input_path)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=300,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"})
    if result.returncode:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix(".log").write_bytes(result.stdout + result.stderr)
        raise ValueError(f"{operation} service failed; inspect local bridge log")
    return read(output)


def ui_policy(scale):
    original = read(ROOT / "data/ui/catalog.json")["policies"][scale]
    actual = requirements_for_scale(scale)
    original.update(version=actual["version"], dimension_tolerances_percent=actual["dimension_tolerances_percent"],
        component_tolerances_percent={"tyre_diameter": actual["dimension_tolerances_percent"]["front_tyre_diameter"],
                                      "tyre_width": actual["dimension_tolerances_percent"]["front_tyre_width"]},
        required_measurements=actual["required_measurements"], reference_views=actual["reference_views"],
        max_step_parts=actual["max_parts_per_step"])
    return original


def physical(spec, request, applied=False, audit=None):
    scale = request.get("scale") or "auto"
    denominator = int(scale.split(":")[1]) if scale in SCALES else None
    sources = spec.get("sources", {})
    audit = audit or {}
    dialogue = audit.get("mode") == "llm_dialogue" or spec.get("acquisition") == "vehicle-spec-dialogue-v1"
    measurements = {}
    for name, item in spec.get("measurements", {}).items():
        if item.get("value") is None:
            continue
        source = sources.get(item.get("source"), {})
        grounded = item.get("status") in {"manufacturer", "reported", "measured"}
        value = item["value"]
        mm = value * 25.4 if item["unit"] == "inch" else value
        measurements[name] = dict(value=value, unit=item["unit"], method="sourced" if grounded else "estimated",
            grounded=grounded, conflicted=False, source_url=source.get("url"), source_kind=item.get("status"),
            source_status="model_provided" if dialogue else "fetched", vehicle_match="model_selected" if dialogue else "source_scoped",
            confidence=item.get("confidence", 1 if grounded else 0),
            target_model_mm=mm / denominator if denominator and item["unit"] in {"mm", "inch"} else None,
            evidence=item.get("evidence", ""), definition=name)
    for axle in ("front", "rear"):
        diameter = tyre_diameter(spec, axle)
        if diameter is not None:
            items = [measurements.get(f"{axle}_{field}", {}) for field in ("tyre_width", "tyre_aspect", "rim_diameter")]
            measurements[f"{axle}_tyre_diameter"] = dict(value=diameter, unit="mm", method="derived",
                grounded=all(i.get("grounded", False) for i in items), conflicted=False,
                target_model_mm=diameter / denominator if denominator else None, source_url=items[0].get("source_url"), evidence="Tyre specification conversion")
    required = requirements_for_scale(scale if scale in SCALES else "1:12")["required_measurements"]
    missing = [n for n, item in spec.get("measurements", {}).items() if item.get("value") is None]
    return dict(version="car2lego3d-physical-v2", selected_variant=spec.get("variant"),
        acquisition_mode="llm_dialogue" if dialogue else "sourced", physical_measurement_verified=False,
        vehicle=dict(make=request.get("brand", request.get("make", "")),
        model=request["model"], year=request.get("year")), scale=scale, applied_to_design=applied,
        overall_grounded=all(measurements.get(n, {}).get("grounded") for n in ("length", "width", "height", "wheelbase")),
        measurements=measurements, missing=missing, required_measurements=required, conflicts=[],
        missing_required=[n for n in required if n not in measurements],
        sources=[dict(url=s.get("url", ""), title=s.get("title", ""), status="fetched",
            measurement_count=sum(m.get("source") == key for m in spec.get("measurements", {}).values()))
            for key, s in sources.items() if s.get("url")],
        research=dict(skill="" if dialogue else "vehicle-spec-search", mode="llm_dialogue" if dialogue else "web_search",
            model=audit.get("model") or sources.get("model", {}).get("model"), version=audit.get("version", "1"), status="completed",
            dialogue_count=1 if dialogue else 0,
            documents_read=len((audit or {}).get("opened_pages", [])),
            queries=[dict(query=q["query"], purpose="vehicle specifications", status="searched", engine="Bing",
                results=q.get("results", [])) for q in (audit or {}).get("calls", [])]))


class Stopped(Exception):
    pass


class DesktopStore:
    def __init__(self, jobs):
        self.jobs = jobs
        self.root = jobs.directory
        self.condition = Condition(RLock())
        self.active = None
        self.worker = None
        self.process = None
        self.geometry_lock = RLock()

    def directory(self, id):
        return self.jobs.job_directory(id)

    def metadata(self, id):
        return read(self.directory(id) / "desktop.json", {})

    def artifacts(self, id):
        directory = self.directory(id)
        name = self.metadata(id).get("artifacts", "artifacts")
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("Invalid artifact directory")
        return path

    def state(self, id):
        with self.condition:
            state = read(self.directory(id) / "status.json", {})
            status = state["status"]
            if status in {"completed", "draft"}:
                status = "draft"
            elif status in {"running", "processing", "pending"}:
                status = "processing" if self.active == id else "failed"
            elif status == "interrupted":
                status = "failed"
            return dict(id=id, status=status, stage=state.get("stage", status),
                        progress=state.get("progress", 100 if status == "draft" else 0),
                        error_message=state.get("error"))

    def update(self, id, **values):
        with self.condition:
            path = self.directory(id) / "status.json"
            data = read(path, {})
            data.update(values)
            write_json(path, data)

    def submit(self, payload, image=None, parent=None):
        make, model = payload.get("make", ""), payload.get("model", "")
        if not isinstance(make, str) or not isinstance(model, str) or len(make) > 100 or len(model) > 100:
            raise ValueError("Invalid vehicle identity")
        if any(ord(c) < 32 for c in make + model):
            raise ValueError("车型名称不能包含控制字符。")
        if not image and (not make.strip() or not model.strip()):
            raise ValueError("请选择品牌并填写车型。")
        scale = payload.get("scale") or "auto"
        if scale not in (*SCALES, "auto"):
            raise ValueError("不支持的比例。")
        year = payload.get("year") or None
        if year is not None and (isinstance(year, bool) or not str(year).isdigit() or not 1900 <= int(year) <= 2030):
            raise ValueError("年款应为 1900 至 2030，或留空。")
        custom = payload.get("customization_text", "")
        if not isinstance(custom, str) or len(custom) > 6000:
            raise ValueError("改装要求超过长度限制。")
        color = payload.get("color")
        if color is not None:
            from .lego import PALETTE
            if type(color) is not int or color not in PALETTE:
                raise ValueError("不支持的乐高配色。")
        image_bytes = None
        if image:
            with Image.open(BytesIO(image)) as opened:
                if opened.format not in {"JPEG", "PNG", "WEBP"} or opened.width * opened.height > 40_000_000:
                    raise ValueError("请选择 JPG、PNG 或 WebP 车辆照片。")
                normalized = ImageOps.exif_transpose(opened).convert("RGB")
                normalized.thumbnail((2048, 2048))
                buffer = BytesIO()
                normalized.save(buffer, "PNG")
                image_bytes = buffer.getvalue()
        with self.condition:
            if self.active or self.jobs.active:
                raise RuntimeError("已有生成任务运行中，请等待完成。")
            id = uuid4().hex
            directory = self.root / id
            request = dict(brand=make.strip(), model=model.strip(), year=int(year) if year else None,
                variant=str(year or ""), scale=scale, custom_request=custom, parent_design_id=parent,
                color=payload.get("color"), seed=42, created_at=datetime.now(timezone.utc).isoformat())
            request.update(generation_config=options({key: payload[key] for key in options() if key in payload}),
                           sizing_mode="automatic_visual", preferred_scale_denominator=VISUAL_SIZE_HINT,
                           pipeline_version=VERSION)
            write_json(directory / "request.json", request)
            write_json(directory / "status.json", dict(status="pending", stage="starting", progress=0, error=None))
            write_json(directory / "desktop.json", dict(artifacts="artifacts", workflow="multiview_v1"))
            if image_bytes:
                (directory / "source.png").write_bytes(image_bytes)
            elif parent and (self.directory(parent) / "source.png").exists():
                shutil.copyfile(self.directory(parent) / "source.png", directory / "source.png")
            self.active = id
            self.worker = Thread(target=self.run, args=(id,), daemon=True)
            self.worker.start()
        return self.detail(id)

    def checkpoint(self, id, stage, progress):
        with self.condition:
            while read(self.directory(id) / "status.json")["status"] == "paused":
                self.condition.wait(1)
            if read(self.directory(id) / "status.json")["status"] == "cancelled":
                raise Stopped()
            self.update(id, status="processing", stage=stage, progress=progress)

    def command(self, id, args):
        with (self.directory(id) / "pipeline.log").open("a", encoding="utf-8") as log:
            with self.condition:
                if read(self.directory(id) / "status.json")["status"] == "cancelled":
                    raise Stopped()
                self.process = subprocess.Popen([str(a) for a in args], cwd=ROOT, stdout=log,
                    stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
                process = self.process
            code = process.wait()
            with self.condition:
                self.process = None
                if read(self.directory(id) / "status.json")["status"] == "cancelled":
                    raise Stopped()
            if code:
                raise ValueError("生成阶段失败，请查看任务日志后恢复。")

    def run(self, id):
        try:
            directory, artifacts = self.directory(id), self.artifacts(id)
            request = read(directory / "request.json")
            source = directory / "source.png"
            if source.exists() and not request.get("recognized"):
                self.checkpoint(id, "recognition", 2)
                features = bridge("recognize", directory / "recognition.json", source)
                request.update(brand=request["brand"] or features["make"], model=request["model"] or features["model"], recognized=True)
                # An omitted year stays omitted; image recognition is not production-year evidence.
                write_json(directory / "request.json", request)
            spec_path = directory / "vehicle-specs.json"
            if not spec_path.exists():
                self.checkpoint(id, "vehicle_research", 5)
                result = self.research(request)
                if result["status"] != "ready":
                    raise ValueError(result.get("error") or "车型资料不完整：" + "；".join(result.get("questions", [])))
                write_json(spec_path, result["spec"])
                write_json(directory / "research-audit.json", result.get("audit", {}))
            spec = read(spec_path)
            plan = plan_design(spec, request.get("scale"))
            write_json(directory / "requirements.json", plan)
            if not plan["generation"]["draft_recipe_available"]:
                raise ValueError("当前比例尚无可用装配配方：" + " ".join(plan["generation"]["blockers"]))
            spec_args = ["--vehicle-specs", str(spec_path)]
            prefix = [sys.executable, "-u", "-m", "car2lego3d"]
            views, mesh, lego, studio = (artifacts / n for n in ("01-image", "02-mesh", "03-lego", "04-studio"))
            prompt = f"{request['brand']} {request['model']} {request.get('year') or ''}. Selected configuration: {spec['variant']}. {request.get('custom_request', '')}"
            if request.get("color") is not None:
                prompt += f" LEGO body color ID {request['color']}."
            stages = [
                ("reference_images", 12, views / "views.json", [*prefix, "image", "--prompt", prompt,
                    "--output", views, *spec_args, *(["--reference-image", source] if source.exists() else [])]),
                ("reconstruction", 45, mesh / "calibrated.glb", [*prefix, "reconstruct", "--views", views / "views.json",
                    "--output", mesh, "--backend", "hunyuan-mv", "--resolution", "256", "--reconstruction-steps", "50", *spec_args]),
                ("assembly", 75, lego / "model.ldr", [*prefix, "lego", "--mesh", mesh / "calibrated.glb",
                    "--output", lego, *(["--scale", request["scale"]] if request.get("scale") in SCALES else []),
                    "--reference-views", views / "views.json", *spec_args,
                    *(["--color", request["color"]] if request.get("color") is not None else [])]),
                ("optimization", 84, lego / "quality-report.json", [*prefix, "optimize", "--assembly-directory", lego]),
                ("exporting", 90, studio / "model.io", [*prefix, "studio", "--ldraw", lego / "model.ldr", "--output", studio]),
            ]
            generation = options(request.get("generation_config"))
            for phase, _, _, command in stages:
                if phase == "assembly":
                    for key in ("proportion_mode", "function_profile", "part_policy", "optimization_profile"):
                        command += ["--"+key.replace("_", "-"), generation[key]]
            if generation["optimization_profile"] == "draft":
                stages = [entry for entry in stages if entry[0] != "optimization"]
            done = read(directory / "checkpoints.json", {})
            for stage, progress, output, command in stages:
                self.checkpoint(id, stage, progress)
                if stage == "assembly" and request.get("color") is None:
                    color_path = directory / "reference-color.json"
                    color_record = read(color_path, {})
                    image_hash = digest(views / "reference.png")
                    if color_record.get("image_sha256") != image_hash:
                        features = bridge("recognize", directory / "reference-appearance.json", views / "reference.png")
                        from .lego import PALETTE
                        selected = features["closest_lego_color"]
                        if selected not in PALETTE:
                            raise ValueError("识别到的车身颜色尚无对应配方，请补充可用的乐高颜色。")
                        color_record = dict(color=selected, image_sha256=image_hash, source="GPT reference appearance analysis")
                        write_json(color_path, color_record)
                    command += ["--color", color_record["color"]]
                signature = stage_signature(stage, command, directory, artifacts, spec_path, request, source)
                checkpoint = done.get(stage)
                valid = (output.exists() and isinstance(checkpoint, dict)
                         and checkpoint.get("files") == output_snapshot(stage, output)
                         and checkpoint.get("inputs") == signature)
                if not valid:
                    self.command(id, command)
                    signature = stage_signature(stage, command, directory, artifacts, spec_path, request, source)
                    done[stage] = dict(files=output_snapshot(stage, output), inputs=signature)
                    if stage == "optimization" and isinstance(done.get("assembly"), dict):
                        # Optimization replaces the assembly with its selected legal version.
                        done["assembly"]["files"] = output_snapshot("assembly", lego/"model.ldr")
                    write_json(directory / "checkpoints.json", done)
                if stage == "reference_images":
                    from .multiview import load_views
                    manifest, _ = load_views(output)
                    selection = selection_record(classify(spec, manifest.get("brief")))
                    write_json(artifacts / "00-recipe/recipe.json", selection)
                    if selection["builder"] is None:
                        raise ValueError("该车身类型尚无可用配方，已保留参考图。")
            self.checkpoint(id, "rendering", 94)
            self.geometry(id)
            source_root, source_python = configuration()
            presentation = artifacts / "05-presentation/presentation.json"
            assembly_hash = digest(lego / "assembly.json")
            if read(presentation, {}).get("assembly_sha256") != assembly_hash:
                self.command(id, [source_python, "-B", ROOT / "scripts/frontend_bridge_worker.py", "presentation",
                    "--source", source_root, "--input", lego / "assembly.json", "--request", directory / "request.json",
                    "--output", presentation])
            self.checkpoint(id, "validation", 98)
            audit = lego / "native-audit.json"
            if read(audit, {}).get("assembly_sha256") != assembly_hash:
                self.command(id, [source_python, "-B", ROOT / "scripts/native_audit_worker.py", "--source", source_root,
                                  "--assembly", lego / "assembly.json", "--output", audit])
            self.checkpoint(id, "draft", 100)
            self.update(id, status="draft", error=None)
        except Stopped:
            pass
        except Exception as error:
            with self.condition:
                if read(self.directory(id) / "status.json")["status"] != "cancelled":
                    self.update(id, status="failed", error=str(error))
        finally:
            with self.condition:
                self.active = None
                self.condition.notify_all()

    def research(self, request):
        identity = dict(brand=request.get("brand", request.get("make")), model=request["model"],
                        variant=str(request.get("year") or request.get("variant") or ""))
        result = self.jobs.research.start(identity)
        while result["status"] == "running":
            time.sleep(.5)
            result = self.jobs.research.get(result["id"])
        return result

    def control(self, id, action, module=None):
        with self.condition:
            state = self.state(id)
            if action == "pause" and self.active == id:
                self.update(id, status="paused")
            elif action == "cancel":
                self.update(id, status="cancelled", stage="cancelled")
                if self.active == id and self.process and self.process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True)
                    else:
                        self.process.terminate()
                self.condition.notify_all()
            elif action in {"resume", "retry"}:
                if self.active and self.active != id:
                    raise RuntimeError("已有生成任务运行中。")
                if self.active == id:
                    self.update(id, status="processing", error=None)
                    self.condition.notify_all()
                else:
                    if state["status"] not in {"failed", "paused", "draft"}:
                        raise ValueError("当前任务不能恢复。")
                    if action == "retry" or not self.metadata(id):
                        old = self.artifacts(id)
                        revision = "revisions/" + uuid4().hex + "/artifacts"
                        target = self.directory(id) / revision
                        previous = read(self.directory(id) / "checkpoints.json", {})
                        done = {}
                        for stage, folder, file in (("reference_images", "01-image", "views.json"),
                                                     ("reconstruction", "02-mesh", "calibrated.glb")):
                            if (old / folder / file).is_file():
                                shutil.copytree(old / folder, target / folder)
                                if isinstance(previous.get(stage), dict):
                                    done[stage] = previous[stage]
                        write_json(self.directory(id) / "desktop.json", dict(artifacts=revision,
                            workflow="multiview_v1", retry_scope="full assembly", requested_module=module))
                        write_json(self.directory(id) / "checkpoints.json", done)
                    self.active = id
                    self.update(id, status="pending", error=None)
                    self.worker = Thread(target=self.run, args=(id,), daemon=True)
                    self.worker.start()
            else:
                raise ValueError("当前状态不支持该操作。")
        return self.state(id)

    def scene(self, id):
        presentation = self.artifacts(id) / "05-presentation/scene.json"
        if presentation.is_file():
            return read(presentation)
        path = self.artifacts(id) / "03-lego/assembly.json"
        if not path.is_file():
            raise FileNotFoundError()
        rows = read(path)["parts"]
        parts = [dict(id=f"p{i}", part_num=p["part"] + ".dat", color_id=p["color"], module=p["module"],
                      feature="", transform=dict(position=p["position"], rotation=p["rotation"])) for i, p in enumerate(rows)]
        steps = sorted({p["step"] for p in rows})
        return dict(version="car2lego3d-scene-v1", parts=parts,
            modules=[dict(id=m, depends_on=[]) for m in sorted({p["module"] for p in rows})],
            steps=[dict(index=i+1, module="assembly", parts=[f"p{j}" for j,p in enumerate(rows) if p["step"] == step])
                   for i,step in enumerate(steps)], joints=[])

    def geometry(self, id):
        with self.geometry_lock:
            assembly = self.artifacts(id) / "03-lego/assembly.json"
            path = self.artifacts(id) / "03-lego" / ("geometry-" + digest(assembly)[:16] + ".json")
            if not path.exists():
                bridge("geometry", path, assembly)
            return path

    def detail(self, id):
        with self.condition:
            directory, artifacts = self.directory(id), self.artifacts(id)
            request, state = read(directory / "request.json"), self.state(id)
            rows = read(artifacts / "03-lego/assembly.json", {}).get("parts", [])
            parts = [dict(part_num=part, ldraw_color_id=color, quantity=count)
                     for (part,color),count in Counter((p["part"], p["color"]) for p in rows).items()]
            base = f"/api/v1/designs/{id}"
            files = {kind: f"/api/v1/export/{kind}/{id}" for kind, relative in
                {"io": "04-studio/model.io", "ldr": "03-lego/model.ldr", "csv": "03-lego/inventory.csv",
                 "mpd": "05-presentation/model.mpd", "pdf": "05-presentation/instructions.pdf"}.items()
                if (artifacts / relative).is_file()}
            if rows:
                files.update(xml=f"/api/v1/export/xml/{id}", quality=base+"/quality")
            manifest = read(artifacts / "01-image/views.json", {})
            from .reference_plan import REFERENCE_GROUPS
            references = [dict(view="rear" if view == "back" else view, model=item.get("model", "unknown"),
                url=base+"/reference-images/"+view, synthetic=True,
                category="detail" if group == "details" else "exterior", purpose=item.get("purpose"),
                modules=item.get("modules", []))
                for group in REFERENCE_GROUPS for view,item in manifest.get(group, {}).items()]
            image_record = read(artifacts / "01-image/image.json", {})
            if (artifacts / "01-image/reference.png").exists():
                references.insert(0, dict(view="overview", model=image_record.get("model", "unknown"),
                    url=base+"/reference-images/overview", synthetic=True, category="exterior"))
            spec = read(directory / "vehicle-specs.json", {})
            scale = request.get("scale") or "auto"
            car = dict(id=id, make=request["brand"], model=request["model"], year=request.get("year"),
                       created_at=request.get("created_at", datetime.fromtimestamp(directory.stat().st_ctime, timezone.utc).isoformat()))
            quality = self.quality(id) if rows else None
            dimensions, deviations = self.native_dimensions(id)
            selection = read(artifacts / "03-lego/recipe-selection.json", {})
            calls = Counter(read(artifacts / "01-image/car2lego/service-record.json", {}).get("calls", []))
            return dict(id=id, **{k:v for k,v in state.items() if k != "id"}, car=car, parts=parts,
                parts_count=len(rows), created_at=car["created_at"], file_urls=files,
                workflow_version=VERSION, requested_scale=scale, sizing_mode=request.get("sizing_mode", "legacy"),
                visual_size_preference=read(artifacts / "03-lego/fit.json", {}).get("requirements_plan", {}).get("visual_size_preference")
                    or read(directory / "requirements.json", {}).get("visual_size_preference"),
                scale_requirements=(ui_policy(scale) | {"applied_to_design": bool(spec)}) if scale in SCALES else None,
                physical_specifications=physical(spec, request, bool(rows), read(directory / "research-audit.json", {})) if spec else None,
                scene_url=base+"/scene" if rows else None,
                assembly_preview_url=base+"/assembly-preview" if (artifacts / "03-lego/native-ldraw.png").exists() else None,
                quality_report=quality, reference_images=references,
                actual_dimensions_mm=dimensions, scale_deviation_percent=deviations,
                official_build_references=[dict(set_num=g["set"], name=g["name"], instructions_url=g["url"],
                    product_url=g["url"]) for g in selection.get("official_references", [])],
                renders=[dict(view=v, url=base+"/renders/"+v) for v in ("front", "rear", "left", "right", "top", "bottom")
                         if (artifacts / f"05-presentation/render_{v}.png").exists()],
                parent_design_id=request.get("parent_design_id"), customization_request=request.get("custom_request"),
                job=dict(state=state["status"], stage=state["stage"], progress=state["progress"],
                    calls=dict(text=calls.get("text", 0), image=calls.get("image", 0)), limits=dict(text=None, image=None), module_repairs={}))

    def native_audit(self, id):
        assembly = self.artifacts(id) / "03-lego/assembly.json"
        audit = read(self.artifacts(id) / "03-lego/native-audit.json", {})
        return audit if assembly.exists() and audit.get("assembly_sha256") == digest(assembly) else {}

    def native_dimensions(self, id):
        audit = self.native_audit(id)
        if "bounds_ldu" not in audit:
            return {}, {}
        low, high = audit["bounds_ldu"]
        actual = {key: round((high[axis]-low[axis])*.4, 3) for key,axis in (("length",0), ("width",2), ("height",1))}
        spec = read(self.directory(id) / "vehicle-specs.json", {})
        scale = read(self.directory(id) / "request.json").get("scale")
        if scale not in SCALES:
            return actual, {}
        deviations = {key: round((value/(spec["measurements"][key]["value"]/int(scale.split(":")[1]))-1)*100, 2)
                      for key,value in actual.items() if spec.get("measurements", {}).get(key, {}).get("value")}
        return actual, deviations

    def quality(self, id):
        report = read(self.artifacts(id) / "03-lego/requirements-report.json", {})
        audit = self.native_audit(id)
        checks = report.get("dimension_checks", [])
        _, deviations = self.native_dimensions(id)
        request = read(self.directory(id) / "request.json")
        visual = options(request.get("generation_config"))["proportion_mode"] == "visual"
        scale = request.get("scale")
        tolerances = requirements_for_scale(scale if scale in SCALES else "1:24")["dimension_tolerances_percent"]
        native_failures = [key for key,value in deviations.items() if abs(value) > tolerances[key]]
        details = read(self.artifacts(id) / "03-lego/detail-report.json", {})
        assembly = self.artifacts(id) / "03-lego/assembly.json"
        if not assembly.exists() or details.get("assembly_sha256") != digest(assembly):
            details = {}
        return dict(digital_status="draft", precision_verified=False,
            gates=dict(parts="unverified", connections=audit.get("connectivity", {}).get("status", "unverified"),
                collisions=("failed" if audit["collision_count"] else
                    "passed" if audit.get("collision_coverage") == "complete" else "unverified") if "collision_count" in audit else "unverified",
                dimensions="advisory" if visual else "failed" if native_failures or any(c["status"] == "out_of_tolerance" for c in checks) else "unverified",
                appearance="unverified", mechanisms="unverified",
                feature_retention="failed" if details.get("critical_unresolved_count") else "unverified"),
            detail_preservation=details or None,
            studio=dict(status="unverified"), physical=dict(status="unverified"),
            issues=[dict(kind=f"{c['field']} 超出比例容差", parts=[], modules=["body_and_chassis"])
                    for c in checks if not visual and c["status"] == "out_of_tolerance"] +
                   [dict(kind=f"{key} 原生零件外包尺寸超差", parts=[], modules=["body_and_chassis"]) for key in native_failures if not visual] +
                   [dict(kind=f"{f['view']} / {f['description']}：细节尚未表达", parts=[], modules=["body_and_chassis"])
                    for f in details.get("features", []) if f["status"] == "unresolved"])

    def export(self, id, kind):
        paths = {"io": "04-studio/model.io", "ldr": "03-lego/model.ldr", "csv": "03-lego/inventory.csv",
                 "mpd": "05-presentation/model.mpd", "pdf": "05-presentation/instructions.pdf"}
        if kind in paths:
            return self.artifacts(id) / paths[kind]
        if kind != "xml":
            raise FileNotFoundError()
        root = Element("INVENTORY")
        for part in self.detail(id)["parts"]:
            item = SubElement(root, "ITEM")
            for key, value in dict(ITEMTYPE="P", ITEMID=part["part_num"], MINQTY=part["quantity"]).items():
                SubElement(item, key).text = str(value)
            # Studio's table supplies the different BrickLink and LDraw namespaces.
            colors = read(ROOT / "data/ui/catalog.json")["colors"]
            color = colors.get(str(part["ldraw_color_id"]))
            if color is None:
                raise ValueError("该颜色尚未配置 BrickLink 映射，不能导出采购清单。")
            SubElement(item, "COLOR").text = str(color["bricklink"])
        path = self.artifacts(id) / "03-lego/wanted.xml"
        path.write_bytes(tostring(root, encoding="utf-8", xml_declaration=True))
        return path


def dispatch_get(handler, path):
    store = handler.desktop
    if path == "/api/v1/designs/scales":
        return handler.json_response(dict(default="auto", selection_enabled=False, sizing_mode="automatic_visual",
            preferred_scale_denominator=VISUAL_SIZE_HINT, detail_standard="1:12", exact_scale_required=False,
            scales=[dict(ratio=s, mechanisms=[], status="legacy_hint", recipe_available=True,
                         precision_supported=False, requirements=ui_policy(s)) for s in SCALES]))
    if path == "/api/v1/mods":
        return handler.json_response(read(ROOT / "data/ui/catalog.json")["mods"])
    if path in {"/api/v1/sets/vehicles", "/api/v1/sets", "/api/v1/sets/known-cars"}:
        vehicles = read(ROOT / "data/ui/catalog.json")["vehicles"]
        for v in vehicles:
            selection = selection_record(classify(dict(model=f"{v['make']} {v['model']}", body_style=v["body_style"])))
            v["recipe_available"] = selection["builder"] is not None
        return handler.json_response(dict(vehicles=vehicles) if path.endswith("vehicles") else vehicles)
    match = re.fullmatch(r"/api/v1/export/(\w+)/([a-f0-9]{32})", path)
    if match:
        return handler.send_file(store.export(match[2], match[1]))
    match = re.fullmatch(r"/api/v1/designs/([a-f0-9]{32})(?:/(.*))?", path)
    if not match:
        raise FileNotFoundError()
    id, action = match.groups()
    if action is None:
        return handler.json_response(store.detail(id))
    if action == "status":
        return handler.json_response(store.state(id))
    if action == "parts":
        return handler.json_response(store.detail(id)["parts"])
    if action == "scene":
        return handler.json_response(store.scene(id))
    if action == "geometry":
        return handler.send_file(store.geometry(id))
    if action == "quality":
        return handler.json_response(store.quality(id))
    if action == "ldr":
        return handler.send_file(store.export(id, "ldr"))
    if action == "assembly-preview":
        return handler.send_file(store.artifacts(id) / "03-lego/native-ldraw.png")
    if action.startswith("renders/") and action.split("/")[-1] in {"front", "rear", "left", "right", "top", "bottom"}:
        return handler.send_file(store.artifacts(id) / f"05-presentation/render_{action.split('/')[-1]}.png")
    if action.startswith("reference-images/"):
        view = action.split("/")[-1]
        directory = store.artifacts(id) / "01-image"
        if view == "overview":
            return handler.send_file(directory / "reference.png")
        from .reference_plan import REFERENCE_GROUPS
        manifest = read(directory / "views.json", {})
        item = next((manifest.get(group, {}).get(view) for group in REFERENCE_GROUPS
                     if view in manifest.get(group, {})), None)
        if not item:
            raise FileNotFoundError()
        path = (directory / item["file"]).resolve()
        if not path.is_relative_to(directory.resolve()) or digest(path) != item["sha256"]:
            raise ValueError("Reference file path or hash mismatch")
        return handler.send_file(path)
    raise FileNotFoundError()


def dispatch_post(handler, path):
    length = int(handler.headers.get("Content-Length", "0"))
    if length < 0 or length > 21 * 1024 * 1024:
        raise ValueError("上传文件不能超过 20 MB。")
    raw = handler.rfile.read(length)
    payload, image = {}, None
    if path == "/api/v1/designs/from-image":
        content_type = handler.headers.get("Content-Type", "")
        message = BytesParser(policy=default).parsebytes(("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw)
        if not message.is_multipart():
            raise ValueError("请选择车辆照片。")
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if name == "image":
                image = part.get_payload(decode=True)
            elif name in {"make", "model", "year", "scale"}:
                payload[name] = part.get_payload(decode=True).decode("utf-8")
        if not image or len(image) > 20 * 1024 * 1024:
            raise ValueError("请选择 20 MB 以内的车辆照片。")
    elif raw:
        if len(raw) > 32000 or handler.headers.get_content_type() != "application/json":
            raise ValueError("无效的 JSON 请求。")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("无效的 JSON 请求。")
    store = handler.desktop
    if path in {"/api/v1/designs", "/api/v1/designs/from-image"}:
        return handler.json_response(store.submit(payload, image), 201)
    if path == "/api/v1/research/physical-specifications":
        result = store.research(payload)
        if result["status"] != "ready":
            raise ValueError(result.get("error") or "未找到可用的车型尺寸。")
        return handler.json_response(physical(result["spec"], payload, audit=result.get("audit")))
    match = re.fullmatch(r"/api/v1/designs/([a-f0-9]{32})/(customize|studio-open|control/(\w+))", path)
    if not match:
        raise FileNotFoundError()
    id, action, control = match.groups()
    if control:
        return handler.json_response(store.control(id, control, parse_qs(urlsplit(handler.path).query).get("module", [None])[0]))
    if action == "customize":
        if store.state(id)["status"] not in {"draft", "completed"}:
            raise ValueError("请等待基础设计生成完成。")
        request = read(store.directory(id) / "request.json")
        result = store.submit(dict(make=request["brand"], model=request["model"], year=request.get("year"),
            scale="auto",
            customization_text=payload.get("customization_text", "")), parent=id)
        return handler.json_response(dict(id=result["id"], status=result["status"], parent_design_id=id, message="改装设计已创建"), 201)
    file = store.export(id, "io")
    if not file.exists():
        raise FileNotFoundError()
    source, _ = configuration()
    studio = source / "Studio 2.0/Studio.exe"
    if not studio.is_file():
        raise ValueError("未找到 BrickLink Studio。")
    subprocess.Popen([str(studio), str(file.resolve())], cwd=studio.parent)
    return handler.json_response(dict(success=True, io_path=str(file), studio_opened=True, file_opened=False))
