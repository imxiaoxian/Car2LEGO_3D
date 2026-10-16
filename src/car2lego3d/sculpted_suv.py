"""Layered SUV chassis with fitted bonnet and native curved roof shoulders."""
import numpy as np

from .surfaces import place_strip


def design_sculpted_suv(fit):
    from .carfit import Car, IDENTITY, HALF, QUARTER, BACK_QUARTER
    car = Car(fit)
    length,width,paint = (fit[k] for k in ("length_studs","width_studs","body_color"))
    center=(width-1)/2
    start,end=fit["roof_start_stud"],fit["roof_end_stud"]
    roof=fit["roof_top_plates"]
    glass=roof-3
    fit["glass_top_plates"]=glass
    deck=glass-fit["windscreen_height_plates"]
    fit["deck_top_plates"]=deck
    cabin_width=fit["cabin_width_studs"]
    left=(width-cabin_width)//2
    cabin=range(left,left+cabin_width)
    sides=(left,left+cabin_width-1)
    inner=range(left+1,left+cabin_width-1)
    edges=(0,1,width-2,width-1)
    car.fill(range(1,length),range(2,width-2),range(2,4),0)
    car.fill(range(length),range(width),range(4,deck-2),paint)
    car.fill(range(start-2,end+2),range(width),[3],0)
    car.fill(range(start-3,end+4),range(1,width-1),range(deck-2,deck),paint)
    car.clear(range(start-2,end+3),inner,range(5,deck))
    car.fill(range(start-2,end+3),inner,[4],0)

    # Curved shoulders rise inward to the narrow cabin instead of full-width walls.
    occupied=set()
    for axle,wheel in zip(fit["axles_studs"],fit["wheel_families"]):
        first=int(axle-2.5)
        occupied.update(range(first,first+6))
        car.clear(range(first,first+6),edges,range(2,deck+2))
        arch_height=int(np.ceil(4.75+wheel["diameter_mm"]/6.4+1))
        car.fill([first,first+5],edges,range(4,arch_height-3),paint)
        x0=int(axle-.5)
        car.clear([x0,x0+1],range(width),[3,4,5])
        for side in (-1,1):
            y=center+side*(center-2)
            car.place("3700",0,axle,y,6,"axle mounting",6)
            car.fill([x0,x0+1],[int(y)], [2],0)
            car.place("62361",paint,axle,center+side*center,arch_height,"wheel arches",12,
                      BACK_QUARTER if side==-1 else QUARTER)
            outside,inside=(0,1) if side==-1 else (width-1,width-2)
            car.fill([x0,x0+1],[inside],range(arch_height-1,arch_height+1),paint)
            car.place("15068",paint,axle,(outside+inside)/2,arch_height,"rounded arch crowns",14,
                      IDENTITY if side==-1 else HALF)
            for x,rotation in ((first+.5,QUARTER),(first+4.5,BACK_QUARTER)):
                car.place("11477",paint,x,outside,arch_height-2,"rounded arch shoulders",14,rotation)
            wheel_y=center+side*wheel["track_studs"]/2
            car.place(wheel["tyre"],0,axle,wheel_y,4.75,"tyres",18)
            car.place(wheel["rim"],72,axle,wheel_y,4.75,"wheel rims",18,IDENTITY if side==1 else HALF)
        car.place({8:"3707",10:"3737",12:"3708"}[width],0,axle,center,4.75,"axles",7,QUARTER)
    for x in range(1,length-1,2):
        if any(xx in occupied for xx in (x,x+1)) or x <= start-1 <= x+1 or x+1 >= length-2:
            continue
        for side in (-1,1):
            outside,inside=(0,1) if side==-1 else (width-1,width-2)
            car.fill([x,x+1],[outside],range(4,deck-2),paint)
            car.fill([x,x+1],[inside],range(4,deck-1),paint)
            car.clear([x,x+1],[outside],range(deck-2,deck+2))
            car.clear([x,x+1],[inside],range(deck-1,deck+2))
            car.place("15068",paint,x+.5,(outside+inside)/2,deck-2,"curved beltline",14,IDENTITY if side==-1 else HALF)

    bonnet=list(range(1,start-3))
    source=np.asarray(fit["height_profile_studs"])
    target=source[bonnet]/source.max()*roof
    target += deck-float(target[-1])
    plans=[]
    for y in range(2,width-2,2):
        plans.append(place_strip(car,bonnet,[y,y+1],target,paint,"fitted bonnet",14))
    fit["surface_fitting"]={"method":"native curved-strip dynamic programming", "bonnet":plans}
    car.fill([start-3,start-2],cabin,range(4,deck),paint)
    car.place(fit["windscreen_part"],40,start-1,center,glass,"front windscreen",16,QUARTER)
    car.place(fit["windscreen_part"],40,end+1,center,glass,"rear windscreen",16,BACK_QUARTER)
    car.fill(range(end+2,end+4),cabin,range(4,deck),paint)
    for side,y in zip((-1,1),sides):
        car.fill(range(start,end+1),[y],range(deck,glass-6),0)
        x=start
        while x<=end:
            if x==start+4 or x==end:
                car.fill([x],[y],range(glass-6,glass),0)
                x+=1
            else:
                car.place("87552",40,x+.5,y,glass,"continuous side glazing",16,HALF if side==-1 else IDENTITY)
                x+=2
    car.fill(range(start-1,end+2),cabin,[glass],0)
    roof_color=fit.get("roof_color",0)
    car.fill(range(start-1,end+2),inner,[glass+1],roof_color)
    for x in range(start-1,end+2):
        car.place("11477",roof_color,x,left+.5,glass+1,"curved roof shoulders",18,IDENTITY)
        car.place("11477",roof_color,x,left+cabin_width-1.5,glass+1,"curved roof shoulders",18,HALF)
    car.tiles(range(start-1,end+2),range(left+2,left+cabin_width-2),roof,roof_color,"roof center",18)

    for side in (-1,1):
        outside=0 if side==-1 else width-1
        facing=IDENTITY if side==-1 else HALF
        panel=(1,0,0,0,0,side,0,-side,0)
        for door_start in (start-2,start+3):
            for x in (door_start,door_start+3):
                car.clear([x],[outside],range(deck-5,deck-2))
                car.place("87087",paint,x,outside,deck-2,"door mounts",9,facing)
            car.place("87079",paint,door_start+1.5,center+side*(center+.9),deck-4.5,"door skin",18,panel)
        x=start-1
        car.fill([x],[outside],range(4,deck-2),paint)
        car.place("4070",paint,x,outside,deck+1,"mirror mounts",13,facing)
        car.place("24246",0,x,center+side*(center+.7),deck-.25,"mirrors",18,panel)
        car.place("3070b",paint,x,outside,deck+2,"mirror caps",18)

    # Flat-mounted transparent tiles expose a slim light edge, not a tall square lamp.
    nose=max(7,round(float(target[0])))
    car.clear([0],range(width),range(nose,40))
    car.fill([0],range(width),range(4,nose),paint)
    car.fill([0],range(width),[3],0)
    for y in (1.5,width-2.5):
        car.place("3069b",47,0,y,nose+1,"slim split headlamps",18,QUARTER)
    car.tiles([0],[3,4,5,6] if width==10 else range(3,width-3),nose+1,paint,"nose center",18)
    front=(0,1,0,0,0,1,1,0,0)
    rear=(0,-1,0,0,0,-1,1,0,0)
    for x,rotation in ((0,QUARTER),(length-1,BACK_QUARTER)):
        for y in range(1,width-1):
            top=nose-1 if x==0 else deck
            car.clear([x],[y],range(top-3,top))
            car.place("87087",0,x,y,top,"fascia mounts",9,rotation)
        for y in np.arange(1.5,width-1,2):
            car.place("3069b",0 if x==0 else 36,-.9 if x==0 else length-.1,y,
                      (nose-1 if x==0 else deck)-1.25,"front intake" if x==0 else "continuous SUV tail lights",18,front if x==0 else rear)
    car.fill(range(end+4,length),range(1,width-1),range(deck-2,deck),paint)
    for y in range(2,width-2,2):
        place_strip(car,range(end+4,length-1),[y,y+1],np.linspace(deck+2,deck+1,length-end-5),paint,"curved tailgate",14)
    # Surface supports must not refill the reserved shafts or native mounting volumes.
    for axle in fit["axles_studs"]:
        x0=int(axle-.5)
        car.clear([x0,x0+1],range(width),[4,5])
        car.clear([x0,x0+1],[2,width-3],[3,4,5])
    car.clear([length-1],range(1,width-1),range(deck-3,deck))
    car.pack()
    car.tiles([length-1],range(1,width-1),deck+1,paint,"tail light caps",18)
    for y in (center-1,center+1):
        car.place("4079",0,start+1.5,y,6,"front seats",15,QUARTER)
        car.place("4079",0,end-.5,y,6,"rear seats",15,QUARTER)
    return car
