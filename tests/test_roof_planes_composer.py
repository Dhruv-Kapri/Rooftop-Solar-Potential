"""Unit tests for the roof-plane composer (`roof_points`, `fit_roof_planes`).

Seam under test: footprint geometry + a DSM GeoTIFF on disk -> per-roof point cloud ->
per-roof plane summary, assembled into a GeoDataFrame the pipeline carries downstream
(ADR-0002: the uncertainty columns must survive this stage, never be dropped).

The synthetic DSM is a south-facing ramp: elevation rises toward the north, at a known
pitch. Its expected tilt/aspect come from that geometry (northing coordinate = a
constant, non-code-derived, gradient), not from the fitting implementation -- the same
style as test_roof_planes.py's `synthetic_roof`.

Fast: pure numpy + rasterio + geopandas, no network, no GRASS.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from rooftop_solar import config, roof_planes

# --- synthetic DSM geometry (independent of the code under test) ---
PIXEL_M = 2.0
GRID_SIZE_PX = 100  # 100 x 100 px @ 2 m -> a 200 m x 200 m tile
ORIGIN_EASTING = 320_000.0  # west edge, a plausible EPSG:6347 (UTM 18N) easting
ORIGIN_NORTHING = 4_307_200.0  # north edge (top-left corner, per from_origin convention)
PITCH_DEG = 20.0
BASE_ELEV_M = 300.0
NODATA = -9999.0

EXPECTED_ASPECT_DEG = 180.0  # rises north -> faces south (test_roof_planes.py convention)


@pytest.fixture
def dsm_path(tmp_path: Path) -> Path:
    """Write a synthetic south-facing-ramp DSM GeoTIFF to `tmp_path` and return its path.

    z = tan(PITCH_DEG) * northing + base: elevation depends only on the northing
    coordinate (no east-west slope), and *rises* as northing increases (further
    north) -- a roof over this surface physically faces due south.
    """
    transform = from_origin(ORIGIN_EASTING, ORIGIN_NORTHING, PIXEL_M, PIXEL_M)

    row_idx = np.arange(GRID_SIZE_PX).reshape(-1, 1)  # row 0 = north edge
    northing = ORIGIN_NORTHING - (row_idx + 0.5) * PIXEL_M  # per-row pixel-centre northing
    row_elev = np.tan(np.radians(PITCH_DEG)) * northing + BASE_ELEV_M  # (GRID_SIZE_PX, 1)
    band = np.broadcast_to(row_elev, (GRID_SIZE_PX, GRID_SIZE_PX)).astype(np.float32)

    path = tmp_path / "synthetic_dsm.tif"
    profile = {
        "driver": "GTiff",
        "height": GRID_SIZE_PX,
        "width": GRID_SIZE_PX,
        "count": 1,
        "dtype": band.dtype,
        "crs": config.WORKING_CRS,
        "transform": transform,
        "nodata": NODATA,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(band, 1)
    return path


@pytest.fixture
def footprints(dsm_path: Path) -> gpd.GeoDataFrame:  # noqa: ARG001 -- fixture ordering only
    """Two footprints over the synthetic ramp: one large, one below MIN_PX pixels."""
    big = box(320_060.0, 4_307_060.0, 320_140.0, 4_307_140.0)  # 80x80 m -> 1600 px @ 2 m
    tiny = box(320_010.0, 4_307_010.0, 320_011.0, 4_307_011.0)  # 1x1 m -> at most 1 px
    return gpd.GeoDataFrame({"id": ["big", "tiny"]}, geometry=[big, tiny], crs=config.WORKING_CRS)


@pytest.fixture
def gable_dsm_path(tmp_path: Path) -> Path:
    """Write a synthetic gable-ridge DSM: two facets of known pitch meeting at a ridge.

    Elevation is a "tent" over the northing (row) axis, peaking along a central
    east-west ridge row and falling away at PITCH_DEG on both sides -- independent,
    geometry-derived two-facet ground truth (the raster equivalent of
    `test_roof_planes.py`'s `_synthetic_gable`), on the same grid as `dsm_path`. The
    side closer to the north edge (smaller row index) falls toward the north as you
    move away from the ridge -- physically faces north; the side closer to the south
    edge rises toward the ridge going north -- physically faces south.
    """
    transform = from_origin(ORIGIN_EASTING, ORIGIN_NORTHING, PIXEL_M, PIXEL_M)
    ridge_row = GRID_SIZE_PX // 2
    row_idx = np.arange(GRID_SIZE_PX).reshape(-1, 1)
    slope = np.tan(np.radians(PITCH_DEG))
    row_elev = BASE_ELEV_M + slope * PIXEL_M * (ridge_row - np.abs(row_idx - ridge_row))
    band = np.broadcast_to(row_elev, (GRID_SIZE_PX, GRID_SIZE_PX)).astype(np.float32)

    path = tmp_path / "synthetic_gable_dsm.tif"
    profile = {
        "driver": "GTiff",
        "height": GRID_SIZE_PX,
        "width": GRID_SIZE_PX,
        "count": 1,
        "dtype": band.dtype,
        "crs": config.WORKING_CRS,
        "transform": transform,
        "nodata": NODATA,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(band, 1)
    return path


def test_multiplane_two_facet_footprint_yields_two_rows(gable_dsm_path, footprints):
    # The "big" footprint straddles the gable ridge -- multiplane must recover both
    # facets as two rows sharing one building_id, dominant (larger) facet first.
    roofs = roof_planes.fit_roof_planes(footprints, gable_dsm_path, method="multiplane")
    big_rows = roofs[roofs["building_id"] == 0].sort_values("plane_id")

    assert len(big_rows) == 2
    assert list(big_rows["plane_id"]) == [0, 1]
    assert (big_rows["n_planes"] == 2).all()
    assert big_rows.iloc[0]["n_px"] >= big_rows.iloc[1]["n_px"]
    for _, row in big_rows.iterrows():
        assert row["inlier_xy"].shape == (row["n_px"], 2)
    dominant = big_rows.iloc[0]
    assert abs(dominant["tilt_deg"] - PITCH_DEG) < 3.0


def test_multiplane_unfittable_footprint_one_low_conf_row(gable_dsm_path, footprints):
    # The "tiny" footprint (< MIN_PX pixel centres) still gets exactly one placeholder
    # row -- mirrors the ransac path's "flag, don't drop" behaviour (ADR-0002).
    roofs = roof_planes.fit_roof_planes(footprints, gable_dsm_path, method="multiplane")
    tiny_rows = roofs[roofs["building_id"] == 1]

    assert len(tiny_rows) == 1
    row = tiny_rows.iloc[0]
    assert row["plane_id"] == 0
    assert row["n_planes"] == 1
    assert np.isnan(row["tilt_deg"])
    assert np.isnan(row["aspect_deg"])
    assert row["coef"] is None
    assert row["inlier_xy"].shape == (0, 2)
    assert row["low_confidence"]


def test_multiplane_output_contract_columns_and_crs(gable_dsm_path, footprints):
    roofs = roof_planes.fit_roof_planes(footprints, gable_dsm_path, method="multiplane")

    assert set(roofs.columns) == {
        "geometry",
        "building_id",
        "plane_id",
        "n_planes",
        "tilt_deg",
        "aspect_deg",
        "inlier_ratio",
        "n_px",
        "coef",
        "inlier_xy",
        "low_confidence",
    }
    assert roofs.crs == footprints.crs


def test_large_footprint_recovers_known_pitch_and_south_aspect(dsm_path, footprints):
    roofs = roof_planes.fit_roof_planes(footprints, dsm_path)
    big = roofs.iloc[0]

    assert abs(big["tilt_deg"] - PITCH_DEG) < 2.0
    assert abs(big["aspect_deg"] - EXPECTED_ASPECT_DEG) < 3.0
    # A GeoDataFrame column of bool dtype hands back numpy.bool_, not Python bool --
    # `is False` would fail identity even on a correct value, so assert truthiness.
    assert not big["low_confidence"]
    assert big["n_px"] >= 50


def test_tiny_footprint_is_flagged_low_confidence(dsm_path, footprints):
    roofs = roof_planes.fit_roof_planes(footprints, dsm_path)
    tiny = roofs.iloc[1]

    assert tiny["low_confidence"]
    assert np.isnan(tiny["tilt_deg"])
    assert tiny["n_px"] < roof_planes.MIN_PX


def test_output_contract_columns_rowcount_and_crs(dsm_path, footprints):
    roofs = roof_planes.fit_roof_planes(footprints, dsm_path)

    assert set(roofs.columns) == {
        "geometry",
        "tilt_deg",
        "aspect_deg",
        "inlier_ratio",
        "n_px",
        "low_confidence",
    }
    assert len(roofs) == len(footprints)
    assert roofs.crs == footprints.crs
    # The ORIGINAL footprint geometry is carried through, not the DSM clip.
    assert list(roofs.geometry) == list(footprints.geometry)


def test_multiplane_ransac_regression(dsm_path, footprints):
    # The Part 2-4 multiplane work must not perturb the existing "ransac" baseline path
    # at all: same 6-column schema, same row-per-footprint shape, byte-for-byte.
    roofs = roof_planes.fit_roof_planes(footprints, dsm_path, method="ransac")

    assert set(roofs.columns) == {
        "geometry",
        "tilt_deg",
        "aspect_deg",
        "inlier_ratio",
        "n_px",
        "low_confidence",
    }
    assert len(roofs) == len(footprints)


def test_unimplemented_method_raises(dsm_path, footprints):
    with pytest.raises(NotImplementedError):
        roof_planes.fit_roof_planes(footprints, dsm_path, method="block")


def test_roof_points_returns_empty_arrays_when_no_pixel_centre_is_covered(dsm_path):
    # A sliver polygon straddling a pixel corner (shared by 4 px) covers no pixel
    # *centre* -- rasterio.mask still returns a (cropped) window, but every pixel in
    # it reads as nodata, so roof_points must report "no points", not a bogus 0-row hit.
    corner_x = ORIGIN_EASTING + 2 * PIXEL_M  # a pixel-boundary corner, well inside the grid
    corner_y = ORIGIN_NORTHING - 2 * PIXEL_M
    sliver = box(corner_x - 0.05, corner_y - 0.05, corner_x + 0.05, corner_y + 0.05)

    X, z = roof_planes.roof_points(sliver, dsm_path)

    assert X.shape == (0, 2)
    assert z.shape == (0,)
