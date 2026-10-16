import pytest

from car2lego3d.common import write_json
from car2lego3d.research import ResearchStore, DIALOGUE_VERSION
from car2lego3d.vehicle import UNITS


@pytest.fixture
def ready_research(tmp_path):
    """Constructed specifications for contract tests; never a live research claim."""
    stores = []

    def create(brand="Ferrari", model="F40", variant=""):
        directory = tmp_path / f"research-{len(stores)}"
        store = ResearchStore(directory)
        stores.append(store)
        identity = {"brand": brand, "model": model, "variant": variant}
        values = {"length": 4430, "width": 1980, "height": 1130, "wheelbase": 2450}
        spec = {"schema_version": 1, "model": f"{brand} {model}", "variant": variant or "test variant",
                "sources": {"test": {"title": "Constructed specification", "url": "https://manufacturer.example/spec"}},
                "measurements": {field: {"value": values.get(field), "unit": unit,
                                        "status": "reported" if field in values else "unknown",
                                        "source": "test" if field in values else None} for field, unit in UNITS.items()}}
        research_id = "a" * 32
        write_json(directory / research_id / "research/spec.json", spec)
        write_json(directory / research_id / "result.json", {"status": "ready", "request": identity,
                   "spec": spec, "audit": {"version": DIALOGUE_VERSION}})
        return store, research_id

    yield create
    for store in stores:
        store.close()
