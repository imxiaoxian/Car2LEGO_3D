import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

from PIL import Image, PngImagePlugin
import pytest

from car2lego3d.common import ROOT, write_json
from car2lego3d.gpt_reference import configuration


worker = SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/car2lego_reference_worker.py")))


@pytest.fixture(autouse=True)
def reference_concurrency(monkeypatch):
    monkeypatch.setenv("CAR2LEGO_REFERENCE_CONCURRENCY", "5")


def image_bytes(color):
    output = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(output, format="PNG")
    return output.getvalue()


def test_reference_queue_fills_five_slots_before_any_view_completes(tmp_path):
    async def scenario():
        started, finished = set(), []
        all_started = asyncio.Event()
        anchor = image_bytes("red")

        def directory(state):
            path = Path(state["storage_path"]) / state["design_id"]
            path.mkdir(parents=True, exist_ok=True)
            return path

        class SourceService:
            async def generate(self, state, only):
                view, = only
                refs = directory(state) / "references"
                refs.mkdir(exist_ok=True)
                if view == "overview":
                    data = anchor
                    manifest = {"brief": {}, "images": []}
                else:
                    manifest = json.loads((refs / "manifest.json").read_text())
                    assert (refs / "overview.png").read_bytes() == anchor
                    assert [ref["view"] for ref in manifest["images"]] == ["overview"]
                    assert directory(state) != tmp_path / "car2lego"
                    batch_index = next(i for i, batch in enumerate(worker.BATCHES) if view in batch)
                    saved = json.loads((tmp_path / "car2lego/references/manifest.json").read_text())
                    previous = {name for batch in worker.BATCHES[:batch_index] for name in batch}
                    assert previous <= {ref["view"] for ref in saved["images"]}
                    started.add(view)
                    if len(started) == 5:
                        all_started.set()
                    await asyncio.wait_for(all_started.wait(), timeout=5)
                    assert len(started) >= 5
                    data = image_bytes((len(finished) * 10, 90, 20))
                    finished.append(view)
                (refs / f"{view}.png").write_bytes(data)
                ref = {"view": view, "path": f"references/{view}.png",
                       "sha256": hashlib.sha256(data).hexdigest()}
                manifest["images"].append(ref)
                write_json(refs / "manifest.json", manifest)
                return {"reference_images": manifest["images"]}

        service = SimpleNamespace(ReferenceImageService=SourceService, design_directory=directory,
                                  write_progress=lambda *args: None)
        result = await worker.generate_references(service, {"design_id": "car2lego", "storage_path": str(tmp_path)},
                                                  write_json, lambda source, target: source.replace(target))
        assert set(finished) == set(worker.PARALLEL_SHOTS)
        assert [ref["view"] for ref in result["reference_images"]] == list(worker.SHOT_NAMES)
        assert result["generation_schedule"]["mode"] == "overview_then_sequential_batches"
        assert result["generation_schedule"]["batch_barrier"] == "all_succeeded"
        assert [len(batch) for batch in worker.BATCHES] == [5, 5, 5, 3]
        assert [view for batch in worker.BATCHES for view in batch] == list(worker.PARALLEL_SHOTS)
        assert not list((tmp_path / "car2lego").glob("reference-batch-*"))

    asyncio.run(scenario())


def test_rate_limit_retries_reduce_concurrency_and_drain_permits():
    class RateLimitError(Exception):
        status_code = 429

    async def scenario():
        gate = worker.ImageRequestGate(4, backoff=0)
        calls, active, peak = {}, 0, 0

        async def provider(view):
            nonlocal active, peak
            calls[view] = calls.get(view, 0) + 1
            if calls[view] == 1:
                raise RateLimitError()
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(.01)
                return view
            finally:
                active -= 1

        result = await asyncio.gather(*(gate.call(lambda v=v: provider(v), v,
                                     lambda e: isinstance(e, RateLimitError)) for v in ("a", "b", "c")))
        assert result == ["a", "b", "c"]
        assert gate.limit == 1 and peak == 1 and gate.active == 0
        assert calls == {"a": 2, "b": 2, "c": 2}
        assert len(gate.retries) == 3
        with pytest.raises(RateLimitError):
            await gate.call(lambda: _reject(), "permanent", lambda e: True)
        assert gate.active == 0

    async def _reject():
        raise RateLimitError()

    asyncio.run(scenario())


def test_queue_snapshot_records_active_retries_and_completion():
    class RateLimitError(Exception):
        status_code = 429

    async def scenario():
        snapshots, calls = [], 0
        gate = worker.ImageRequestGate(5, backoff=0, update=snapshots.append)

        async def provider():
            nonlocal calls
            calls += 1
            assert snapshots[-1]["active_views"] == ["lights"]
            assert snapshots[-1]["active_tasks"] == 1
            if calls == 1:
                raise RateLimitError()
            return "ok"

        assert await gate.call(provider, "lights", lambda error: True) == "ok"
        final = snapshots[-1]
        assert final["configured_limit"] == 5 and final["effective_limit"] == 2
        assert final["active_tasks"] == 0 and final["active_views"] == []
        assert final["states"] == {"lights": "completed"}
        assert final["attempts"] == {"lights": 2}
        assert any(s["states"]["lights"] == "retry_wait" for s in snapshots)
        assert final["retries"][0]["status"] == 429

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [0, 6, True, 2.5])
def test_gate_rejects_invalid_provider_concurrency(limit):
    with pytest.raises(ValueError, match="between 1 and 5"):
        worker.ImageRequestGate(limit)


@pytest.mark.parametrize("limit", ["0", "6", "invalid"])
def test_concurrency_outside_provider_limit_fails_before_any_request(tmp_path, monkeypatch, limit):
    monkeypatch.setenv("CAR2LEGO_REFERENCE_CONCURRENCY", limit)
    service = SimpleNamespace(design_directory=lambda state: tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(worker.generate_references(service, {}, write_json, None))


@pytest.fixture
def upstream(monkeypatch, tmp_path):
    """Exercise the reused service with an in-memory provider and no network calls."""
    pytest.importorskip("langchain_openai")
    pytest.importorskip("pydantic_settings")
    source, _ = configuration()
    if not (source / "backend/app/services/reference_images.py").is_file():
        pytest.skip("Original Car2LEGO service is required for bridge integration tests")
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.syspath_prepend(str(source / "backend"))
    from app.services import reference_images as service
    from app.services.assembly_v3.jobs import atomic_json, replace_file

    monkeypatch.setattr(service, "REFERENCE_VIEWS", dict(service.REFERENCE_VIEWS))
    monkeypatch.setattr(service, "ALL_REFERENCE_VIEWS", dict(service.ALL_REFERENCE_VIEWS))
    monkeypatch.setattr(service, "ReferenceShotRequest", service.ReferenceShotRequest)
    worker.configure_service(service)
    monkeypatch.setattr(service.settings, "openai_api_key", "test-no-network")
    monkeypatch.setattr(service.settings, "openai_image_model", "gpt-image-test")
    source_photo = tmp_path / "source.png"
    source_photo.write_bytes(image_bytes("white"))
    progress, calls = [], []
    state = {"design_id": "car2lego", "storage_path": str(tmp_path / "output"),
             "make": "Lotus", "model": "For Me", "year": None, "mod_ids": [],
             "vehicle_features": {}, "custom_request": "test", "source_image_path": str(source_photo),
             "requested_reference_shots": worker.shot_requests(),
             "checkpoint_progress": lambda stage, percent: progress.append((stage, percent)),
             "consume_call": calls.append}

    class Provider:
        overview_color = "red"

        def reset(self, expected, failures=(), hold=False):
            self.expected, self.failures = set(expected), set(failures)
            self.hold = hold
            self.started, self.requests, self.anchors = set(), [], {}
            self.first_expected_batch = next((set(batch) & self.expected for batch in worker.BATCHES
                                             if set(batch) & self.expected), set())
            self.all_started, self.release = asyncio.Event(), asyncio.Event()
            self.active = self.peak = self.briefs = 0

        async def render(self, image=None, **kwargs):
            assert kwargs["model"] == "gpt-image-test"
            assert kwargs["n"] == 1
            view = "overview" if worker.SINGLE_VEHICLE_VIEW in kwargs["prompt"] else next(
                name for name in worker.PARALLEL_SHOTS if
                (worker.shot_instructions(worker.VIEW_SHOTS[name]) if name in worker.VIEW_SHOTS
                 else worker.auxiliary_instructions(name)) in kwargs["prompt"])
            self.requests.append(view)
            if image:
                self.anchors[view] = [file.read() for file in image]
            if view == "overview":
                assert self.anchors[view] == [source_photo.read_bytes()]
                if view in self.failures:
                    raise RuntimeError("simulated overview failure")
                data = image_bytes(self.overview_color)
            else:
                assert view in self.expected
                batch_index = next(i for i, batch in enumerate(worker.BATCHES) if view in batch)
                saved = json.loads((Path(state["storage_path"]) / "car2lego/references/manifest.json").read_text())
                previous = {name for batch in worker.BATCHES[:batch_index] for name in batch}
                assert previous <= {ref["view"] for ref in saved["images"]}
                self.started.add(view)
                self.active += 1
                self.peak = max(self.active, self.peak)
                if len(self.started & self.first_expected_batch) >= min(
                        int(os.environ["CAR2LEGO_REFERENCE_CONCURRENCY"]), len(self.first_expected_batch)):
                    self.all_started.set()
                try:
                    await asyncio.wait_for(self.all_started.wait(), timeout=5)
                    await asyncio.sleep(0)
                    if self.hold:
                        await self.release.wait()
                    if view in self.failures:
                        raise RuntimeError(f"simulated {view} failure")
                    data = image_bytes((list(worker.PARALLEL_SHOTS).index(view) * 10, 60, 10))
                finally:
                    self.active -= 1
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(data).decode(), url=None)])

    provider = Provider()
    provider.reset(worker.PARALLEL_SHOTS)

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            self.images = SimpleNamespace(generate=provider.render, edit=provider.render)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class TextModel:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            provider.briefs += 1
            return service.VehicleReferenceBrief(body_style="suv", paint="red", visual_description="test")

    monkeypatch.setattr(service, "AsyncOpenAI", Client)
    monkeypatch.setattr(service, "create_text_llm", lambda **kwargs: TextModel())
    return SimpleNamespace(service=service, state=state, provider=provider, calls=calls, progress=progress,
                           directory=Path(state["storage_path"]) / "car2lego", source=source,
                           run=lambda: worker.generate_references(service, state, atomic_json, replace_file))


def manifest(harness):
    return json.loads((harness.directory / "references/manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("concurrency", [1, 2, 5])
def test_upstream_parallel_views_share_anchor_brief_and_cache(upstream, monkeypatch, concurrency):
    monkeypatch.setenv("CAR2LEGO_REFERENCE_CONCURRENCY", str(concurrency))
    result = asyncio.run(upstream.run())
    provider = upstream.provider
    assert provider.requests[0] == "overview"
    assert set(provider.requests[1:]) == set(worker.PARALLEL_SHOTS)
    assert provider.peak == concurrency
    assert provider.briefs == 1
    assert upstream.calls.count("image") == 19
    anchor = (upstream.directory / "references/overview.png").read_bytes()
    for view in worker.PARALLEL_SHOTS:
        assert provider.anchors[view] == [Path(upstream.state["source_image_path"]).read_bytes(), anchor]
    for ref in result["reference_images"]:
        assert hashlib.sha256((upstream.directory / ref["path"]).read_bytes()).hexdigest() == ref["sha256"]
    percentages = [percent for _, percent in upstream.progress]
    assert percentages == sorted(percentages)
    assert percentages[-1] == 35
    schedule = manifest(upstream)["generation_schedule"]
    assert schedule["max_parallel_views"] == concurrency
    assert schedule["batch_size"] == 5
    assert schedule["batches"] == worker.BATCHES
    assert schedule["batch_barrier"] == "all_succeeded"
    queue = json.loads((upstream.directory / "reference-queue.json").read_text())
    assert queue["active_tasks"] == 0 and queue["peak_active_tasks"] == concurrency
    assert queue["batch_index"] == len(worker.BATCHES)
    assert queue["batch_views"] == worker.BATCHES[-1]
    assert set(queue["states"].values()) == {"completed"}
    provider.reset(())
    asyncio.run(upstream.run())
    assert provider.requests == []
    assert provider.briefs == 0
    assert not list(upstream.directory.glob("reference-batch-*"))


@pytest.mark.parametrize("failed_view", ["rear", "front_lights", "dashboard"])
def test_upstream_partial_failure_pauses_later_batches_and_resumes_missing_views(upstream, failed_view):
    batch_index = next(i for i, batch in enumerate(worker.BATCHES) if failed_view in batch)
    attempted = {view for batch in worker.BATCHES[:batch_index + 1] for view in batch}
    upstream.provider.reset(worker.PARALLEL_SHOTS, failures={failed_view})
    with pytest.raises(RuntimeError, match=f"Reference views failed: {failed_view}"):
        asyncio.run(upstream.run())
    saved = manifest(upstream)
    assert {ref["view"] for ref in saved["images"]} == {"overview"} | (attempted - {failed_view})
    assert set(saved["generation_failures"]) == {failed_view}
    assert set(upstream.provider.requests) == {"overview"} | attempted
    assert upstream.provider.active == 0
    assert upstream.calls.count("image") == len(attempted) + 1
    queue = json.loads((upstream.directory / "reference-queue.json").read_text())
    assert queue["batch_index"] == batch_index + 1
    assert queue["states"][failed_view] == "failed"
    assert not list(upstream.directory.glob("reference-batch-*"))
    expected = set(worker.PARALLEL_SHOTS) - (attempted - {failed_view})
    upstream.provider.reset(expected)
    asyncio.run(upstream.run())
    assert upstream.provider.requests[0] == failed_view
    assert set(upstream.provider.requests) == expected
    assert len(upstream.provider.requests) == len(expected)
    assert upstream.calls.count("image") == 20
    assert len(manifest(upstream)["images"]) == 19
    assert "generation_failures" not in manifest(upstream)
    hashes = {ref["view"]: ref["sha256"] for ref in manifest(upstream)["images"]}
    assert all(hashes[ref["view"]] == ref["sha256"] for ref in saved["images"])


@pytest.mark.parametrize("retry", [False, True])
def test_next_batch_waits_for_last_view_and_its_retry(upstream, monkeypatch, retry):
    class TemporaryError(Exception):
        status_code = 503

    async def scenario():
        provider = upstream.provider
        original_render = provider.render
        original_progress = upstream.state["checkpoint_progress"]
        blocked_view = worker.BATCHES[0][-1]
        others_saved, blocked, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        next_batch_started = asyncio.Event()
        others = set(worker.BATCHES[0]) - {blocked_view}

        async def render(**kwargs):
            response = await original_render(**kwargs)
            if any(worker.auxiliary_instructions(view) in kwargs["prompt"]
                   for view in worker.BATCHES[1] if view in worker.SUPPLEMENTAL_SHOTS):
                next_batch_started.set()
            if worker.auxiliary_instructions(blocked_view) in kwargs["prompt"]:
                if retry and provider.requests.count(blocked_view) == 1:
                    raise TemporaryError("retry this view before the next batch")
                blocked.set()
                await release.wait()
            return response

        def progress(stage, percent):
            original_progress(stage, percent)
            if stage.endswith(":completed") and others <= {ref["view"] for ref in manifest(upstream)["images"]}:
                others_saved.set()

        monkeypatch.setattr(provider, "render", render)
        monkeypatch.setattr(upstream.service, "transient_model_error",
                            lambda error: isinstance(error, TemporaryError))
        upstream.state["checkpoint_progress"] = progress
        task = asyncio.create_task(upstream.run())
        try:
            await asyncio.wait_for(others_saved.wait(), timeout=5)
            await asyncio.wait_for(blocked.wait(), timeout=5)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(next_batch_started.wait(), timeout=.05)
            assert not task.done()
            assert set(provider.requests) == {"overview"} | set(worker.BATCHES[0])
            assert {ref["view"] for ref in manifest(upstream)["images"]} == {"overview"} | others
            queue = json.loads((upstream.directory / "reference-queue.json").read_text())
            assert queue["batch_index"] == 1
            assert queue["active_views"] == [blocked_view]
            assert queue["attempts"][blocked_view] == (2 if retry else 1)
            release.set()
            result = await asyncio.wait_for(task, timeout=5)
            assert next_batch_started.is_set()
            assert len(result["reference_images"]) == 19
            assert len(result["generation_retries"]) == int(retry)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("tampered", ["body", "door_windows", "overview"])
def test_upstream_tampered_cache_invalidates_view_or_all_anchor_dependents(upstream, tampered):
    asyncio.run(upstream.run())
    path = upstream.directory / "references" / f"{tampered}.png"
    path.write_bytes(image_bytes("blue"))
    if tampered == "overview":
        # Simulate an older batch whose views have not yet recorded their anchor.
        saved = manifest(upstream)
        for ref in saved["images"]:
            ref.pop("identity_anchor_sha256", None)
        write_json(upstream.directory / "references/manifest.json", saved)
        upstream.provider.overview_color = "yellow"
        expected = set(worker.PARALLEL_SHOTS)
    else:
        expected = {tampered}
    upstream.provider.reset(expected)
    asyncio.run(upstream.run())
    assert set(upstream.provider.requests) == expected | ({"overview"} if tampered == "overview" else set())
    assert upstream.provider.briefs == 0
    assert list((upstream.directory / "references/history").glob(f"{tampered}-*.png"))


def test_upstream_overview_failure_sends_no_view_requests(upstream):
    upstream.provider.reset(worker.PARALLEL_SHOTS, failures={"overview"})
    with pytest.raises(RuntimeError, match="simulated overview failure"):
        asyncio.run(upstream.run())
    assert upstream.provider.requests == ["overview"]
    assert not list(upstream.directory.glob("reference-batch-*"))


def test_cancellation_drains_all_requests_before_removing_task_directories(upstream):
    async def scenario():
        upstream.provider.reset(worker.PARALLEL_SHOTS, hold=True)
        task = asyncio.create_task(upstream.run())
        await asyncio.wait_for(upstream.provider.all_started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert upstream.provider.active == 0
        assert not list(upstream.directory.glob("reference-batch-*"))
        assert [ref["view"] for ref in manifest(upstream)["images"]] == ["overview"]

    asyncio.run(scenario())


def test_image_entrypoint_publishes_geometry_and_detail_evidence(upstream, monkeypatch, tmp_path, capsys):
    from car2lego3d import gpt_reference
    from car2lego3d.multiview import load_reference_images, load_views

    def run_worker(command, **kwargs):
        monkeypatch.setattr(sys, "argv", command[2:])
        returncode = worker.main()
        captured = capsys.readouterr()
        return SimpleNamespace(returncode=returncode, stdout=captured.out, stderr=captured.err)

    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "unused-storage"))
    # Keep worker environment changes local to this test.
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_IMAGE_MODEL",
                "IMAGE_QUALITY", "IMAGE_SIZE", "IMAGE_TIMEOUT"):
        monkeypatch.setenv(key, "test-no-network")
    monkeypatch.setattr(gpt_reference, "configuration", lambda: (upstream.source, sys.executable))
    monkeypatch.setattr(gpt_reference.subprocess, "run", run_worker)
    output = tmp_path / "entrypoint"
    # The provider mock inspects the same storage directory the CLI receives.
    upstream.state["storage_path"] = str(output)
    views = gpt_reference.generate("Lotus For Me", output, reference_image=upstream.state["source_image_path"])
    _, paths = load_views(views)
    assert set(paths) == {"front", "back", "left", "right"}
    _, groups = load_reference_images(views)
    assert len(groups["supplemental_views"]) == 4
    assert len(groups["details"]) == 10
    record = json.loads((output / "image.json").read_text(encoding="utf-8"))
    service_record = json.loads((output / "car2lego/service-record.json").read_text(encoding="utf-8"))
    assert record["status"] == "completed"
    assert record["generation_schedule"] == service_record["generation_schedule"] == worker.GENERATION_SCHEDULE
    assert service_record["source_modified"] is False
    assert service_record["calls"].count("image") == 19
    assert upstream.provider.peak == 5


def test_image_entrypoint_preserves_png_identity_bytes_and_accepts_own_anchor(tmp_path, monkeypatch):
    from car2lego3d import gpt_reference

    source, _ = configuration()
    output = tmp_path / "images"
    output.mkdir()
    anchor = output / "identity-anchor.png"
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("source", "Original vehicle crop")
    Image.new("RGB", (32, 32), "white").save(anchor, pnginfo=metadata)
    original = anchor.read_bytes()
    provided = tmp_path / "source.png"
    provided.write_bytes(original)
    monkeypatch.setattr(gpt_reference, "configuration", lambda: (source, sys.executable))
    calls = []

    def stop_after_anchor(*args, **kwargs):
        calls.append(anchor.read_bytes())
        return SimpleNamespace(returncode=1, stdout="stopped before requests", stderr="")

    monkeypatch.setattr(gpt_reference.subprocess, "run", stop_after_anchor)
    for path in (provided, anchor):
        with pytest.raises(RuntimeError, match="reference generation failed"):
            gpt_reference.generate("Countach", output, reference_image=path)
    assert calls == [original, original]
