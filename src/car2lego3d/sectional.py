"""Robust lateral sections from the reconstructed surface, in the LEGO grid."""
import numpy as np
from scipy.ndimage import median_filter


def lateral_sections(points, extent, length, width, height):
    points = np.asarray(points, dtype=float)
    extent = np.asarray(extent, dtype=float)
    if (points.ndim != 2 or points.shape[1] != 3 or not len(points)
            or extent.shape != (3,) or not np.isfinite(points).all()
            or not np.isfinite(extent).all() or np.any(extent <= 0)
            or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (length,width,height))):
        raise ValueError("Nondegenerate XYZ surface samples are required.")
    lateral = np.abs(points[:, 1]-extent[1]/2)*width/extent[1]
    vertical = points[:, 2]*height/extent[2]
    result = np.full((length, height), np.nan)
    counts = np.zeros((length, height), dtype=int)
    for x in range(length):
        longitudinal = np.abs(points[:, 0]-x) < .85
        for h in range(height):
            selected = longitudinal & (np.abs(vertical-h-.5) < 1.25)
            counts[x, h] = int(selected.sum())
            if counts[x, h] >= 8:
                result[x, h] = np.quantile(lateral[selected], .94)
    # Fill only within each height slice. Missing roof samples cannot widen a sill.
    for h in range(height):
        valid = np.flatnonzero(np.isfinite(result[:, h]))
        if len(valid):
            result[:, h] = np.interp(np.arange(length), valid, result[valid, h])
        else:
            result[:, h] = width/2
    result = median_filter(result, size=(3, 1))
    return dict(method="94th-percentile lateral surface sections; symmetric, no part scaling",
                half_width_studs=result.tolist(), sample_counts=counts.tolist(),
                limitations=["Reconstruction errors and symmetry assumptions remain in the fitted sections."])
