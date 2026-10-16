"""Section-fitted SUV with native curved doors, thin lamps and hollow glazing."""
import numpy as np

from .surfaces import curved_modules, place_strip


def cabin_layout(fit, start, end):
    """Choose the pillar and native pane height from opposing-view observations."""
    from .glazing import side_layout
    return side_layout(fit, start, end)


def cabin_width(fit):
    """Quantize the measured upper body to available glazing families."""
    width = fit["width_studs"]
    ratio = fit.get("source_upper_body_width_ratio")
    if ratio is None or not np.isfinite(ratio):
        return 8 if width >= 10 else 6
    return min([w for w in (6, 8) if w <= width-2], key=lambda w: abs(w-width*ratio))


def select_windscreen(belt, start, glass_top, cabin_width, body_width=10):
    """Fit native glass only when its upper socket meets the roof boundary."""
    # 62576's bounding depth includes an open rear section: moving it by -2
    # studs aligns the cowl but leaves its upper studs ahead of the roof.
    # The wide cowl can taper into a narrower cabin behind the glass.
    # 87606 has a two-by-two upper stud grid. A half-stud longitudinal
    # offset aligns it with the integer roof grid and avoids the first roof row.
    candidates = [("92583",3,6,6,0)] if body_width == 8 else [("24248",6,9,8,0)]
    if body_width >= 10 and cabin_width == 6:
        candidates.append(("87606",6,6,6,-.5))
    def error(candidate):
        _,depth,rise,_,_=candidate
        cowl=float(np.median(belt[max(0,start-depth+1):start]))
        return abs(glass_top+1-rise-cowl)
    selected=min(candidates,key=lambda c: error(c)+abs(c[3]-cabin_width))
    part,depth,rise,width,offset=selected
    return dict(part=part,depth_studs=depth,rise_plates=rise,
                origin_offset_studs=offset, roof_anchor_offset_studs=-offset,
                width_studs=width, cowl_error_plates=error(selected),
                rejected_candidates=[dict(part="62576",reason="Upper studs do not meet this roof boundary")]
                    if cabin_width == 8 else [])


def select_axle(width, wheel):
    """Reach the complete 55982 hub socket, rather than its inner half."""
    length = width
    if wheel["rim"] == "55982" and width in (8,10):
        # Each hub's native socket spans one stud outward from its centre.
        length = max(width, int(np.ceil(wheel["track_studs"]+2)))
    parts = {8:"3707",9:"60485",10:"3737",11:"23948",12:"3708"}
    if length not in parts:
        raise ValueError("No supported axle reaches the complete wheel sockets")
    return dict(part=parts[length],length_studs=length,color=14 if length in (9,11) else 0)


def prepare_fascia(car):
    """Quantize identity polygons at plate height, instead of square tile height."""
    from .details import assign_cells, cell_box
    fit = car.fit
    length, width, paint = (fit[k] for k in ("length_studs", "width_studs", "body_color"))
    center = (width-1)/2
    fit["detail_native_cells"] = []
    for name, end in (("front", 0), ("back", length-1)):
        view = next(v for v in fit["detail_program"]["views"] if v["view"] == name)
        features = [f for f in view["features"] if f["confidence"] >= .55 and
                    f["kind"] in {"headlamp", "taillamp", "intake", "grille"}]
        # Wrap the frontal image around the tapered nose/tail instead of losing the outer lamps.
        sections = np.array(fit["mesh_sections"]["half_width_studs"])
        surface = {}
        for y in range(width):
            depth = next((d for d in range(3) if abs(y-center) <= np.median(sections[abs(end-d),5:11])-.15), 2)
            surface[y] = depth if name == "front" else end-depth
        ceilings = fit.get("surface_support_ceilings", {})
        cells = [(y,h) for y,x in surface.items()
                 for h in range(3,min(int(round(fit["appearance_envelope"]["paint_plates"][x]))+1,
                                      ceilings.get(f"{x},{y}", 40)))]
        labels, _ = assign_cells(features, [cell_box(fit, name, y, h+1) for y, h in cells], .4)
        for (y, h), f in zip(cells, labels):
            x = surface[y]
            color = fit["appearance_envelope"].get("fascia_colors",{}).get(name,{}).get(f"{y},{h}",paint)
            if f:
                if f["kind"] in {"intake", "grille"}:
                    color = 0
                elif f["kind"] in {"headlamp", "taillamp"}:
                    color = 0
            car.cells[x,y,h] = color
            if f and f["kind"] in {"intake", "grille"}:
                fit["detail_native_cells"].append(dict(view=name,id=f["id"],x=x,y=y,h=h,color=color))
        for y,x in surface.items():
            car.fill([x],[y],[2],0)
    sculpt_front(car)
    expose_lamps(car)
    sculpt_diffuser(car)


def expose_lamps(car):
    """Keep each observed light band on the exposed nose/tail, ahead of curved panels."""
    from .details import mask
    fit = car.fit
    length, width = fit["length_studs"], fit["width_studs"]
    for name, x in (("front", 0), ("back", length-1)):
        view = next(v for v in fit["detail_program"]["views"] if v["view"] == name)
        lights = [f for f in view["features"] if f["confidence"] >= .55 and
                  f["kind"] == ("headlamp" if name == "front" else "taillamp")]
        slots = {}
        for f in lights:
            pixels = mask(f)
            for y in range(1,width-1):
                u = 1-(y+.5)/width if name == "front" else (y+.5)/width
                rows = np.flatnonzero(pixels[:,int(round(u*(pixels.shape[1]-1)))])
                if not len(rows):
                    continue
                v = float(np.median(rows)/(pixels.shape[0]-1))
                h = int(round(fit["roof_top_plates"]-v*(fit["roof_top_plates"]-1)-.5))
                slots.setdefault((y,h),[]).append(f["id"])
        for y in range(1,width//2):
            other = width-1-y
            left = sorted(h for column,h in slots if column==y)
            right = sorted(h for column,h in slots if column==other)
            if len(left) != len(right):
                continue
            pairs = list(zip(left,right))
            if any(abs(a-b)>1 for a,b in pairs):
                continue
            merged = [(int(np.floor((a+b)/2+.5)),slots[y,a]+slots[other,b]) for a,b in pairs]
            for a,b in pairs:
                del slots[y,a]
                del slots[other,b]
            for h,ids in merged:
                slots[y,h] = slots[other,h] = list(set(ids))
        color = 47 if name == "front" else 36
        surfaces = {}
        mounted = {}
        belt = fit.get("appearance_envelope",{}).get("paint_plates")
        shifts={}
        if name == "front" and belt is not None:
            for y in {column for column,_ in slots}:
                surface=min(2,max(0,3-min(y,width-1-y)))
                high=max(h for column,h in slots if column==y)
                shifts[y]=max(0,high-(int(np.ceil(belt[surface]))-2))
        for (y,h),ids in slots.items():
            # Outer lamps wrap into the fenders. Extending them to the centre
            # nose plane creates tall, unsupported towers ahead of the bonnet.
            edge = min(y,width-1-y)
            surface = min(2,max(0,3-edge)) if name == "front" else x
            # Translate the complete light stack; clipping each band separately
            # would merge split lamps into a solid transparent block.
            h -= shifts.get(y,0)
            mounted.setdefault((y,h),[]).extend(ids)
            surfaces[y,h] = surface
        slots=mounted
        for (y,h), ids in slots.items():
            surface = surfaces[y,h]
            if name == "front":
                car.clear(range(surface),[y],range(9,40))
                car.fill([surface,surface+1],[y],range(9,h),0)
            car.fill([surface],[y],[h-1],0)
            car.clear([surface],[y],[h])
            behind = surface+1 if name == "front" else surface-1
            car.fill([behind],[y],[h-1],0)
            if name == "front" and belt is not None and h == max(level for column,level in slots if column==y):
                car.clear([surface],[y],range(h+1,40))
            for id in ids:
                fit["detail_native_cells"].append(dict(view=name,id=id,x=surface,y=y,h=h,color=color))
        for surface,h in sorted(set((surfaces[y,h],h) for y,h in slots)):
            columns=[y for y,level in slots if level==h and surfaces[y,h]==surface]
            if name == "front":
                support=set(columns)
                for y in columns:
                    inside=y+1 if y<(width-1)/2 else y-1
                    support.add(inside)
                    car.fill([surface],[inside],range(8,h-1),0)
                # Inboard sockets attach each lens to the body without a stud above it.
                while support:
                    first=min(support)
                    span=next(n for n in (6,4,2,1) if set(range(first,first+n))<=support)
                    plate={1:"3024",2:"3023",4:"3710",6:"3666"}[span]
                    from .carfit import QUARTER
                    car.place(plate,0,surface,first+(span-1)/2,h,"lamp housing ties",18,QUARTER)
                    support-=set(range(first,first+span))
            # Smooth contiguous lenses; no bodywork relies on their upper surfaces.
            car.tiles([surface],columns,h+1,color,"lamp lenses",19)
        fit.setdefault("lamp_fitting",{})[name]=dict(lens="smooth native tiles",lens_thickness_plates=1,
            housings="inboard plate ties and opaque backing",preserve_split_bands=True,
            native_cells=len(slots))


def sculpt_diffuser(car):
    """Round the observed dark lower rear with supported, rear-facing curved bricks."""
    from .carfit import BACK_QUARTER
    fit = car.fit
    length,width = fit["length_studs"],fit["width_studs"]
    colors = fit["appearance_envelope"].get("fascia_colors",{}).get("back",{})
    ys = range(2,width-2)
    if not colors or np.mean([colors.get(f"{y},{h}",fit["body_color"]) == 0
                             for y in ys for h in range(3,8)]) < .6:
        return
    facing = (0,-1,0,0,0,1,-1,0,0)
    car.clear([length-2,length-1],ys,range(3,8))
    car.clear([length-1],ys,range(1,3))
    car.fill([length-2],ys,range(2,5),0)
    for y in ys:
        car.place("87087",0,length-2,y,8,"rear diffuser mounts",10,BACK_QUARTER)
    for y in range(2,width-3,2):
        car.place("15068",0,length-1.5,y+.5,5.5,"rounded rear diffuser",19,facing)


def sculpt_front(car):
    """Mount curved paint cheeks and a recessed grille on real side-facing studs."""
    from .carfit import QUARTER
    fit = car.fit
    width, paint = fit["width_studs"], fit["body_color"]
    colors = fit["appearance_envelope"].get("fascia_colors", {}).get("front", {})
    if not colors:
        return
    facing = (0, 1, 0, 0, 0, 1, 1, 0, 0)
    for ys in ((0,1),(width-2,width-1)):
        # Use a cheek only where most of its lower band is observed body paint.
        observed = [colors.get(f"{y},{h}", 0) == paint for y in ys for h in range(5,9)]
        if np.mean(observed) < .45:
            continue
        car.clear([0,1],ys,range(4,10))
        for y in ys:
            car.fill([1,2],[y],range(2,6),0)
            car.place("87087",paint,1,y,9,"front cheek mounts",10,QUARTER)
        car.place("15068",paint,.5,float(np.mean(ys)),6.5,"curved front cheeks",17,facing)
    for y in range(2,width-2):
        car.clear([0],[y],range(3,6))
        car.fill([0,1],[y],[2],0)
        car.place("87087",0,0,y,6,"lower grille mounts",9,QUARTER)
    for y in range(2,width-3,2):
        car.place("2412b",0,-.9,y+.5,4.75,"recessed lower grille",18,facing)


def design_envelope_suv(fit):
    from .carfit import Car, IDENTITY, HALF, QUARTER, BACK_QUARTER
    car = Car(fit)
    length,width,paint = (fit[k] for k in ("length_studs","width_studs","body_color"))
    profile = np.array(fit["appearance_envelope"]["silhouette_plates"])
    belt = np.array(fit["appearance_envelope"]["paint_plates"])
    sections = np.array(fit["mesh_sections"]["half_width_studs"])
    center = (width-1)/2
    roof = fit["roof_top_plates"]
    high = np.flatnonzero(profile >= profile.max()*.97)
    start = int(np.clip(high[0], 9, length-14))
    end = int(np.clip(high[-1]+1, start+4, length-5))
    upper_width = cabin_width(fit)
    left = (width-upper_width)//2
    cabin = range(left,left+upper_width)
    sides = (left,left+upper_width-1)
    inner = range(left+1,left+upper_width-1)
    glass_top = roof-2
    windscreen=select_windscreen(belt,start,glass_top,upper_width,width)
    front_width=windscreen["width_studs"]
    front_cabin=range((width-front_width)//2,(width+front_width)//2)
    front_part,front_depth=windscreen["part"],windscreen["depth_studs"]
    front = start-front_depth+1
    rear = end+3
    deck = int(round(np.median(belt[start:end+1])))
    fit.update(roof_start_stud=start,roof_end_stud=end,cabin_width_studs=upper_width,
        glass_top_plates=glass_top,deck_top_plates=deck,windscreen_part=front_part,
        detail_surface_mode="envelope",recipe="section-envelope-suv-v13",
        design_prior="Mesh lateral sections and four-view paint/glazing boundaries with native SUV parts",
        fixed_parameters=["native tyre, curved panel and windscreen geometry", "symmetric section quantization"],
        windscreen_fitting=windscreen,
        fitted_windscreen_angle_degrees=float(np.degrees(np.arctan2(windscreen["rise_plates"]*3.2,(front_depth-1)*8))),
        layout=dict(front_glass_interval_studs=[front,start],roof_interval_studs=[start,end],
                    rear_glass_interval_studs=[end+1,rear],tailgate_interval_studs=[rear+1,length-1]))
    car.fill(range(length),range(2,width-2),range(1,4),0)
    car.fill(range(length),range(1,width-1),range(2,4),0)
    for x in range(length):
        for h in range(4,int(round(belt[x]))):
            half = max(width*.32, sections[x,min(h,sections.shape[1]-1)])
            ys = [y for y in range(width) if abs(y-center) <= half-.15]
            car.fill([x],ys,[h],0 if h == 4 else paint)
    car.clear(range(front,start+1),front_cabin,range(glass_top-windscreen["rise_plates"],40))
    car.clear(range(end+1,rear+1),inner,range(glass_top-6,40))
    car.clear(range(front,rear),inner,range(5,40))
    car.fill(range(start+1,end+1),inner,[4],0)
    for cell in list(car.cells):
        if front <= cell[0] <= start and cell[1] in front_cabin:
            car.cells[cell] = 0
    car.fill([rear],range(2,width-2),range(5,glass_top-5),0)

    plans=[]
    for y in range(2,width-2,2):
        nose = 0 if 3 <= y and y+1 <= width-4 else 2
        target = belt[nose:front]-.8
        minimum = np.full(len(target), -np.inf)
        if nose:
            minimum[0] = np.floor(target[0]+.5)-1
        plans.append(place_strip(car,range(nose,front),[y,y+1],target,paint,"fitted bonnet",14,
                                 min_support=minimum))
        if rear+1 < length-1:
            plans.append(place_strip(car,range(rear+1,length-1),[y,y+1],belt[rear+1:-1]-.5,paint,"fitted tailgate",14))
    for y in (0,width-1):
        target = belt[2:-2]-.5
        # Keep the outer belt below the glass sockets, instead of masking the side panes.
        target[max(0,start-1):min(len(target),end)] = np.minimum(
            target[max(0,start-1):min(len(target),end)],glass_top-5)
        # The mirror occupies a one-stud notch, even when the belt uses long tiles.
        for first,last in ((2,front),(front+1,length-2)):
            if first < last:
                plans.append(place_strip(car,range(first,last),[y],target[first-2:last-2],
                                         paint,"continuous shoulder line",14))
    for y in (1,width-2):
        if front > 4:
            plans.append(place_strip(car,range(4,front),[y],belt[4:front]-.6,paint,"curved front fenders",14))
        if upper_width == 6:
            plans.append(place_strip(car,range(start+1,rear+1),[y],belt[start+1:rear+1]-.35,
                                     paint,"rising rear shoulders",14))
    for y in sides:
        if upper_width > 6:
            plans.append(place_strip(car,range(end+1,rear+1),[y],profile[end+1:rear+1]-.6,0,"tapered rear pillars",18))
        # Tie the inset rear pillar into the tailgate below the glass socket.
        car.clear([rear,rear+1],[y],[glass_top-7])
        car.place("3023",0,rear+.5,y,glass_top-6,"rear pillar ties",14)
    for y in (1,width-2):
        if y not in sides:
            car.clear([rear,rear+1],[y],[glass_top-7])
            car.place("3023",paint,rear+.5,y,glass_top-6,"rear shoulder ties",14)

    for axle,wheel in zip(fit["axles_studs"],fit["wheel_families"]):
        first=int(axle-2.5)
        arch_height=int(np.ceil(4.75+wheel["diameter_mm"]/6.4+1))
        x0=int(axle-.5)
        radius=wheel["diameter_mm"]/2+1.0
        for x in range(first,first+6):
            dx=max(0,abs(x-axle)*8-4)
            if dx<radius:
                top=int(np.ceil(4.75+np.sqrt(radius**2-dx**2)/3.2))
                car.clear([x],(0,1,width-2,width-1),range(2,top))
        car.fill(range(first,first+6),(2,width-3),range(3,arch_height),0)
        for side in (-1,1):
            car.place("62361",0,axle,center+side*center,arch_height,"wheel arches",12,
                      BACK_QUARTER if side==-1 else QUARTER)
            wheel_y=center+side*wheel["track_studs"]/2
            car.place(wheel["tyre"],0,axle,wheel_y,4.75,"tyres",18)
            car.place(wheel["rim"],fit["appearance_envelope"].get("rim_color",72),
                      axle,wheel_y,4.75,"wheel rims",18,IDENTITY if side==1 else HALF)
            mount=center+side*(center-2)
            car.clear([x0,x0+1],[int(mount)],range(3,6))
            car.fill([x0,x0+1],[int(mount)],[2],0)
            car.place("3700",0,axle,mount,6,"axle mounting",6)
        car.clear([x0,x0+1],range(width),[4,5])
        car.clear([x0,x0+1],range(1,width-1),[3,4,5])
        shaft=select_axle(width,wheel)
        fit.setdefault("axle_fitting",[]).append(shaft)
        car.place(shaft["part"],shaft["color"],axle,center,4.75,"axles",7,QUARTER)
        # Match the crown support colour across the inner liner so packing can bridge it.
        for y,inside in ((1,2),(width-2,width-3)):
            for x in (x0,x0+1):
                if (x,y,arch_height-1) in car.cells:
                    car.cells[x,inside,arch_height-1] = paint

    car.place(front_part,40,start+windscreen["origin_offset_studs"],center,
              glass_top+1,"front windscreen",16,QUARTER)
    car.place("92583",40,end+1,center,glass_top+1,"rear windscreen",16,BACK_QUARTER)
    glazing = cabin_layout(fit,start,end)
    fit["cabin_fitting"] = glazing
    window_bottom=glass_top-glazing["pane_height_plates"]
    from .glazing import place_sides, support_roof
    place_sides(car,glazing,start,end,sides)
    support_roof(car,glazing,start,end,sides)
    car.tiles([start],range(left,left+upper_width),glass_top+2,0,"windscreen header trim",18)
    car.tiles([end+1],cabin,glass_top+2,0,"rear glass header trim",18)
    car.fill(range(start+1,end+1),cabin,[glass_top],0)
    for y in range(left+2,left+upper_width-2,2):
        # A thin substrate crosses the pillar beam; it must not fill the cabin.
        x=start+1
        while x <= end:
            span=next(n for n in (6,4,3,2,1) if x+n-1 <= end)
            plate={6:"3795",4:"3020",3:"3021",2:"3022",1:"3023"}[span]
            car.place(plate,0,x+(span-1)/2,y+.5,glass_top,"roof substrate",17,
                      QUARTER if span==1 else IDENTITY)
            x+=span
        plans.append(place_strip(car,range(start+1,end+1),[y,y+1],
            profile[start+1:end+1],0,"profiled roof center",18,support_from=glass_top))
    for part, first, span in curved_modules(start+1,end):
        x = first+(span-1)/2
        car.place(part,0,x,left+.5,roof-2,"rounded roof shoulders",18,IDENTITY)
        car.place(part,0,x,left+upper_width-1.5,roof-2,"rounded roof shoulders",18,HALF)
    fit["surface_fitting"] = dict(method="bounded silhouette fit with large smooth native panels",
        max_error_plates=.85, strips=plans)

    door_first=int(np.ceil(fit["axles_studs"][0]+3))
    door_last=int(np.floor(fit["axles_studs"][1]-3))
    for side in (-1,1):
        outside=0 if side==-1 else width-1
        inset=1 if side==-1 else width-2
        panel=np.array((1,0,0,0,0,side,0,-side,0)).reshape(3,3)
        curve=tuple(float(v) for v in (panel@(np.array(HALF).reshape(3,3) if side==-1 else np.eye(3))).ravel())
        car.clear(range(door_first,door_last+1),[outside],range(4,14))
        for x in range(door_first,door_last+1):
            car.fill([x],[outside],range(9,int(round(belt[x]))-1),paint)
        for part, x, span in curved_modules(door_first,door_last):
            for mount in range(x,x+span):
                car.clear([mount],[inset],range(6,9))
                car.place("87087",paint,mount,inset,9,"curved flank mounts",9,IDENTITY if side==-1 else HALF)
            car.place(part,paint,x+(span-1)/2,center+side*(center-.5),6.5,"curved lower doors",18,curve)
        tie_x=start+1
        tie_height=min(window_bottom,int(round(belt[tie_x]))-2)
        car.clear([tie_x],[outside,inset],[tie_height-1])
        car.place("3023",paint,tie_x,(outside+inset)/2,tie_height,
                  "cowl shoulder ties",13,QUARTER)
        x=front
        car.fill([x],[outside,inset],[deck-4],paint)
        car.clear([x],[outside],range(deck-1,deck+1))
        car.place("4070",paint,x,outside,deck,"mirror mounts",13,IDENTITY if side==-1 else HALF)
        car.place("3070b",0,x,center+side*(center+.7),deck-1.25,"mirrors",18,tuple(float(v) for v in panel.ravel()))
        car.place("3070b",paint,x,outside,deck+1,"mirror caps",18)
    from .interior import furnish_cabin
    furnish_cabin(car,start,end,sides,glass_top)
    car.pack()
    return car
