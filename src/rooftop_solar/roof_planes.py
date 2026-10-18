"""Stage 4 — roof-plane / tilt / aspect extraction (architecture.md §5, §9).

v1 default (LiDAR per-roof): clip the DSM/point cloud per footprint and fit individual roof
planes — RANSAC baseline in Phase 1; ML segmentation (RoofN3D-trained) in Phase 2.
Block-model/LOD-1 fallback: one height per building, flat-roof / fixed-tilt assumption.

WEAKEST LINK (risks §14.4): errors here propagate straight into capacity/kWh/CO₂. Report
uncertainty; don't over-claim precision.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import rasterio
import rasterio.mask
import rasterio.transform
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from sklearn.exceptions import UndefinedMetricWarning
from sklearn.linear_model import RANSACRegressor

# Below this many DSM pixels, a plane fit isn't trustworthy — don't attempt one.
MIN_PX = 6

# Part 2-4 multi-plane constants (ADR-0011; explicit/inspectable/tunable, spec §"Constants").
# Cap on facets fit per roof — most real roofs (gable/hip/complex) have well under this.
MAX_PLANES = 4
# A fitted plane with fewer inliers than this isn't a real facet — could be noise/clutter
# getting a lucky fit on the leftover points; stop rather than record it.
MIN_PLANE_PX = 8
# RANSAC inlier band for the multi-plane fit — reuses the single-plane fit_roof_plane value.
RESIDUAL_THRESHOLD_M = 0.5
# A later plane smaller than this (px; ~100 m² at the 2 m DSM) AND raised above the dominant
# plane is an HVAC / penthouse / bulkhead / overhanging-tree-sized superstructure, not a
# facet (#30 / ADR-0013). Larger raised planes are real roof levels (e.g. whole-block
# footprints) and are kept.
SUPERSTRUCTURE_MAX_PX = 25


def _fit_ransac(X: npt.NDArray[np.float64], z: npt.NDArray[np.float64]) -> RANSACRegressor:
    """Fit RANSAC with the shared inlier band, silencing sklearn's undefined-R^2 warning.

    R^2 is only RANSAC's tie-breaker between candidates; it is undefined (nan either way) on
    <2-sample candidate sets, so ignoring the warning on degenerate roofs changes no output.
    """
    ransac = RANSACRegressor(residual_threshold=RESIDUAL_THRESHOLD_M, random_state=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UndefinedMetricWarning)
        ransac.fit(X, z)
    return ransac


def fit_roof_plane(X: npt.NDArray[np.float64], z: npt.NDArray[np.float64]) -> dict[str, object]:
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
    ransac = _fit_ransac(X, z)
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


def _is_superstructure(
    plane: dict[str, object],
    dominant: dict[str, object],
    X: npt.NDArray[np.float64],
    z: npt.NDArray[np.float64],
) -> bool:
    """True if `plane` is a small raised block on the `dominant` plane (#30 / ADR-0013).

    Small (`n_px < SUPERSTRUCTURE_MAX_PX`) and its inliers' median height above the dominant
    plane exceeds `RESIDUAL_THRESHOLD_M`. Above-only: a plane below the dominant one (a gable's
    far facet, a sunken well) is never a superstructure.
    """
    if plane["n_px"] >= SUPERSTRUCTURE_MAX_PX:
        return False
    a, b, c = dominant["coef"]
    idx = plane["inlier_idx"]
    return bool(np.median(z[idx] - (a * X[idx, 0] + b * X[idx, 1] + c)) > RESIDUAL_THRESHOLD_M)


def fit_planes_multi(
    X: npt.NDArray[np.float64], z: npt.NDArray[np.float64], max_planes: int = MAX_PLANES
) -> list[dict[str, object]]:
    """Sequential RANSAC: peel off up to `max_planes` roof facets from one point cloud.

    Fits a single robust plane (:func:`fit_roof_plane`'s formulas, reused directly) to
    whatever points remain, removes its inliers, and refits on the rest — up to
    `max_planes` times (default :data:`MAX_PLANES`). Stops early when either too few
    points remain to trust a fit (`MIN_PX`, mirrors `fit_roof_plane`'s own guard) or the
    best fit on what's left doesn't win enough inliers to count as a real facet rather
    than noise (`MIN_PLANE_PX`). This is how a gable/hip/complex roof's multiple facets
    get separated: the dominant facet is peeled off first, then the next-largest, etc.
    Passing `max_planes=1` caps the fit at a single (dominant) facet — the single-plane
    RANSAC baseline `validation.compare_self_consistency` compares multiplane against,
    fit through this exact code path rather than a separate one.

    Each returned plane is a dict: ``coef`` — `(a, b, c)` of the fitted `z = a*x + b*y +
    c` (`X`'s units — the working CRS in the pipeline); ``inlier_idx`` — this plane's
    inlier indices, as **global** positions into the original `X`/`z` (not the
    remaining-points subset); ``n_px``; ``tilt_deg``; ``aspect_deg``. Sorted by `n_px`
    descending, so index 0 is always the dominant (largest) facet. Returns `[]` if
    `len(z) < MIN_PX` to begin with, or the very first fit can't clear `MIN_PLANE_PX`.

    Raised superstructures are dropped (#30 / ADR-0013): any later plane that is small
    (`SUPERSTRUCTURE_MAX_PX`) and sits above the dominant plane (median residual >
    `RESIDUAL_THRESHOLD_M`) is removed from the result, no refit — its pixels fall to the
    nearest kept plane downstream and anything well above it becomes an obstruction. The
    dominant plane is never dropped.
    """
    remaining = np.arange(len(z))
    planes: list[dict[str, object]] = []
    for _ in range(max_planes):
        if len(remaining) < MIN_PX:
            break
        ransac = _fit_ransac(X[remaining], z[remaining])
        mask = ransac.inlier_mask_
        inliers = remaining[mask]
        if len(inliers) < MIN_PLANE_PX:
            break
        a, b = (float(v) for v in ransac.estimator_.coef_)
        c = float(ransac.estimator_.intercept_)
        tilt_deg = float(np.degrees(np.arctan(np.hypot(a, b))))
        aspect_deg = float(np.degrees(np.arctan2(-a, -b)) % 360.0)
        planes.append(
            {
                "coef": (a, b, c),
                "inlier_idx": inliers,
                "n_px": int(len(inliers)),
                "tilt_deg": tilt_deg,
                "aspect_deg": aspect_deg,
            }
        )
        remaining = remaining[~mask]
    planes.sort(key=lambda p: p["n_px"], reverse=True)
    if planes:
        dominant = planes[0]
        planes = [dominant] + [p for p in planes[1:] if not _is_superstructure(p, dominant, X, z)]
    return planes


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
    footprints: gpd.GeoDataFrame,
    dsm_path: str | Path,
    method: str = "ransac",
    *,
    max_planes: int = MAX_PLANES,
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

    "multiplane" (Part 2-4, ADR-0011) instead fits **up to `MAX_PLANES` facets per
    footprint** (sequential RANSAC, :func:`fit_planes_multi`) and returns **one row per
    (building, plane)** — a different schema (see below), because a single roof can now
    span several rows. `"ransac"`'s one-row-per-footprint output is unchanged (a
    regression test pins this byte-for-byte).

    The "multiplane" schema, columns: ``geometry`` (the ORIGINAL footprint, repeated
    across a building's plane rows), ``building_id`` (positional index into
    `footprints`), ``plane_id`` (0-based within building; 0 = the dominant/largest-
    inlier-count plane), ``n_planes`` (repeated per building), ``tilt_deg``,
    ``aspect_deg``, ``inlier_ratio`` (this plane's inliers / the building's *total*
    pixel count — how much of the roof this one plane explains), ``n_px`` (this plane's
    inlier count), ``coef`` (`(a, b, c)` of `z = a*x + b*y + c`, for obstruction
    residuals downstream), ``inlier_xy`` ((n_px, 2) easting/northing pixel centres —
    this plane's membership, consumed by `radiation.plane_poa`), ``low_confidence``
    (per building, repeated: True if the building's overall inlier fraction < 0.5 or its
    total pixel count < 20 — the same threshold `fit_roof_plane` uses at n_px<20).

    An **unfittable** footprint (too few DSM pixel centres, or not even one facet clears
    `MIN_PLANE_PX`) still gets exactly **one** row — never dropped (ADR-0002's "flag,
    don't drop" carried into Part 2-4): `plane_id=0`, `n_planes=1`, NaN tilt/aspect/
    inlier_ratio, `n_px` = the building's raw pixel count, `coef=None`, `inlier_xy` an
    empty `(0, 2)` array, `low_confidence=True`.

    Args:
        method: "ransac" is the Phase 1 baseline — a single robust plane per footprint,
            kept for comparison. "multiplane" (Part 2-4) is the production path: multiple
            facets per footprint, per-plane rows. "ml" (RoofN3D-trained inference demo,
            ADR-0011) and "block" (the LOD-1 fallback: one height/building, no per-roof
            pitch) are other stage contracts, not built here.
        max_planes: cap on facets per footprint, "multiplane" only (ignored by "ransac").
            The single-plane baseline hook: `max_planes=1` runs the same fitter and the
            same multiplane schema/downstream chain with one facet per roof -- "N facets vs
            1" through one code path, as `validation.compare_self_consistency` does.
    """
    if method == "ransac":
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

    if method != "multiplane":
        raise NotImplementedError(
            f"fit_roof_planes(method={method!r}) is not implemented — only 'ransac' "
            "(Phase 1 baseline) and 'multiplane' (Part 2-4 production path) are. 'ml' is "
            "the ADR-0011 inference demo; 'block' is the LOD-1 fallback path."
        )

    records = []
    for building_id, geom in enumerate(footprints.geometry):
        X, z = roof_points(geom, dsm_path)
        total_px = int(len(z))
        planes = fit_planes_multi(X, z, max_planes=max_planes)

        if not planes:
            # Unfittable: too few pixels, or no facet cleared MIN_PLANE_PX. One
            # placeholder row, never dropped (mirrors the ransac tiny-footprint case).
            records.append(
                {
                    "geometry": geom,
                    "building_id": building_id,
                    "plane_id": 0,
                    "n_planes": 1,
                    "tilt_deg": np.nan,
                    "aspect_deg": np.nan,
                    "inlier_ratio": np.nan,
                    "n_px": total_px,
                    "coef": None,
                    "inlier_xy": np.empty((0, 2), dtype=np.float64),
                    "low_confidence": True,
                }
            )
            continue

        n_planes = len(planes)
        total_inliers = sum(plane["n_px"] for plane in planes)
        low_confidence = bool(total_inliers / total_px < 0.5 or total_px < 20)
        for plane_id, plane in enumerate(planes):
            records.append(
                {
                    "geometry": geom,
                    "building_id": building_id,
                    "plane_id": plane_id,
                    "n_planes": n_planes,
                    "tilt_deg": plane["tilt_deg"],
                    "aspect_deg": plane["aspect_deg"],
                    "inlier_ratio": plane["n_px"] / total_px,
                    "n_px": plane["n_px"],
                    "coef": plane["coef"],
                    "inlier_xy": X[plane["inlier_idx"]],
                    "low_confidence": low_confidence,
                }
            )
    return gpd.GeoDataFrame(records, crs=footprints.crs)
