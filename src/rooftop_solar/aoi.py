"""AOI helpers — the study-area box, and a metric-buffered version of it.

Both fetch stages (footprints.py, dsm.py — and later radiation.py) need the SAME
buffered polygon: the buffer exists so shadow-casting buildings just outside the core
frame are still fetched (risks §8, trap 2 — clip too tight and inter-building shading
silently drops out). Keeping the buffering logic in one small module means every stage
buffers identically instead of re-deriving it.
"""

from __future__ import annotations

from pyproj import Transformer
from shapely.geometry import Polygon, box
from shapely.ops import transform

from rooftop_solar import config


def core_aoi_wgs84() -> Polygon:
    """The hand-picked Glover Park bbox (config.AOI_BBOX_WGS84) as a Polygon, EPSG:4326.

    This is the "core" AOI — the frame we actually report/display against (e.g. the Esri
    tutorial benchmark). Do NOT fetch data with it directly: it hasn't been buffered, so
    buildings just outside it would be missing and could still cast shadows into it.
    Use buffered_aoi() for any actual data fetch.
    """
    west, south, east, north = config.AOI_BBOX_WGS84
    return box(west, south, east, north)


def buffered_aoi(crs: str = "EPSG:4326") -> Polygon:
    """The core AOI grown by config.AOI_BUFFER_M metres, returned in `crs`.

    Degrees of longitude/latitude aren't a constant distance, so buffering by metres has
    to happen in a metric (projected) CRS — hence the round trip through
    config.WORKING_CRS. `crs` defaults to EPSG:4326 because that's what STAC searches and
    osmnx expect; pass config.WORKING_CRS to skip the final reprojection.
    """
    core = core_aoi_wgs84()

    to_working = Transformer.from_crs("EPSG:4326", config.WORKING_CRS, always_xy=True).transform
    core_working = transform(to_working, core)
    buffered_working = core_working.buffer(config.AOI_BUFFER_M)

    if crs == config.WORKING_CRS:
        return buffered_working

    to_target = Transformer.from_crs(config.WORKING_CRS, crs, always_xy=True).transform
    return transform(to_target, buffered_working)
