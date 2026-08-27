"""Stage 2 — Digital Surface Model from LiDAR (data-sources.md, risks §8).

USGS 3DEP LiDAR. Either read raw EPT/LAZ from AWS `usgs-lidar-public` with PDAL
(`readers.ept` -> `writers.gdal`, output_type=max) and rasterise, or use Planetary
Computer's pre-derived `3dep-lidar-dsm` STAC collection where it covers the city.

CRITICAL: this is the DSM (full surface — buildings + trees + terrain), NEVER the DTM
(bare earth). The DTM zeroes out all inter-building shading.
"""

from __future__ import annotations


def build_dsm(aoi, resolution_m: float = 1.0, source: str = "planetary-computer"):
    """Return a DSM raster for `aoi` at `resolution_m`.

    Buffer `aoi` before calling so shadow-casters just outside the study area are included
    (risks §8, trap 2).
    """
    raise NotImplementedError("Phase 1")
