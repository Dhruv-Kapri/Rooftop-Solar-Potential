"""Stage 1 — building footprints (data-sources.md).

Recommended: Microsoft Global ML Building Footprints (STAC `ms-buildings` on Planetary
Computer, or bulk GeoJSON). Fallback: OSM via osmnx. Returns a GeoDataFrame of footprints
clipped to the (buffered) study area.
"""

from __future__ import annotations

import math

import geopandas as gpd
import osmnx as ox
import pandas as pd
import planetary_computer
import pystac_client
from shapely.geometry import mapping

PC_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

# Bing Maps tile zoom the ms-buildings dataset is partitioned at (its parquet directories
# are named `quadkey=<9-digit string>`). Confirmed by listing the "United States" region
# partition on Planetary Computer — every leaf directory has a 9-character quadkey, i.e.
# zoom 9. There's no metadata field for this, so it's an empirical constant, not a spec.
_MS_BUILDINGS_QUADKEY_ZOOM = 9


def load_footprints(aoi, source: str = "ms-buildings"):
    """Return building footprints within `aoi` as a GeoDataFrame.

    Args:
        aoi: study-area geometry (buffer applied upstream — see the shading traps, risks §8).
        source: "ms-buildings" (default) or "osm".
    """
    if source == "ms-buildings":
        gdf = _load_ms_buildings(aoi)
    elif source == "osm":
        gdf = _load_osm(aoi)
    else:
        raise ValueError(
            f"Unknown footprints source: {source!r} (expected 'ms-buildings' or 'osm')"
        )

    return gdf.reset_index(drop=True)


def _load_ms_buildings(aoi):
    """Microsoft Global ML Building Footprints via the Planetary Computer STAC collection.

    The collection is one STAC item per US state/region (e.g. "United States"), each
    pointing at a *directory* of geoparquet files on Azure Blob Storage partitioned by
    Bing Maps quadkey — not a single readable file. Reading the whole US region (~130M
    rows) is impractical for a small AOI, so this narrows to just the quadkey tile(s)
    that intersect `aoi` before reading, and clips the result to `aoi` afterwards (a
    quadkey tile is much bigger than this AOI — at zoom 9, roughly tens of km across).
    """
    catalog = pystac_client.Client.open(PC_STAC_URL, modifier=planetary_computer.sign_inplace)
    search = catalog.search(collections=["ms-buildings"], intersects=mapping(aoi))
    items = list(search.items())
    if not items:
        raise RuntimeError("No ms-buildings STAC items intersect this AOI")

    quadkeys = _bbox_quadkeys(aoi.bounds, _MS_BUILDINGS_QUADKEY_ZOOM)

    frames = []
    for item in items:
        asset = item.assets["data"]
        storage_options = asset.extra_fields.get("table:storage_options", {})
        for qk in quadkeys:
            partition_href = f"{asset.href}/quadkey={qk}"
            try:
                frames.append(gpd.read_parquet(partition_href, storage_options=storage_options))
            except FileNotFoundError:
                # Not every quadkey tile has data (e.g. mostly-water tiles) or overlaps
                # every matched region item — skip rather than fail the whole fetch.
                continue

    if not frames:
        raise RuntimeError("No ms-buildings parquet partitions found for this AOI's quadkey(s)")

    gdf = frames[0] if len(frames) == 1 else gpd.GeoDataFrame(pd.concat(frames, ignore_index=True))
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    return gpd.clip(gdf, aoi)


def _bbox_quadkeys(bounds: tuple[float, float, float, float], zoom: int) -> set[str]:
    """Bing Maps quadkeys (at `zoom`) covering a (west, south, east, north) WGS84 bbox.

    Reimplemented directly (no `mercantile` dependency) — it's ~15 lines of standard
    Web Mercator tile math: https://learn.microsoft.com/bingmaps/articles/bing-maps-tile-system
    """
    west, south, east, north = bounds

    def deg2tile(lat: float, lon: float) -> tuple[int, int]:
        lat_rad = math.radians(lat)
        n = 2**zoom
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
        return x, y

    def tile2quadkey(x: int, y: int) -> str:
        digits = []
        for i in range(zoom, 0, -1):
            mask = 1 << (i - 1)
            digit = (1 if x & mask else 0) + (2 if y & mask else 0)
            digits.append(str(digit))
        return "".join(digits)

    x_nw, y_nw = deg2tile(north, west)
    x_se, y_se = deg2tile(south, east)
    xs = range(min(x_nw, x_se), max(x_nw, x_se) + 1)
    ys = range(min(y_nw, y_se), max(y_nw, y_se) + 1)
    return {tile2quadkey(x, y) for x in xs for y in ys}


def _load_osm(aoi):
    """OSM building footprints via osmnx, kept to polygonal geometries only."""
    gdf = ox.features_from_polygon(aoi, {"building": True})
    return gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
