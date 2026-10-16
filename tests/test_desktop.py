"""Desktop contract tests use local fixtures; no model calls or web search."""
from copy import deepcopy
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from threading import Event, Thread

import pytest

from car2lego3d.common import ROOT, digest, write_json
from car2lego3d.desktop import DesktopStore, physical, read, ui_policy
from car2lego3d.web import Handler, JobStore


@pytest.fixture
def store(tmp_path):
    store = DesktopStore(JobStore(tmp_path, None))
    id = "b"*32
    directory = tmp_path / id
    write_json(directory / "request.json", dict(brand="Lotus", model="For Me", year=None, scale="1:24"))
    write_json(directory / "status.json", dict(status="completed"))
    write_json(directory / "artifacts/03-lego/assembly.json", dict(parts=[dict(part="3001", color=4,
        position=[0,0,0], rotation=[1,0,0,0,1,0,0,0,1], module="body", step=1)]))
    yield store, id
    if store.worker:
        store.worker.join(5)


def test_imported_completion_is_draft_without_embedded_acceptance_reference(store):
    desktop, id = store
    detail = desktop.detail(id)
    assert detail["status"] == "draft"
    assert detail["car"]["year"] is None
    assert detail["parts_count"] == sum(p["quantity"] for p in detail["parts"]) == 1
    assert not detail["quality_report"]["precision_verified"]
    assert "delivery_standard" not in detail["quality_report"]
    assert "target_image" not in json.dumps(detail)
    scene = desktop.scene(id)
    assert scene["steps"][0]["parts"] == [scene["parts"][0]["id"]]
    assert scene["joints"] == []


def test_zero_collisions_with_missing_colliders_is_not_a_pass(store):
    desktop,id=store
    assembly=desktop.artifacts(id) / "03-lego/assembly.json"
    audit=dict(assembly_sha256=digest(assembly),collision_count=0,collision_coverage="partial",
               connectivity=dict(status="passed"))
    target=assembly.parent / "native-audit.json"
    write_json(target,audit)
    assert desktop.quality(id)["gates"]["collisions"]=="unverified"
    audit["collision_coverage"]="complete"
    write_json(target,audit)
    assert desktop.quality(id)["gates"]["collisions"]=="passed"
    assembly.write_text('{}',encoding="utf-8")
    assert desktop.quality(id)["gates"]["collisions"]=="unverified"


def test_specification_mapping_preserves_unknown_and_estimates():
    spec = read(ROOT / "data/vehicles/ferrari_f40_1987.json")
    spec["measurements"]["length"]["status"] = "estimated"
    result = physical(spec, dict(make="Ferrari", model="F40", year=None, scale="1:24"),
        audit=dict(calls=[dict(query="F40 dimensions", results=[])], opened_pages=[{}]))
    assert result["vehicle"]["year"] is None
    assert not result["overall_grounded"]
    assert not result["measurements"]["length"]["grounded"]
    assert result["measurements"]["front_tyre_diameter"]["method"] == "derived"
    assert result["measurements"]["front_rim_diameter"]["target_model_mm"] == pytest.approx(17*25.4/24)
    assert "door_length" in result["missing"]
    assert "door_length" not in result["measurements"]
    assert result["research"]["queries"][0]["status"] == "searched"
    assert result["research"]["documents_read"] == 1


def test_each_scale_uses_pipeline_tolerances():
    from car2lego3d.requirements import requirements_for_scale, SCALES
    for scale in SCALES:
        assert ui_policy(scale)["dimension_tolerances_percent"] == requirements_for_scale(scale)["dimension_tolerances_percent"]


def test_resume_respects_pause_and_cancel_at_checkpoint(store):
    desktop, id = store
    desktop.active = id
    desktop.update(id, status="processing")
    desktop.control(id, "pause")
    passed = Event()
    worker = Thread(target=lambda: (desktop.checkpoint(id, "assembly", 75), passed.set()))
    worker.start()
    assert not passed.wait(.05)
    assert desktop.state(id)["status"] == "paused"
    desktop.control(id, "resume")
    assert passed.wait(2)
    worker.join()
    desktop.control(id, "cancel")
    assert desktop.state(id)["status"] == "cancelled"


def test_retry_preserves_existing_draft_and_reuses_multiview_inputs(store, monkeypatch):
    desktop, id = store
    old = desktop.artifacts(id)
    write_json(old / "01-image/views.json", {"views": {"front": "test"}})
    (old / "02-mesh").mkdir()
    (old / "02-mesh/calibrated.glb").write_bytes(b"fixture")
    monkeypatch.setattr(desktop, "run", lambda id: None)
    desktop.control(id, "retry", "body")
    new = desktop.artifacts(id)
    assert new != old and (old / "03-lego/assembly.json").exists()
    assert (new / "02-mesh/calibrated.glb").read_bytes() == b"fixture"
    assert desktop.metadata(id)["retry_scope"] == "full assembly"
    assert not (new / "03-lego/assembly.json").exists()


def test_submission_keeps_year_optional_and_customization_parent(store, monkeypatch):
    desktop, id = store
    monkeypatch.setattr(desktop, "run", lambda id: None)
    result = desktop.submit(dict(make="New Brand", model="Test", scale="1:24", customization_text="black paint"), parent=id)
    request = read(desktop.directory(result["id"]) / "request.json")
    assert request["year"] is None and request["variant"] == ""
    assert request["parent_design_id"] == id
    assert request["custom_request"] == "black paint"
    assert request["preferred_scale_denominator"] == 12
    with pytest.raises(RuntimeError):
        desktop.submit(dict(make="New Brand", model="Test"))


def test_invalid_photo_and_year_do_not_start_worker(store):
    desktop, _ = store
    with pytest.raises(ValueError):
        desktop.submit(dict(make="Ferrari", model="F40", year="tomorrow"))
    with pytest.raises((ValueError, OSError)):
        desktop.submit({}, image=b"not an image")
    assert desktop.active is None


def test_detail_reference_gallery_and_http_integrity(store):
    from PIL import Image
    desktop, id = store
    directory = desktop.artifacts(id) / "01-image"
    directory.mkdir()
    Image.new("RGB", (64, 48), "red").save(directory / "front.png")
    Image.new("RGB", (64, 48), "blue").save(directory / "cockpit.png")
    manifest = directory / "views.json"
    record = dict(file="cockpit.png", sha256=digest(directory / "cockpit.png"), model="gpt-image-test",
                  purpose="Seat and dashboard evidence", modules=["interior"])
    write_json(manifest, dict(views={"front": dict(file="front.png", sha256=digest(directory / "front.png"))},
                             details={"cockpit": record}))
    gallery = {image["view"]: image for image in desktop.detail(id)["reference_images"]}
    assert gallery["cockpit"]["category"] == "detail"
    assert gallery["cockpit"]["modules"] == ["interior"]
    assert gallery["front"]["category"] == "exterior"
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, jobs=desktop.jobs, desktop=desktop,
                                                        directory=str(ROOT / "frontend/public")))
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        url = gallery["cockpit"]["url"]
        connection.request("GET", url)
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == (directory / "cockpit.png").read_bytes()
        (directory / "cockpit.png").write_bytes(b"changed")
        connection.request("GET", url)
        response = connection.getresponse()
        assert response.status == 400
        response.read()
        record["file"] = "../outside.png"
        write_json(manifest, dict(details={"cockpit": record}))
        connection.request("GET", url)
        response = connection.getresponse()
        assert response.status == 400
        response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_compatibility_http_and_origin_guard(store, monkeypatch):
    desktop, id = store
    monkeypatch.setenv("CAR2LEGO_FRONTEND_PORT", "8768")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, jobs=desktop.jobs, desktop=desktop, directory=str(ROOT / "frontend/public")))
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        for path in ("designs/scales", "sets/vehicles", "mods", f"designs/{id}", f"designs/{id}/scene", f"designs/{id}/status", f"designs/{id}/parts"):
            connection.request("GET", "/api/v1/"+path)
            response = connection.getresponse()
            assert response.status == 200, path
            assert json.loads(response.read())
        connection.request("POST", f"/api/v1/designs/{id}/control/cancel", headers={"Origin":"https://untrusted.example"})
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.request("POST", f"/api/v1/designs/{id}/control/cancel", headers={"Origin":"http://127.0.0.1:8768"})
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["status"] == "cancelled"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_worker_uses_four_views_and_reference_color_then_resumes_without_calls(tmp_path, ready_research, monkeypatch):
    from PIL import Image
    research, _ = ready_research()
    desktop = DesktopStore(JobStore(tmp_path / "jobs", research))
    id = "c"*32
    directory = desktop.root / id
    write_json(directory / "request.json", dict(brand="Ferrari", model="F40", year=None, scale="1:24", color=None))
    write_json(directory / "status.json", dict(status="pending"))
    write_json(directory / "desktop.json", dict(artifacts="artifacts"))
    commands = []

    def command(job, arguments):
        args = [str(a) for a in arguments]
        commands.append(args)
        if "optimize" in args:
            output = Path(args[args.index("--assembly-directory")+1])
            write_json(output/"quality-report.json", dict(status="not_accepted", delivery_accepted=False))
            return
        output = Path(args[args.index("--output")+1])
        if "presentation" in args or "--assembly" in args:
            write_json(output, dict(assembly_sha256=digest(directory / "artifacts/03-lego/assembly.json")))
            return
        output.mkdir(parents=True, exist_ok=True)
        if "image" in args:
            Image.new("RGB", (64,64), "black").save(output / "reference.png")
            views = {}
            for index, view in enumerate(("front", "back", "left", "right")):
                Image.new("RGB", (64,64), (index*40, 20, 30)).save(output / f"{view}.png")
                views[view] = dict(file=f"{view}.png", sha256=digest(output / f"{view}.png"), model="gpt-image-test")
            write_json(output / "views.json", dict(schema_version=1, synthetic=True, views=views, brief=dict(body_style="sports_car")))
        elif "reconstruct" in args:
            assert args[args.index("--backend")+1] == "hunyuan-mv"
            assert args[args.index("--reconstruction-steps")+1] == "50"
            assert "--views" in args and "--image" not in args
            (output / "calibrated.glb").write_bytes(b"mock reconstruction")
        elif "lego" in args:
            assert args[args.index("--color")+1] == "0"
            (output / "model.ldr").write_text("mock assembly")
            write_json(output / "assembly.json", dict(parts=[]))
        elif "studio" in args:
            (output / "model.io").write_bytes(b"mock archive")

    def appearance(operation, output, input_path):
        assert operation == "recognize" and input_path.name == "reference.png"
        return dict(closest_lego_color=0)

    monkeypatch.setattr(desktop, "command", command)
    monkeypatch.setattr(desktop, "geometry", lambda id: None)
    monkeypatch.setattr("car2lego3d.desktop.bridge", appearance)
    desktop.active = id
    desktop.run(id)
    assert desktop.state(id)["status"] == "draft", read(directory / "status.json")
    assert len(commands) == 7
    (directory / "artifacts/02-mesh/calibrated.glb").write_bytes(b"damaged mesh")
    desktop.active = id
    desktop.run(id)
    assert desktop.state(id)["status"] == "draft"
    assert len(commands) == 8
    assert all("image" not in args for args in commands[7:])
    desktop.active = id
    desktop.run(id)
    assert desktop.state(id)["status"] == "draft"
    assert len(commands) == 8
    desktop.control(id, "retry", "roof")
    desktop.worker.join(5)
    assert desktop.state(id)["status"] == "draft"
    assert all("image" not in args and "reconstruct" not in args for args in commands[8:])
