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

from pathlib import Path

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
        output_dir: where the GeoPackage + choropleth go (default config.OUTPUTS_DIR).
        write_outputs: if False, compute the result but write no files.

    The returned GeoDataFrame (working CRS) carries geometry + tilt/aspect + uncertainty
    (ADR-0002) + usable area + capacity/energy/CO₂ + within-AOI suitability (ADR-0004).
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
    in_core = fp.geometry.representative_point().within(core_working)
    fp_core = fp[in_core].reset_index(drop=True)

    # DSM + shaded annual insolation cover the buffered frame (shading; traps 1-2).
    dsm_path = dsm.build_dsm(buffered)
    insol_path = radiation.surface_irradiance(dsm_path, day_range=day_range)

    # Per-roof geometry -> shaded insolation -> usable area -> yield + suitability.
    planes = roof_planes.fit_roof_planes(fp_core, dsm_path)
    planes = radiation.zonal_insolation(planes, insol_path)
    usable = usable_area.usable_area(planes)
    result = yield_pv.estimate_yield(usable)

    if write_outputs:
        out_dir = Path(output_dir) if output_dir is not None else config.OUTPUTS_DIR
        write_geopackage(result, out_dir / GEOPACKAGE_NAME)
        render_choropleth(result, out_dir / CHOROPLETH_NAME)

    return result


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
        write_tract_geopackage(result, out_dir / TRACT_GEOPACKAGE_NAME)
        render_tract_choropleth(
            result,
            out_dir / POTENTIAL_CHOROPLETH_NAME,
            column="potential_per_household",
            title="Glover Park — rooftop solar potential per household (kWh/yr)",
        )
        render_tract_choropleth(
            result,
            out_dir / EQUITY_CHOROPLETH_NAME,
            column="equity_class",
            categorical=True,
            title="Glover Park — equity quadrant (per-household potential × energy burden)",
        )
        render_potential_burden_scatter(result, out_dir / EQUITY_SCATTER_NAME)

    return result


def _load_buildings(buildings: gpd.GeoDataFrame | str | Path | None) -> gpd.GeoDataFrame:
    """Resolve the per-roof input: pass a GeoDataFrame through, else read the GeoPackage
    (layer ``roofs``), defaulting to the Stage-1 output. Result is in ``config.WORKING_CRS``."""
    if isinstance(buildings, gpd.GeoDataFrame):
        return buildings
    path = Path(buildings) if buildings is not None else config.OUTPUTS_DIR / GEOPACKAGE_NAME
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


def render_potential_burden_scatter(tracts_gdf: gpd.GeoDataFrame, path: str | Path) -> Path:
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
    ax.set_title("Glover Park — potential vs energy burden (median quadrant split)")
    ax.legend(loc="best", fontsize=8)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path
