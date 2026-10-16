"""Loopback UI adapter for the existing CLI; model stages still run in subprocesses."""
from collections import deque
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from threading import Lock, Thread
from urllib.parse import urlsplit
from uuid import uuid4

from .common import ROOT, write_json
from .project import PROJECT_NAME, FRONTEND_PORT, API_PORT, LEGACY_WEB_PORT
from .requirements import SCALES, plan_design, requirements_for_scale
from .research import ResearchStore
from .vehicle import load_spec


CATALOG = ROOT / "frontend/lib/data/vehicle-brands.json"
FILES = {"image": "01-image/reference.png", "mesh": "02-mesh/mesh.glb",
         "front": "01-image/front.png", "back": "01-image/back.png",
         "left": "01-image/left.png", "right": "01-image/right.png", "views": "01-image/views.json",
         "recipe": "03-lego/recipe-selection.json", "mesh_preview": "02-mesh/mesh-preview.png",
         "native": "03-lego/native-ldraw.png",
         "preview": "03-lego/mesh-preview.png", "ldraw": "03-lego/model.ldr",
         "studio": "04-studio/model.io", "request": "request.json", "specs": "vehicle-specs.json"}
COLORS = {4: ("红色", "red"), 25: ("橙色", "orange"), 0: ("黑色", "black"), 15: ("白色", "white"),
          1: ("蓝色", "blue"), 14: ("黄色", "yellow"), 2: ("绿色", "green")}


def catalog_brands():
    return json.loads(CATALOG.read_text(encoding="utf-8"))["brands"]


def write_status(path, state):
    # File scanners can briefly hold newly created files on Windows.
    for attempt in range(6):
        try:
            write_json(path, state)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(.05 * (attempt + 1))


def text_field(payload, name, limit, required=True):
    value = payload.get(name, "")
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f"{name} 字段无效或超过 {limit} 字符。")
    value = value.strip()
    if required and not value:
        raise ValueError(f"请填写 {name}。")
    return value


def vehicle_identity(payload):
    if not isinstance(payload, dict):
        raise ValueError("请求必须为 JSON 对象。")
    custom = payload.get("custom", False)
    if not isinstance(custom, bool):
        raise ValueError("品牌选择状态无效。")
    brand = text_field(payload, "brand", 100)
    selected = next((item for item in catalog_brands() if item["name"] == brand), None)
    if not custom and selected is None:
        raise ValueError("请从目录选择品牌，或选择其他后输入品牌。")
    model = text_field(payload, "model", 64)
    variant = text_field(payload, "variant", 100, required=False)
    return {"brand": brand, "model": model, "variant": variant}, selected, custom


def prepare_request(payload, research):
    identity, selected, custom = vehicle_identity(payload)
    brand, model, variant = (identity[k] for k in ("brand", "model", "variant"))
    description = text_field(payload, "description", 100, required=False)
    scale = payload.get("scale", "auto")
    if scale not in (*SCALES, "custom", "auto"):
        raise ValueError("不支持的比例。")
    color = payload.get("color", 4)
    seed = payload.get("seed", 42)
    if type(color) is not int or color not in COLORS:
        raise ValueError("车身颜色无效。")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("种子应为 0 到 4294967295 的整数。")
    request = {"brand": brand, "brand_id": selected["id"] if not custom else None,
               "brand_zh": selected["name_zh"] if not custom else brand,
               "custom": custom, "model": model, "variant": variant, "description": description,
               "scale": scale, "color": color, "seed": seed}
    prompt = f"a {COLORS[color][1]} {brand} {model} {variant} car".replace("  ", " ")
    if description:
        prompt += f", {description}"
    arguments = ["--prompt", prompt, "--seed", str(seed), "--steps", "4", "--resolution", "256",
                 "--strategy", "car", "--color", str(color)]
    spec_path = research.specification(payload.get("research_id"), identity)
    spec = load_spec(spec_path)
    request.update(research_id=payload["research_id"], resolved_model=spec["model"], resolved_variant=spec["variant"])
    if scale == "auto":
        plan = plan_design(spec)
    elif scale != "custom":
        plan = plan_design(spec, scale)
        arguments += ["--scale", scale]
    else:
        length = payload.get("length_studs", 24)
        if type(length) is not int or not 23 <= length <= 32:
            raise ValueError("当前研究配方的车长范围为 23–32 studs。")
        request["length_studs"] = length
        plan = plan_design(spec, length_studs=length)
        arguments += ["--length-studs", str(length)]
    arguments += ["--vehicle-specs", str(spec_path)]
    request["vehicle_spec_fingerprint"] = plan["vehicle_spec_fingerprint"]
    request["prompt"] = prompt
    return request, arguments, plan


class JobStore:
    def __init__(self, directory, research):
        self.directory = Path(directory)
        self.research = research
        self.lock = Lock()
        self.active = None
        self.worker = None

    def start(self, payload):
        request, arguments, plan = prepare_request(payload, self.research)
        if not plan["generation"]["draft_recipe_available"]:
            raise ValueError("所选比例或尺寸的生成配方尚未实现：" + " ".join(plan["generation"]["blockers"]))
        with self.lock:
            if self.active is not None:
                raise RuntimeError("已有生成任务运行中，请等待完成。")
            job_id = uuid4().hex
            directory = self.directory / job_id
            # CLI run requires an empty output directory; metadata lives beside its artifacts.
            write_json(directory / "request.json", request)
            write_json(directory / "requirements.json", plan)
            spec_index = arguments.index("--vehicle-specs") + 1
            snapshot = directory / FILES["specs"]
            shutil.copyfile(arguments[spec_index], snapshot)
            arguments[spec_index] = str(snapshot)
            write_status(directory / "status.json", {"status": "running", "error": None})
            self.active = job_id
            self.worker = Thread(target=self._run, args=(job_id, arguments))
            self.worker.start()
        return self.get(job_id)

    def _run(self, job_id, arguments):
        directory = self.directory / job_id
        try:
            with (directory / "pipeline.log").open("w", encoding="utf-8") as log:
                result = subprocess.run([sys.executable, "-u", "-m", "car2lego3d", "run", *arguments,
                                         "--output", str(directory / "artifacts")], cwd=ROOT,
                                        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"},
                                        stdout=log, stderr=subprocess.STDOUT, check=False)
            if result.returncode:
                raise RuntimeError("生成失败，详情见运行日志。")
            if not (directory / "artifacts" / FILES["studio"]).is_file():
                raise RuntimeError("流程未生成 Studio 文件。")
            state = {"status": "completed", "error": None}
        except Exception as error:
            state = {"status": "failed", "error": str(error)}
        finally:
            with self.lock:
                try:
                    write_status(directory / "status.json", state)
                finally:
                    self.active = None

    def job_directory(self, job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise FileNotFoundError("任务不存在。")
        directory = self.directory / job_id
        if not (directory / "status.json").is_file():
            raise FileNotFoundError("任务不存在。")
        return directory

    def file(self, job_id, kind):
        directory = self.job_directory(job_id)
        if kind not in FILES:
            raise FileNotFoundError("文件不存在。")
        path = directory / (FILES[kind] if kind in {"request", "specs"} else f"artifacts/{FILES[kind]}")
        if not path.is_file():
            raise FileNotFoundError("文件尚未生成。")
        return path

    def get(self, job_id):
        directory = self.job_directory(job_id)
        # Windows disallows replacing a file while another thread has it open.
        with self.lock:
            state = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            if state["status"] == "running" and self.active != job_id:
                state = {"status": "interrupted", "error": "服务曾中断，请重新生成。"}
        log = directory / "pipeline.log"
        with log.open(encoding="utf-8", errors="replace") if log.exists() else open(os.devnull) as stream:
            lines = list(deque(stream, maxlen=40))
        files = {}
        for kind in FILES:
            try:
                self.file(job_id, kind)
                files[kind] = f"/api/jobs/{job_id}/files/{kind}"
            except FileNotFoundError:
                pass
        return {"id": job_id, **state, "request": json.loads((directory / "request.json").read_text(encoding="utf-8")),
                "files": files, "log": "".join(lines)[-8000:], "delivery_accepted": False}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, jobs, desktop=None, **kwargs):
        self.jobs = jobs
        self.desktop = desktop
        super().__init__(*args, **kwargs)

    def local_host(self):
        ports = {str(self.server.server_port)}
        if self.desktop:
            ports.add(os.environ.get("CAR2LEGO_FRONTEND_PORT", str(FRONTEND_PORT)))
        return self.headers.get("Host") in {f"{host}:{port}" for host in ("localhost", "127.0.0.1") for port in ports}

    def research(self):
        return getattr(self.jobs, "research", None)

    def json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        if not path.is_file():
            raise FileNotFoundError(path)
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Length", str(path.stat().st_size))
        if path.suffix in {".io", ".glb", ".ldr", ".json"}:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        with path.open("rb") as stream:
            self.copyfile(stream, self.wfile)

    def do_GET(self):
        if not self.local_host():
            return self.json_response({"error": "仅支持本机访问。"}, 403)
        path = urlsplit(self.path).path
        try:
            if path.startswith("/api/v1/") and self.desktop:
                from .desktop import dispatch_get
                return dispatch_get(self, path)
            if path == "/api/config":
                return self.json_response({"scales": [requirements_for_scale(s) for s in SCALES],
                                           "colors": [{"code": code, "name": name[0]} for code, name in COLORS.items()]})
            if path == "/api/preview":
                return self.send_file(ROOT / "examples/f40-physical/03-lego/native-ldraw.png")
            match = re.fullmatch(r"/api/research/([a-f0-9]{32})", path)
            if match:
                if not self.research():
                    return self.json_response({"error": "规格搜索暂不可用。"}, 503)
                return self.json_response(self.research().get(match[1]))
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})(?:/files/([a-z_]+))?", path)
            if match:
                if match[2]:
                    return self.send_file(self.jobs.file(match[1], match[2]))
                return self.json_response(self.jobs.get(match[1]))
            if path.startswith("/api/"):
                raise FileNotFoundError
            return super().do_GET()
        except FileNotFoundError:
            self.json_response({"error": "任务或文件不存在。"}, 404)
        except (ValueError, RuntimeError) as error:
            self.json_response({"error": str(error)}, 400)

    def do_POST(self):
        origin = self.headers.get("Origin")
        ports = {str(self.server.server_port)}
        if self.desktop:
            ports.add(os.environ.get("CAR2LEGO_FRONTEND_PORT", str(FRONTEND_PORT)))
        if not self.local_host() or origin not in {f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in ports}:
            return self.json_response({"error": "仅接受同源本机请求。"}, 403)
        if urlsplit(self.path).path.startswith("/api/v1/") and self.desktop:
            from .desktop import dispatch_post
            try:
                return dispatch_post(self, urlsplit(self.path).path)
            except FileNotFoundError:
                return self.json_response({"error": "任务或文件不存在。"}, 404)
            except (ValueError, UnicodeDecodeError) as error:
                return self.json_response({"error": str(error)}, 400)
            except RuntimeError as error:
                return self.json_response({"error": str(error)}, 409)
        if self.path not in {"/api/jobs", "/api/research", "/api/plan"}:
            return self.json_response({"error": "接口不存在。"}, 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192 or self.headers.get_content_type() != "application/json":
                raise ValueError("请提交有效 JSON 请求。")
            payload = json.loads(self.rfile.read(length))
            if self.path == "/api/research":
                if not self.research():
                    return self.json_response({"error": "规格搜索暂不可用。"}, 503)
                identity, _, _ = vehicle_identity(payload)
                refresh = payload.get("refresh", False)
                if not isinstance(refresh, bool):
                    raise ValueError("重新搜索选项无效。")
                return self.json_response(self.jobs.research.start(identity, refresh), 202)
            if self.path == "/api/plan":
                if not self.research():
                    return self.json_response({"error": "规格搜索暂不可用。"}, 503)
                return self.json_response(prepare_request(payload, self.research())[2])
            self.json_response(self.jobs.start(payload), 202)
        except (ValueError, UnicodeDecodeError) as error:
            self.json_response({"error": str(error)}, 400)
        except FileNotFoundError:
            self.json_response({"error": "请先搜索当前车型的物理规格。"}, 400)
        except RuntimeError as error:
            self.json_response({"error": str(error)}, 409)


def serve(port=LEGACY_WEB_PORT):
    frontend = ROOT / "frontend/dist"
    if not (frontend / "index.html").is_file():
        raise RuntimeError("Build the page first: cd frontend; npm ci; npm run build")
    research = ResearchStore(ROOT / "runs/vehicle-research")
    jobs = JobStore(ROOT / "runs/web", research)
    handler = partial(Handler, directory=str(frontend), jobs=jobs)
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"{PROJECT_NAME}: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if jobs.worker:
                jobs.worker.join()
            research.close()


def serve_desktop(port=API_PORT):
    from .desktop import DesktopStore
    research = ResearchStore(ROOT / "runs/vehicle-research")
    jobs = JobStore(ROOT / "runs/web", research)
    desktop = DesktopStore(jobs)
    handler = partial(Handler, directory=str(ROOT / "frontend/public"), jobs=jobs, desktop=desktop)
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"{PROJECT_NAME} API: http://127.0.0.1:{port}", flush=True)
        try:
            server.serve_forever()
        finally:
            if desktop.active:
                desktop.control(desktop.active, "cancel")
            if desktop.worker:
                desktop.worker.join()
            research.close()
