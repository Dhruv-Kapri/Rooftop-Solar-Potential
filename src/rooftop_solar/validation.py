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

from rooftop_solar import cache, config, pipeline, radiation, roof_planes, usable_area, yield_pv
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


def load_spot_check_sample(spot_dir: str | Path) -> gpd.GeoDataFrame:
    """Rebuild the sample of an earlier spot-check dir (#23): ``sample.csv`` rows, in order, with
    footprints from the ``multiplane`` rows of its ``buildings.parquet``. Raises on a missing id."""
    spot_dir = Path(spot_dir)
    sample = pd.read_csv(spot_dir / "sample.csv", dtype={"building_id": str})
    buildings = gpd.read_parquet(spot_dir / "buildings.parquet")
    footprints = buildings[buildings["method"] == "multiplane"].set_index("building_id").geometry
    missing = sorted(set(sample["building_id"]) - set(footprints.index))
    if missing:
        raise ValueError(
            f"no footprint geometry in {spot_dir / 'buildings.parquet'} for: {missing}"
        )
    geometry = footprints.loc[sample["building_id"]].to_numpy()
    return gpd.GeoDataFrame(sample, geometry=geometry, crs=buildings.crs)


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


def _checked_cache_artifact(
    tiles_dir: Path, row: int, col: int, kind: pipeline.TileArtifact
) -> Path:
    """A tile's cached artifact path (raster or roofs parquet), proven present and sidecar-matched.

    Missing -> `FileNotFoundError` (the cache is populated by `pipeline.run_city`, issue #19,
    never here). Sidecar absent, legacy-format, or not matching the file's bytes (a truncated
    or swapped copy after a cache handoff, issue #18) -> `ValueError` (ADR-0014).
    """
    path = pipeline.tile_cache_path(tiles_dir, row, col, kind)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found; the tile cache must be populated by pipeline.run_city (issue #19)"
        )
    key = cache.read_key_sidecar(path)
    if key is None or not cache.sidecar_matches(path, key):
        raise ValueError(
            f"{path} does not match its cache sidecar (missing, legacy-format, "
            "truncated or swapped); re-run pipeline.run_city to rebuild it (ADR-0014)"
        )
    return path


def rescore_sample(sample: gpd.GeoDataFrame, tiles_dir: str | Path) -> DetailedScore:
    """:func:`detailed_score` over a spot-check sample, one tile raster pair at a time.

    `sample` is :func:`select_spot_check_sample`'s output (`building_id`, `tile_row`,
    `tile_col`, geometry). Rasters come from the `pipeline.run_city` tile cache, named
    `tile_{row}_{col}_dsm.tif` / `tile_{row}_{col}_insolation.tif`
    (`pipeline.tile_cache_path`, ADR-0009/0014); a missing one raises
    `FileNotFoundError` -- the cache is populated by the city run (issue #19), never here.
    A raster whose sidecar is absent, in the legacy format, or no longer matches the file's
    bytes (a truncated or swapped copy after a cache handoff, issue #18) raises `ValueError`
    rather than being read silently. An empty `sample` raises `ValueError`. Per-tile results
    are concatenated.
    """
    if sample.empty:
        raise ValueError("rescore_sample: empty sample -- nothing to re-score")
    tiles_dir = Path(tiles_dir)
    scores = []
    for (row, col), group in sample.groupby(["tile_row", "tile_col"]):
        dsm_path = _checked_cache_artifact(tiles_dir, row, col, "dsm")
        insol_path = _checked_cache_artifact(tiles_dir, row, col, "insolation")
        scores.append(detailed_score(group, dsm_path, insol_path))
    return DetailedScore(
        **{
            f.name: pd.concat([getattr(s, f.name) for s in scores], ignore_index=True)
            for f in dataclasses.fields(DetailedScore)
        }
    )


def tile_self_consistency(tiles_dir: str | Path, row: int, col: int) -> gpd.GeoDataFrame:
    """DSM self-consistency (:func:`compare_self_consistency`) over one cached city tile.

    Reads `tile_{row}_{col}_roofs.parquet` (the production per-building output) and the
    tile's DSM from the `pipeline.run_city` cache; both must pass the same presence and
    sidecar check as :func:`rescore_sample` (`FileNotFoundError` / `ValueError`, ADR-0014),
    so a missing roofs parquet is a `FileNotFoundError` and a swapped or sidecarless one a
    `ValueError`. Returns compare's rows (same order as the parquet) plus
    `tile_row`, `tile_col`, `roof_id` (:func:`spot_check_id` over the parquet's own
    `building_id`, which also overwrites compare's positional `building_id`), `roof_class`
    (from the parquet) and `roof_type` (:func:`roof_type` over compare's `n_planes`, except
    a roof with `n_planes == 0` -- unfittable -- is tagged ``"unfittable"``, not
    gable/flat, #31). Plan §6, risks §14.4, #24.
    """
    tiles_dir = Path(tiles_dir)
    roofs_path = _checked_cache_artifact(tiles_dir, row, col, "roofs")
    dsm_path = _checked_cache_artifact(tiles_dir, row, col, "dsm")
    roofs = gpd.read_parquet(roofs_path)
    out = compare_self_consistency(roofs, dsm_path)
    out["building_id"] = roofs["building_id"].to_numpy()
    out["tile_row"] = row
    out["tile_col"] = col
    out["roof_id"] = [spot_check_id(row, col, b) for b in roofs["building_id"]]
    out["roof_class"] = roofs["roof_class"].to_numpy()
    out["roof_type"] = [
        "unfittable" if n == 0 else roof_type(n, c)
        for n, c in zip(out["n_planes"], out["roof_class"], strict=True)
    ]
    return out


# `ROOF_TYPES` plus the self-consistency-only bin for unfittable roofs (#31)
SELF_CONSISTENCY_TYPES = (*ROOF_TYPES, "unfittable")

_SUMMARY_METRICS = {
    "rmse_multiplane_median": ("rmse_multiplane", 0.5),
    "rmse_ransac_median": ("rmse_ransac", 0.5),
    "rmse_improvement_median": ("rmse_improvement", 0.5),
    "rmse_improvement_p90": ("rmse_improvement", 0.9),
    "pct_within_multiplane_median": ("pct_within_multiplane", 0.5),
    "pct_within_ransac_median": ("pct_within_ransac", 0.5),
}


def summarize_self_consistency(per_roof: pd.DataFrame) -> pd.DataFrame:
    """Per-roof-type rollup of :func:`tile_self_consistency` rows (plan §6, #24).

    One row per type in `SELF_CONSISTENCY_TYPES` order (unfittable roofs get their own
    bin, #31), then ``"all"``; index named `roof_type`. `n_roofs` counts every row in the
    bin, `n_unfittable` those with NaN `rmse_multiplane`;
    the median / p90 metrics are over fittable rows only. A type with no rows still appears,
    with zero counts and NaN metrics.
    """
    rows = {}
    for name in (*SELF_CONSISTENCY_TYPES, "all"):
        group = per_roof if name == "all" else per_roof[per_roof["roof_type"] == name]
        fit = group[group["rmse_multiplane"].notna()]
        row = {"n_roofs": len(group), "n_unfittable": len(group) - len(fit)}
        for col, (src, q) in _SUMMARY_METRICS.items():
            # Series.quantile of an empty series is NaN without a numpy warning
            row[col] = float(fit[src].quantile(q))
        rows[name] = row
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("roof_type")


# --- Spot-check comparison (Part 2-4 step 7b; plan §6, ADR-0013) ---------------------

# A model plane count agrees with the hand label when within this many planes of it.
# Hand labels above `roof_planes.MAX_PLANES` are clamped to it before comparing: the fitter
# can never emit more, so such a roof tests the cap / merged footprints, not the fitter
# (#23; docs/benchmarks/spot_check/README.md "Scoring for the stop rule"). Raw agreement
# stays visible.
PLANE_AGREE_TOL = 1
# The labeller's opt-out: a roof whose `notes` start with this (any case) is left out of the
# comparison -- e.g. "EXCLUDE (likely not a roof)" for a footprint/DSM mismatch.
EXCLUDE_NOTE = "EXCLUDE"
_OBSTRUCTION_WORDS = {
    "y": True, "yes": True, "true": True, "1": True,
    "n": False, "no": False, "false": False, "0": False,
}  # fmt: skip


def _parse_obstruction(value: object) -> bool | None:
    """y/yes/true/1 -> True, n/no/false/0 -> False (case/whitespace-insensitive); else None."""
    return _OBSTRUCTION_WORDS.get(str(value).strip().lower())


def _parse_plane_count(value: object) -> int | None:
    """An integer >= 1 (text or number), else None."""
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if not np.isfinite(number) or number != int(number) or number < 1:
        return None
    return int(number)


def _obstruction_outcome(label: bool, model: bool) -> str:
    """Confusion-matrix name of a (label, model) obstruction pair (#29)."""
    return {
        (True, True): "hit",
        (True, False): "miss",
        (False, True): "false_positive",
        (False, False): "true_negative",
    }[(label, model)]


def _is_excluded(labels: pd.DataFrame) -> pd.Series:
    """Rows whose `notes` start with :data:`EXCLUDE_NOTE` (any case, leading space ok)."""
    if "notes" not in labels:
        return pd.Series(False, index=labels.index)
    return (
        labels["notes"].fillna("").astype(str).str.strip().str.upper().str.startswith(EXCLUDE_NOTE)
    )


def excluded_labels(labels: pd.DataFrame) -> list[str]:
    """The `building_id`s :func:`compare_labels` leaves out by their `notes` (sorted)."""
    return sorted(labels.loc[_is_excluded(labels), "building_id"].astype(str).str.strip())


def _parse_labels(labels: pd.DataFrame, scored_ids: set[str]) -> pd.DataFrame:
    """Validated labels: `building_id` (str), `label_planes` (int), `label_obstruction` (bool).

    Raises a `ValueError` naming the offending building_ids for duplicate ids, ids the
    score does not contain, and blank/invalid `n_major_planes` / `has_obstruction`. Rows
    whose `notes` start with :data:`EXCLUDE_NOTE` are dropped before any check.
    """
    labels = labels[~_is_excluded(labels)]
    ids = labels["building_id"].astype(str).str.strip()
    duplicated = sorted(set(ids[ids.duplicated()]))
    if duplicated:
        raise ValueError(f"duplicate building_id in labels: {duplicated}")
    unknown = sorted(set(ids) - scored_ids)
    if unknown:
        raise ValueError(f"labelled building_id not in the re-score: {unknown}")
    planes = [_parse_plane_count(v) for v in labels["n_major_planes"]]
    obstruction = [_parse_obstruction(v) for v in labels["has_obstruction"]]
    for column, parsed in (("n_major_planes", planes), ("has_obstruction", obstruction)):
        bad = [i for i, p in zip(ids, parsed, strict=True) if p is None]
        if bad:
            raise ValueError(f"blank or invalid {column} (unfinished labels?) for: {bad}")
    return pd.DataFrame(
        {"building_id": ids.to_numpy(), "label_planes": planes, "label_obstruction": obstruction}
    )


def compare_labels(labels: pd.DataFrame, score: DetailedScore) -> pd.DataFrame:
    """Score each hand-labelled roof against each :data:`SPOT_CHECK_METHODS` method.

    `labels` is the raw hand-filled CSV frame (`LABEL_COLUMNS`; a `notes` starting with
    :data:`EXCLUDE_NOTE` drops the roof, other notes are ignored);
    `has_obstruction` takes y/yes/true/1 or n/no/false/0, `n_major_planes` an integer >= 1.
    Buildings scored but not labelled are ignored. One row per (building, method), sorted
    by `building_id` then method order:

    - `label_planes` (raw), `label_planes_capped` (`min(label, roof_planes.MAX_PLANES)`, the
      same cap for every method -- see the clamp note above :data:`PLANE_AGREE_TOL`),
      `model_planes` (`score.buildings.n_planes`, but 0 for an unfittable roof -- NaN
      `tilt_deg`, where the fitter emits a placeholder `n_planes=1`),
      `plane_diff` = model - capped label, `plane_agree` = `|plane_diff| <= PLANE_AGREE_TOL`,
      `plane_exact` = `model_planes == label_planes_capped` (#30: reported next to the +/-1
      rule so a change that collapses a 2-plane gable to 1 can't score as a win),
      `plane_agree_raw` = the same rule against the unclamped label.
    - `label_obstruction`, `model_obstruction` (any `score.obstructions` row with area > 0),
      `obstruction_outcome`: "hit", "miss", "false_positive" or "true_negative".
    - `large_planar_obstruction`: the label says obstruction and the model fit more planes
      than labelled -- the obstruction was fit as its own facet and escaped subtraction
      (ADR-0013). `extra_planes` is the surplus and `escaped_usable_m2` the usable area
      those planes carry, i.e. roof counted that should have been subtracted. Assumes the
      surplus planes are the smallest (by `plane_area_m2`, ties on `plane_id`), i.e. the
      superstructures; 0 / 0.0 when not flagged.
    """
    buildings = score.buildings.set_index(["building_id", "method"])
    parsed = _parse_labels(labels, set(score.buildings["building_id"].astype(str)))
    real = score.obstructions[score.obstructions["obstruction_area_m2"] > 0]
    obstructed = set(zip(real["building_id"], real["method"], strict=True))
    rows = []
    for label in parsed.sort_values("building_id").itertuples():
        for method in SPOT_CHECK_METHODS:
            key = (label.building_id, method)
            built = buildings.loc[key]
            model_planes = 0 if pd.isna(built["tilt_deg"]) else int(built["n_planes"])
            model_obstruction = key in obstructed
            label_capped = min(label.label_planes, roof_planes.MAX_PLANES)
            diff = model_planes - label_capped
            flagged = label.label_obstruction and diff > 0
            escaped_usable_m2 = 0.0
            if flagged:
                surplus = (
                    score.planes[
                        (score.planes["building_id"] == label.building_id)
                        & (score.planes["method"] == method)
                    ]
                    .sort_values(["plane_area_m2", "plane_id"])
                    .head(diff)
                )
                escaped_usable_m2 = float(surplus["usable_area_m2"].sum())
            rows.append(
                {
                    "building_id": label.building_id,
                    "method": method,
                    "label_planes": label.label_planes,
                    "label_planes_capped": label_capped,
                    "model_planes": model_planes,
                    "plane_diff": diff,
                    "plane_agree": abs(diff) <= PLANE_AGREE_TOL,
                    "plane_exact": model_planes == label_capped,
                    "plane_agree_raw": abs(model_planes - label.label_planes) <= PLANE_AGREE_TOL,
                    "label_obstruction": label.label_obstruction,
                    "model_obstruction": model_obstruction,
                    "obstruction_outcome": _obstruction_outcome(
                        label.label_obstruction, model_obstruction
                    ),
                    "large_planar_obstruction": flagged,
                    "extra_planes": diff if flagged else 0,
                    "escaped_usable_m2": escaped_usable_m2,
                }
            )
    return pd.DataFrame(rows)


def summarize_spot_check(comparison: pd.DataFrame) -> pd.DataFrame:
    """Per-method roll-up of :func:`compare_labels` -- the validation write-up table.

    Indexed by `method` (:data:`SPOT_CHECK_METHODS` order): `n_roofs`, `plane_agree` (count)
    and `plane_agree_pct` (0-100) against the clamped label, `plane_exact` / `plane_exact_pct`
    (same, exact match -- #30), `plane_agree_raw` /
    `plane_agree_raw_pct` against the raw label, the four obstruction outcome counts,
    `large_planar_obstructions` (flagged roofs) and their summed `escaped_usable_m2`.
    """
    rows = {}
    for method in SPOT_CHECK_METHODS:
        m = comparison[comparison["method"] == method]
        outcome = m["obstruction_outcome"]
        rows[method] = {
            "n_roofs": len(m),
            "plane_agree": int(m["plane_agree"].sum()),
            "plane_agree_pct": 100.0 * m["plane_agree"].mean() if len(m) else float("nan"),
            "plane_exact": int(m["plane_exact"].sum()),
            "plane_exact_pct": 100.0 * m["plane_exact"].mean() if len(m) else float("nan"),
            "plane_agree_raw": int(m["plane_agree_raw"].sum()),
            "plane_agree_raw_pct": (
                100.0 * m["plane_agree_raw"].mean() if len(m) else float("nan")
            ),
            "obstruction_hits": int((outcome == "hit").sum()),
            "obstruction_misses": int((outcome == "miss").sum()),
            "obstruction_false_positives": int((outcome == "false_positive").sum()),
            "obstruction_true_negatives": int((outcome == "true_negative").sum()),
            "large_planar_obstructions": int(m["large_planar_obstruction"].sum()),
            "escaped_usable_m2": float(m["escaped_usable_m2"].sum()),
        }
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("method")
