from copy import deepcopy
import json
from threading import Event
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from car2lego3d.common import write_json
from car2lego3d.research import ResearchStore, compile_result, identity_key, search_audit
from car2lego3d.vehicle import UNITS
from car2lego3d.web_access import bing_link, check_url, search


IDENTITY = {"brand": "Test", "model": "Coupe", "variant": "2024"}


def findings():
    values = {"length": 4400, "width": 1900, "height": 1200, "wheelbase": 2600}
    answer = {"status": "ready", "model": "Test Coupe", "variant": "2024", "width_reference": "body",
              "questions": [], "notes": [], "sources": [{"id": "maker", "title": "Constructed test page",
              "url": "https://manufacturer.example/spec", "kind": "manufacturer"}], "measurements": []}
    for field, unit in UNITS.items():
        answer["measurements"].append({"field": field, "value": values.get(field), "unit": unit,
            "source": "maker" if field in values else None, "status": "manufacturer" if field in values else "unknown",
            "evidence": f"{values[field]} mm" if field in values else "", "note": ""})
    audit = {"mode": "live", "web_search_calls": 1, "calls": [], "opened_pages": [{
        "url": "https://manufacturer.example/spec", "requested_url": "https://manufacturer.example/spec",
        "text": "Length 4400 mm Width 1900 mm Height 1200 mm Wheelbase 2600 mm", "text_sha256": "constructed"}]}
    return answer, audit


def test_compilation_uses_live_evidence_and_preserves_unknowns():
    answer, audit = findings()
    result = compile_result(answer, IDENTITY, audit)
    assert result["status"] == "ready"
    assert result["spec"]["measurements"]["door_length"]["value"] is None
    assert result["plans"]["1:24"]["generation"]["draft_recipe_available"]
    assert result["plans"]["1:16"]["generation"]["draft_recipe_available"]
    assert result["plans"]["1:16"]["soft_constraints"]
    assert "text" not in result["audit"]["opened_pages"][0]
    assert result["delivery_accepted"] is False
    answer["sources"][0]["kind"] = "reference"
    for item in answer["measurements"]:
        if item["value"] is not None:
            item["status"] = "reported"
    assert compile_result(answer, IDENTITY, audit)["status"] == "ready"


@pytest.mark.parametrize("failure", ["no_search", "unread", "quote", "unit", "duplicate", "missing_field", "private", "status"])
def test_bad_evidence_cannot_become_ready(failure):
    answer, audit = findings()
    if failure == "no_search": audit["web_search_calls"] = 0
    if failure == "unread": audit["opened_pages"] = []
    if failure == "quote": answer["measurements"][0]["evidence"] = "9999 mm"
    if failure == "unit": answer["measurements"][0]["unit"] = "inch"
    if failure == "duplicate": answer["measurements"].append(deepcopy(answer["measurements"][0]))
    if failure == "missing_field": answer["measurements"].pop()
    if failure == "private": answer["sources"][0]["url"] = "http://127.0.0.1/private"
    if failure == "status": answer["measurements"][0]["status"] = "unknown"
    with pytest.raises(ValueError):
        compile_result(answer, IDENTITY, audit)


def test_missing_core_mirror_width_and_ambiguous_variants_block_specs():
    answer, audit = findings()
    answer["width_reference"] = "including_mirrors"
    assert compile_result(answer, IDENTITY, audit)["status"] == "not_found"
    answer["width_reference"] = "body"
    answer["measurements"][0].update(value=None, status="unknown", source=None, evidence="")
    assert compile_result(answer, IDENTITY, audit)["status"] == "not_found"
    answer["status"] = "needs_clarification"
    answer["questions"] = ["请选择年款"]
    result = compile_result(answer, IDENTITY, audit)
    assert result["status"] == "needs_clarification" and result["plans"] == {}


def test_implausible_measurements_fail_closed():
    answer, audit = findings()
    answer["measurements"][0]["value"] = 4.4
    answer["measurements"][0]["evidence"] = "4.4 mm"
    audit["opened_pages"][0]["text"] += " 4.4 mm"
    assert compile_result(answer, IDENTITY, audit)["status"] == "not_found"


def test_real_command_event_shape_is_audited(tmp_path):
    path = tmp_path / "events.jsonl"
    events = [{"type": "vehicle_web_search", "success": True, "query": "test"},
              {"type": "vehicle_web_open", "success": True, "url": "https://example.com", "text": "dimensions"}]
    path.write_text("\n".join(json.dumps({"type": "item.completed", "item": {
        "type": "command_execution", "command": "python -m car2lego3d.web_access", "exit_code": 0,
        "aggregated_output": json.dumps(item)}}) for item in events), encoding="utf-8")
    audit = search_audit(path)
    assert audit["web_search_calls"] == 1 and len(audit["opened_pages"]) == 1


def test_exact_identity_cache_refresh_and_binding(ready_research):
    store, research_id = ready_research()
    assert store.start({"brand": "ferrari", "model": " f40 ", "variant": ""})["cached"]
    with pytest.raises(ValueError):
        store.specification(research_id, {"brand": "Ferrari", "model": "F40", "variant": "1990"})
    with pytest.raises(FileNotFoundError):
        store.get("../escape")
    with patch("car2lego3d.research.research_vehicle", side_effect=RuntimeError("network unavailable")):
        refreshed = store.start({"brand": "Ferrari", "model": "F40", "variant": ""}, refresh=True)
        store.close()
    assert refreshed["id"] != research_id
    assert store.get(refreshed["id"])["status"] == "failed"
    with pytest.raises(ValueError):
        store.specification(refreshed["id"], {"brand": "Ferrari", "model": "F40", "variant": ""})


def test_one_live_search_at_a_time_and_restart_marks_interrupted(tmp_path):
    started, release = Event(), Event()
    store = ResearchStore(tmp_path)

    def work(*args):
        started.set()
        release.wait(5)
        return {"status": "not_found", "request": IDENTITY, "error": None}

    try:
        with patch("car2lego3d.research.research_vehicle", side_effect=work):
            initial = store.start(IDENTITY)
            assert started.wait(2)
            assert store.start(IDENTITY)["id"] == initial["id"]
            with pytest.raises(RuntimeError):
                store.start({**IDENTITY, "variant": "2025"})
            release.set()
            store.close()
        old_id = "b" * 32
        write_json(tmp_path / old_id / "result.json", {"status": "running", "request": IDENTITY})
        assert store.get(old_id)["status"] == "interrupted"
    finally:
        release.set()
        store.close()


def test_web_tools_parse_search_results_without_fetching_in_tests():
    page = BeautifulSoup('<li class="b_algo"><h2><a href="https://example.com/spec">Coupe specs</a></h2><p>4400 mm</p></li>', "html.parser")
    with patch("car2lego3d.web_access.fetch_html", return_value=("https://www.bing.com/search", page)):
        result = search("Coupe dimensions")
    assert result["success"] and result["results"][0]["url"] == "https://example.com/spec"
    assert bing_link("https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9zcGVj") == "https://example.com/spec"
    with pytest.raises(ValueError):
        check_url("http://127.0.0.1/private")
    with pytest.raises(ValueError):
        check_url("file:///tmp/data")
    with pytest.raises(ValueError):
        check_url("http://example.com:8080/spec")


def test_identity_keys_include_variant():
    assert identity_key(IDENTITY) != identity_key({**IDENTITY, "variant": "2025"})
