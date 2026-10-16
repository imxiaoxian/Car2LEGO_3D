"""Measure silhouettes and paint boundaries from the actual multiview pixels."""
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import median_filter, gaussian_filter1d

from .common import digest, write_json
from .lego import PALETTE
from .multiview import load_views

VERSION = "pixel-envelope-v5"


def fascia_paint_fraction(pixels, paint_rgb):
    """Do not interpret tinted reflections inside a dark intake as body paint."""
    pixels=np.asarray(pixels,dtype=float)
    paint_rgb=np.asarray(paint_rgb,dtype=float)
    if not len(pixels):
        return 0.
    paint_chroma=paint_rgb-paint_rgb.min()
    chroma=pixels-pixels.min(axis=1,keepdims=True)
    norm=np.linalg.norm(chroma,axis=1)
    if np.linalg.norm(paint_chroma) > .2:
        aligned=(chroma@paint_chroma)/(norm*np.linalg.norm(paint_chroma)+1e-6) > .9
        selected=(norm>.25)&aligned
    else:
        selected=np.linalg.norm(pixels-paint_rgb,axis=1)<.25
    selected &= pixels.max(axis=1) >= max(.18,.45*float(paint_rgb.max()))
    return float(selected.mean())


def rim_brightness(rgb, fit):
    """Sample the neutral inner wheel annulus, excluding tyres, hubs and painted calipers."""
    rows, columns = np.indices(rgb.shape[:2])
    x = columns/max(1,rgb.shape[1]-1)*(fit["length_studs"]-1)
    h = (1-rows/max(1,rgb.shape[0]-1))*fit["roof_top_plates"]
    values = []
    for axle,wheel in zip(fit["axles_studs"],fit["wheel_families"]):
        radius = wheel["diameter_mm"]/2
        radial = np.sqrt(((x-axle)*8/radius)**2+((h-4.75)*3.2/radius)**2)
        pixels = rgb[(radial>.3)&(radial<.7)&(np.ptp(rgb,axis=2)<.12)]
        if len(pixels) >= 16:
            values.append(float(np.median(pixels.mean(axis=1))))
    return values


def glazing_grid(rgb, alpha, roof, belt, count, height):
    """Vote actual neutral glass pixels inside the measured upper body, front first."""
    chroma = rgb.max(axis=2)-rgb.min(axis=2)
    brightness = rgb.mean(axis=2)
    glass = alpha & (chroma < .16) & (brightness > .17)
    result = np.zeros((count, height), dtype=float)
    for x in range(count):
        x0, x1 = round(x*rgb.shape[1]/count), round((x+1)*rgb.shape[1]/count)
        for h in range(max(0, int(np.floor(belt[x]))), min(height, int(np.ceil(roof[x])))):
            y0 = round((1-(h+1)/height)*rgb.shape[0])
            y1 = round((1-h/height)*rgb.shape[0])
            region = glass[max(0,y0):y1,x0:x1]
            result[x,h] = float(region.mean()) if region.size else 0.
    return result


def column_profile(pixels, count, height):
    values = np.full(count, np.nan)
    for x in range(count):
        band = pixels[:, round(x*pixels.shape[1]/count):round((x+1)*pixels.shape[1]/count)]
        rows = np.where(band)[0]
        if len(rows) >= 3:
            values[x] = (1-float(np.quantile(rows, .025))/pixels.shape[0])*height
    valid = np.flatnonzero(np.isfinite(values))
    if len(valid) < count//2:
        raise ValueError("Reference pixels do not provide a reliable body boundary.")
    return gaussian_filter1d(median_filter(np.interp(np.arange(count), valid, values[valid]), size=3), .5)


def measure(views, fit, directory):
    import rembg
    _, paths = load_views(views)
    directory = Path(directory)
    signature = dict(version=VERSION, images={k:digest(v) for k,v in paths.items()},
        length=fit["length_studs"], width=fit["width_studs"], height=fit["roof_top_plates"],
        color=fit["body_color"], axles=fit["axles_studs"], wheels=fit["wheel_families"])
    target = directory / "envelope.json"
    if target.is_file():
        saved = json.loads(target.read_text(encoding="utf-8"))
        if saved.get("signature") == signature:
            return saved
    directory.mkdir(parents=True, exist_ok=True)
    session = rembg.new_session("u2netp", providers=["CPUExecutionProvider"])
    records = {}
    paint_rgb = np.array(PALETTE[fit["body_color"]], dtype=float)/255
    paint_chroma = paint_rgb-paint_rgb.min()
    paint_chroma /= max(np.linalg.norm(paint_chroma), 1e-6)
    fascia = {}
    rims = []
    for view in ("left", "right", "front", "back"):
        source = Image.open(paths[view]).convert("RGBA")
        foreground = rembg.remove(source, session=session)
        alpha = np.array(foreground.getchannel("A")) > 160
        yy, xx = np.where(alpha)
        bbox = [int(xx.min()), int(yy.min()), int(xx.max()+1), int(yy.max()+1)]
        x0,y0,x1,y1 = bbox
        rgb = np.array(source.convert("RGB"))[y0:y1,x0:x1].astype(float)/255
        alpha = alpha[y0:y1,x0:x1]
        chroma = rgb-rgb.min(axis=2, keepdims=True)
        norm = np.linalg.norm(chroma, axis=2)
        if np.linalg.norm(paint_rgb-paint_rgb.min()) > .2:
            paint = (norm>.25)&((chroma@paint_chroma)/(norm+1e-6)>.9)&alpha
        else:
            paint = (np.linalg.norm(rgb-paint_rgb,axis=2)<.25)&alpha
        if view in {"front", "back"}:
            from .details import cell_box
            colors = {}
            for y in range(fit["width_studs"]):
                for h in range(2, fit["roof_top_plates"]):
                    u0,v0,u1,v1 = cell_box(fit,view,y,h+1)
                    xa,xb = np.clip(np.round(np.array([u0,u1])*rgb.shape[1]),0,rgb.shape[1]).astype(int)
                    ya,yb = np.clip(np.round(np.array([v0,v1])*rgb.shape[0]),0,rgb.shape[0]).astype(int)
                    pixels = rgb[ya:yb,xa:xb][alpha[ya:yb,xa:xb]]
                    if len(pixels):
                        colors[f"{y},{h}"] = fit["body_color"] if fascia_paint_fraction(pixels,paint_rgb) > .4 else 0
            fascia[view] = colors
            continue
        if view == "right":
            alpha, paint, rgb = alpha[:,::-1], paint[:,::-1], rgb[:,::-1]
        rims.extend(rim_brightness(rgb,fit))
        roof = column_profile(alpha, fit["length_studs"], fit["roof_top_plates"])
        body = column_profile(paint, fit["length_studs"], fit["roof_top_plates"])
        glazing = glazing_grid(rgb, alpha, roof, body, fit["length_studs"], fit["roof_top_plates"])
        records[view] = dict(bbox=bbox, silhouette_plates=roof.tolist(), paint_plates=body.tolist(),
                             glazing_fraction=glazing.tolist())
        foreground.save(directory / f"{view}-foreground.png")
    roof = np.mean([v["silhouette_plates"] for v in records.values()],axis=0)
    body = np.mean([v["paint_plates"] for v in records.values()],axis=0)
    # Averages preserve opposing-view disagreement rather than silently choosing one side.
    result = dict(signature=signature, views=records, fascia_colors=fascia, silhouette_plates=roof.tolist(), paint_plates=body.tolist(),
        rim_color=0 if rims and np.median(rims)<.28 else 72, rim_brightness_samples=rims,
        opposing_profile_mae_plates=float(np.mean(np.abs(np.array(records["left"]["silhouette_plates"])-
            np.array(records["right"]["silhouette_plates"])))), appearance_accepted=False)
    write_json(target,result)
    return result
