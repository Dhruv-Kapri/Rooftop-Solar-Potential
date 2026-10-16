"""Stage — validation metrics (plan §6, risks §14.4).

Ground-truth-free accuracy checks for the Part 2-4 geometry: DSM self-consistency
(reconstruct the DSM from the fitted planes; per-roof residual), multiplane vs the
single-plane ransac baseline. The spot-check sampler + labels template (step 7b) live here too.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import pandas as pd
from shapely.geometry.base import BaseGeometry

from rooftop_solar import config, pipeline, radiation, roof_planes, usable_area, yield_pv
from rooftop_solar.roof_planes import fit_planes_multi, roof_points
from rooftop_solar.tiling import tile_index

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


# --- Spot-check sampling (Part 2-4 step 7b; plan §6) ---------------------------------

# Skip sheds/garages too small for a human to label a roof structure on.
SPOT_CHECK_MIN_AREA_M2 = 50.0
# n_planes at/above which a roof counts as multi-facet.
MULTI_PLANE_MIN = 3
ROOF_TYPES = ("flat", "gable", "hip", "complex")
# The hand-label CSV schema: the building plus what the labeller sees on the imagery.
LABEL_COLUMNS = ("building_id", "n_major_planes", "has_obstruction", "notes")


def roof_type(n_planes: int, roof_class: str) -> str:
    """Stratification proxy for roof archetype from the model's own output (plan §6).

    There is no ground truth, so this only spreads the sample across archetypes: flat /
    gable (1-2 pitched facets) / hip (3+ pitched facets) / complex (flat-dominant with
    extra planes -- penthouses, mechanical, mixed; the ADR-0013 facet-vs-obstruction cases).
    """
    multi = n_planes >= MULTI_PLANE_MIN
    if roof_class == "flat":
        return "complex" if multi else "flat"
    return "hip" if multi else "gable"


def spot_check_id(tile_row: int, tile_col: int, building_id: int) -> str:
    """City-unique, stable building id (`building_id` is only unique within its tile)."""
    return f"{tile_row}_{tile_col}_{building_id}"


def select_spot_check_sample(
    city: gpd.GeoDataFrame,
    areas: dict[str, BaseGeometry],
    *,
    per_cell: int = 3,
    seed: int = 20,
    tile_size_m: float = config.TILE_SIZE_M,
    origin: tuple[float, float] = config.GRID_ORIGIN,
    min_area_m2: float = SPOT_CHECK_MIN_AREA_M2,
) -> gpd.GeoDataFrame:
    """Stratified, deterministic sample of roofs for the hand spot-check (plan §6).

    `areas` maps name -> polygon in `city`'s CRS; a building is in an area if its
    representative point is within it, and goes to the first matching area (dict order).
    Candidates need a fitted roof (`tilt_deg` not NaN) and `footprint_area_m2 >=
    min_area_m2`. From each (area, `roof_type`) cell, `min(per_cell, n)` are drawn with a
    per-cell rng seeded `[seed, area_index, type_index]` over id-sorted candidates, so the
    result is independent of input row order and of other cells' sizes. The per-tile int
    `building_id` is kept as `tile_building_id`; `building_id` becomes the city-unique
    `spot_check_id` (tile cache files are keyed by the same row/col, ADR-0009).
    """
    cols = [
        "building_id",
        "area",
        "roof_type",
        "tile_row",
        "tile_col",
        "tile_building_id",
        "n_planes",
        "roof_class",
        "footprint_area_m2",
        "geometry",
    ]
    reps = city.geometry.representative_point()
    area_of = pd.Series(pd.NA, index=city.index, dtype=object)
    for name, poly in areas.items():
        free = area_of.isna() & reps.within(poly)
        area_of[free] = name

    keep = area_of.notna() & city["tilt_deg"].notna() & (city["footprint_area_m2"] >= min_area_m2)
    cand = city[keep].copy()
    cand["area"] = area_of[keep]
    cand["tile_building_id"] = cand["building_id"]
    rc = [tile_index(p, tile_size_m, origin) for p in reps[keep]]
    cand["tile_row"] = [r for r, _ in rc]
    cand["tile_col"] = [c for _, c in rc]
    cand["building_id"] = [
        spot_check_id(r, c, b)
        for r, c, b in zip(
            cand["tile_row"], cand["tile_col"], cand["tile_building_id"], strict=True
        )
    ]
    cand["roof_type"] = [
        roof_type(n, c) for n, c in zip(cand["n_planes"], cand["roof_class"], strict=True)
    ]

    picked = []
    for ai, name in enumerate(areas):
        for ti, rtype in enumerate(ROOF_TYPES):
            cell = cand[(cand["area"] == name) & (cand["roof_type"] == rtype)]
            cell = cell.sort_values("building_id")
            take = min(per_cell, len(cell))
            if take == 0:
                continue
            idx = np.random.default_rng([seed, ai, ti]).choice(len(cell), take, replace=False)
            picked.append(cell.iloc[np.sort(idx)])

    if not picked:
        return gpd.GeoDataFrame(columns=cols, geometry="geometry", crs=city.crs)
    return gpd.GeoDataFrame(pd.concat(picked)[cols], crs=city.crs).reset_index(drop=True)


def write_labels_template(sample: gpd.GeoDataFrame | pd.DataFrame, path: str | Path) -> Path:
    """Write the blank hand-label CSV (`LABEL_COLUMNS`), `building_id` pre-filled.

    Refuses to overwrite: an existing file may hold hand-entered labels.
    """
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} exists; refusing to overwrite hand-entered labels")
    path.parent.mkdir(parents=True, exist_ok=True)
    out = pd.DataFrame({c: "" for c in LABEL_COLUMNS}, index=range(len(sample)))
    out["building_id"] = list(sample["building_id"])
    out.to_csv(path, index=False)
    return path


# --- Spot-check detailed re-score (Part 2-4 step 7b; plan §6, ADR-0011/0013) ---------

# Method label -> `max_planes` for the re-score. "ransac" is the single-plane BASELINE fit
# through the same multiplane fitter and the same obstruction-aware downstream chain -- not
# the Phase-1 `fit_roof_planes(method="ransac")` / footprint-mean / flat-0.70 path -- so the
# comparison is "N facets vs 1" and nothing else differs.
SPOT_CHECK_METHODS = {"multiplane": roof_planes.MAX_PLANES, "ransac": 1}


@dataclasses.dataclass(frozen=True)
class DetailedScore:
    """The detail the production collapse discards, for the spot-check (all long format).

    Every frame has a `method` column (a :data:`SPOT_CHECK_METHODS` key) and the caller's
    `building_id`. `planes`: one row per (building, plane) -- `yield_pv.plane_yields` output
    minus the array-valued `coef`/`inlier_xy` (so it is parquet-writable), footprint
    geometry. `obstructions`: one row per plane with obstruction pixels (`building_id,
    method, plane_id, obstruction_area_m2, geometry` = the obstruction polygon). `membership`:
    a plain DataFrame, one row per footprint DSM pixel per method (`building_id, method, x,
    y, z, plane_id, is_obstruction`; `plane_id` is the nearest plane, -1 for an unfittable
    roof). `buildings`: `yield_pv.collapse_to_buildings` output, i.e. what production emits.
    """

    planes: gpd.GeoDataFrame
    obstructions: gpd.GeoDataFrame
    membership: pd.DataFrame
    buildings: gpd.GeoDataFrame


def _membership(
    footprints: gpd.GeoDataFrame, dsm_path: str | Path, fit: gpd.GeoDataFrame
) -> pd.DataFrame:
    """Per-pixel plane assignment + obstruction flag for one method's fit (positional ids)."""
    frames = []
    for building_id, geom in enumerate(footprints.geometry):
        X, z = roof_points(geom, dsm_path)
        group = fit[fit["building_id"] == building_id].sort_values("plane_id")
        if group.iloc[0]["coef"] is None:
            plane_id = np.full(len(z), -1)
            is_obstruction = np.zeros(len(z), dtype=bool)
        else:
            plane_id, is_obstruction = usable_area.obstruction_pixels(
                X, z, list(group["coef"]), usable_area.OBSTRUCTION_TAU_M
            )
        frames.append(
            pd.DataFrame(
                {
                    "building_id": building_id,
                    "x": X[:, 0],
                    "y": X[:, 1],
                    "z": z,
                    "plane_id": plane_id,
                    "is_obstruction": is_obstruction,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def detailed_score(
    footprints: gpd.GeoDataFrame, dsm_path: str | Path, insol_path: str | Path
) -> DetailedScore:
    """Re-score `footprints` per :data:`SPOT_CHECK_METHODS`, keeping per-plane detail.

    `footprints` needs a `building_id` column (any hashable ids, e.g. the spot-check
    "7_2_15" strings) and geometry in the rasters' CRS. Per method this is the production
    multiplane chain (`pipeline._score_roofs`, ADR-0011/0012/0013) with the facet cap swapped:
    `fit_roof_planes(method="multiplane", max_planes=k)` -> `radiation.plane_poa` ->
    `usable_area.detect_obstructions` -> `usable_area.usable_area(obstructions=...)` ->
    `yield_pv.plane_yields` -> `collapse_to_buildings`. The fitter emits positional ids; they
    are mapped back to the caller's.

    In `buildings`, `suitability` is ranked within the given footprints only (not the city
    rank); every other column is per-building and independent of which others are scored.
    """
    footprints = footprints.reset_index(drop=True)
    ids = footprints["building_id"]
    planes, obstructions, membership, buildings = [], [], [], []
    for method, max_planes in SPOT_CHECK_METHODS.items():
        fit = roof_planes.fit_roof_planes(
            footprints, dsm_path, method="multiplane", max_planes=max_planes
        )
        fit = radiation.plane_poa(fit, insol_path)
        obs = usable_area.detect_obstructions(fit, dsm_path)
        yields = yield_pv.plane_yields(usable_area.usable_area(fit, obstructions=obs))

        def label(df, method=method):
            return df.assign(building_id=df["building_id"].map(ids), method=method)

        planes.append(label(yields.drop(columns=["coef", "inlier_xy"])))
        has_obs = obs["obstruction_area_m2"] > 0
        obstructions.append(
            label(
                gpd.GeoDataFrame(
                    {
                        "building_id": fit.loc[has_obs, "building_id"],
                        "plane_id": fit.loc[has_obs, "plane_id"],
                        "obstruction_area_m2": obs.loc[has_obs, "obstruction_area_m2"],
                    },
                    geometry=obs.loc[has_obs, "geometry"],
                    crs=obs.crs,
                )
            )[["building_id", "method", "plane_id", "obstruction_area_m2", "geometry"]]
        )
        membership.append(label(_membership(footprints, dsm_path, fit)))
        buildings.append(label(yield_pv.collapse_to_buildings(yields)))

    def stack(frames):
        return pd.concat(frames, ignore_index=True)

    membership_cols = ["building_id", "method", "x", "y", "z", "plane_id", "is_obstruction"]
    return DetailedScore(
        planes=stack(planes),
        obstructions=stack(obstructions),
        membership=stack(membership)[membership_cols],
        buildings=stack(buildings),
    )


def rescore_sample(sample: gpd.GeoDataFrame, tiles_dir: str | Path) -> DetailedScore:
    """:func:`detailed_score` over a spot-check sample, one tile raster pair at a time.

    `sample` is :func:`select_spot_check_sample`'s output (`building_id`, `tile_row`,
    `tile_col`, geometry). Rasters come from the `pipeline.run_city` tile cache, named
    `tile_{row}_{col}_dsm.tif` / `tile_{row}_{col}_insolation.tif`
    (`pipeline.tile_cache_path`, ADR-0009/0014); a missing one raises
    `FileNotFoundError` -- the cache is populated by the city run (issue #19), never here.
    An empty `sample` raises `ValueError`. Per-tile results are concatenated.
    """
    if sample.empty:
        raise ValueError("rescore_sample: empty sample -- nothing to re-score")
    tiles_dir = Path(tiles_dir)
    scores = []
    for (row, col), group in sample.groupby(["tile_row", "tile_col"]):
        dsm_path = pipeline.tile_cache_path(tiles_dir, row, col, "dsm")
        insol_path = pipeline.tile_cache_path(tiles_dir, row, col, "insolation")
        for path in (dsm_path, insol_path):
            if not path.exists():
                raise FileNotFoundError(
                    f"{path} not found; the tile cache must be populated by "
                    "pipeline.run_city (issue #19)"
                )
        scores.append(detailed_score(group, dsm_path, insol_path))
    return DetailedScore(
        **{
            f.name: pd.concat([getattr(s, f.name) for s in scores], ignore_index=True)
            for f in dataclasses.fields(DetailedScore)
        }
    )
