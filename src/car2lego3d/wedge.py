"""Compact mid-engine wedge architecture fitted to a reconstructed vehicle.

Door panels, window sills and glazing form a continuous cabin envelope.
Observed cabin landmarks are fitted independently of engine covers and wings.
"""
import math
import re

import numpy as np

from .vehicle import constraint_targets


LENGTH_RANGE = (22, 32)


def junction_report(car, audit):
    """Require direct native attachments at every intended cabin interface."""
    if audit["part_count"] != len(car.parts):
        raise ValueError("Junction audit does not match the assembly part count")
    rules = (
        ("windscreen to cowl", "door glazing and front windscreen", "windscreen half-stud anchors"),
        ("roof to windscreen", "proportioned cabin roof", "door glazing and front windscreen"),
        ("roof to rear pillars", "proportioned cabin roof", "shared cabin rear pillars"),
        ("door panels to mounts", "continuous door skins", "integrated door panel mounts"),
        ("rear ramp to anchors", "cabin to engine deck ramp", "rear deck half-stud anchors"),
        ("rear shoulders to anchors", "cabin to rear shoulder buttresses", "rear buttress half-stud anchors"),
    )
    checks = []
    for name, surface, support in rules:
        surfaces = {f"p{i}" for i, p in enumerate(car.parts) if p.module == surface}
        supports = {f"p{i}" for i, p in enumerate(car.parts) if p.module == support}
        edges = [e for e in audit["connections"]
                 if ((e["a"] in surfaces and e["b"] in supports)
                     or (e["b"] in surfaces and e["a"] in supports))]
        reached = {p for e in edges for p in (e["a"], e["b"])}
        missing = sorted((surfaces | supports)-reached)
        checks.append(dict(junction=name, surface_parts=sorted(surfaces), support_parts=sorted(supports),
            connections=edges, missing_attachments=missing,
            status="passed" if surfaces and supports and not missing else "failed"))
    return dict(status="passed" if all(c["status"] == "passed" for c in checks) else "failed",
        method="Direct native connector matches for every surface and intended support",
        checks=checks, appearance_accepted=False, studio_gui_verified=False, physical_verified=False,
        limitations=["Direct connector matches do not measure clutch strength or insertion access.",
                     "Connected modules still require visual review for vehicle likeness."])


def supports(spec):
    identity = " ".join(str((spec or {}).get(k, "")) for k in ("model", "variant"))
    return bool(re.search(r"\bcountach\b", identity, re.I))


def fit_wedge(fit, spec):
    from .coupe import WHEELS
    length, width = fit["length_studs"], fit["width_studs"]
    targets = constraint_targets(spec, length, fit.get("target_scale_denominator"))["target_model_mm"]
    wheels = []
    for axle in ("front", "rear"):
        diameter = targets.get(f"{axle}_tyre_diameter", 25)
        tyre_width = targets.get(f"{axle}_tyre_width", 12)
        wheel = dict(min(WHEELS[:2], key=lambda w: 4*(w["diameter_mm"]/diameter-1)**2
                         + .25*(w["width_mm"]/tyre_width-1)**2))
        wheel.update(target_diameter_mm=diameter, target_width_mm=tyre_width,
                     center_height_plates=3.75, arch="62361", arch_span_studs=6,
                     track_studs=float(width-1))
        wheels.append(wheel)
    ground = min(w["center_height_plates"]*3.2-w["diameter_mm"]/2 for w in wheels)
    roof = int(np.clip(round((targets["height"]+ground)/3.2), 14, 18))
    profile = np.asarray(fit["height_profile_studs"])
    observations = fit.get("layout_observations")
    if observations:
        targets_x = {k: v*(length-1) for k,v in observations["fractions"].items()}
        layout_source = "two observed side views; native canopy and roof bounds"
    else:
        first, last = round(length*.37), round(length*.63)
        peak = first+int(np.argmax(profile[first:last+1]))
        targets_x = dict(cowl_x=peak-5, roof_front_x=peak-1.5,
                         roof_rear_x=peak+2.5, cabin_rear_x=peak+1.5)
        layout_source = "bounded mesh cabin peak; observed landmarks unavailable"
    # Fit the canopy's actual slope and flat top, not its rear attachment stud.
    target_width = float(fit.get("source_upper_body_width_ratio") or .65)*width
    candidates = []
    for anchor in np.arange(10.5, length-8, 1):
        for part, roof_length, roof_width in (("87079", 2, 4), ("6180", 4, 6), ("10202", 6, 6)):
            for roof_center in np.arange(math.floor(anchor)-1, math.floor(anchor)+4):
                first, last = roof_center-roof_length/2, roof_center+roof_length/2
                if not (anchor-3 <= first <= anchor-1.5 and anchor+.5 <= last <= length-6):
                    continue
                error = (2*(anchor-5.5-targets_x["cowl_x"])**2
                         + (anchor-2-targets_x["roof_front_x"])**2
                         + (first-targets_x["roof_front_x"])**2
                         + (last-targets_x["roof_rear_x"])**2 + .1*(roof_width-target_width)**2)
                candidates.append((error, float(anchor), part, roof_length, roof_width, float(roof_center)))
    _, anchor, roof_part, roof_length, roof_width, roof_center = min(candidates)
    start, end = roof_center-roof_length/2, roof_center+roof_length/2
    door_last = int(np.clip(round(targets_x["cabin_rear_x"]), anchor+.5, end))
    front = int(round(anchor-5.5))
    fitted = dict(cowl_x=anchor-5.5, roof_front_x=start, roof_rear_x=end,
                  cabin_rear_x=float(door_last))
    errors = {k: fitted[k]-targets_x[k] for k in fitted}
    proportion_report = dict(source=layout_source, target_studs=targets_x, fitted_studs=fitted,
        errors_studs=errors, max_landmark_error_studs=max(abs(v) for v in errors.values()),
        roof_length_fraction=roof_length/length, roof_width_fraction=roof_width/width,
        appearance_accepted=False, physical_verified=False)
    fit.update(recipe="compact-wedge-v1", body_family="sports_car", family_builder="wedge",
        styling_prior="countach-5000-qv", native_geometry_required=True,
        detail_surface_mode="wedge", roof_start_stud=start, roof_end_stud=end,
        roof_top_plates=roof, deck_top_plates=roof-5, ground_y_mm=ground,
        wheel_families=wheels, windscreen_part="65633", cabin_width_studs=6,
        fitted_windscreen_angle_degrees=math.degrees(math.atan2(27.5,70)),
        door_window_integration="Continuous lower door belt and shared front/rear door uprights",
        layout=dict(front_glass_interval_studs=[anchor-5.5,anchor-2],
                    roof_interval_studs=[start,end],
                    roof_center_stud=roof_center, roof_part=roof_part, glass_anchor_stud=anchor,
                    door_interval_studs=[front+1,door_last],
                    proportion_report=proportion_report,
                    engine_position="mid-rear", rear_wing_default=True),
        design_prior="Native planar wedge panels, integrated door glazing and flared wheel modules; mesh roof and physical wheelbase",
        fixed_parameters=["native glazing and wheel geometry", "continuous door, window sill and uprights",
                          "Countach wedge, NACA side intakes and raised rear wing identity priors"],
        inferred_parameters=fit["inferred_parameters"]+[layout_source, "native wheel family"],
        limitations=fit["limitations"]+["Compact native glazing approximates the side-window outline; no printed parts or decals.",
                                         "Identity priors are not recovered CAD; small badges and seams remain below native resolution."])
    return fit


def design_wedge(fit, rear_wing=True):
    from .carfit import Car, IDENTITY, HALF, QUARTER, BACK_QUARTER
    from .envelope_suv import select_axle
    car = Car(fit)
    length, width, paint = (fit[k] for k in ("length_studs","width_studs","body_color"))
    center = (width-1)/2
    roof = fit["roof_top_plates"]
    glass = roof-1
    belt = glass-4
    layout = fit["layout"]
    anchor = layout["glass_anchor_stud"]
    front = int(round(layout["front_glass_interval_studs"][0]))
    door_first,door_last = layout["door_interval_studs"]
    end = int(round(fit["roof_end_stud"]))
    left = (width-6)//2
    sides = [left,left+5]
    inner = range(left+1,left+5)
    car.fill(range(length),range(2,width-2),range(0,3),0)
    car.fill(range(length),range(1,width-1),[2],0)
    car.fill(range(door_first,door_last+1),range(1,width-1),[1],0)
    car.fill(range(length),range(2,width-2),range(3,belt),paint)
    car.clear(range(front+2,door_last+1),inner,range(3,roof))
    car.fill(range(front+2,door_last+1),inner,[2],0)

    for axle,wheel in zip(fit["axles_studs"],fit["wheel_families"]):
        x0=int(axle-.5)
        for side in (-1,1):
            mount=center+side*(center-2)
            arch_y=center+side*center
            car.place("62361",paint,axle,arch_y,9,"angular wheel arches",12,
                      BACK_QUARTER if side==-1 else QUARTER)
            wheel_y=center+side*wheel["track_studs"]/2
            car.place(wheel["tyre"],0,axle,wheel_y,3.75,"tyres",18,IDENTITY if side==1 else HALF)
            rim_facing=(IDENTITY if side==-1 else HALF) if wheel["rim"]=="18976" else (IDENTITY if side==1 else HALF)
            rim_y=wheel_y-side*.3 if wheel["rim"]=="18976" else wheel_y
            car.place(wheel["rim"],72,axle,rim_y,3.75,"wheel rims",18,rim_facing)
            car.place("3700",0,axle,mount,5,"axle mounting",6)
        car.clear([x0,x0+1],range(1,width-1),range(2,5))
        car.fill([x0,x0+1],range(2,width-2),[1],0)
        shaft=select_axle(width,wheel) if wheel["rim"]!="18976" else dict(part="3707",color=0)
        car.place(shaft["part"],shaft["color"],axle,center,3.75,"axles",7,QUARTER)
        radius=wheel["diameter_mm"]/2+1
        for x in range(max(0,x0-2),min(length,x0+4)):
            dx=max(0,abs(x-axle)*8-4)
            if dx<radius:
                top=int(np.ceil(3.75+np.sqrt(radius**2-dx**2)/3.2))
                car.clear([x],[0,1,width-2,width-1],range(1,top))
        for side in (-1,1):
            outside=0 if side==-1 else width-1
            for foot in (x0-2,x0+3):
                if 0<=foot<length:
                    car.fill([foot],[outside],range(1,6),paint)
                    car.fill([foot],[1 if side==-1 else width-2],range(1,5),paint)
            for offset, rotation in ((-1, QUARTER), (1, BACK_QUARTER)):
                car.place("11477",paint,axle+offset,outside,8,"continuous fender crowns",14,rotation)
            inset = 1 if side == -1 else width-2
            for foot in (x0-2,x0+3):
                car.fill([foot],[inset],range(5,8),paint)
                car.place("11477",paint,foot,(outside+inset)/2,7,"fender to body shoulders",14,
                          IDENTITY if side == -1 else HALF)

    car.clear(range(front,door_last+1),range(left,left+6),range(belt,roof))
    for y in (left+.5,left+2.5,left+4.5):
        car.place("30363",paint,front-1,y,belt,"planar wedge bonnet",14,QUARTER)
    car.tiles([0,1],range(2,width-2),belt-2,paint,"low nose panels",14)
    car.clear(range(front),range(left,left+6),range(belt-3,roof))
    car.fill([front,front+1],range(left+1,left+5),range(4,belt),0)
    # The transverse cowl closes the half-stud seam at the bonnet's rear edge.
    car.place("3795",paint,front+.5,center,belt-1,"bonnet to cabin cowl beam",14,QUARTER)
    for y in (center-.5,center+.5):
        car.place("3794b",paint,front+.5,y,belt,"windscreen half-stud anchors",15)
    for y in (left+.5,left+4.5):
        car.place("3068b",paint,front+.5,y,belt,"continuous cowl shoulders",15)
    car.place("65633",40,anchor,center,glass,"door glazing and front windscreen",16,QUARTER)
    car.place(layout["roof_part"],paint,layout["roof_center_stud"],center,roof,"proportioned cabin roof",18,QUARTER)
    for y in inner:
        car.place("3794b",paint,end+.5,y,glass-3,"rear deck half-stud anchors",15)
    car.place("3037",paint,end+.5,center,glass,"cabin to engine deck ramp",16,BACK_QUARTER)

    # The transparent side walls sit directly on the door belt, between its uprights.
    for side,y in zip((-1,1),sides):
        car.fill(range(door_first,door_last+1),[y],range(3,belt),paint)
        # A half-stud rear pillar meets the canopy edge and clutches the roof.
        pillar = anchor+1
        car.place("3794b",paint,pillar,y,belt,"shared door pillar anchors",14)
        car.place("3024",paint,pillar,y,belt+1,"shared door pillar foot",15)
        car.place("3005",paint,pillar,y,glass,"shared cabin rear pillars",16)
        car.place("3794b",paint,end+1.5,y,glass-3,"rear buttress half-stud anchors",15)
        car.place("4286",paint,end+.5,y,glass,"cabin to rear shoulder buttresses",16,BACK_QUARTER)
        panel_rotation=(1,0,0,0,0,side,0,-side,0)
        first = door_first
        while first <= door_last:
            span = min(6,door_last-first+1)
            part = {1:"3069b",2:"3068b",3:"26603",4:"87079",5:"87079",6:"69729"}[span]
            if span == 5:
                span = 4
            for x in sorted({max(first,front+2),first+span-1}):
                car.clear([x],[y],range(belt-4,belt-1))
                car.place("87087",paint,x,y,belt-1,"integrated door panel mounts",13,
                          IDENTITY if side == -1 else HALF)
            rotation=panel_rotation
            if span == 1:
                rotation=tuple((np.array(panel_rotation).reshape(3,3)@np.array(QUARTER).reshape(3,3)).ravel())
            car.place(part,paint,first+(span-1)/2,y+side*.9,belt-3.5,
                      "continuous door skins",18,rotation)
            first += span
        for x in (door_last+1,door_last+2):
            car.fill([x],[y],range(4,belt-1),0)
        # Long lower sills bridge the inset cabin to the flared fenders.
        outer=1 if side==-1 else width-2
        sill_top=belt-6
        car.fill(range(door_first,door_last+1),[outer],range(2,sill_top),paint)
        car.tiles(range(door_first,door_last+1),[outer],sill_top,paint,"smooth lower sills",14)

    engine_first=max(door_last+2,end+3)
    for y in (left,left+5):
        car.fill(range(engine_first,length-1),[y],[belt-1],paint)
        car.tiles(range(engine_first,length-1),[y],belt+1,paint,"rear shoulder panels",14)
    car.fill(range(engine_first,length-1),inner,[belt],0)
    car.fill([engine_first-1],inner,[belt],0)
    car.tiles([engine_first-1],inner,belt+1,0,"rear ramp to engine cover seam",18)
    for x in range(engine_first,length-1,2):
        for y in (center-1,center+1):
            car.place("2412b",0,x,y,belt+1,"engine louvres",18,QUARTER)
    car.tiles([length-1],range(2,width-2),belt+3,paint,"rear deck edge",14)

    front_rotation=(0,1,0,0,0,1,1,0,0)
    rear_rotation=(0,-1,0,0,0,-1,1,0,0)
    for back,x,rotation,normal in ((False,0,QUARTER,front_rotation),
                                 (True,length-1,BACK_QUARTER,rear_rotation)):
        top=belt+1 if back else belt-3
        for y in range(1,width-1):
            lamp=y in (1,2,width-3,width-2)
            car.place("4070" if lamp else "87087",0,x,y,top,"rear fascia mounts" if back else "front fascia mounts",9,rotation)
        for y in (1.5,width-2.5):
            car.place("3069b",36 if back else 47,length-.3 if back else -.7,y,top-1.25,
                      "rectangular tail lamps" if back else "recessed rectangular headlamps",18,normal)
        for y in (center-1,center+1):
            car.place("2412b",0,length-.1 if back else -.9,y,top-1.25,
                      "rear grille" if back else "front lower openings",18,normal)
        car.fill([x],range(1,width-1),range(1,top-3),paint)

    for y in (center-1,center+1):
        car.place("4079",4,door_last-1.5,y,4,"red bucket seats",15,QUARTER)
    car.place("3829c01",0,front+2,center-1,3,"steering wheel",15,QUARTER)
    car.fill([front+1],inner,[3],0)
    car.tiles([front+1],inner,5,0,"dashboard",15)

    if rear_wing:
        for y in (2,width-3):
            car.fill([length-2],[y],range(3,roof-1),paint)
        car.place({8:"3666",10:"3460",12:"4477"}[width],paint,length-2,center,roof-1,"thin rear wing",20,QUARTER)
        car.tiles([length-2],range(1,width-1),roof,paint,"smooth rear wing",21)
    car.pack()
    return car
