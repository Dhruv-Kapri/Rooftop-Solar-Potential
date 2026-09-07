"""Stage 4 — roof-plane / tilt / aspect extraction (architecture.md §5, §9).

v1 default (LiDAR per-roof): clip the DSM/point cloud per footprint and fit individual roof
planes — RANSAC baseline in Phase 1; ML segmentation (RoofN3D-trained) in Phase 2.
Block-model/LOD-1 fallback: one height per building, flat-roof / fixed-tilt assumption.

WEAKEST LINK (risks §14.4): errors here propagate straight into capacity/kWh/CO₂. Report
uncertainty; don't over-claim precision.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import rasterio
import rasterio.mask
import rasterio.transform
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from sklearn.linear_model import RANSACRegressor

# Below this many DSM pixels, a plane fit isn't trustworthy — don't attempt one.
MIN_PX = 6


def fit_roof_plane(
    X: npt.NDArray[np.float64], z: npt.NDArray[np.float64]
) -> dict[str, object]:
    """Fit one RANSAC plane z = a*x + b*y + c to a roof's point cloud.

    `X` is (n, 2) easting/northing; `z` is (n,) surface elevation. Returns the plane
    summary the pipeline carries downstream: ``tilt_deg``, ``aspect_deg`` (compass
    azimuth of the downslope direction, 0=N/90=E/180=S/270=W), ``inlier_ratio``,
    ``n_px``, and a ``low_confidence`` flag. ``inlier_ratio`` and ``n_px`` are the
    uncertainty proxies (ADR-0002): a low ratio or few pixels means "treat this
    tilt/aspect as low-confidence", not "the plane is right".
    """
    n_px = int(len(z))
    if n_px < MIN_PX:
        return {
            "tilt_deg": np.nan,
            "aspect_deg": np.nan,
            "inlier_ratio": np.nan,
            "n_px": n_px,
            "low_confidence": True,
        }
    ransac = RANSACRegressor(residual_threshold=0.5, random_state=0)
    ransac.fit(X, z)
    a, b = (float(v) for v in ransac.estimator_.coef_)
    inlier_ratio = float(ransac.inlier_mask_.mean())
    tilt_deg = float(np.degrees(np.arctan(np.hypot(a, b))))
    aspect_deg = float(np.degrees(np.arctan2(-a, -b)) % 360.0)
    return {
        "tilt_deg": tilt_deg,
        "aspect_deg": aspect_deg,
        "inlier_ratio": inlier_ratio,
        "n_px": n_px,
        "low_confidence": bool(inlier_ratio < 0.5 or n_px < 20),
    }


def roof_points(
    geom: BaseGeometry, dsm_path: str | Path
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Clip the DSM at `dsm_path` to `geom` and return the roof's raw point cloud.

    Returns ``(X, z)``: `X` is an (n, 2) array of (easting, northing) pixel centres in
    the DSM's CRS, `z` is the matching (n,) array of surface elevations. Nodata pixels
    are excluded. This is the unfiltered DSM clip — clutter (HVAC units, parapets,
    overhanging trees) is included exactly as :func:`fit_roof_plane` will see it.
    Returns two empty arrays if no DSM pixel centre falls inside `geom` (e.g. a
    footprint over an all-nodata gap, or too small/oddly-placed to cover any pixel
    centre).
    """
    with rasterio.open(dsm_path) as src:
        out_image, out_transform = rasterio.mask.mask(src, [mapping(geom)], crop=True)
        nodata = src.nodata
    band = out_image[0]
    rows, cols = np.where(band != nodata)
    if rows.size == 0:
        return np.empty((0, 2)), np.empty((0,))
    xs, ys = rasterio.transform.xy(out_transform, rows, cols)
    X = np.column_stack([xs, ys]).astype(np.float64)
    z = band[rows, cols].astype(np.float64)
    return X, z


def fit_roof_planes(
    footprints: gpd.GeoDataFrame, dsm_path: str | Path, method: str = "ransac"
) -> gpd.GeoDataFrame:
    """Fit one RANSAC roof plane per footprint and return the per-roof plane table.

    For every row in `footprints`, clips the DSM at `dsm_path` to that geometry
    (:func:`roof_points`) and fits a plane (:func:`fit_roof_plane`). The output keeps
    the ORIGINAL footprint geometry (never the DSM clip) plus the five columns
    downstream stages need: ``tilt_deg``, ``aspect_deg``, ``inlier_ratio``, ``n_px``,
    ``low_confidence`` — the uncertainty columns are carried through, never dropped
    (ADR-0002). No footprint is dropped, even when its fit is low-confidence: the
    output has the same row count as `footprints`, and `footprints.crs` is preserved.
    Footprint area is NOT computed here — that is a downstream stage's job
    (usable_area.py).

    Args:
        method: "ransac" is the only baseline implemented in Phase 1 — a single robust
            plane per footprint. "ml" (Phase 2 RoofN3D-trained obstruction segmentation
            + multi-plane fitting) and "block" (the LOD-1 fallback: one height/building,
            no per-roof pitch) are later-phase stage contracts, not yet built.
    """
    if method != "ransac":
        raise NotImplementedError(
            f"fit_roof_planes(method={method!r}) is not implemented — only the Phase 1 "
            "'ransac' single-plane baseline is. 'ml' is Phase 2 (RoofN3D-trained "
            "segmentation); 'block' is the LOD-1 fallback path."
        )

    fits = [fit_roof_plane(*roof_points(geom, dsm_path)) for geom in footprints.geometry]
    records = [
        {
            "geometry": geom,
            "tilt_deg": fit["tilt_deg"],
            "aspect_deg": fit["aspect_deg"],
            "inlier_ratio": fit["inlier_ratio"],
            "n_px": fit["n_px"],
            "low_confidence": fit["low_confidence"],
        }
        for geom, fit in zip(footprints.geometry, fits, strict=True)
    ]
    return gpd.GeoDataFrame(records, crs=footprints.crs)
