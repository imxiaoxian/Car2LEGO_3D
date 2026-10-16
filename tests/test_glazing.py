"""Pane fitting must keep clear openings and avoid transparent brick filler."""
from copy import deepcopy

import pytest

from car2lego3d.carfit import Car
from car2lego3d.details import retention_report
from car2lego3d.glazing import PANELS, place_sides, side_layout


def fit_fixture(height=4):
    features = [dict(id=id, kind=kind, confidence=.95, description=id,
                     polygon=[[lo,.05],[hi,.05],[hi,.25],[lo,.25]])
                for id,kind,lo,hi in (("front_window","side_window",.35,.51),
                    ("rear_window","side_window",.54,.79),("b_pillar","pillar",.51,.54))]
    right = [dict(f, polygon=[[1-x,y] for x,y in f["polygon"]]) for f in features]
    return dict(length_studs=27,width_studs=10,body_color=25,glass_top_plates=19,roof_top_plates=21,
                appearance_envelope=dict(paint_plates=[19-height]*27),
                detail_program=dict(views=[dict(view="left",features=features),dict(view="right",features=right)]))


@pytest.mark.parametrize("height,expected", [(2.8,3),(4,3),(5.4,6),(7,6)])
def test_height_uses_window_belt_at_different_scales(height,expected):
    layout = side_layout(fit_fixture(height),12,21)
    assert layout["pane_height_plates"] == expected
    assert layout["height_error_plates"] == pytest.approx(abs(expected-height))


@pytest.mark.parametrize("span", [4,5,6,7,8,9,10,11])
def test_pane_partition_has_no_overlaps_gaps_or_transparent_brick_fallback(span):
    layout = side_layout(fit_fixture(),10,10+span)
    columns = list(layout["opaque_columns"])
    for pane in layout["panels"]:
        width,height = PANELS[pane["part"]]
        assert height == layout["pane_height_plates"]
        columns.extend(range(pane["first"],pane["first"]+width))
    assert sorted(columns) == list(range(11,11+span))
    assert not layout["transparent_brick_fallback"]
    assert 11 <= layout["pillar"] <= 10+span


def test_orientation_uses_labelled_panes_and_handles_front_first_vision_output():
    fit = fit_fixture()
    mirrored = side_layout(fit,12,21)
    fit["detail_program"]["views"][1]["features"] = deepcopy(fit["detail_program"]["views"][0]["features"])
    same_order = side_layout(fit,12,21)
    assert same_order["observed_pillar_studs"] == pytest.approx(mirrored["observed_pillar_studs"])
    assert same_order["panels"] == mirrored["panels"]


def test_short_panes_leave_cabin_hollow_and_attach_to_an_opaque_sill():
    fit = fit_fixture()
    fit.update(roof_start_stud=12,roof_end_stud=21,cabin_width_studs=6)
    layout = side_layout(fit,12,21)
    car = Car(fit)
    place_sides(car,layout,12,21,[2,7])
    assert all(p.part in PANELS for p in car.parts)
    assert not any(color==40 for color in car.cells.values())
    assert not any(2<y<7 for x,y,h in car.cells)
    for pane in layout["panels"]:
        for x in range(pane["first"],pane["first"]+pane["width"]):
            assert car.cells[x,2,15] == 0
            assert not any((x,2,h) in car.cells for h in range(16,19))
    assert layout["roof_support"].startswith("opaque pillars")


def test_long_pane_is_counted_only_where_final_native_glass_exists():
    fit = fit_fixture()
    fit.update(roof_start_stud=12,roof_end_stud=21,cabin_width_studs=6,
               detail_glazing_cells=dict(left=[dict(id="front_window",x=14,y=2,h=17,color=40)]))
    car = Car(fit)
    assert retention_report(car)["represented_count"] == 0
    car.place("30413",40,14.5,2,19,"side window glass",16)
    report = retention_report(car)
    assert next(f for f in report["features"] if f["view"]=="left" and f["id"]=="front_window")["part_indices"] == [0]
    assert next(f for f in report["features"] if f["view"]=="right" and f["id"]=="front_window")["part_indices"] == []
