"""Discrete strip fitting using measured native curved-part cross sections."""
from functools import lru_cache

import numpy as np


# Heights are plate units relative to the native LDraw origin. Cross sections
# were ray-sampled from local LDraw triangles; supports are real socket planes.
PROFILES = (
    ("15068", (1.2098918, 1.9092852), (0, 1)),
    ("50950", (-1.6520581, -.5651996, -.0662886), (-3, -3, -2)),
    ("93606", (-1.9244237, -.9758047, -.3718063, -.0768424), (-3, -3, -2, -1)),
)


def profiles_for_width(width):
    if width not in (1, 2):
        raise ValueError("Surface strips must be one or two studs wide.")
    tiles = (("6636", 6), ("2431", 4), ("3069b", 2), ("3070b", 1)) if width == 1 else (
        ("87079", 4), ("3068b", 2), ("3069b", 1))
    return tuple((part, (0.,)*size, (-1,)*size) for part, size in tiles) + PROFILES


def fit_strip(target, width=2, max_error=.85, min_support=None):
    """Minimize silhouette error, seams and endpoint jumps over native parts."""
    target = np.asarray(target, dtype=float)
    if target.ndim != 1 or not len(target) or not np.isfinite(target).all():
        raise ValueError("A finite, nonempty height strip is required.")
    if not np.isfinite(max_error) or max_error < .5:
        raise ValueError("The fit tolerance must allow half-plate quantization.")
    profiles = profiles_for_width(width)
    minimum = np.full(len(target), -np.inf) if min_support is None else np.asarray(min_support, dtype=float)
    if minimum.shape != target.shape or np.isnan(minimum).any():
        raise ValueError("Support constraints must match the surface strip.")

    @lru_cache(None)
    def solve(index, previous):
        if index == len(target):
            return 0., ()
        best = (float("inf"), ())
        for part, profile, supports in profiles:
            size = len(profile)
            if index+size > len(target):
                continue
            for reverse in (False, True) if size > 1 else (False,):
                offsets = np.array(profile[::-1] if reverse else profile)
                estimated = int(round(float(np.mean(target[index:index+size]-offsets))))
                for origin in range(estimated-1, estimated+2):
                    actual = offsets+origin
                    if np.max(np.abs(actual-target[index:index+size])) > max_error:
                        continue
                    sockets = np.asarray(supports[::-1] if reverse else supports)+origin
                    if np.any(sockets < minimum[index:index+size]):
                        continue
                    junction = 0 if previous is None else 1.3*(actual[0]-previous/100)**2
                    slope = np.diff(actual)-np.diff(target[index:index+size])
                    # Prefer continuous panels within a hard silhouette tolerance.
                    pieces = width if part == "50950" else 1
                    small = .6 if size*width <= 2 else 0
                    cost = float(np.sum((actual-target[index:index+size])**2) + 1.8*np.sum(slope**2)) + junction + 1.2*pieces + small
                    tail_cost, tail = solve(index+size, round(float(actual[-1])*100))
                    if cost+tail_cost < best[0]:
                        best = (cost+tail_cost, ((part,index,origin,reverse),)+tail)
        return best

    cost, pieces = solve(0, None)
    if not np.isfinite(cost):
        raise ValueError("No native panel satisfies the silhouette and mounting constraints.")
    return {"cost": cost, "pieces": pieces, "target_plates": target.tolist(),
            "width_studs": width, "max_error_plates": max_error}


def place_strip(car, xs, ys, target, color, module, step, *, support_from=4, min_support=None):
    from .carfit import IDENTITY, QUARTER, BACK_QUARTER
    xs, ys = list(xs), list(ys)
    if not xs or len(ys) not in (1,2) or len(xs) != len(target) or xs != list(range(xs[0], xs[-1]+1)) or ys != list(range(ys[0], ys[-1]+1)):
        raise ValueError("Surface strips require a contiguous length and one/two-stud width.")
    plan = fit_strip(target, len(ys), min_support=min_support)
    profiles = {part: (profile,supports) for part,profile,supports in profiles_for_width(len(ys))}
    for part,index,origin,reverse in plan["pieces"]:
        profile,supports = profiles[part]
        support = list(reversed(supports)) if reverse else supports
        footprint = xs[index:index+len(profile)]
        for x,offset in zip(footprint,support):
            height = origin+offset
            car.clear([x],ys,range(height,40))
            car.fill([x],ys,range(support_from,height),color)
            for y in ys:
                car.fit.setdefault("surface_support_ceilings", {})[f"{x},{y}"] = height
        native={"15068":"11477","93606":"61678"}.get(part,part) if len(ys)==1 else part
        # Paired one-wide curves have native collider and socket data in Studio.
        lanes = ys if part == "50950" else [float(np.mean(ys))]
        flat = not any(profile)
        rotation = (QUARTER if len(ys)==2 and len(profile)==1 else IDENTITY) if flat else (
            BACK_QUARTER if reverse else QUARTER)
        for lane in lanes:
            car.place(native,color,float(np.mean(footprint)),lane,origin,module,step,
                      rotation)
    return plan


def curved_modules(first, last):
    """Cover a constant cross section with native 4-, 2-, then 1-wide curves."""
    cursor = first
    while cursor <= last:
        span = next(n for n in (4, 2, 1) if cursor+n-1 <= last)
        yield {4:"88930", 2:"15068", 1:"11477"}[span], cursor, span
        cursor += span
