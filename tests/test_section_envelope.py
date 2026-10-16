"""Surface evidence and actual exported feature coverage, without model calls."""
import numpy as np
import pytest

from car2lego3d.carfit import Car, HALF, split_steps
from car2lego3d.details import retention_report
from car2lego3d.envelope_suv import prepare_fascia
from car2lego3d.sectional import lateral_sections
from car2lego3d.appearance import rim_brightness


def test_cabin_uses_observed_pillar_and_native_glass_height():
    from car2lego3d.envelope_suv import cabin_layout
    def feature(id,kind,x):
        return dict(id=id,kind=kind,confidence=.9,
                    polygon=[[x-.01,.05],[x+.01,.05],[x+.01,.3],[x-.01,.3]])
    left=[feature("front","side_window",.44),feature("rear","side_window",.67),
          feature("a","pillar",.32),feature("b","pillar",.52),feature("c","pillar",.74)]
    right=[dict(f,polygon=[[1-x,y] for x,y in f["polygon"]]) for f in left]
    fit=dict(length_studs=27,glass_top_plates=19,appearance_envelope=dict(paint_plates=[15]*27),
             detail_program=dict(views=[dict(view="left",features=left),dict(view="right",features=right)]))
    actual=cabin_layout(fit,12,21)
    assert actual["pillar"]==17
    assert actual["pane_height_plates"]==3
    assert actual["pane_part"]=="4865b"
    assert len(actual["panels"]) == 2
    assert actual["observed_pillar_studs"][0] == pytest.approx(actual["observed_pillar_studs"][1])
    fit["appearance_envelope"]["paint_plates"]=[13]*27
    assert cabin_layout(fit,12,21)["pane_height_plates"]==6


def test_windscreen_requires_roof_interface_even_when_cowl_error_is_smaller():
    from car2lego3d.envelope_suv import select_windscreen
    shallow=select_windscreen(np.full(27,14.5),12,19,8)
    tall=select_windscreen(np.full(27,11.),12,19,8)
    assert shallow["part"]=="24248"
    assert shallow["origin_offset_studs"] + shallow["roof_anchor_offset_studs"] == 0
    assert shallow["rejected_candidates"][0]["part"]=="62576"
    assert tall["part"]=="24248" and tall["rise_plates"]==9
    narrow=select_windscreen(np.full(27,14.5),12,19,6,10)
    assert narrow["part"]=="87606" and narrow["width_studs"]==6
    assert narrow["origin_offset_studs"] == -.5
    assert narrow["origin_offset_studs"]+narrow["roof_anchor_offset_studs"] == 0
    assert narrow["cowl_error_plates"] < shallow["cowl_error_plates"]
    small=select_windscreen(np.full(24,11.),10,16,6,8)
    assert small["part"]=="92583" and small["width_studs"]==6


@pytest.mark.parametrize("width,ratio,expected",[(10,.6256,6),(10,.8,8),(12,.65,8),(8,.8,6),
                                               (10,None,8),(10,float("nan"),8)])
def test_upper_cabin_follows_mesh_width_without_resizing_native_glass(width,ratio,expected):
    from car2lego3d.envelope_suv import cabin_width
    assert cabin_width(dict(width_studs=width,source_upper_body_width_ratio=ratio)) == expected


@pytest.mark.parametrize("width,track,expected",[(8,7,9),(10,9,11),(10,8,10)])
def test_axle_reaches_both_outer_socket_ends(width,track,expected):
    from car2lego3d.envelope_suv import select_axle
    result=select_axle(width,dict(rim="55982",track_studs=track))
    assert result["length_studs"] == expected
    assert result["length_studs"]/2 >= track/2+1


def test_rim_sampling_ignores_background_and_colored_calipers():
    fit=dict(length_studs=27,roof_top_plates=21,axles_studs=[5.5,21.5],
             wheel_families=[dict(diameter_mm=30.4)]*2)
    dark=np.full((210,270,3),.13)
    dark[:100]=1
    dark[160:170,55:65]=[1,0,0]
    bright=dark.copy()
    bright[100:]=.65
    assert len(rim_brightness(dark,fit))==2
    assert np.median(rim_brightness(dark,fit)) < .28
    assert np.median(rim_brightness(bright,fit)) > .28


def test_fascia_excludes_tinted_dark_reflections_and_wrong_paint_hues():
    from car2lego3d.appearance import fascia_paint_fraction
    paint=np.array([254,138,24])/255
    assert fascia_paint_fraction(np.tile([.38,.08,.01],(20,1)),paint) == 0
    assert fascia_paint_fraction(np.tile([.9,.43,.05],(20,1)),paint) == 1
    assert fascia_paint_fraction(np.tile([.05,.3,.9],(20,1)),paint) == 0
    assert fascia_paint_fraction(np.tile([.9,.9,.9],(20,1)),np.ones(3)) == 1
    assert fascia_paint_fraction([],paint) == 0


def test_sections_follow_taper_and_are_symmetric():
    rng = np.random.default_rng(1)
    x = rng.uniform(0, 26, 18000)
    z = rng.uniform(0, 8, len(x))
    half = 1.5 + 2.5*np.sin(np.pi*x/26)
    points = np.column_stack([x, 5+rng.choice([-1,1],len(x))*half, z])
    result = lateral_sections(points, [26,10,8], 27,10,21)
    widths = np.array(result["half_width_studs"])
    reflected = points.copy()
    reflected[:,1] = 10-reflected[:,1]
    np.testing.assert_allclose(widths,lateral_sections(reflected,[26,10,8],27,10,21)["half_width_studs"])
    assert widths[13,10] > widths[0,10]+1.5
    assert np.isfinite(widths).all()
    assert np.max(result["sample_counts"]) >= 8


@pytest.mark.parametrize("points,extent", [([], [1,1,1]), ([[0,0,float("nan")]], [1,1,1]),
    ([[0,0,0]], [1,0,1]), ([[0,0,0]], [1,1,float("inf")])])
def test_sections_reject_missing_or_nonfinite_geometry(points,extent):
    with pytest.raises(ValueError):
        lateral_sections(points,extent,27,10,21)


def test_retention_requires_a_matching_final_native_part():
    feature = dict(id="lamp",kind="headlamp",description="thin light",confidence=.9)
    pane = dict(id="glass",kind="side_window",description="rear window",confidence=.9)
    fit = dict(length_studs=27,width_studs=10,detail_program=dict(views=[
        dict(view="front",features=[feature]),dict(view="left",features=[pane])]),
        detail_native_cells=[dict(view="front",id="lamp",x=0,y=2,h=12,color=15)],
        detail_glazing_cells=dict(left=[dict(id="glass",x=14,y=1,h=15,color=40)]))
    car = Car(fit)
    car.place("3024",25,0,2,13,"body and chassis",1)
    assert retention_report(car)["represented_count"] == 0
    car.place("3024",15,0,2,13,"body and chassis",3)
    car.place("4864b",40,14.5,1,19,"side window glass",2,HALF)
    split_steps(car,12)
    report = retention_report(car)
    assert report["represented_count"] == 2
    for row in report["features"]:
        assert len(row["part_indices"]) == 1
        assert car.parts[row["part_indices"][0]].color == (15 if row["id"] == "lamp" else 40)
    assert not report["appearance_accepted"]
    car.parts = [p for p in car.parts if p.part != "4864b"]
    assert retention_report(car)["unresolved_count"] == 1


def test_thin_split_lamps_wrap_tapered_nose_and_keep_dark_bumper():
    def light(id,lo,hi):
        return dict(id=id,kind="headlamp",confidence=.9,polygon=[[.02,lo],[.25,lo],[.25,hi],[.02,hi]])
    views = [dict(view="front",features=[light("upper",.35,.4),light("lower",.48,.53)]),
             dict(view="back",features=[])]
    fit = dict(length_studs=27,width_studs=10,body_color=25,roof_top_plates=21,
        detail_program=dict(views=views),mesh_sections=dict(half_width_studs=np.full((27,21),3.).tolist()),
        appearance_envelope=dict(paint_plates=[14]*27,fascia_colors=dict(front={"4,8":0})))
    car = Car(fit)
    prepare_fascia(car)
    assert car.cells[0,4,8] == 0
    lamps = [c for c in fit["detail_native_cells"] if c["color"] == 47]
    assert {c["id"] for c in lamps} == {"upper","lower"}
    for id in ("upper","lower"):
        ys = {c["y"] for c in lamps if c["id"] == id}
        assert len([c for c in lamps if c["id"] == id]) == len(ys)
    assert all(c["x"] == min(2,max(0,3-min(c["y"],9-c["y"]))) for c in lamps)
    assert all(c["h"] <= 12 for c in lamps)
    assert any(c["x"] > 0 for c in lamps)
    for y in {c["y"] for c in lamps}:
        levels=sorted({c["h"] for c in lamps if c["y"]==y})
        assert len(levels)==2 and levels[1]-levels[0]>=2
    assert all(p.part in {"3070b","3069b","2431","6636"} for p in car.parts if p.module=="lamp lenses")
    assert any(p.module=="lamp housing ties" for p in car.parts)
    assert not any(p.module=="flush lamp brows" for p in car.parts)


def test_front_lamp_quantization_restores_bilateral_symmetry():
    from car2lego3d.envelope_suv import expose_lamps
    features = [dict(id=id,kind="headlamp",confidence=.9,polygon=polygon) for id,polygon in (
        ("left",[[.1,.34],[.3,.34],[.3,.37],[.1,.37]]),
        ("right",[[.7,.37],[.9,.37],[.9,.4],[.7,.4]]))]
    fit = dict(length_studs=27,width_studs=10,roof_top_plates=21,detail_native_cells=[],
        detail_program=dict(views=[dict(view="front",features=features),dict(view="back",features=[])]))
    car = Car(fit)
    expose_lamps(car)
    lamps={(c["y"],c["h"]) for c in fit["detail_native_cells"]}
    assert lamps=={(9-y,h) for y,h in lamps}


def test_fascia_keeps_curved_surface_support_ceiling():
    fit = dict(length_studs=27,width_studs=10,body_color=25,roof_top_plates=21,
        detail_program=dict(views=[dict(view=name,features=[]) for name in ("front","back")]),
        mesh_sections=dict(half_width_studs=np.full((27,21),4.5).tolist()),
        appearance_envelope=dict(paint_plates=[14]*27),surface_support_ceilings={"0,4":8})
    car = Car(fit)
    prepare_fascia(car)
    assert car.cells[0,4,7] == 25
    assert not any(x==0 and y==4 and h>=8 for x,y,h in car.cells)


def test_overlapping_tail_polygons_share_a_native_lens_and_report_both():
    from car2lego3d.envelope_suv import expose_lamps
    features = [dict(id=id,kind="taillamp",confidence=.9,description="observed light",
                     polygon=[[.1,.27],[.9,.27],[.9,.3],[.1,.3]]) for id in ("strip","segment")]
    fit = dict(length_studs=27,width_studs=10,roof_top_plates=21,detail_native_cells=[],
        detail_program=dict(views=[dict(view="front",features=[]),dict(view="back",features=features)]))
    car = Car(fit)
    expose_lamps(car)
    assert sum(1 if p.part=="3070b" else 2 if p.part=="3069b" else 4 if p.part=="2431" else 6
               for p in car.parts) == 8
    assert len({(p.position,p.rotation) for p in car.parts}) == len(car.parts)
    assert retention_report(car)["represented_count"] == 2


def test_packing_joins_wheel_crown_to_the_supported_inner_liner(monkeypatch):
    monkeypatch.setattr("car2lego3d.reservation.reserve",lambda car: None)
    car = Car(dict(length_studs=5,width_studs=4,detail_surface_mode="envelope"))
    car.fill([0],[0],range(10,13),25)
    car.fill([0],[1],[9,10],25)
    car.pack()
    bridge = next(p for p in car.parts if p.part=="3023")
    assert bridge.position[1] == -88
    assert bridge.rotation != (1,0,0,0,1,0,0,0,1)
