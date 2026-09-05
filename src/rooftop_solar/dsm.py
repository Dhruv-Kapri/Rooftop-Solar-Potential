"""Stage 2 — Digital Surface Model from LiDAR (data-sources.md, risks §8).

USGS 3DEP LiDAR. Either read raw EPT/LAZ from AWS `usgs-lidar-public` with PDAL
(`readers.ept` -> `writers.gdal`, output_type=max) and rasterise, or use Planetary
Computer's pre-derived `3dep-lidar-dsm` STAC collection where it covers the city.

CRITICAL: this is the DSM (full surface — buildings + trees + terrain), NEVER the DTM
(bare earth). The DTM zeroes out all inter-building shading.

This module uses the pre-derived Planetary Computer collection (2 m native resolution)
rather than deriving a DSM from the raw point cloud — that's a heavier, later option.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import planetary_computer
import pyproj
import pystac_client
import rasterio
from rasterio.crs import CRS
from rasterio.mask import mask
from rasterio.merge import merge
from rasterio.warp import Resampling, calculate_default_transform, reproject
from shapely.geometry import mapping
from shapely.ops import transform as shp_transform

from rooftop_solar import config

PC_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
DSM_COLLECTION = "3dep-lidar-dsm"

# The PC 3dep-lidar-dsm collection is native 2 m — this pipeline never resamples up from
# that (risks §8: don't fabricate resolution the source data doesn't have).
_NATIVE_RESOLUTION_M = 2.0


def build_dsm(aoi, resolution_m: float = 2.0, source: str = "planetary-computer") -> Path:
    """Build a DSM GeoTIFF for `aoi` and return its path.

    Args:
        aoi: study-area geometry in EPSG:4326, already buffered (risks §8, trap 2 — a
            shadow-caster just outside the frame still needs to be in the DSM).
        resolution_m: descriptive only — see module note above. Passed through only to
            confirm it matches the source's native resolution; it does NOT trigger
            resampling.
        source: only "planetary-computer" is implemented (the pre-derived collection).

    Steps: STAC search -> sign assets -> mosaic (rasterio.merge) -> reproject to
    config.WORKING_CRS -> clip to `aoi` -> write GeoTIFF to
    DATA_DIR/dsm/glover_park_dsm.tif. NoData is carried through unchanged from the
    source (-9999 for this collection), never invented.
    """
    if source != "planetary-computer":
        raise ValueError(f"Unknown DSM source: {source!r} (expected 'planetary-computer')")
    if resolution_m != _NATIVE_RESOLUTION_M:
        raise ValueError(
            f"resolution_m={resolution_m} but 3dep-lidar-dsm is native "
            f"{_NATIVE_RESOLUTION_M} m — this module does not resample."
        )

    hrefs = _search_dsm_hrefs(aoi)
    mosaic, mosaic_transform, src_crs, nodata = _mosaic(hrefs, aoi)
    dst_array, dst_transform, dst_crs = _reproject_to_working_crs(
        mosaic, mosaic_transform, src_crs, nodata, resolution_m
    )
    clipped, clipped_transform = _clip_to_aoi(dst_array, dst_transform, dst_crs, aoi, nodata)

    out_path = config.DATA_DIR / "dsm" / "glover_park_dsm.tif"
    _write_geotiff(out_path, clipped, clipped_transform, dst_crs, nodata)
    return out_path


def _search_dsm_hrefs(aoi) -> list[str]:
    """Signed URLs of every 3dep-lidar-dsm STAC item intersecting `aoi`.

    A small AOI can straddle more than one LiDAR project's tiling scheme (and, in
    practice for this AOI, more than one project's vintage — see dsm.py callers'
    docstrings / the driver script output), hence a list to mosaic rather than one item.
    """
    catalog = pystac_client.Client.open(PC_STAC_URL, modifier=planetary_computer.sign_inplace)
    search = catalog.search(collections=[DSM_COLLECTION], intersects=mapping(aoi))
    items = list(search.items())
    if not items:
        raise RuntimeError(f"No {DSM_COLLECTION} STAC items intersect this AOI")
    return [item.assets["data"].href for item in items]


def _mosaic(hrefs: list[str], aoi_wgs84) -> tuple[np.ndarray, rasterio.Affine, CRS, float]:
    """Open + mosaic the DSM tiles over just the AOI window.

    Returns (array, transform, horizontal CRS, nodata).

    Each source COG tile covers a much larger area than this AOI (~8km across vs. our
    ~2km AOI), and it's a remote HTTPS read — merge()'s default (mosaic the tiles' full
    extents, THEN the caller clips) pulls every pixel of every source tile over the
    network before throwing most of it away. Measured cost of that: ~110s to fully read
    ONE 4097x4097 source tile. Passing `bounds=` to merge() makes it windowed-read only
    the AOI's footprint in each source tile instead — ~12s for all three tiles combined.

    The source rasters carry a COMPOUND CRS (horizontal UTM + NAVD88 vertical height) —
    rasterio.crs.CRS can't round-trip that through calculate_default_transform, and the
    vertical component doesn't matter for a 2D warp anyway, so this extracts just the
    horizontal sub-CRS via pyproj.
    """
    srcs = [rasterio.open(href) for href in hrefs]
    try:
        nodata = srcs[0].nodata
        horizontal_crs = pyproj.CRS.from_wkt(srcs[0].crs.to_wkt()).sub_crs_list[0]
        src_crs = CRS.from_epsg(horizontal_crs.to_epsg())

        to_src_crs = pyproj.Transformer.from_crs(
            "EPSG:4326", horizontal_crs, always_xy=True
        ).transform
        aoi_in_src_crs = shp_transform(to_src_crs, aoi_wgs84)

        mosaic, mosaic_transform = merge(srcs, bounds=aoi_in_src_crs.bounds)
    finally:
        for src in srcs:
            src.close()
    return mosaic[0], mosaic_transform, src_crs, nodata


def _reproject_to_working_crs(
    array: np.ndarray,
    src_transform: rasterio.Affine,
    src_crs: CRS,
    nodata: float,
    resolution_m: float,
) -> tuple[np.ndarray, rasterio.Affine, CRS]:
    """Reproject the mosaic from its source UTM zone to config.WORKING_CRS."""
    dst_crs = CRS.from_string(config.WORKING_CRS)
    height, width = array.shape
    left, top = src_transform * (0, 0)
    right, bottom = src_transform * (width, height)
    dst_transform, dst_width, dst_height = calculate_default_transform(
        src_crs,
        dst_crs,
        width,
        height,
        left=left,
        bottom=bottom,
        right=right,
        top=top,
        resolution=resolution_m,
    )

    dst_array = np.full((dst_height, dst_width), nodata, dtype=array.dtype)
    reproject(
        source=array,
        destination=dst_array,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        src_nodata=nodata,
        dst_nodata=nodata,
        resampling=Resampling.bilinear,
    )
    return dst_array, dst_transform, dst_crs


def _clip_to_aoi(
    array: np.ndarray,
    transform: rasterio.Affine,
    crs: CRS,
    aoi_wgs84,
    nodata: float,
) -> tuple[np.ndarray, rasterio.Affine]:
    """Clip the reprojected mosaic to `aoi` (reprojected into `crs` first)."""
    to_crs = pyproj.Transformer.from_crs("EPSG:4326", crs.to_string(), always_xy=True).transform
    aoi_in_crs = shp_transform(to_crs, aoi_wgs84)

    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": array.dtype,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
    }
    with rasterio.io.MemoryFile() as memfile:
        with memfile.open(**profile) as dataset:
            dataset.write(array, 1)
        with memfile.open() as dataset:
            clipped, clipped_transform = mask(
                dataset, [mapping(aoi_in_crs)], crop=True, nodata=nodata
            )
    return clipped[0], clipped_transform


def _write_geotiff(
    path: Path, array: np.ndarray, transform: rasterio.Affine, crs: CRS, nodata: float
) -> None:
    """Write a single-band GeoTIFF, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": array.dtype,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array, 1)
