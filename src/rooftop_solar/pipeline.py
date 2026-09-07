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
from pyproj import Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform

from rooftop_solar import (
    aoi,
    config,
    dsm,
    footprints,
    radiation,
    roof_planes,
    usable_area,
    yield_pv,
)

GEOPACKAGE_NAME = "glover_park_roofs.gpkg"
CHOROPLETH_NAME = "glover_park_suitability.png"


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
