"""Panel size preference must respect silhouette and occupied cabin space."""
import numpy as np
import pytest

from car2lego3d.carfit import Car
from car2lego3d.interior import furnish_cabin
from car2lego3d.surfaces import curved_modules, fit_strip, place_strip, profiles_for_width


@pytest.mark.parametrize("width,length,maximum", [(1,12,2),(2,12,3),(1,9,3),(2,8,2)])
def test_flat_surfaces_choose_long_native_panels(width,length,maximum):
    result=fit_strip(np.full(length,14.1),width)
    assert len(result["pieces"]) <= maximum
    if length%2==0:
        assert not any(part=="3070b" for part,_,_,_ in result["pieces"])


@pytest.mark.parametrize("width",[1,2])
def test_panel_preference_cannot_flatten_identifying_silhouette(width):
    target=np.array([11.1,12.3,13.2,14.,14.1,14.,14.1,15.2,16.,16.1])
    plan=fit_strip(target,width)
    profiles={p: np.array(v) for p,v,_ in profiles_for_width(width)}
    actual=[]
    for part,index,origin,reverse in plan["pieces"]:
        assert index==len(actual)
        values=profiles[part][::-1] if reverse else profiles[part]
        actual.extend(values+origin)
    assert len(actual)==len(target)
    assert np.max(np.abs(actual-target)) <= plan["max_error_plates"]
    assert actual[-1]-actual[0] > 4


def test_one_wide_curve_is_not_replaced_by_four_small_tiles():
    car=Car(dict(length_studs=8,width_studs=8))
    target=np.array([-1.9244237,-.9758047,-.3718063,-.0768424])+14
    place_strip(car,range(2,6),[0],target,25,"shoulder",14)
    assert [p.part for p in car.parts]==["61678"]


def test_panel_rotations_keep_flat_tiles_within_the_requested_footprint():
    car=Car(dict(length_studs=10,width_studs=8))
    place_strip(car,range(2,6),[2,3],np.full(4,14.),25,"bonnet",14)
    assert [p.part for p in car.parts]==["87079"]
    assert car.parts[0].rotation==(1,0,0,0,1,0,0,0,1)
    assert len(car.fit["surface_support_ceilings"])==8


def test_constant_curve_modules_cover_the_span_without_scaling():
    modules=list(curved_modules(13,21))
    assert [p for p,_,_ in modules]==["88930","88930","11477"]
    assert [x for _,start,span in modules for x in range(start,start+span)]==list(range(13,22))


def test_roof_skin_does_not_fill_the_reserved_cabin():
    car=Car(dict(length_studs=27,width_studs=10))
    place_strip(car,range(13,22),[4,5],np.full(9,20.),0,"roof",18,support_from=19)
    assert not any(h<19 for x,y,h in car.cells)


def test_panel_fit_respects_the_lamp_mounting_plane():
    target=[12.925,13.457,13.796,13.994,14.001]
    result=fit_strip(target,2,min_support=[12,-np.inf,-np.inf,-np.inf,-np.inf])
    part,index,origin,reverse=result["pieces"][0]
    support={p:s for p,_,s in profiles_for_width(2)}[part]
    assert origin+(support[-1] if reverse else support[0]) >= 12
    with pytest.raises(ValueError,match="No native panel"):
        fit_strip([10,10],min_support=[12,12])


@pytest.mark.parametrize("sides",[(2,7),(1,8)])
def test_two_row_cabin_has_supported_seats_and_roof_clearance(sides):
    car=Car(dict(length_studs=27,width_studs=10))
    furnish_cabin(car,12,21,sides,19)
    seats=[p for p in car.parts if p.part=="4079"]
    assert len(seats)==4
    assert {p.module for p in seats}=={"front seats","rear seats"}
    assert car.fit["interior_fitting"]["seat_top_plates"] < 18
    assert any(p.module=="steering wheel" for p in car.parts)
    assert any(p.module=="dashboard" for p in car.parts)
    for p in seats:
        x=p.position[0]/20+13
        y=p.position[2]/20+4.5
        h=-p.position[1]/8
        for sx in (int(x-.5),int(x+.5)):
            for sy in (int(y-.5),int(y+.5)):
                assert (sx,sy,int(h)-2) in car.cells
        assert sides[0]+.5 <= y-1 and y+1 <= sides[1]-.5


def test_short_cabin_uses_one_row_instead_of_overlapping_seats():
    car=Car(dict(length_studs=24,width_studs=8))
    furnish_cabin(car,10,16,(1,6),17)
    assert car.fit["interior_fitting"]["row_count"]==1
    assert len([p for p in car.parts if p.part=="4079"])==2
