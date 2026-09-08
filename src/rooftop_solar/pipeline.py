"""Stage-1 orchestrator: the whole Glover Park spine, footprints -> per-roof yield.

Wires the stage modules in order (README mermaid; stage-1-plan.md §4):

    footprints -> DSM -> radiation (shaded) -> roof planes -> zonal insolation
      -> usable area -> PV yield + suitability -> GeoPackage + choropleth

Each stage is a pure GeoDataFrame/Path in -> out; the one GRASS session is owned by the
radiation stage. Nothing here re-derives buffering or CRS logic — that lives in aoi.py/dsm.py.

Two AOIs matter (risks §8, trap 2): the **buffered** AOI is what the DSM and the r.sun
insolation cover, so buildings just outside the frame still cast shadows into it; the
**core** AOI is what we actually report. Footprints are fetched over the buffered frame but
only whole footprints whose representative point falls in the core are scored and mapped —
and the suitability percentile (ADR-0004) is therefore computed over that core reporting set,
so the choropleth describes the neighbourhood, not the buffer ring.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform

from rooftop_solar import (
    aggregate,
    aoi,
    config,
    dsm,
    footprints,
    radiation,
    roof_planes,
    tiling,
    tracts,
    usable_area,
    yield_pv,
)

GEOPACKAGE_NAME = "glover_park_roofs.gpkg"
CHOROPLETH_NAME = "glover_park_suitability.png"

# Part 2-1 tract-aggregation deliverables (stage-2-part1-plan.md §9).
TRACT_GEOPACKAGE_NAME = "glover_park_tracts.gpkg"
POTENTIAL_CHOROPLETH_NAME = "glover_park_tract_potential.png"
EQUITY_CHOROPLETH_NAME = "glover_park_tract_equity.png"
EQUITY_SCATTER_NAME = "glover_park_tract_scatter.png"


def run_stage1(
    *,
    core: BaseGeometry | None = None,
    buffered: BaseGeometry | None = None,
    footprints_source: str = "ms-buildings",
    day_range: list[int] | None = None,
    method: str = "multiplane",
    output_dir: str | Path | None = None,
    write_outputs: bool = True,
) -> gpd.GeoDataFrame:
    """Run the Stage-1 pipeline and return the per-roof result GeoDataFrame.

    Args:
        core / buffered: the reporting and shadow-caster AOIs (EPSG:4326). Default to the
            config Glover Park frame via aoi.py; overridable for a small integration smoke.
        footprints_source: "ms-buildings" (canonical) or "osm" (documented fallback).
        day_range: r.sun days. None = the calibrated 12 mid-month days (ADR-0001); a short
            list is a fast, non-calibrated smoke path (see radiation.surface_irradiance).
        method: geometry front-end — "multiplane" (Part 2-4 production; per-facet + obstruction
            -aware, ADR-0011/0012/0013) or "ransac" (Phase-1 single-plane baseline, kept for the
            validation comparison, plan §6). See :func:`_score_roofs`.
        output_dir: where the GeoPackage + choropleth go (default config.OUTPUTS_DIR).
        write_outputs: if False, compute the result but write no files.

    The returned GeoDataFrame (working CRS) carries geometry + tilt/aspect + usable area +
    capacity/energy/CO₂ + within-AOI suitability (ADR-0004). The "multiplane" default adds
    ``n_planes``/``low_confidence`` and is one row per building (facets collapsed, ADR-0011);
    "ransac" is one row per footprint with the ``inlier_ratio``/``n_px`` uncertainty columns
    (ADR-0002).
    """
    if core is None:
        core = aoi.core_aoi_wgs84()
    if buffered is None:
        buffered = aoi.buffered_aoi()

    # Footprints over the buffered frame, in the metric working CRS; report only the whole
    # footprints whose representative point lies in the core (never geometrically clipped,
    # which would chop boundary-straddling buildings).
    fp = footprints.load_footprints(buffered, source=footprints_source).to_crs(config.WORKING_CRS)
    core_working = _to_working_crs(core)
    fp_core = select_by_membership(fp, lambda pts: pts.within(core_working))

    # DSM + shaded r.sun over the buffered frame -> roof planes -> usable -> yield (traps 1-2).
    result = _score_roofs(fp_core, buffered, day_range=day_range, method=method)

    if write_outputs:
        out_dir = Path(output_dir) if output_dir is not None else config.OUTPUTS_DIR
        write_geopackage(result, out_dir / GEOPACKAGE_NAME)
        render_choropleth(result, out_dir / CHOROPLETH_NAME)

    return result


def _score_roofs(
    fp_core: gpd.GeoDataFrame,
    buffered_wgs84: BaseGeometry,
    *,
    day_range: list[int] | None,
    dsm_out_path: str | Path | None = None,
    method: str = "multiplane",
) -> gpd.GeoDataFrame:
    """The shared per-AOI scoring spine both scale paths run **identically** (ADR-0009).

    DSM + shaded `r.sun` over the BUFFERED frame (so casters just outside still shade edge
    roofs — traps 1-2) -> roof planes -> per-roof insolation -> usable area -> PV yield.
    Stage-1 (`run_stage1`) passes its WGS84 buffered bbox and the default DSM path; the city
    runner (`run_city`) passes a tile's buffered extent and a per-tile DSM `out_path`
    (transient, so tiles don't clobber one another). Keeping this one function is what
    guarantees the neighbourhood and the city produce identical downstream results for the
    same roof — the "radiation -> yield is identical downstream" architecture invariant.

    `method` selects the geometry front-end and its downstream chain (both end in the same
    per-building schema — the "swappable geometry front-end" invariant, architecture §5):

    - ``"multiplane"`` (default, Part 2-4 production — ADR-0011/0012/0013): multi-facet
      RANSAC -> **per-plane** POA (`radiation.plane_poa`, over each facet's own pixels,
      not the diluted footprint mean) -> obstruction-aware per-plane usable area
      (`detect_obstructions` + `usable_area(obstructions=…)`, replacing the flat 0.70) ->
      per-plane yield summed into one row per building (`collapse_to_buildings`).
    - ``"ransac"`` (Phase-1 single-plane baseline, kept for the Part 2-4 validation
      comparison — plan §6): one plane/footprint -> footprint-mean POA
      (`zonal_insolation`) -> flat-fraction usable area -> per-footprint yield.
    """
    dsm_path = dsm.build_dsm(buffered_wgs84, out_path=dsm_out_path)
    insol_path = radiation.surface_irradiance(dsm_path, day_range=day_range)
    if method == "ransac":
        planes = roof_planes.fit_roof_planes(fp_core, dsm_path, method="ransac")
        planes = radiation.zonal_insolation(planes, insol_path)
        usable = usable_area.usable_area(planes)
        return yield_pv.estimate_yield(usable)
    if method == "multiplane":
        planes = roof_planes.fit_roof_planes(fp_core, dsm_path, method="multiplane")
        planes = radiation.plane_poa(planes, insol_path)
        obstructions = usable_area.detect_obstructions(planes, dsm_path)
        usable = usable_area.usable_area(planes, obstructions=obstructions)
        return yield_pv.collapse_to_buildings(yield_pv.plane_yields(usable))
    raise NotImplementedError(
        f"_score_roofs(method={method!r}) — only 'multiplane' (production) and 'ransac' "
        "(baseline) are wired into the pipeline."
    )


def select_by_membership(
    fp: gpd.GeoDataFrame, membership: Callable[[gpd.GeoSeries], pd.Series]
) -> gpd.GeoDataFrame:
    """Return the whole footprints whose representative point satisfies ``membership``.

    The shared core-selection step both scale paths use (ADR-0009): Stage-1 passes a
    ``.within(core)`` predicate over its single bbox; the city runner (:func:`run_city`,
    Part 2-2) passes ``floor`` tile membership. A footprint is reported **whole or not at
    all**, keyed on where its representative point lands — never geometrically clipped, which
    would chop a building straddling the boundary. The index is reset so downstream
    positional steps (roof-plane fitting, zonal joins) stay aligned.

    Args:
        fp: footprints in ``config.WORKING_CRS``.
        membership: a predicate over the representative-point GeoSeries returning a boolean
            mask (one entry per footprint) — e.g. ``lambda pts: pts.within(core)``.
    """
    mask = membership(fp.geometry.representative_point())
    return fp[mask].reset_index(drop=True)


def merge_tile_roofs(tile_roofs: Iterable[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    """Stack per-tile roof frames into one city frame (Part 2-2, ADR-0009).

    Exactly-once already holds by construction upstream — every footprint's representative
    point maps to exactly one `(row, col)` via `tiling.tile_membership`'s `floor` partition,
    so no tile's `select_by_membership` output can double-count or drop a building relative
    to any other tile's. That means merging is **just a concatenation**: no dedup, no
    reconciliation pass, no geometry union — see stage-2-part2-plan.md §5's conservation
    invariant and ADR-0009's "conservation is provable, not probabilistic".

    Args:
        tile_roofs: per-tile roof `GeoDataFrame`s (e.g. one per `tiling.Tile`, from
            `select_by_membership`), each in `config.WORKING_CRS`. Materialized eagerly
            (a generator is consumed once here), so any lazy per-tile compute upstream must
            already be done by the time this is called.

    Returns:
        One `GeoDataFrame` — non-empty frames concatenated with a fresh `0..n-1` index,
        `geometry="geometry"`, and the shared CRS. If every frame is empty, the first
        frame is returned unchanged (schema-preserving); if there are no frames at all,
        an empty `GeoDataFrame()` is returned.

    Raises:
        ValueError: if non-empty frames disagree on `.crs` — merging mixed CRSs would
        silently produce nonsense geometry.
    """
    frames = list(tile_roofs)
    if not frames:
        return gpd.GeoDataFrame()

    nonempty = [f for f in frames if len(f) > 0]
    if not nonempty:
        return frames[0]

    crs = nonempty[0].crs
    if any(f.crs != crs for f in nonempty[1:]):
        raise ValueError("merge_tile_roofs: non-empty tile frames disagree on CRS")

    merged = pd.concat(nonempty, ignore_index=True)
    return gpd.GeoDataFrame(merged, geometry="geometry", crs=crs)


def recompute_city_suitability(city: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Recompute the ADR-0004 suitability percentile over the WHOLE merged city set.

    ``suitability`` is a *within-AOI* percentile rank (ADR-0004). Scored tile-by-tile it would
    silently become a *within-tile* rank — a roof at one tile's 90th percentile isn't
    comparable to another tile's — so the city per-roof choropleth would be quietly wrong. At
    city scale "within-AOI" means within-DC, so once the tiles are merged this recomputes the
    percentile once over the entire District's roofs (decision locked with the user, 2026-09-07;
    a gap the plan didn't cover). ``run_aggregation`` uses none of ``suitability`` (only the
    physical extensive columns), so the equity payoff is unaffected — this only fixes the
    secondary per-roof city map. Reuses ``yield_pv.suitability_score`` unchanged — the same
    function ``estimate_yield`` applies per tile, now over the merged frame.
    """
    if len(city) == 0:
        return city
    city = city.copy()
    city["suitability"] = yield_pv.suitability_score(
        city["energy_density_kwh_m2"].to_numpy(),
        city["usable_area_m2"].to_numpy(),
    )
    return city


def _to_working_crs(geom_wgs84: BaseGeometry) -> BaseGeometry:
    """Reproject a WGS84 geometry into config.WORKING_CRS (metric)."""
    to_working = Transformer.from_crs(
        "EPSG:4326", config.WORKING_CRS, always_xy=True
    ).transform
    return shp_transform(to_working, geom_wgs84)


def write_geopackage(result: gpd.GeoDataFrame, path: str | Path) -> Path:
    """Write the per-roof result to a GeoPackage (layer 'roofs'); return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    result.to_file(path, driver="GPKG", layer="roofs")
    return path


def render_choropleth(result: gpd.GeoDataFrame, path: str | Path) -> Path:
    """Render the suitability choropleth (0-100 percentile rank) to a PNG; return the path.

    matplotlib is imported lazily with the Agg backend so importing this module — and the
    non-mapping pipeline path — never requires a display.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(9, 9))
    result.plot(
        ax=ax,
        column="suitability",
        cmap="RdYlGn",
        vmin=0,
        vmax=100,
        legend=True,
        edgecolor="black",
        lw=0.2,
    )
    ax.set_title("Glover Park — rooftop solar suitability (within-AOI percentile rank)")
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
# Stage 7 — census-tract aggregation + equity overlay (Phase 2, Part 2-1)     #
# --------------------------------------------------------------------------- #

def run_aggregation(
    *,
    buildings: gpd.GeoDataFrame | str | Path | None = None,
    tracts_gdf: gpd.GeoDataFrame | None = None,
    acs: pd.DataFrame | None = None,
    energy_burden: pd.DataFrame | None = None,
    state_fips: str = config.DC_STATE_FIPS,
    output_dir: str | Path | None = None,
    area_name: str = "Glover Park",
    file_prefix: str = "glover_park",
    write_outputs: bool = True,
) -> gpd.GeoDataFrame:
    """Roll the per-roof Stage-1 result up to census tracts + equity overlay (plan §4).

    Mirrors :func:`run_stage1`: composes the three pure transforms from ``aggregate.py``
    (roll-up → equity join → quadrant classification) and writes the tract deliverables.

    Args:
        buildings: the per-roof input — a GeoDataFrame, a path to a GeoPackage (layer
            ``roofs``), or None to default to the Stage-1 output (``config.OUTPUTS_DIR /
            GEOPACKAGE_NAME``). Must be in ``config.WORKING_CRS``.
        tracts_gdf / acs / energy_burden: the census spine. Each defaults to a network fetch
            via ``tracts.py`` when None; inject them to run offline (and to decouple from the
            DOE LEAD burden-methodology question — plan §11).
        state_fips: state FIPS for the network loaders (default DC).
        output_dir: where the tract GeoPackage + choropleths go (default config.OUTPUTS_DIR).
        area_name / file_prefix: label the deliverables for the study area — the map titles
            (``area_name``) and the output filenames (``<file_prefix>_tracts.gpkg`` etc.). Default
            to Glover Park (Part 2-1); the city runner passes DC values so a whole-District run
            doesn't write Glover-Park-named files (Part 2-2).
        write_outputs: if False, compute the result but write no files.

    Returns the per-tract GeoDataFrame (working CRS): tract geometry + extensive sums +
    intensive summaries + equity attributes + ``equity_class`` / ``is_priority`` (ADR-0007).
    """
    buildings = _load_buildings(buildings)
    if tracts_gdf is None:
        tracts_gdf = tracts.load_tracts(state_fips=state_fips)
    if acs is None:
        acs = tracts.load_acs(state_fips=state_fips)
    if energy_burden is None:
        energy_burden = tracts.load_energy_burden(state_fips=state_fips)

    result = aggregate.aggregate_to_tracts(buildings, tracts_gdf)
    result = aggregate.attach_equity(result, acs, energy_burden)
    result = aggregate.classify_equity(result)

    if write_outputs:
        out_dir = Path(output_dir) if output_dir is not None else config.OUTPUTS_DIR
        write_tract_geopackage(result, out_dir / f"{file_prefix}_tracts.gpkg")
        render_tract_choropleth(
            result,
            out_dir / f"{file_prefix}_tract_potential.png",
            column="potential_per_household",
            title=f"{area_name} — rooftop solar potential per household (kWh/yr)",
        )
        render_tract_choropleth(
            result,
            out_dir / f"{file_prefix}_tract_equity.png",
            column="equity_class",
            categorical=True,
            title=f"{area_name} — equity quadrant (per-household potential × energy burden)",
        )
        render_potential_burden_scatter(
            result, out_dir / f"{file_prefix}_tract_scatter.png", area_name=area_name
        )

    return result


def _load_buildings(buildings: gpd.GeoDataFrame | str | Path | None) -> gpd.GeoDataFrame:
    """Resolve the per-roof input to a GeoDataFrame in ``config.WORKING_CRS``.

    Pass a GeoDataFrame straight through; otherwise read from a path — **GeoParquet** for
    Part 2-2's city output (``run_city``; ADR-0009) or a **GeoPackage** (layer ``roofs``) for
    the Part 2-1 Stage-1 output, chosen by extension. Defaults to the Stage-1 GeoPackage. This
    is what lets ``run_aggregation`` compose with either scale path's saved output."""
    if isinstance(buildings, gpd.GeoDataFrame):
        return buildings
    path = Path(buildings) if buildings is not None else config.OUTPUTS_DIR / GEOPACKAGE_NAME
    if path.suffix == ".parquet":
        return gpd.read_parquet(path)
    return gpd.read_file(path, layer="roofs")


def write_tract_geopackage(tracts_gdf: gpd.GeoDataFrame, path: str | Path) -> Path:
    """Write the per-tract result to a GeoPackage (layer 'tracts'); return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tracts_gdf.to_file(path, driver="GPKG", layer="tracts")
    return path


def render_tract_choropleth(
    tracts_gdf: gpd.GeoDataFrame,
    path: str | Path,
    *,
    column: str,
    title: str,
    categorical: bool = False,
) -> Path:
    """Render a per-tract choropleth (continuous or categorical) to a PNG; return the path.

    ``categorical=True`` draws the discrete equity-quadrant classes; otherwise a sequential
    ramp with data-missing tracts greyed out (never silently coloured as 0 — ADR-0007).
    matplotlib is imported lazily with the Agg backend so this needs no display.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(9, 9))
    plot_kwargs: dict = {
        "ax": ax,
        "column": column,
        "cmap": "tab10" if categorical else "viridis",
        "legend": True,
        "edgecolor": "black",
        "lw": 0.4,
    }
    if categorical:
        plot_kwargs["categorical"] = True
        plot_kwargs["legend_kwds"] = {"loc": "lower left", "fontsize": 8}
    else:
        plot_kwargs["missing_kwds"] = {"color": "lightgrey", "label": "no data"}
    tracts_gdf.plot(**plot_kwargs)

    ax.set_title(title)
    ax.set_xlabel("Easting (m)")
    ax.set_ylabel("Northing (m)")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def render_potential_burden_scatter(
    tracts_gdf: gpd.GeoDataFrame, path: str | Path, *, area_name: str = "Glover Park"
) -> Path:
    """Render the potential-vs-burden scatter that supports the quadrant map (ADR-0007).

    Plots each classifiable tract at (per-household potential, energy burden), draws the two
    medians as the quadrant split, and highlights priority tracts (high × high). Flagged-missing
    tracts are excluded (they have no place on the axes — never plotted as 0). Lazy Agg backend.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    df = tracts_gdf[~tracts_gdf["equity_data_missing"]].dropna(
        subset=["potential_per_household", "energy_burden"]
    )
    priority = df["is_priority"].to_numpy()

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.scatter(
        df.loc[~priority, "potential_per_household"],
        df.loc[~priority, "energy_burden"],
        c="tab:blue", alpha=0.7, label="other tracts",
    )
    ax.scatter(
        df.loc[priority, "potential_per_household"],
        df.loc[priority, "energy_burden"],
        c="tab:red", alpha=0.9, label="priority (high × high)",
    )
    if len(df):
        ax.axvline(df["potential_per_household"].median(), color="grey", ls="--", lw=0.8)
        ax.axhline(df["energy_burden"].median(), color="grey", ls="--", lw=0.8)

    ax.set_xlabel("per-household potential (kWh/household/yr)")
    ax.set_ylabel("energy burden (fraction of income)")
    ax.set_title(f"{area_name} — potential vs energy burden (median quadrant split)")
    ax.legend(loc="best", fontsize=8)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
# City-scale idempotency — params stamp + tile manifest (Phase 2, Part 2-2,   #
# ADR-0009: "idempotency = a tile manifest + a params stamp")                #
# --------------------------------------------------------------------------- #

# Bump to force a full city recompute (e.g. a Part 2-4/2-5 accuracy swap) even when every
# other param is unchanged — folded into params_stamp so it invalidates every cached tile.
# 2-4.0: the multi-plane + obstruction-aware geometry swap (ADR-0011/0012/0013) — every tile's
# result changes, so every 2-2.0 cache entry must be recomputed.
PIPELINE_VERSION = "2-4.0"


def params_stamp(
    *,
    day_range: list[int] | None,
    buffer_m: float,
    tile_size_m: float,
    origin: tuple[float, float],
    dsm_source: str,
    footprints_source: str,
    pipeline_version: str = PIPELINE_VERSION,
) -> str:
    """Canonical fingerprint of the params that invalidate a cached tile (ADR-0009).

    A tile is only ever recomputed when something that could change its *result* changes —
    the radiation day range, the shading buffer, the grid geometry, the DSM/footprints
    source, or the pipeline version itself (bumped for e.g. a Part 2-4/2-5 accuracy swap).
    `TileManifest.is_current` compares this string verbatim against what a tile was last
    computed with, so the encoding only needs to be **stable** (same params -> same string)
    and **sensitive** (any param above changing -> a different string) — not human-readable.

    Built as a stable JSON encoding: `sort_keys=True` so key order never matters, `day_range`
    sorted (`None` passed through unchanged) so day-range *order* isn't a spurious cache
    miss, and `origin` turned into a list (a JSON array, since JSON has no tuple type — a
    bare `tuple` isn't `json.dumps`-stable across `list`/`tuple` callers otherwise).

    Args:
        day_range: r.sun days (`None` = the calibrated 12-day default; ADR-0001).
        buffer_m: tile buffer, metres (`config.TILE_BUFFER_M`).
        tile_size_m: grid cell edge length, metres (`config.TILE_SIZE_M`).
        origin: grid origin, metres (`config.GRID_ORIGIN`).
        dsm_source: identifies which DSM build feeds the tile (e.g. "pc-2m").
        footprints_source: "ms-buildings" or "osm".
        pipeline_version: bump to force a full recompute independent of the params above.

    Returns:
        A JSON string suitable as an opaque, order-independent cache-invalidation key.
    """
    payload = {
        "day_range": sorted(day_range) if day_range is not None else None,
        "buffer_m": buffer_m,
        "tile_size_m": tile_size_m,
        "origin": list(origin),
        "dsm_source": dsm_source,
        "footprints_source": footprints_source,
        "pipeline_version": pipeline_version,
    }
    return json.dumps(payload, sort_keys=True)


class TileManifest:
    """Per-tile status + params-stamp, persisted as JSON under `config.TILES_CACHE_DIR`
    (ADR-0009).

    Makes an expensive, multi-hour city run resumable: `run_city` (Part 2-2) checks
    `is_current` before doing a tile's work at all, and a re-run after a crash or an
    accuracy-swapped `params_stamp` recomputes only the tiles that actually need it — never
    a from-scratch re-run, never a silently stale cache (a bare file-exists check would
    serve stale results across a params change; ADR-0009's "Alternatives considered").

    Entries are keyed `"<row>_<col>"` (stable across runs because `Tile.row`/`Tile.col` are
    stable for a fixed `origin`/`tile_size_m` — ADR-0009's fixed-origin property) and hold
    `{"status": "done" | "failed", "stamp": str, "n_roofs": int}` (plus `"error"` for a
    failed tile). Callers use `is_current`/`mark_done`/`mark_failed` — the dict itself is an
    implementation detail, not part of the public contract.
    """

    def __init__(self, path: str | Path, entries: dict[str, dict[str, Any]] | None = None) -> None:
        self.path = Path(path)
        self.entries: dict[str, dict[str, Any]] = entries if entries is not None else {}

    @classmethod
    def load(cls, path: str | Path) -> TileManifest:
        """Read the manifest JSON at `path` if it exists, else start empty."""
        path = Path(path)
        if path.exists():
            entries = json.loads(path.read_text())
        else:
            entries = {}
        return cls(path, entries)

    def save(self) -> None:
        """Write the manifest to `self.path` as JSON, creating parent dirs as needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, sort_keys=True, indent=2))

    def is_current(self, tile: tiling.Tile, stamp: str) -> bool:
        """True only if `tile` is recorded `"done"` with a `stamp` matching exactly.

        False for an unknown tile, a `"failed"` tile (so it's retried on rerun), or a
        `"done"` tile whose recorded stamp has since changed (so a params change forces
        recompute) — the three cases `run_city` needs to decide "skip" vs "(re)compute".
        """
        entry = self.entries.get(_tile_key(tile))
        if entry is None:
            return False
        return entry.get("status") == "done" and entry.get("stamp") == stamp

    def mark_done(self, tile: tiling.Tile, stamp: str, n_roofs: int) -> None:
        """Record `tile` as successfully computed with `stamp`, having produced `n_roofs`."""
        self.entries[_tile_key(tile)] = {
            "status": "done",
            "stamp": stamp,
            "n_roofs": n_roofs,
        }

    def mark_failed(self, tile: tiling.Tile, stamp: str, error: str) -> None:
        """Record `tile` as failed under `stamp` (skip-and-continue; retried on rerun)."""
        self.entries[_tile_key(tile)] = {
            "status": "failed",
            "stamp": stamp,
            "error": error,
        }


def _tile_key(tile: tiling.Tile) -> str:
    """The manifest's per-tile cache key: `"<row>_<col>"` — stable across runs because
    `(row, col)` is stable for a fixed grid `origin`/`tile_size_m` (ADR-0009)."""
    return f"{tile.row}_{tile.col}"


# --------------------------------------------------------------------------- #
# City runner — tile the District, score per tile, cache, merge (Part 2-2,    #
# ADR-0009: sequential + per-tile cache, resumable/idempotent).               #
# --------------------------------------------------------------------------- #

CITY_ROOFS_PARQUET_NAME = "dc_roofs.parquet"
MANIFEST_NAME = "manifest.json"


def run_city(
    *,
    aoi: BaseGeometry | None = None,
    tile_size_m: float = config.TILE_SIZE_M,
    buffer_m: float = config.TILE_BUFFER_M,
    origin: tuple[float, float] = config.GRID_ORIGIN,
    footprints_source: str = "ms-buildings",
    day_range: list[int] | None = None,
    dsm_source: str = "pc-2m",
    tiles_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    resume: bool = True,
    write_outputs: bool = True,
) -> gpd.GeoDataFrame:
    """Scale the Stage-1 per-roof spine over `aoi` by tiling it; return the city per-roof result.

    Runs the SAME stages as `run_stage1`, once per tile of a fixed buffered grid, caching each
    tile's roofs (GeoParquet) so an expensive multi-hour run is resumable/idempotent. Feed the
    result to the already-built `run_aggregation` for the city equity map (reused untouched).

    Args:
        aoi: area to tile, in `config.WORKING_CRS` (metres). None -> the whole District via
            `aoi.dc_aoi()` (the TIGER tract union).
        tile_size_m / buffer_m / origin: the grid (config defaults). `buffer_m` IS the effective
            inter-tile shadow reach (ADR-0009); `origin` is fixed so cache keys are stable.
        footprints_source / day_range / dsm_source: passed to the per-tile spine; together with
            the grid params they form the params-stamp that invalidates the cache.
        tiles_dir: per-tile parquet cache + manifest (default `config.TILES_CACHE_DIR`).
        output_dir: where the city per-roof GeoParquet goes (default `config.OUTPUTS_DIR`).
        resume: skip tiles already recorded done under the current params-stamp (idempotent
            re-run); False recomputes every tile.
        write_outputs: if False, compute but write no city parquet.

    Every footprint is scored in EXACTLY ONE tile by floor membership (`tiling.tile_membership`),
    so merging conserves — no building dropped or double-counted (§5). Suitability is recomputed
    once city-wide after the merge (ADR-0004 at city scale). Per-tile failures are recorded and
    skipped (skip-and-continue); empty tiles record 0 roofs.
    """
    if aoi is None:
        from rooftop_solar import aoi as aoi_mod  # local import: `aoi` is a parameter name here

        aoi = aoi_mod.dc_aoi()
    tiles_dir = Path(tiles_dir) if tiles_dir is not None else config.TILES_CACHE_DIR

    grid = tiling.make_grid(aoi, tile_size_m, buffer_m, origin)
    stamp = params_stamp(
        day_range=day_range,
        buffer_m=buffer_m,
        tile_size_m=tile_size_m,
        origin=origin,
        dsm_source=dsm_source,
        footprints_source=footprints_source,
    )
    manifest = TileManifest.load(tiles_dir / MANIFEST_NAME)

    tile_frames: list[gpd.GeoDataFrame] = []
    for tile in grid:
        cache = tiles_dir / f"tile_{tile.row}_{tile.col}_roofs.parquet"
        if resume and manifest.is_current(tile, stamp) and cache.exists():
            tile_frames.append(gpd.read_parquet(cache))
            continue
        try:
            roofs = _score_tile(
                tile,
                tile_size_m=tile_size_m,
                origin=origin,
                footprints_source=footprints_source,
                day_range=day_range,
                dsm_out_path=tiles_dir / f"tile_{tile.row}_{tile.col}_dsm.tif",
            )
        except Exception as exc:  # one bad tile must not abort a multi-hour batch (ADR-0009)
            manifest.mark_failed(tile, stamp, error=repr(exc))
            manifest.save()
            continue
        _write_parquet(roofs, cache)
        manifest.mark_done(tile, stamp, n_roofs=len(roofs))
        manifest.save()
        tile_frames.append(roofs)

    city = recompute_city_suitability(merge_tile_roofs(tile_frames))

    if write_outputs:
        out_dir = Path(output_dir) if output_dir is not None else config.OUTPUTS_DIR
        _write_parquet(city, out_dir / CITY_ROOFS_PARQUET_NAME)
    return city


def _score_tile(
    tile: tiling.Tile,
    *,
    tile_size_m: float,
    origin: tuple[float, float],
    footprints_source: str,
    day_range: list[int] | None,
    dsm_out_path: str | Path,
) -> gpd.GeoDataFrame:
    """Score one tile's core footprints via the shared spine over its buffered extent.

    Fetches footprints/DSM over the tile's BUFFERED cell (reprojected to WGS84 — what the fetch
    stages take), but selects the CORE footprints by `floor` membership in the metric working
    CRS — so a building is scored by exactly one tile (§5), never the `.within` boundary-drop.
    An empty core (the common case — DC is 43% of its bbox) short-circuits before the expensive
    DSM/`r.sun` work, returning 0 roofs.
    """
    buffered_wgs84 = _working_to_wgs84(tile.buffered)
    fp = footprints.load_footprints(buffered_wgs84, source=footprints_source).to_crs(
        config.WORKING_CRS
    )
    fp_core = select_by_membership(fp, tiling.tile_membership(tile, tile_size_m, origin))
    if len(fp_core) == 0:
        return _empty_roofs()
    # The city always runs the Part 2-4 multiplane production path (plan §3); explicit here so
    # the choice is visible at the call site, not left to _score_roofs's default.
    return _score_roofs(
        fp_core, buffered_wgs84, day_range=day_range, dsm_out_path=dsm_out_path, method="multiplane"
    )


def _empty_roofs() -> gpd.GeoDataFrame:
    """An empty per-roof frame (working CRS) for a tile with no core footprints ("done, 0
    roofs" — ADR-0009). `merge_tile_roofs` skips empty frames, so its bare schema is harmless."""
    return gpd.GeoDataFrame({"geometry": gpd.GeoSeries([], crs=config.WORKING_CRS)})


def _working_to_wgs84(geom_working: BaseGeometry) -> BaseGeometry:
    """Reproject a `config.WORKING_CRS` geometry to EPSG:4326 (what the fetch stages take)."""
    to_wgs84 = Transformer.from_crs(config.WORKING_CRS, "EPSG:4326", always_xy=True).transform
    return shp_transform(to_wgs84, geom_working)


def _write_parquet(gdf: gpd.GeoDataFrame, path: str | Path) -> Path:
    """Write a GeoDataFrame to GeoParquet (Part 2-2's city/tile format), mkdir-ing parents."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(path)
    return path
