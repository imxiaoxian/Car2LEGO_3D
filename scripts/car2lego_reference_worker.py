"""Run the original Car2LEGO GPT service with isolated output and no source writes."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from car2lego3d.reference_plan import (DETAIL_SHOTS, PRIMARY_SHOTS, SHOT_NAMES,
                                     SUPPLEMENTAL_SHOTS, auxiliary_instructions, shot_requests)

SINGLE_VEHICLE_VIEW = (
    "ONE single complete car in a front-left three-quarter view, seen from slightly above, "
    "camera elevation about 15 degrees, long focal length to limit perspective distortion. "
    "The front points toward the left of the image; show its front fascia and left side. "
    "All four wheel positions, roof, bumpers and mirrors must remain within frame. "
    "Use a seamless white background with a minimal contact shadow. The car occupies "
    "about 80 percent of the image width. This is the identity anchor for reference generation: "
    "output exactly one car, no contact sheet, panels, alternate angles or duplicated cars. "
    "If a source photo is present, preserve its exact vehicle identity, door count, lamp shapes, "
    "wheel style, roofline and paint. Reconstruct only the requested camera view."
)

VIEW_SHOTS = PRIMARY_SHOTS
PARALLEL_SHOTS = SHOT_NAMES[1:]
MAX_CONCURRENCY = 5
BATCHES = [list(PARALLEL_SHOTS[start:start + MAX_CONCURRENCY])
           for start in range(0, len(PARALLEL_SHOTS), MAX_CONCURRENCY)]
GENERATION_SCHEDULE = {"mode": "overview_then_sequential_batches", "anchor": "overview",
                       "parallel_views": list(PARALLEL_SHOTS), "max_parallel_views": MAX_CONCURRENCY,
                       "batch_size": MAX_CONCURRENCY, "batches": BATCHES,
                       "batch_barrier": "all_succeeded",
                       "reconstruction_views": list(PRIMARY_SHOTS),
                       "supplemental_views": list(SUPPLEMENTAL_SHOTS), "detail_views": list(DETAIL_SHOTS)}


class ImageRequestGate:
    """Lower concurrent provider calls after rejection; retain independent shot tasks."""

    def __init__(self, limit, attempts=4, backoff=2, update=None):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_CONCURRENCY:
            raise ValueError("Image request concurrency must be between 1 and 5")
        self.limit, self.attempts, self.backoff = limit, attempts, backoff
        self.configured_limit = limit
        self.active = 0
        self.peak = 0
        self.active_views = set()
        self.attempts_by_view = {}
        self.states = {}
        self.batch_index = 0
        self.batch_views = []
        self.update = update
        self.condition = asyncio.Condition()
        self.retries = []

    def publish(self):
        if self.update:
            self.update(dict(configured_limit=self.configured_limit, effective_limit=self.limit,
                active_tasks=self.active, peak_active_tasks=self.peak,
                active_views=sorted(self.active_views), states=dict(self.states),
                batch_index=self.batch_index, batch_views=list(self.batch_views),
                attempts=dict(self.attempts_by_view), retries=list(self.retries)))

    async def call(self, factory, view, retryable):
        self.states[view] = "queued"
        self.publish()
        for attempt in range(1, self.attempts + 1):
            async with self.condition:
                await self.condition.wait_for(lambda: self.active < self.limit)
                self.active += 1
                self.peak = max(self.peak, self.active)
                self.active_views.add(view)
                self.attempts_by_view[view] = attempt
                self.states[view] = "active"
            try:
                self.publish()
                result = await factory()
                self.states[view] = "completed"
                return result
            except asyncio.CancelledError:
                self.states[view] = "cancelled"
                raise
            except Exception as error:
                if attempt == self.attempts or not retryable(error):
                    self.states[view] = "failed"
                    raise
                status = getattr(error, "status_code", None)
                if status == 429:
                    self.limit = max(1, self.limit // 2)
                response = getattr(error, "response", None)
                header = response.headers.get("retry-after", "0") if response is not None else "0"
                try:
                    retry_after = max(0, float(header))
                except (ValueError, TypeError):
                    retry_after = 0
                delay = min(60, max(retry_after, self.backoff * 2 ** (attempt - 1)))
                self.retries.append(dict(view=view, attempt=attempt, status=status,
                                         delay_seconds=delay, concurrency_limit=self.limit))
                self.states[view] = "retry_wait"
            finally:
                async with self.condition:
                    self.active -= 1
                    self.active_views.discard(view)
                    self.condition.notify_all()
                self.publish()
            await asyncio.sleep(delay)


def configure_service(service):
    """Extend only this process's shot registry and request validator."""
    from pydantic import Field, create_model
    service.ReferenceShotRequest = create_model(
        "MultiviewReferenceShotRequest", __base__=service.ReferenceShotRequest,
        view=(str, Field(pattern="^(" + "|".join(SHOT_NAMES) + ")$")))
    service.REFERENCE_VIEWS["overview"] = SINGLE_VEHICLE_VIEW
    service.ALL_REFERENCE_VIEWS["overview"] = SINGLE_VEHICLE_VIEW
    for slot, view in VIEW_SHOTS.items():
        service.REFERENCE_VIEWS[slot] = shot_instructions(view)
        service.ALL_REFERENCE_VIEWS[slot] = shot_instructions(view)
    for name in (*SUPPLEMENTAL_SHOTS, *DETAIL_SHOTS):
        service.ALL_REFERENCE_VIEWS[name] = auxiliary_instructions(name)


def shot_instructions(view):
    directions = {
        "front": "exact straight front elevation, looking at the front bumper",
        "back": "exact straight rear elevation, looking at the rear bumper",
        "left": "exact left side elevation, front of the car toward image LEFT",
        "right": "exact right side elevation, front of the car toward image RIGHT",
    }
    return (
        f"ONE complete car in {directions[view]}. Orthographic camera at wheel hub height, "
        "zero elevation, no perspective or three-quarter view. Keep wheels straight, doors and "
        "all panels closed. Include the whole car, roof, tyres, mirrors and bumpers, with a "
        "15 percent clean margin. Plain white background, uniform studio light, minimal shadow. "
        "This image is one labelled input of a four-view 3D reconstruction. Preserve EXACTLY "
        "the same vehicle identity, paint, door count, wheel style and body proportions as the "
        "overview and source photo. Output one view only; no panels, contact sheet, close-ups, "
        "captions or measurement annotations. Do not mirror the left image to create the right side."
    )


async def generate_references(service, state, atomic_json, replace_file):
    """Reuse the source service with isolated manifests for concurrent view calls."""
    directory = service.design_directory(state)
    references = directory / "references"
    manifest_path = references / "manifest.json"
    concurrency = int(os.environ.get("CAR2LEGO_REFERENCE_CONCURRENCY", MAX_CONCURRENCY))
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError("CAR2LEGO_REFERENCE_CONCURRENCY must be between 1 and 5")
    gate = ImageRequestGate(concurrency, update=lambda snapshot: atomic_json(
        directory / "reference-queue.json", snapshot))
    retryable = getattr(service, "transient_model_error", lambda error: getattr(error, "status_code", None) == 429)
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    previous_anchor = next((ref.get("sha256") for ref in previous.get("images", [])
                            if ref["view"] == "overview"), None)
    for ref in previous.get("images", []):
        if ref["view"] in PARALLEL_SHOTS:
            ref.setdefault("identity_anchor_sha256", previous_anchor)
    if previous:
        atomic_json(manifest_path, previous)
    await gate.call(lambda: service.ReferenceImageService().generate(state, only={"overview"}),
                    "overview", retryable)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    anchor = next(ref for ref in manifest["images"] if ref["view"] == "overview")
    anchor_hash = hashlib.sha256((references / "overview.png").read_bytes()).hexdigest()
    if anchor.get("sha256") != anchor_hash:
        raise ValueError("Overview reference hash mismatch")
    # Cached views must follow a regenerated anchor, including after interrupted runs.
    manifest["images"] = [ref for ref in manifest["images"] if ref["view"] not in PARALLEL_SHOTS or (
        previous_anchor == anchor_hash and ref.get("identity_anchor_sha256", anchor_hash) == anchor_hash)]
    manifest["generation_schedule"] = {**GENERATION_SCHEDULE, "max_parallel_views": concurrency}
    manifest.pop("generation_failures", None)
    atomic_json(manifest_path, manifest)
    completed = set()

    def progress(stage, _percent=0):
        percent = 5 + round(30 * len(completed) / len(PARALLEL_SHOTS))
        service.write_progress(state, stage, percent)
        if state.get("checkpoint_progress"):
            state["checkpoint_progress"](stage, percent)

    async def generate_view(view, temporary):
        nonlocal manifest
        view_state = {**state, "storage_path": str(Path(temporary) / view),
                      "checkpoint_progress": progress}
        isolated = service.design_directory(view_state)
        isolated_refs = isolated / "references"
        isolated_refs.mkdir()
        snapshot = {**manifest, "images": [ref for ref in manifest["images"]
                                           if ref["view"] in {"overview", view}]}
        for ref in snapshot["images"]:
            origin = directory / ref["path"]
            if origin.is_file():
                shutil.copyfile(origin, isolated_refs / f"{ref['view']}.png")
        atomic_json(isolated_refs / "manifest.json", snapshot)
        result = await gate.call(lambda: service.ReferenceImageService().generate(view_state, only={view}),
                                 view, retryable)
        ref = next(ref for ref in result["reference_images"] if ref["view"] == view)
        origin = isolated_refs / f"{view}.png"
        if (ref["path"] != f"references/{view}.png" or
                hashlib.sha256(origin.read_bytes()).hexdigest() != ref["sha256"]):
            raise ValueError(f"Generated reference hash mismatch for {view}")
        target = references / f"{view}.png"
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != ref["sha256"]:
            if target.is_file():
                history = references / "history"
                history.mkdir(exist_ok=True)
                old_hash = hashlib.sha256(target.read_bytes()).hexdigest()
                shutil.copyfile(target, history / f"{view}-{old_hash}.png")
            pending = references / f"{view}.{uuid.uuid4().hex}.tmp"
            try:
                shutil.copyfile(origin, pending)
                replace_file(pending, target)
            finally:
                pending.unlink(missing_ok=True)
        ref = {**ref, "identity_anchor_sha256": anchor_hash,
               "category": "detail" if view in DETAIL_SHOTS else "exterior",
               "reconstruction_input": view in PRIMARY_SHOTS,
               "modules": {**SUPPLEMENTAL_SHOTS, **DETAIL_SHOTS}.get(view, (None, []))[1]}
        # No await between merging and saving: one event loop owns the final manifest.
        updated = {**manifest, "images": [item for item in manifest["images"] if item["view"] != view] + [ref]}
        atomic_json(manifest_path, updated)
        manifest = updated
        completed.add(view)
        progress(f"reference_images:{view}:completed")

    progress("reference_images:parallel")
    failures = {}
    with tempfile.TemporaryDirectory(prefix="reference-batch-", dir=directory) as temporary:
        for batch_index, batch in enumerate(BATCHES, start=1):
            gate.batch_index, gate.batch_views = batch_index, batch
            gate.publish()
            progress(f"reference_images:batch:{batch_index}:started")
            tasks = [asyncio.create_task(generate_view(view, temporary)) for view in batch]
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            failures = {view: error for view, error in zip(batch, results)
                        if isinstance(error, BaseException)}
            if failures:
                # No later batch starts until this batch has persisted every successful view.
                break
            progress(f"reference_images:batch:{batch_index}:completed")
    manifest["generation_retries"] = gate.retries
    manifest["effective_concurrency_limit"] = gate.limit
    if failures:
        manifest["generation_failures"] = {
            view: service.failure_details(error, f"reference_images:{view}", "image",
                                          attempt=gate.attempts_by_view.get(view, 1))
            for view, error in failures.items()}
        atomic_json(manifest_path, manifest)
        raise RuntimeError("Reference views failed: " + ", ".join(failures) +
                           ". Successful views are cached; rerun to retry missing views.") from next(iter(failures.values()))
    order = {view: index for index, view in enumerate(SHOT_NAMES)}
    manifest["images"].sort(key=lambda ref: order.get(ref["view"], len(order)))
    atomic_json(manifest_path, manifest)
    return {"reference_brief": manifest["brief"], "reference_images": manifest["images"],
            "generation_schedule": manifest["generation_schedule"], "generation_retries": gate.retries,
            "effective_concurrency_limit": gate.limit}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    source = args.source_root.resolve()
    state = json.loads(args.request.read_text(encoding="utf-8"))
    storage = Path(state["storage_path"]).resolve()
    if storage.is_relative_to(source) or state["design_id"] != "car2lego":
        raise ValueError("All generated files must stay outside the source project.")
    inherited = dotenv_values(source / "backend/.env")
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_IMAGE_MODEL",
                "IMAGE_QUALITY", "IMAGE_SIZE", "IMAGE_TIMEOUT"):
        if inherited.get(key) and not os.environ.get(key):
            os.environ[key] = inherited[key]
    os.environ["STORAGE_PATH"] = str(storage)
    sys.path.insert(0, str(source / "backend"))
    from app.services import reference_images as service
    from app.services.assembly_v3.jobs import atomic_json, replace_file
    if not service.settings.openai_image_model.startswith("gpt-image-"):
        raise ValueError("The source configuration must select a GPT Image model.")
    configure_service(service)
    state["requested_reference_shots"] = shot_requests()
    calls = []
    state["consume_call"] = lambda kind: calls.append(kind)
    state["checkpoint_progress"] = lambda stage, percent: print(json.dumps({"stage": stage, "progress": percent}), flush=True)
    try:
        result = asyncio.run(generate_references(service, state, atomic_json, replace_file))
    except Exception as error:
        print(json.dumps(service.failure_details(error, "reference", attempt=1)), flush=True)
        return 1
    atomic_json(storage / "car2lego/service-record.json", {
        "model": service.settings.openai_image_model, "quality": service.settings.image_quality,
        "size": service.settings.image_size, "calls": calls, "reference_images": result["reference_images"],
        "source_modified": False, "reconstruction_views": VIEW_SHOTS,
        "generation_schedule": result["generation_schedule"],
        "generation_retries": result["generation_retries"],
        "effective_concurrency_limit": result["effective_concurrency_limit"],
        "view_overrides": {**{view: shot_instructions(view) for view in VIEW_SHOTS.values()},
                           **{name: auxiliary_instructions(name) for name in (*SUPPLEMENTAL_SHOTS, *DETAIL_SHOTS)}}})
    print(json.dumps({"model": service.settings.openai_image_model, "calls": calls, "status": "completed"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
