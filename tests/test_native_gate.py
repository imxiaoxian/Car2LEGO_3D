"""The final assembly audit must block incomplete or stale geometry evidence."""
from types import SimpleNamespace

import pytest

from car2lego3d.common import digest, write_json
from car2lego3d.reservation import audit_assembly


@pytest.mark.parametrize("fault", [None,"stale","missing_colliders","collision","disconnected"])
def test_export_gate_requires_matching_complete_native_audit(tmp_path,monkeypatch,fault):
    assembly=tmp_path / "assembly.json"
    assembly.write_text('{"parts": []}',encoding="utf-8")
    report=dict(assembly_sha256=digest(assembly),collision_count=0,collision_coverage="complete",
                connectivity=dict(status="passed"))
    if fault=="stale":
        report["assembly_sha256"]="old"
    elif fault=="missing_colliders":
        report["collision_coverage"]="partial"
    elif fault=="collision":
        report["collision_count"]=1
    elif fault=="disconnected":
        report["connectivity"]["status"]="failed"
    monkeypatch.setattr("car2lego3d.reservation.configuration",lambda: (tmp_path,"python"))
    def run(*args,**kwargs):
        write_json(tmp_path / "native-audit.json",report)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr("car2lego3d.reservation.subprocess.run",run)
    if fault:
        with pytest.raises(RuntimeError):
            audit_assembly(tmp_path)
    else:
        assert audit_assembly(tmp_path)["connectivity"]["status"]=="passed"
