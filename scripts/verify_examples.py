"""Check published artifacts against their measured stage records, without model downloads."""
import csv
import json
from collections import Counter
from car2lego3d.common import ROOT, digest
from car2lego3d.requirements import assess_requirements, plan_design, requirements_for_scale, validate_plan
from car2lego3d.studio import verify_archive
from car2lego3d.vehicle import load_spec
from car2lego3d.requirements import POLICY_VERSION


def check_policy_snapshot(snapshot, spec):
    """Keep older published evidence immutable while validating current snapshots."""
    if all(policy.get("version") == POLICY_VERSION for policy in snapshot["policies"].values()):
        assert snapshot["policies"] == {scale: requirements_for_scale(scale) for scale in snapshot["policies"]}
        assert snapshot["plans"] == {scale: plan_design(spec, scale) for scale in snapshot["plans"]}
        return
    for scale, plan in snapshot["plans"].items():
        assert plan["policy"] == snapshot["policies"][scale]
        assert plan["requested_scale"] == scale
        assert plan["generation"]["delivery_ready"] is False
    print("Historical policy snapshots retained; current-policy equivalence is not asserted.")


def verify():
    base = ROOT / "examples"
    count = 0
    policies = base / "scale-requirements/requirements.json"
    if policies.exists():
        snapshot = json.loads(policies.read_text(encoding="utf-8"))
        spec = load_spec(ROOT / "data/vehicles/ferrari_f40_1987.json")
        check_policy_snapshot(snapshot, spec)
    for name in ("image.json", "reconstruction.json", "lego.json", "studio.json", "measurement.json"):
        for record_path in base.rglob(name):
            record = json.loads(record_path.read_text(encoding="utf-8"))
            assert record["status"] == "completed", record_path
            for filename, sha256 in record.get("outputs", {}).items():
                assert digest(record_path.parent / filename) == sha256, filename
                count += 1
            if name == "image.json":
                assert digest(record_path.parent / record["output"]) == record["sha256"]
                count += 1
            if name == "studio.json":
                source = record_path.parent.parent / "03-lego" / record["inputs"]["ldraw"]
                assert verify_archive(record_path.parent / "model.io", source)["part_count"] == record["part_count"]
    for path in base.rglob("model.ldr"):
        folder = path.parent
        parts = [line.split() for line in path.read_text().splitlines() if line.startswith("1 ")]
        report = json.loads((folder / "validation.json").read_text(encoding="utf-8"))
        assembly = json.loads((folder / "assembly.json").read_text(encoding="utf-8"))
        with (folder / "inventory.csv").open(newline="") as stream:
            total = sum(int(row["quantity"]) for row in csv.DictReader(stream))
        assert len(parts) == total == report["part_count"] == len(assembly.get("parts", assembly.get("plates", [])))
        assert all(len(p) == 15 and p[-1].endswith(".dat") for p in parts)
        assert report["physical_verified"] is False
        if report.get("strategy") == "car":
            assert report["body_cells_exact"] and report["appearance_accepted"] is False
            if (folder / "requirements.json").exists():
                plan = json.loads((folder / "requirements.json").read_text(encoding="utf-8"))
                fit = json.loads((folder / "fit.json").read_text(encoding="utf-8"))
                spec = load_spec(folder.parent / "00-vehicle/spec.json")
                current_policy = plan["policy"]["version"] == POLICY_VERSION
                if current_policy:
                    validate_plan(plan, spec, fit["length_studs"], fit["width_studs"], plan["requested_scale"])
                else:
                    assert plan["grid"]["length_studs"] == fit["length_studs"]
                    assert plan["grid"]["width_studs"] == fit["width_studs"]
                assert fit["requirements_plan"] == plan
                counts = list(Counter(part["step"] for part in assembly["parts"]).values())
                dimensions = json.loads((folder / "dimensions.json").read_text(encoding="utf-8"))
                assessment = json.loads((folder / "requirements-report.json").read_text(encoding="utf-8"))
                if current_policy:
                    assert assessment == assess_requirements(plan, dimensions, counts)
                else:
                    assert assessment["policy_version"] == plan["policy"]["version"]
                assert max(counts) <= plan["policy"]["max_parts_per_step"]
                assert not report["delivery_accepted"] and not assessment["delivery_accepted"]
        else:
            assert report["coverage_exact"]
        native = folder / "native-render.json"
        if native.exists():
            record = json.loads(native.read_text(encoding="utf-8"))
            if record.get("status", "completed") == "completed":
                if "model_sha256" in record:
                    assert digest(path) == record["model_sha256"]
                else:
                    assert (folder / "native-ldraw.json").is_file()
                if record.get("preview"):
                    assert digest(folder / record["preview"]) == record["image_sha256"]
        native = folder / "native-ldraw.json"
        if native.exists():
            record = json.loads(native.read_text(encoding="utf-8"))
            assert digest(path) == record["model_sha256"]
            assert digest(native.with_suffix(".png")) == record["image_sha256"]
            assert not record["missing_parts"] and not record["appearance_accepted"]
            count += 2
    for path in base.rglob("pipeline.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["status"] == "completed"
        folder = path.parent
        reconstruction = json.loads((folder / "02-mesh/reconstruction.json").read_text(encoding="utf-8"))
        lego = json.loads((folder / "03-lego/lego.json").read_text(encoding="utf-8"))
        if record["inputs"]["mode"] == "prompt":
            source = folder / "01-image" / reconstruction["inputs"]["image"]
            assert digest(source) == reconstruction["inputs"]["sha256"]
            count += 1
        mesh = folder / "02-mesh" / lego["inputs"]["mesh"]
        assert digest(mesh) == lego["inputs"]["sha256"]
        count += 1
        if "vehicle_spec" in record:
            snapshot = folder / "00-vehicle" / record["vehicle_spec"]["file"]
            load_spec(snapshot)
            assert digest(snapshot) == record["vehicle_spec"]["sha256"]
            for stage in (folder / "01-image/image.json", folder / "02-mesh/reconstruction.json",
                          folder / "03-lego/lego.json"):
                if stage.exists():
                    assert json.loads(stage.read_text(encoding="utf-8"))["vehicle_spec"]["sha256"] == digest(snapshot)
            count += 1
        if "studio_file" in record:
            archive = folder / record["studio_file"]
            studio = json.loads((archive.parent / "studio.json").read_text(encoding="utf-8"))
            source = folder / "03-lego" / studio["inputs"]["ldraw"]
            assert digest(source) == studio["inputs"]["sha256"]
            assert digest(archive) == studio["outputs"][archive.name]
            count += 2
        if "requirements" in record:
            snapshot = folder / record["requirements"]["file"]
            assert digest(snapshot) == record["requirements"]["sha256"]
            assert digest(snapshot) == digest(folder / "03-lego/requirements.json")
            count += 2
    for path in base.rglob("controlled-comparison.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        before, after = (path.parent / name for name in ("comparison-before", "comparison-after"))
        assert digest(before / "02-mesh/calibrated.glb") == record["source_mesh_sha256"]
        assert digest(after / "02-mesh/calibrated.glb") == record["source_mesh_sha256"]
        assert digest(before / "03-lego/model.ldr") == record["old_ldraw_sha256"]
        assert digest(after / "03-lego/model.ldr") == record["new_ldraw_sha256"]
        assert digest(path.parent / "comparison.png") == record["comparison_image_sha256"]
        count += 5
    print(f"Verified {count} recorded file hashes and all LDraw/BOM part totals.")


if __name__ == "__main__":
    verify()
