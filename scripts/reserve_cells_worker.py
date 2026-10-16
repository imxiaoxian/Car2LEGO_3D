"""Reserve exact native collider volumes before body-cell packing, read-only upstream."""
import argparse
import json
import os
from pathlib import Path
import sys

import numpy as np
from scipy.spatial import cKDTree


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--input",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve().is_relative_to(args.source.resolve()):
        raise ValueError("Original project is read-only")
    sys.dont_write_bytecode=True
    os.environ["STUDIO_LDRAW_PATH"]=str(args.source/"Studio 2.0/ldraw")
    os.environ["STORAGE_PATH"]=str(args.output.resolve().parent)
    sys.path.insert(0,str(args.source/"backend"))
    from app.services.assembly_geometry import local_boxes,_intersects
    from app.services.assembly_preview import part_triangles
    from app.services.assembly_v3.catalog import ports
    request=json.loads(args.input.read_text(encoding="utf-8"))
    cells={tuple(c):request.get("body_color",0) for c in request["cells"]}
    added={}
    for part in request["parts"]:
        matrix=np.array(part["rotation"]).reshape(3,3)
        origin=np.array(part["position"])
        for port in ports(part["part"]+".dat"):
            if port.kind!="hole" or port.area<4 or port.altitude<=0:
                continue
            if not np.allclose(matrix@port.direction,[0,1,0],atol=1e-4):
                continue
            point=origin+matrix@port.position
            grid=np.array([point[0]/20+(request["length"]-1)/2,
                           point[2]/20+(request["width"]-1)/2,-point[1]/8])
            if not np.allclose(grid,np.round(grid),atol=1e-3):
                continue
            x,y,h=map(int,np.round(grid))
            if not (0<=x<request["length"] and 0<=y<request["width"] and 3<h<35):
                continue
            nearest = next((z for z in range(h-1,2,-1) if (x,y,z) in cells), None)
            # A roof can span the cabin. Filling every socket to the floor obscures the glazing.
            if nearest is None or h-nearest > 4:
                continue
            for z in range(h-1,nearest,-1):
                if (x,y,z) in cells:
                    break
                color=0 if part["color"] in (0,40,47) else request.get("body_color",0)
                cells[x,y,z]=color
                added[x,y,z]=color
    request["cells"]=list(cells)
    points=np.array([c[:3] for c in request["cells"]],dtype=float)
    centers=np.column_stack(((points[:,0]-(request["length"]-1)/2)*20,
                            -(points[:,2]+.5)*8,(points[:,1]-(request["width"]-1)/2)*20))
    tree=cKDTree(centers)
    plate_boxes=local_boxes("3024.dat")
    blocked=set()
    fallback=set()
    for part in request["parts"]:
        matrix=np.array(part["rotation"]).reshape(3,3)
        origin=np.array(part["position"])
        name=part["part"]+".dat"
        try:
            boxes=local_boxes(name)
        except ValueError:
            geometry=part_triangles(name)
            low,high=geometry.min(axis=(0,1)),geometry.max(axis=(0,1))
            boxes=[((low+high)/2,np.eye(3),(high-low)/2)]
            fallback.add(name)
        for center,axes,half in boxes:
            center,axes=origin+matrix@center,matrix@axes
            for i in tree.query_ball_point(center,float(np.linalg.norm(half)+16)):
                extent=np.abs(axes)@half
                box=(center,axes,half,center-extent,center+extent)
                plate_origin=centers[i]+[0,-4,0]
                for pc,pa,ph in plate_boxes:
                    pc=pc+plate_origin
                    pe=np.abs(pa)@ph
                    if _intersects((pc,pa,ph,pc-pe,pc+pe),box):
                        blocked.add(int(i))
                        break
    args.output.write_text(json.dumps(dict(blocked_cells=[request["cells"][i] for i in sorted(blocked)],
        support_cells=[[*cell,color] for cell,color in added.items()],
        conservative_triangle_bounds=sorted(fallback))),encoding="utf-8")


if __name__=="__main__":
    main()
