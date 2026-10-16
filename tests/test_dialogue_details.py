"""Constructed dialogue and feature fixtures, with no paid model calls."""
import json

import httpx
import numpy as np
import pytest

from car2lego3d import dialogue
from car2lego3d.carfit import Car, split_steps
from car2lego3d.desktop import physical
from car2lego3d.details import assign_cells, cell_box, finish_structure, retention_report
from car2lego3d.research import compile_dialogue
from car2lego3d.vehicle import UNITS


def answer():
    values = dict(length=4700, width=1900, height=1400, wheelbase=2800)
    return dict(status="ready", model="BMW M4", variant="F82 test configuration", width_reference="body",
        questions=[], notes=[], measurements=[dict(field=f, value=values.get(f), unit=u,
        basis="recalled" if f in values else "unknown", confidence=.8 if f in values else 0, note="fixture")
        for f, u in UNITS.items()])


def feature(id, kind, x0, y0, x1, y1):
    return dict(id=id, kind=kind, description=id, confidence=.99,
                polygon=[[x0,y0],[x1,y0],[x1,y1],[x0,y1]])


def test_direct_dialogue_has_no_search_tools_and_persists_no_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(dialogue, "settings", lambda: ("secret-test-key", "https://gateway.example/v1", "test"))
    def post(url, **kwargs):
        assert url.endswith("/chat/completions")
        assert "tools" not in kwargs["json"] and "response_format" not in kwargs["json"]
        return httpx.Response(200, json={"model":"test", "choices":[{"finish_reason":"stop",
            "message":{"content":json.dumps({"value":12})}}]})
    monkeypatch.setattr(dialogue.httpx, "post", post)
    value, audit = dialogue.request_json("prompt", {"type":"object"}, tmp_path)
    assert value == {"value":12} and audit["web_search_calls"] == 0
    assert "secret-test-key" not in "".join(p.read_text() for p in tmp_path.glob("*.json"))


def test_invalid_dialogue_keeps_response_evidence_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(dialogue, "settings", lambda: ("secret-test-key", "https://gateway.example/v1", "test"))
    monkeypatch.setattr(dialogue.httpx, "post", lambda *args, **kwargs: httpx.Response(200, json={
        "id": "invalid-fixture", "model": "test", "choices": [{"finish_reason": "stop",
        "message": {"content": '{"value": 12,'}}]}))
    with pytest.raises(ValueError, match="JSON"):
        dialogue.request_json("prompt", {"type": "object"}, tmp_path)
    evidence = json.loads((tmp_path / "invalid-response.json").read_text())
    assert evidence["content"] == '{"value": 12,'
    assert evidence["response_id"] == "invalid-fixture"
    assert "secret-test-key" not in "".join(p.read_text() for p in tmp_path.glob("*.json"))


@pytest.mark.parametrize("text", ['{"value":', '{"value":12,', '{"value":"unfinished',
                                 '{"value":[1,2}', '{"value":tru', 'prefix {"value":{}}'])
def test_json_normalization_rejects_missing_values_and_malformed_content(text):
    with pytest.raises(ValueError):
        dialogue.decode_json_response(text)


def test_json_normalization_only_appends_missing_outer_containers():
    answer, normalization = dialogue.decode_json_response('{"views":[{"features":[{"id":"lamp"}]}')
    assert answer == {"views": [{"features": [{"id": "lamp"}]}]}
    assert normalization == dict(kind="close_outer_containers", appended="]}")
    assert dialogue.decode_json_response('{"value":12}') == ({"value": 12}, None)


def test_saved_invalid_response_recovery_verifies_request_identity(tmp_path, monkeypatch):
    from car2lego3d.common import digest, write_json
    monkeypatch.setattr(dialogue, "settings", lambda: ("secret-test-key", "https://gateway.example/v1", "test"))
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(200, json={"id": "fixture", "model": "test", "choices": [
            {"finish_reason": "stop", "message": {"content": '{"value":12}'}}]})
    monkeypatch.setattr(dialogue.httpx, "post", post)
    dialogue.request_json("prompt", {"type": "object"}, tmp_path)
    write_json(tmp_path / "invalid-response.json", dict(model="test", response_id="saved-fixture",
        finish_reason="stop", request_sha256=digest(tmp_path / "request.json"), content='{"value":[{"id":1}'))
    answer, audit = dialogue.request_json("prompt", {"type": "object"}, tmp_path)
    assert answer == {"value": [{"id": 1}]}
    assert audit["recovered_saved_response"] and audit["new_api_calls"] == 0
    assert len(calls) == 1
    dialogue.request_json("changed prompt", {"type": "object"}, tmp_path)
    assert len(calls) == 2


def test_model_data_is_estimated_and_year_optional(tmp_path):
    result = compile_dialogue(answer(), dict(brand="BMW",model="M4",variant=""),
                             dict(model="test",mode="llm_dialogue"), tmp_path/"answer.json")
    assert result["status"] == "ready"
    mapped = physical(result["spec"], dict(brand="BMW",model="M4",year=None,scale="1:24"), audit=result["audit"])
    assert mapped["vehicle"]["year"] is None
    assert mapped["selected_variant"].startswith("F82")
    assert mapped["research"]["queries"] == [] and not mapped["overall_grounded"]
    assert mapped["measurements"]["length"]["source_status"] == "model_provided"
    assert mapped["measurements"]["length"]["confidence"] == .8


@pytest.mark.parametrize("field,value", [("length",47),("wheelbase",6000),("height",True),("height",float("nan"))])
def test_bad_dimensions_cannot_enter_planning(tmp_path, field, value):
    data = answer()
    next(m for m in data["measurements"] if m["field"] == field)["value"] = value
    try:
        result = compile_dialogue(data, {}, dict(model="test"), tmp_path/"answer.json")
    except Exception as error:
        assert "True" in str(error) or "boolean" in str(error)
    else:
        assert result["status"] != "ready" and not result["plans"]


def test_distinct_light_representative_and_real_export_order():
    fit = dict(length_studs=24,width_studs=8,body_color=4,deck_top_plates=9,roof_top_plates=18)
    views = [dict(view=v,vehicle_bbox=[0,0,1,1],features=[]) for v in ("front","back","left","right")]
    views[0]["features"] = [feature("left_light","headlamp",.05,.15,.3,.2),
                            feature("right_light","headlamp",.7,.15,.95,.2),
                            feature("grille","grille",.3,.25,.7,.7)]
    fit["detail_program"] = dict(views=views)
    car = Car(fit)
    car.fill(range(24),range(8),range(2,9),4)
    car.pack()
    finish_structure(car)
    split_steps(car,12)
    report = retention_report(car)
    for f in report["features"]:
        assert f["status"] == "represented"
        assert all(car.parts[i].module.endswith("/"+f["id"]) for i in f["part_indices"])
    assert not report["appearance_accepted"]
    assert {p.color for p in car.parts if p.module.startswith("detail/front/")} >= {0,47}
    for p in car.parts:
        matrix = np.array(p.rotation).reshape(3,3)
        np.testing.assert_allclose(matrix.T@matrix,np.eye(3))
        assert np.linalg.det(matrix) == pytest.approx(1)


def test_side_view_coordinates_mirror_and_empty_cell_set():
    fit = dict(length_studs=24,width_studs=8,roof_top_plates=18)
    assert cell_box(fit,"left",0,10)[0] < cell_box(fit,"left",23,10)[0]
    assert cell_box(fit,"right",0,10)[0] > cell_box(fit,"right",23,10)[0]
    assert assign_cells([feature("a","headlamp",0,0,1,1)],[])[0] == []
