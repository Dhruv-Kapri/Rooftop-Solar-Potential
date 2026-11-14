"""ETL for the Glover Park step-by-step walkthrough page (issue #41, ADR-0015; plan §4).

Turns one Glover Park run of the production spine (`pipeline.score_stages`) into the static
assets `web/walkthrough.html` reads, one per stage of Esri's "Estimate solar power potential"
tutorial (the ADR-0005 benchmark) plus the stages our pipeline adds:

  - **Rasters** (raster PMTiles, ADR-0015): `hillshade`, shaded `insolation`, pixel `slope` and
    `aspect`, colour-mapped at build time. `hillshade` / `slope_aspect` / `mask_to_footprints` /
    `colorize` are the numpy half; `write_raster_pmtiles` is the GDAL MBTiles -> `pmtiles` half.
  - **Vectors** (GeoJSON, as ADR-0010's tract layer): `facet_polygons` (one polygon per fitted
    roof plane), `obstruction_polygons`, `buildings_to_web`.
  - **Manifest**: `walkthrough_manifest` — bounds, raster legends and value ranges, the
    per-reason counts and the benchmark numbers, the one JSON the page reads.

`build_walkthrough` is the orchestration (and the only function here that needs GRASS and the
network); everything else is a pure array / GeoDataFrame transform.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import matplotlib
import numpy as np
import numpy.typing as npt
import rasterio
import rasterio.features
import shapely
from affine import Affine
from osgeo import gdal
from pmtiles.convert import mbtiles_to_pmtiles
from pmtiles.reader import MmapSource, Reader
from rasterio.enums import ColorInterp

from rooftop_solar import aoi, config, pipeline, radiation
from rooftop_solar.usable_area import FLAT_THRESHOLD_DEG

# The per-plane and per-building "why not suitable" column (`usable_area.unsuitable_reason`,
# `yield_pv.collapse_to_buildings`): one of `usable_area.UNSUITABLE_REASONS`, or null when the
# plane / building passes.
REASON_COLUMN: str = "unsuitable_reason"
# The walkthrough's step order (tests pin that it is the same set as
# `usable_area.UNSUITABLE_REASONS`): #40's precedence, steep > north > low insolation, then the
# per-building area gate, so a plane failing several cutoffs carries the first and is removed at
# that step. no_plane_fit is shown from the planes step on.
REASONS: list[str] = ["too_steep", "north_facing", "low_insolation", "too_small", "no_plane_fit"]
# The reasons that strike out a *plane* (steps 7a-7c); too_small is per-building, no_plane_fit
# has no plane.
_PLANE_REASONS: list[str] = ["too_steep", "north_facing", "low_insolation"]

_WEB_CRS: str = "EPSG:4326"
_WEB_PRECISION_DEG: float = 1e-6  # ~0.1 m: keeps the GeoJSON small without moving a footprint edge
_SNAP_M: float = 1e-3  # pixel squares share edges exactly after this, so the union has no slivers
_FLAT_RGBA: tuple[int, int, int, int] = (
    190,
    190,
    190,
    255,
)  # aspect is undefined on a flat roof: draw it neutral grey
_SLOPE_RANGE_DEG: tuple[float, float] = (0.0, 60.0)
_ASPECT_RANGE_DEG: tuple[float, float] = (0.0, 360.0)
_INSOLATION_PERCENTILES: tuple[float, float] = (2.0, 98.0)

_ASSETS_SUBDIR: Path = Path("web") / "assets" / "walkthrough"
_VECTOR_NAMES: dict[str, str] = {
    "facets": "facets.geojson",
    "obstructions": "obstructions.geojson",
    "buildings": "buildings.geojson",
}
MANIFEST_NAME: str = "walkthrough.json"  # the page fetches this name (web/walkthrough.js)


# --- raster math --------------------------------------------------------------


def _gradients(
    dsm: npt.NDArray[np.float64], res_m: float
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """(dz/d_easting, dz/d_northing) of a north-up DSM; NaN where the pixel or a neighbour is nodata
    (`np.gradient` skips a pixel's own value, so its own nodata is masked back in)."""
    dz_drow, dz_dcol = np.gradient(dsm, res_m)
    nodata = np.isnan(dsm)
    return np.where(nodata, np.nan, dz_dcol), np.where(nodata, np.nan, -dz_drow)  # rows run S


def slope_aspect(
    dsm: npt.NDArray[np.float64], res_m: float
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Pixel slope and aspect of a north-up DSM, in degrees (walkthrough stages 3-4).

    Aspect follows `roof_planes`' convention exactly: the compass azimuth of the *downslope*
    side (0 = N, 90 = E, clockwise), i.e. ``atan2(-dz/dx, -dz/dy)`` — a plane that rises to the
    north faces south (180). Aspect is NaN where the pixel is perfectly flat (it has none)."""
    a, b = _gradients(dsm, res_m)
    slope = np.degrees(np.arctan(np.hypot(a, b)))
    aspect = np.degrees(np.arctan2(-a, -b)) % 360.0
    return slope, np.where(slope > 0, aspect, np.nan)


def hillshade(
    dsm: npt.NDArray[np.float64], res_m: float, azimuth: float = 315.0, altitude: float = 45.0
) -> npt.NDArray[np.float64]:
    """Shaded relief of a north-up DSM, 0-255 (walkthrough stage 1; Esri's Hillshade formula).

    `azimuth` is the sun's compass bearing and `altitude` its elevation above the horizon, in
    degrees (the conventional NW light, 315 / 45). Flat ground reads 255 * sin(altitude);
    nodata (NaN) stays NaN."""
    a, b = _gradients(dsm, res_m)
    slope = np.arctan(np.hypot(a, b))
    downslope = np.arctan2(-b, -a)  # math angle (CCW from east) of the downslope direction
    sun = np.radians(450.0 - azimuth)  # compass bearing -> math angle
    zenith = np.radians(90.0 - altitude)
    lit = np.cos(zenith) * np.cos(slope) + np.sin(zenith) * np.sin(slope) * np.cos(sun - downslope)
    return 255.0 * np.clip(lit, 0.0, 1.0)


def mask_to_footprints(
    array: npt.NDArray[np.float64], transform: Affine, footprints: gpd.GeoDataFrame
) -> npt.NDArray[np.float64]:
    """`array` with every pixel whose centre is outside all `footprints` set to NaN.

    `footprints` must be in the raster's CRS (the working CRS in the pipeline)."""
    inside = rasterio.features.geometry_mask(
        footprints.geometry, out_shape=array.shape, transform=transform, invert=True
    )
    return np.where(inside, array, np.nan)


# --- colouring + raster PMTiles ----------------------------------------------------------------


def colorize(
    values: npt.NDArray[np.float64], vmin: float, vmax: float, cmap: str
) -> npt.NDArray[np.uint8]:
    """Colour-map `values` to an RGBA uint8 array of shape (H, W, 4).

    Values are clamped to ``[vmin, vmax]`` before the matplotlib colormap `cmap` is applied;
    NaN pixels are fully transparent (alpha 0), so nodata and the area outside the footprints
    show the basemap through (ADR-0015)."""
    scaled = np.clip((values - vmin) / (vmax - vmin), 0.0, 1.0)
    rgba = matplotlib.colormaps[cmap](np.nan_to_num(scaled), bytes=True)
    rgba[np.isnan(values), 3] = 0
    return rgba


def legend(vmin: float, vmax: float, unit: str, cmap: str, n_stops: int = 6) -> dict[str, Any]:
    """The legend the page draws for one raster: its range, unit and `n_stops` colour stops."""
    values = np.linspace(vmin, vmax, n_stops)
    colors = matplotlib.colormaps[cmap](np.linspace(0.0, 1.0, n_stops), bytes=True)
    return {
        "min": float(vmin),
        "max": float(vmax),
        "unit": unit,
        "stops": [
            [float(v), "#{:02x}{:02x}{:02x}".format(*c[:3])]
            for v, c in zip(values, colors, strict=True)
        ],
    }


def write_raster_pmtiles(
    rgba: npt.NDArray[np.uint8],
    transform: Affine,
    crs: Any,
    out_path: str | Path,
    *,
    minzoom: int = 13,
    maxzoom: int = 17,
) -> Path:
    """Write an RGBA raster (H, W, 4) as a raster PMTiles archive of PNG tiles (ADR-0015).

    The raster goes to a temp GeoTIFF, GDAL's MBTiles driver tiles it (reprojecting to web
    mercator, at the zoom matching the raster's resolution, capped at `maxzoom`), overviews
    down to `minzoom` are built, and `pmtiles.convert` repackages the MBTiles. A 2 m DSM tiles
    at z16, so the archive's real max zoom can be below `maxzoom`; read it from the header
    (:func:`zoom_range`)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gdal.UseExceptions()
    with tempfile.TemporaryDirectory() as tmp:
        tif, mbtiles = Path(tmp) / "rgba.tif", Path(tmp) / "tiles.mbtiles"
        height, width = rgba.shape[:2]
        with rasterio.open(
            tif,
            "w",
            driver="GTiff",
            width=width,
            height=height,
            count=4,
            dtype="uint8",
            crs=crs,
            transform=transform,
        ) as dst:
            dst.write(np.moveaxis(rgba, -1, 0))
            dst.colorinterp = [
                ColorInterp.red,
                ColorInterp.green,
                ColorInterp.blue,
                ColorInterp.alpha,
            ]
        gdal.Translate(
            str(mbtiles),
            str(tif),
            format="MBTiles",
            creationOptions=[
                "TILE_FORMAT=PNG",
                "ZOOM_LEVEL_STRATEGY=UPPER",
                f"MAXZOOM={maxzoom}",
                f"MINZOOM={minzoom}",
            ],
        ).FlushCache()
        top = _mbtiles_maxzoom(mbtiles)
        if top > minzoom:
            ds = gdal.Open(str(mbtiles), gdal.GA_Update)
            ds.BuildOverviews("AVERAGE", [2**k for k in range(1, top - minzoom + 1)])
            ds = None
        mbtiles_to_pmtiles(str(mbtiles), str(out_path), min(top, maxzoom))
    return out_path


def _mbtiles_maxzoom(mbtiles: Path) -> int:
    with sqlite3.connect(mbtiles) as conn:
        return int(conn.execute("SELECT MAX(zoom_level) FROM tiles").fetchone()[0])


def zoom_range(pmtiles_path: str | Path) -> tuple[int, int]:
    """``(min_zoom, max_zoom)`` from a PMTiles archive's header."""
    with open(pmtiles_path, "rb") as f:
        header = Reader(MmapSource(f)).header()
    return int(header["min_zoom"]), int(header["max_zoom"])


# --- vector layers ------------------------------------------------------------


def _to_web(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reproject to EPSG:4326 and snap coordinates to ~0.1 m (small GeoJSON, same shapes)."""
    web = gdf.to_crs(_WEB_CRS)
    web["geometry"] = shapely.set_precision(web.geometry.to_numpy(), _WEB_PRECISION_DEG)
    return web


def facet_polygons(
    usable_planes: gpd.GeoDataFrame, pixel_size_m: float, *, buildings: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """One polygon per fitted roof plane: the union of the DSM pixel squares it owns (stage 5).

    `usable_planes` is `ScoringStages.usable_planes` (one row per (building, plane), working
    CRS); a plane's pixels are its ``inlier_xy`` centres, grown to `pixel_size_m` squares.
    Unfittable placeholder rows (empty ``inlier_xy``) have no facet. Properties: ``building_id``,
    ``plane_id``, ``tilt_deg``, ``aspect_deg``, ``area_m2``, ``poa_kwh_m2`` (shaded clear-sky,
    :data:`radiation.POA_COLUMN`), ``reason`` (the plane's own cutoff, :data:`REASON_COLUMN`) and
    ``building_reason`` (its building's, from `buildings`, which is what the 30 m² gate sets).
    EPSG:4326, unsimplified (a facet is a handful of pixel squares)."""
    building_reason = buildings.set_index("building_id")[REASON_COLUMN]
    half = pixel_size_m / 2.0

    records: list[dict[str, Any]] = []
    for row in usable_planes.itertuples(index=False):
        xy = np.asarray(row.inlier_xy, dtype=np.float64)
        if len(xy) == 0:
            continue
        squares = shapely.set_precision(
            shapely.box(xy[:, 0] - half, xy[:, 1] - half, xy[:, 0] + half, xy[:, 1] + half), _SNAP_M
        )
        facet = shapely.union_all(squares)
        records.append(
            {
                "building_id": int(row.building_id),
                "plane_id": int(row.plane_id),
                "tilt_deg": round(float(row.tilt_deg), 1),
                "aspect_deg": round(float(row.aspect_deg), 0),
                "area_m2": round(float(facet.area), 1),
                "poa_kwh_m2": round(float(getattr(row, radiation.POA_COLUMN)), 0),
                "reason": getattr(row, REASON_COLUMN),
                "building_reason": building_reason.get(row.building_id),
                "geometry": facet,
            }
        )
    facets = gpd.GeoDataFrame(records, geometry="geometry", crs=usable_planes.crs)
    return _to_web(facets)


def obstruction_polygons(
    obstructions: gpd.GeoDataFrame, planes: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """The detected obstruction pixels (stage 6): one polygon per plane that has any.

    `obstructions` is `ScoringStages.obstructions`, row-aligned to `planes`; planes without an
    obstruction (null geometry) are dropped. Properties: ``building_id``, ``plane_id``,
    ``area_m2``. EPSG:4326."""
    has_geometry = obstructions.geometry.notna()
    out = gpd.GeoDataFrame(
        {
            "building_id": planes.loc[has_geometry, "building_id"].astype(int),
            "plane_id": planes.loc[has_geometry, "plane_id"].astype(int),
            "area_m2": obstructions.loc[has_geometry, "obstruction_area_m2"].round(1),
        },
        geometry=obstructions.geometry[has_geometry],
        crs=obstructions.crs,
    )
    return _to_web(out.reset_index(drop=True))


def buildings_to_web(buildings: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """The collapsed per-building result (stage 8) in the page's property schema, EPSG:4326.

    ``building_id``, ``usable``, ``unsuitable_reason``, ``annual_energy_mwh``, ``capacity_kw``,
    ``usable_area_m2``, ``tilt_deg``, ``aspect_deg``, ``n_planes``; numbers at display precision."""
    out = gpd.GeoDataFrame(
        {
            "building_id": buildings["building_id"].astype(int),
            "usable": buildings["usable"].astype(bool),
            "unsuitable_reason": buildings[REASON_COLUMN].astype(object),
            "annual_energy_mwh": (buildings["annual_energy_kwh"] / 1000.0).round(2),
            "capacity_kw": buildings["capacity_kw"].round(1),
            "usable_area_m2": buildings["usable_area_m2"].round(1),
            "tilt_deg": buildings["tilt_deg"].round(1),
            "aspect_deg": buildings["aspect_deg"].round(0),
            "n_planes": buildings["n_planes"].astype(int),
        },
        geometry=buildings.geometry,
        crs=buildings.crs,
    )
    return _to_web(out)


def _write_geojson(gdf: gpd.GeoDataFrame, path: Path) -> Path:
    path.write_text(gdf.to_json(drop_id=True))
    return path


# --- manifest -----------------------------------------------------------------


def walkthrough_manifest(
    *,
    bounds: tuple[float, float, float, float],
    rasters: dict[str, dict[str, Any]],
    buildings: gpd.GeoDataFrame,
    planes: gpd.GeoDataFrame,
) -> dict[str, Any]:
    """The JSON the walkthrough page reads: view, raster layers + legends, vector files, stats.

    `bounds` is WGS84 ``(west, south, east, north)``; `rasters` is :func:`build_rasters`'s
    output; `buildings` / `planes` are `ScoringStages.buildings` / `.usable_planes` (working
    CRS). Stats follow `scripts/run_stage1.py`'s headline: the median is over suitable
    buildings, the specific yield over all (unsuitable ones contribute 0 kWh and 0 kW). The Esri
    numbers are `config.ESRI_*` (the benchmark line on the last step). ``reason_counts`` counts
    buildings (by their dominant-plane reason); ``plane_reason_counts`` counts fitted planes by
    their own reason, which is what steps 7a-7c strike out."""
    west, south, east, north = bounds
    suitable = buildings[buildings["usable"]]
    energy_kwh = float(buildings["annual_energy_kwh"].sum())
    capacity_kw = float(buildings["capacity_kw"].sum())
    reasons = buildings.loc[~buildings["usable"], REASON_COLUMN]
    fitted = planes[
        [len(xy) > 0 for xy in planes["inlier_xy"]]
    ]  # the planes `facet_polygons` emits
    return {
        "bounds": [west, south, east, north],
        "center": [(west + east) / 2.0, (south + north) / 2.0],
        "rasters": rasters,
        "vectors": dict(_VECTOR_NAMES),
        "aspect_flat": {
            "max_tilt_deg": FLAT_THRESHOLD_DEG,
            "color": "#{:02x}{:02x}{:02x}".format(*_FLAT_RGBA[:3]),
        },
        "reasons": list(REASONS),
        "stats": {
            "n_buildings": int(len(buildings)),
            "n_planes": int(len(fitted)),
            "n_suitable": int(len(suitable)),
            "reason_counts": {r: int((reasons == r).sum()) for r in REASONS},  # buildings
            "plane_reason_counts": {  # fitted planes, by their own reason (steps 7a-7c)
                r: int((fitted[REASON_COLUMN] == r).sum()) for r in _PLANE_REASONS
            },
            "total_energy_mwh": energy_kwh / 1000.0,
            "median_energy_mwh": (
                float(suitable["annual_energy_kwh"].median()) / 1000.0 if len(suitable) else 0.0
            ),
            "specific_yield_kwh_kwp": energy_kwh / capacity_kw if capacity_kw > 0 else 0.0,
            "esri_specific_yield_kwh_kwp": config.ESRI_SPECIFIC_YIELD_KWH_KWP,
            "esri_median_energy_mwh": config.ESRI_MEDIAN_ENERGY_MWH,
        },
    }


# --- orchestration ------------------------------------------------------------


def _read_masked(path: str | Path) -> tuple[npt.NDArray[np.float64], Affine, Any]:
    """A single-band raster as float64 with its nodata set to NaN, plus its transform and CRS."""
    with rasterio.open(path) as src:
        band = src.read(1).astype(np.float64)
        if src.nodata is not None:
            band[band == src.nodata] = np.nan
        return band, src.transform, src.crs


def build_rasters(
    dsm_path: str | Path,
    insol_path: str | Path,
    footprints: gpd.GeoDataFrame,
    out_dir: str | Path,
) -> dict[str, dict[str, Any]]:
    """Write the four raster PMTiles into `out_dir` and return their manifest entries.

    Hillshade covers the whole DSM (the buffer ring too: it is the context). Insolation, slope
    and aspect are clipped to `footprints` (working CRS), as in the Esri tutorial. Colour ramps
    (ADR-0015): hillshade greys; insolation ``inferno`` over the footprint pixels' 2nd-98th
    percentile; slope ``viridis`` 0-60 degrees; aspect ``twilight_shifted`` 0-360 (cyclic), with
    flat pixels (<= `FLAT_THRESHOLD_DEG`, where aspect means nothing) neutral grey.
    Each entry is ``{"url", "minzoom", "maxzoom", "legend"}``."""
    out_dir = Path(out_dir)
    dsm, transform, crs = _read_masked(dsm_path)
    insol, _, _ = _read_masked(insol_path)
    res_m = abs(transform.a)
    slope, aspect = slope_aspect(dsm, res_m)
    insol, slope, aspect = (
        mask_to_footprints(a, transform, footprints) for a in (insol, slope, aspect)
    )

    lo, hi = np.nanpercentile(insol, _INSOLATION_PERCENTILES)
    aspect_rgba = colorize(aspect, *_ASPECT_RANGE_DEG, "twilight_shifted")
    aspect_rgba[np.isfinite(slope) & (slope <= FLAT_THRESHOLD_DEG)] = _FLAT_RGBA
    layers: dict[str, tuple[npt.NDArray[np.uint8], dict[str, Any]]] = {
        "hillshade": (
            colorize(hillshade(dsm, res_m), 0.0, 255.0, "gray"),
            legend(0, 255, "", "gray"),
        ),
        "insolation": (colorize(insol, lo, hi, "inferno"), legend(lo, hi, "kWh/m²/yr", "inferno")),
        "slope": (
            colorize(slope, *_SLOPE_RANGE_DEG, "viridis"),
            legend(*_SLOPE_RANGE_DEG, "°", "viridis"),
        ),
        "aspect": (aspect_rgba, legend(*_ASPECT_RANGE_DEG, "° from N", "twilight_shifted")),
    }

    manifest: dict[str, dict[str, Any]] = {}
    for name, (rgba, layer_legend) in layers.items():
        path = write_raster_pmtiles(rgba, transform, crs, out_dir / f"{name}.pmtiles")
        minzoom, maxzoom = zoom_range(path)
        manifest[name] = {
            "url": path.name,
            "minzoom": minzoom,
            "maxzoom": maxzoom,
            "legend": layer_legend,
        }
    return manifest


def walkthrough_keys(day_range: list[int] | None) -> tuple[str, str]:
    """Tiered-cache keys (ADR-0014) for the persisted Glover Park DSM and insolation rasters.

    The DSM key fingerprints the AOI, DSM resolution/source and `pipeline.DSM_VERSION`; the
    radiation key chains it with `day_range` and the running GRASS version, via
    `pipeline.radiation_key`. A rebuild with the same keys skips the DSM fetch and `r.sun`."""
    dsm_key = json.dumps(
        {
            "area": "glover_park",
            "aoi_bbox_wgs84": list(config.AOI_BBOX_WGS84),
            "buffer_m": config.AOI_BUFFER_M,
            "resolution_m": pipeline.DSM_RESOLUTION_M,
            "dsm_source": "pc-2m",
            "dsm_version": pipeline.DSM_VERSION,
        },
        sort_keys=True,
    )
    radiation_key = pipeline.radiation_key(
        dsm_key=dsm_key, day_range=day_range, grass_version=radiation.grass_version()
    )
    return dsm_key, radiation_key


def build_walkthrough(
    *, out_dir: str | Path | None = None, day_range: list[int] | None = None
) -> dict[str, Path]:
    """Run Glover Park through the production spine and write the walkthrough assets.

    Same AOIs and footprint selection as `pipeline.run_stage1`, run through
    `pipeline.score_stages`, with the DSM and insolation rasters persisted under
    ``config.DATA_DIR/walkthrough/`` behind cache keys (:func:`walkthrough_keys`), so only the
    first build pays for `r.sun`. Writes the four raster PMTiles, the three GeoJSON layers and
    ``walkthrough.json`` into `out_dir` (default ``web/assets/walkthrough/``); needs GRASS and the
    network on a cold cache. `day_range` is as for `run_stage1`.

    Returns the written paths keyed by role (``hillshade`` / ``insolation`` / ``slope`` /
    ``aspect`` / ``facets`` / ``obstructions`` / ``buildings`` / ``manifest``)."""
    out = Path(out_dir) if out_dir is not None else config.REPO_ROOT / _ASSETS_SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    cache_dir = config.DATA_DIR / "walkthrough"
    dsm_key, radiation_key = walkthrough_keys(day_range)

    core, buffered = aoi.core_aoi_wgs84(), aoi.buffered_aoi()
    fp_core = pipeline.core_footprints(core, buffered)
    stages = pipeline.score_stages(
        fp_core,
        buffered,
        day_range=day_range,
        dsm_out_path=cache_dir / "glover_park_dsm.tif",
        dsm_cache_key=dsm_key,
        insol_out_path=cache_dir / "glover_park_insol.tif",
        radiation_cache_key=radiation_key,
    )

    rasters = build_rasters(stages.dsm_path, stages.insol_path, fp_core, out)
    with rasterio.open(stages.dsm_path) as src:
        pixel_size_m = abs(src.transform.a)

    facets = facet_polygons(stages.usable_planes, pixel_size_m, buildings=stages.buildings)
    manifest = walkthrough_manifest(
        bounds=config.AOI_BBOX_WGS84,
        rasters=rasters,
        buildings=stages.buildings,
        planes=stages.usable_planes,
    )

    paths = {name: out / entry["url"] for name, entry in rasters.items()}
    paths["facets"] = _write_geojson(facets, out / _VECTOR_NAMES["facets"])
    paths["obstructions"] = _write_geojson(
        obstruction_polygons(stages.obstructions, stages.planes),
        out / _VECTOR_NAMES["obstructions"],
    )
    paths["buildings"] = _write_geojson(
        buildings_to_web(stages.buildings), out / _VECTOR_NAMES["buildings"]
    )
    paths["manifest"] = out / MANIFEST_NAME
    paths["manifest"].write_text(json.dumps(manifest, indent=2))
    return paths
