from types import SimpleNamespace

import pytest

from car2lego3d.carfit import Placement
from car2lego3d.sequence import resequence


def candidate(edges):
    parts = [Placement(name,0,(0,0,0),(1,0,0,0,1,0,0,0,1),module,step)
             for name,module,step in [("roof","roof",1),("base","body and chassis",3),
                 ("pillar","body and chassis",4),("window","glass",2)]]
    audit = dict(part_count=4,per_part_bounds_ldu=[[[0,-24,0],[20,-16,20]],
        [[0,-8,0],[40,0,20]],[[0,-16,0],[20,-8,20]],[[0,-24,0],[20,-16,20]]],
        connections=[dict(a=f"p{a}",b=f"p{b}") for a,b in edges])
    return SimpleNamespace(parts=parts),audit


def test_unsupported_roof_is_reordered_after_connected_supports():
    car,audit = candidate([(1,2),(2,3),(3,0)])
    before = {p.part:(p.position,p.rotation,p.color) for p in car.parts}
    report = resequence(car,audit,1)
    assert [p.part for p in car.parts] == ["base","pillar","window","roof"]
    assert [p.step for p in car.parts] == [1,2,3,4]
    assert {p.part:(p.position,p.rotation,p.color) for p in car.parts} == before
    assert all(row["parent_index"] < row["part_index"] for row in report["attachments"][1:])
    assert report["connected_prefixes"] and not report["insertion_paths_verified"]


def test_disconnected_build_sequence_is_rejected():
    car,audit = candidate([(1,2),(3,0)])
    with pytest.raises(ValueError,match="Disconnected"):
        resequence(car,audit,12)
