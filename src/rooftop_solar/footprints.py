"""Stage 1 — building footprints (data-sources.md).

Recommended: Microsoft Global ML Building Footprints (STAC `ms-buildings` on Planetary
Computer, or bulk GeoJSON). Fallback: OSM via osmnx. Returns a GeoDataFrame of footprints
clipped to the (buffered) study area.
"""

from __future__ import annotations


def load_footprints(aoi, source: str = "ms-buildings"):
    """Return building footprints within `aoi` as a GeoDataFrame.

    Args:
        aoi: study-area geometry (buffer applied upstream — see the shading traps, risks §8).
        source: "ms-buildings" (default) or "osm".
    """
    raise NotImplementedError("Phase 1")
