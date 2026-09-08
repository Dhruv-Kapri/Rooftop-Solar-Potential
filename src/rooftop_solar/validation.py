"""Stage — validation metrics (plan §6, risks §14.4).

Ground-truth-free accuracy checks for the Part 2-4 geometry: DSM self-consistency
(reconstruct the DSM from the fitted planes; per-roof residual), multiplane vs the
single-plane ransac baseline. The spot-check harness (step 7b) will join it here later.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import numpy.typing as npt

from rooftop_solar.roof_planes import fit_planes_multi, roof_points

# A pixel counts as "reconstructed within tolerance" when |residual| <= this. Reuses the
# RANSAC inlier band (roof_planes.RESIDUAL_THRESHOLD_M) -- a roof surface the fit
# explains to within 0.5 m.
SELF_CONSISTENCY_TOL_M = 0.5


def reconstruct_residuals(
    X: npt.NDArray[np.float64],
    z: npt.NDArray[np.float64],
    plane_coefs: list[tuple[float, float, float]] | None,
) -> npt.NDArray[np.float64]:
    """Reconstruct each pixel's elevation from its nearest fitted plane; return the residual.

    `X` is (n, 2) easting/northing, `z` is (n,) elevation, `plane_coefs` is the roof's
    fitted plane list (`(a, b, c)` of `z = a*x + b*y + c`) -- one entry for a single-plane
    (ransac) fit, several for a multiplane fit. Each pixel is assigned to whichever plane
    minimises `|residual|` (the same nearest-plane rule `usable_area.obstruction_pixels`
    uses, recomputed directly here rather than imported -- a different concern), and the
    returned value is the signed residual `z - reconstructed` against that plane.

    An unfittable roof has no surface to reconstruct against: `plane_coefs` empty or
    `None` returns an all-NaN array of shape `(n,)`.
    """
    n = len(z)
    if not plane_coefs:
        return np.full(n, np.nan)

    resid = np.empty((n, len(plane_coefs)), dtype=np.float64)
    for j, (a, b, c) in enumerate(plane_coefs):
        resid[:, j] = z - (a * X[:, 0] + b * X[:, 1] + c)

    support_idx = np.argmin(np.abs(resid), axis=1)
    return resid[np.arange(n), support_idx]


def self_consistency(
    residuals: npt.NDArray[np.float64], tol: float = SELF_CONSISTENCY_TOL_M
) -> dict[str, float | int]:
    """Roll up per-pixel reconstruction residuals into a self-consistency summary.

    `residuals` is the signed per-pixel residual (:func:`reconstruct_residuals`'s
    output; NaN pixels are excluded, not treated as failures). Returns
    ``{"rmse": float, "pct_within_tol": float, "n_px": int}``: `rmse` is the root-mean-
    square residual over the non-NaN pixels, `pct_within_tol` is the percentage of
    non-NaN pixels with `|residual| <= tol`, and `n_px` is the non-NaN pixel count.

    An empty or all-NaN `residuals` has nothing to summarise: returns
    `rmse=NaN, pct_within_tol=NaN, n_px=0` rather than raising or emitting a
    numpy "empty slice" warning.
    """
    finite = residuals[~np.isnan(residuals)]
    n_px = int(finite.size)
    if n_px == 0:
        return {"rmse": float(np.nan), "pct_within_tol": float(np.nan), "n_px": 0}

    rmse = float(np.sqrt(np.mean(finite**2)))
    pct_within_tol = float(100.0 * np.mean(np.abs(finite) <= tol))
    return {"rmse": rmse, "pct_within_tol": pct_within_tol, "n_px": n_px}


def compare_self_consistency(
    footprints: gpd.GeoDataFrame, dsm_path: str | Path
) -> gpd.GeoDataFrame:
    """Per-roof DSM self-consistency, multiplane vs the single-plane ransac baseline.

    One row per `footprints` row (same order, index reset). For each footprint, clips
    the DSM (:func:`roof_planes.roof_points`) and fits both the multiplane facets
    (:func:`roof_planes.fit_planes_multi`) and, through the exact same fitter, a single-
    plane baseline (`fit_planes_multi(X, z, max_planes=1)`) -- so the comparison is
    "N facets vs 1," not two different code paths. Each fit's plane coefs reconstruct
    the roof's own DSM pixels (:func:`reconstruct_residuals`), rolled up via
    :func:`self_consistency`.

    Columns: ``geometry`` (the footprint), ``building_id`` (positional index),
    ``n_planes`` (multiplane facet count; 0 if unfittable), ``rmse_multiplane``,
    ``pct_within_multiplane``, ``rmse_ransac``, ``pct_within_ransac``,
    ``rmse_improvement`` (`rmse_ransac - rmse_multiplane`; positive means multiplane
    reconstructs better). An unfittable roof (`fit_planes_multi` returns `[]`) gets
    `n_planes=0` and every metric NaN. `footprints.crs` is preserved.
    """
    records = []
    for building_id, geom in enumerate(footprints.geometry):
        X, z = roof_points(geom, dsm_path)
        multi_planes = fit_planes_multi(X, z)
        single_planes = fit_planes_multi(X, z, max_planes=1)

        multi_coefs = [p["coef"] for p in multi_planes]
        single_coefs = [p["coef"] for p in single_planes]

        multi_metrics = self_consistency(reconstruct_residuals(X, z, multi_coefs))
        single_metrics = self_consistency(reconstruct_residuals(X, z, single_coefs))

        records.append(
            {
                "geometry": geom,
                "building_id": building_id,
                "n_planes": len(multi_planes),
                "rmse_multiplane": multi_metrics["rmse"],
                "pct_within_multiplane": multi_metrics["pct_within_tol"],
                "rmse_ransac": single_metrics["rmse"],
                "pct_within_ransac": single_metrics["pct_within_tol"],
                "rmse_improvement": single_metrics["rmse"] - multi_metrics["rmse"],
            }
        )

    return gpd.GeoDataFrame(records, crs=footprints.crs)
