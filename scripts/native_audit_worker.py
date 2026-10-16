"""Read-only bridge to the original project's native Studio geometry checks."""
import argparse
import csv
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import plistlib
import sys
from types import SimpleNamespace

import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("D:/lego"))
    parser.add_argument("--assembly", type=Path)
    parser.add_argument("--parts", nargs="*")
    parser.add_argument("--query")
    parser.add_argument("--index", action="store_true", help="Include native collider and material metadata")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.source.resolve()):
        raise ValueError("The original project is read-only.")
    sys.dont_write_bytecode = True
    os.environ["STUDIO_LDRAW_PATH"] = str(args.source / "Studio 2.0/ldraw")
    os.environ["STORAGE_PATH"] = str(args.output.resolve().parent)
    sys.path.insert(0, str(args.source / "backend"))
    from app.services.assembly_preview import part_triangles
    from app.services.assembly_geometry import local_boxes
    from app.services.assembly_v3.catalog import ports, part_validity
    from app.services.assembly_v3.connections import infer_connections
    from app.services.assembly_v3.validation import collisions
    from app.services.assembly_v3.scene import PartInstance, Transform

    root = args.source / "Studio 2.0/ldraw"
    with (root / "Bricksmith Parts.plist").open("rb") as stream:
        names = plistlib.load(stream)["Part List"]
    if args.assembly:
        rows = json.loads(args.assembly.read_text(encoding="utf-8"))["parts"]
        selected = sorted({p["part"]+".dat" for p in rows})
    else:
        selected = [p if p.endswith(".dat") else p+".dat" for p in args.parts or []]
        if args.query:
            selected += [p for p, item in names.items() if item["Part Name"].startswith(args.query)]
    catalog = {}
    materials = {}
    if args.index:
        with (args.source / "Studio 2.0/data/StudioColorDefinition.txt").open(encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream, delimiter="\t"):
                code = row["LDraw Color Code"]
                if code.isdigit():
                    materials[code] = dict(name=row["LDraw Color Name"], rgb=row["RGB value"],
                        transparent="trans" in row["LDraw Color Name"].lower())
    for name in sorted(set(selected)):
        try:
            triangles = part_triangles(name)
            low, high = triangles.min(axis=(0, 1)), triangles.max(axis=(0, 1))
            entry = {"description": names.get(name, {}).get("Part Name", ""),
                     "low_ldu": low.tolist(), "high_ldu": high.tolist(),
                     "size_mm": ((high-low)*.4).tolist(), "validity": part_validity(name)}
            entry["connectors"] = [dict(kind=p.kind, subtype=p.subtype, tag=p.tag,
                position=p.position, direction=p.direction, semantic=p.semantic,
                length=p.length, altitude=p.altitude, area=p.area) for p in ports(name)]
            if args.index:
                if not entry["description"]:
                    geometry_path = root / entry["validity"]["geometry_source"]
                    entry["description"] = geometry_path.read_text(encoding="utf-8", errors="replace").splitlines()[0][2:]
                entry["collision_boxes"] = [dict(center=c.tolist(), axes=a.tolist(), half_extent=h.tolist())
                                             for c, a, h in local_boxes(name)]
                entry["resource_sha256"] = {kind: hashlib.sha256(path.read_bytes()).hexdigest() for kind, path in
                    (("geometry", root / entry["validity"]["geometry_source"]),
                     ("connections", root / "connectivity" / (Path(name).stem+".conn")),
                     ("collider", root / "collider" / (Path(name).stem+".col")))}
                entry.update(transparent_capable=True, transparency_basis="LDraw material assignment; manufactured availability unknown",
                    available_colors=None, color_availability_verified=False,
                    printed="pattern" in entry["description"].lower(),
                    printed_variants=[k for k in names if k.startswith(Path(name).stem+"p") and k.endswith(".dat")])
            if args.parts and any(kind in entry["description"] for kind in ("Slope", "Tile", "Windscreen")):
                sys.path.append(str(Path(__file__).resolve().parents[1] / ".venv/Lib/site-packages"))
                import trimesh
                geometry = trimesh.Trimesh(vertices=triangles.reshape(-1,3),
                    faces=np.arange(len(triangles)*3).reshape(-1,3), process=False)
                stations = np.arange(low[2]+10, high[2], 20)
                origins = np.column_stack([np.zeros(len(stations)), np.full(len(stations), low[1]-20), stations])
                hits, rays, _ = geometry.ray.intersects_location(origins, np.tile([0,1,0], (len(stations),1)))
                entry["surface_profile"] = [[float(z), float(hits[rays==i,1].min())]
                    for i,z in enumerate(stations) if np.any(rays==i)]
            catalog[name] = entry
        except ValueError as error:
            catalog[name] = {"error": str(error)}
    result = {"catalog": catalog, "physical_verified": False, "studio_gui_verified": False}
    if args.index:
        result["materials"] = materials
        result["source_root"] = str(root.resolve())
    if args.assembly:
        result["assembly_sha256"] = hashlib.sha256(args.assembly.read_bytes()).hexdigest()
        features = {"tyres": "tyre", "wheel rims": "rim"}
        instances = [PartInstance(id=f"p{i}", part_num=row["part"]+".dat", color_id=row["color"],
                     module=row["module"], feature=features.get(row["module"], ""),
                     transform=Transform(position=row["position"], rotation=row["rotation"])) for i,row in enumerate(rows)]
        scene = SimpleNamespace(parts=instances, modules=[SimpleNamespace(id=name, structural=True)
                                for name in {p.module for p in instances}], connections=[])
        scene.connections, connectivity = infer_connections(scene)
        issues = collisions(scene, limit=10000)
        bounds = []
        for row in rows:
            geometry = part_triangles(row["part"]+".dat") @ np.array(row["rotation"]).reshape(3,3).T + row["position"]
            bounds.append([geometry.min(axis=(0,1)), geometry.max(axis=(0,1))])
        bounds = np.array(bounds)
        missing = sorted(k for k,v in catalog.items() if not v.get("validity",{}).get("collider"))
        result.update(connectivity=connectivity, collisions=issues, collision_count=len(issues),
                      missing_native_colliders=missing,
                      collision_coverage="partial" if missing else "complete",
                      connections=[c.model_dump() for c in scene.connections],
                      part_count=len(rows), bounds_ldu=[bounds[:,0].min(axis=0).tolist(), bounds[:,1].max(axis=0).tolist()],
                      disconnected_modules=dict(Counter(instances[int(p[1:])].module for p in connectivity["disconnected_parts"])),
                      per_part_bounds_ldu=bounds.tolist())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "parts": len(catalog),
                      "collisions": result.get("collision_count"), "connectivity": result.get("connectivity")}))


if __name__ == "__main__":
    main()
