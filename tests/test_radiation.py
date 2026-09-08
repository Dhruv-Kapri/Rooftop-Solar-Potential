"""Unit tests for the radiation module's *pure* helpers (ADR-0001).

The GRASS r.sun pass (`surface_irradiance`) is integration-only and lives in the smoke test,
not here. What's unit-testable without GRASS:
  - `annual_insolation` — sum 12 monthly representative-day rasters (Wh/m²/day), weighted by
    days-in-month, to an annual kWh/m²/yr raster; nodata in any month propagates to NaN.
  - `zonal_insolation` — attach each footprint's mean shaded insolation from a raster on disk.

Expected values are hand-computed from the day weights and from a hand-built raster.
Fast: pure numpy + rasterio, no GRASS, no network.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from rooftop_solar import config, radiation


def test_representative_day_sampling_is_monthly():
    # 12 mid-month days, weighted by days-in-month summing to a full (non-leap) year.
    assert len(radiation.MID_MONTH_DAYS) == 12
    assert len(radiation.DAYS_IN_MONTH) == 12
    assert sum(radiation.DAYS_IN_MONTH) == 365


def test_annual_insolation_weighted_sum_to_kwh():
    # Each month a uniform 100 Wh/m²/day raster -> 100 * 365 Wh = 36500 Wh -> 36.5 kWh/m²/yr.
    daily = [np.full((1, 2), 100.0) for _ in range(12)]

    annual = radiation.annual_insolation(daily, radiation.DAYS_IN_MONTH)

    np.testing.assert_allclose(annual, np.full((1, 2), 36.5))


def test_annual_insolation_propagates_nodata():
    # A pixel that is nodata (NaN) in ANY month can't have a trustworthy annual sum -> NaN.
    daily = [np.full((1, 2), 100.0) for _ in range(12)]
    daily[3][0, 1] = np.nan  # one cell missing in one month

    annual = radiation.annual_insolation(daily, radiation.DAYS_IN_MONTH)

    assert np.isnan(annual[0, 1])
    assert not np.isnan(annual[0, 0])


def test_zonal_insolation_per_roof_mean(tmp_path):
    # A 2x4 raster of 10 m pixels: left half (cols 0-1) = 1000, right half (cols 2-3) = 2000,
    # with one nodata cell in the left half. Two footprints, one over each half.
    nodata = -9999.0
    values = np.array(
        [[nodata, 1000.0, 2000.0, 2000.0], [1000.0, 1000.0, 2000.0, 2000.0]],
        dtype="float64",
    )
    transform = from_origin(0.0, 20.0, 10.0, 10.0)  # top-left (0,20), 10 m pixels
    raster_path = tmp_path / "insol.tif"
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        height=2,
        width=4,
        count=1,
        dtype="float64",
        nodata=nodata,
        crs=config.WORKING_CRS,
        transform=transform,
    ) as dst:
        dst.write(values, 1)

    footprints = gpd.GeoDataFrame(
        {"id": ["left", "right"]},
        geometry=[box(0.0, 0.0, 20.0, 20.0), box(20.0, 0.0, 40.0, 20.0)],
        crs=config.WORKING_CRS,
    )

    out = radiation.zonal_insolation(footprints, raster_path)

    # Left roof: nodata cell excluded, remaining valid cells all 1000 -> mean 1000.
    # Right roof: all 2000. (A nodata leak would drag the left mean far negative.)
    np.testing.assert_allclose(out["poa_clear_sky_kwh_m2"].to_numpy(), [1000.0, 2000.0])
    assert len(out) == len(footprints)


def _clean_two_value_raster(tmp_path):
    """A nodata-free 2x4 raster: left half (cols 0-1) = 1000, right half (cols 2-3) = 2000.

    Companion to `_insol_raster` but with no nodata cell, for the plane_poa tests that
    aren't specifically exercising nodata exclusion.
    """
    values = np.array(
        [[1000.0, 1000.0, 2000.0, 2000.0], [1000.0, 1000.0, 2000.0, 2000.0]],
        dtype="float64",
    )
    transform = from_origin(0.0, 20.0, 10.0, 10.0)
    path = tmp_path / "poa_clean.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=2,
        width=4,
        count=1,
        dtype="float64",
        nodata=-9999.0,
        crs=config.WORKING_CRS,
        transform=transform,
    ) as dst:
        dst.write(values, 1)
    return path


def test_plane_poa_per_plane_mean(tmp_path):
    # Same raster shape as the zonal test (cols 0-1 = 1000, cols 2-3 = 2000), but the
    # membership is now per-PLANE pixel centres, not a footprint mask: the left plane's
    # inlier_xy is exactly the four left-half pixel centres, the right plane's is the
    # four right-half ones. Pixel centres hand-computed from the raster's
    # from_origin(0, 20, 10, 10) transform (top-left (0,20), 10 m pixels): col c, row r
    # -> centre (c*10+5, 20-r*10-5).
    raster_path = _clean_two_value_raster(tmp_path)
    left_xy = np.array([[5.0, 15.0], [15.0, 15.0], [5.0, 5.0], [15.0, 5.0]])
    right_xy = np.array([[25.0, 15.0], [35.0, 15.0], [25.0, 5.0], [35.0, 5.0]])
    planes = gpd.GeoDataFrame(
        {"inlier_xy": [left_xy, right_xy]},
        geometry=[box(0.0, 0.0, 20.0, 20.0), box(20.0, 0.0, 40.0, 20.0)],
        crs=config.WORKING_CRS,
    )

    out = radiation.plane_poa(planes, raster_path)

    np.testing.assert_allclose(out[radiation.POA_COLUMN].to_numpy(), [1000.0, 2000.0])
    assert len(out) == len(planes)


def test_plane_poa_excludes_nodata(tmp_path):
    # Reuse the existing zonal fixture, whose top-left cell (pixel centre (5, 15), row 0
    # col 0) is nodata. A plane whose membership includes that pixel must exclude it from
    # the mean -- averaging only the remaining three valid 1000 cells.
    raster_path = _insol_raster(tmp_path)
    xy = np.array([[5.0, 15.0], [15.0, 15.0], [5.0, 5.0], [15.0, 5.0]])
    planes = gpd.GeoDataFrame(
        {"inlier_xy": [xy]}, geometry=[box(0.0, 0.0, 20.0, 20.0)], crs=config.WORKING_CRS
    )

    out = radiation.plane_poa(planes, raster_path)

    np.testing.assert_allclose(out[radiation.POA_COLUMN].to_numpy(), [1000.0])


def test_plane_poa_empty_membership_is_nan(tmp_path):
    # The unfittable-footprint placeholder plane (empty inlier_xy) must get NaN, not an
    # error or a spurious zero -- mirrors zonal_insolation's "no valid pixel" convention.
    raster_path = _clean_two_value_raster(tmp_path)
    planes = gpd.GeoDataFrame(
        {"inlier_xy": [np.empty((0, 2), dtype=np.float64)]},
        geometry=[box(0.0, 0.0, 20.0, 20.0)],
        crs=config.WORKING_CRS,
    )

    out = radiation.plane_poa(planes, raster_path)

    assert np.isnan(out[radiation.POA_COLUMN].to_numpy()[0])


def _insol_raster(tmp_path):
    """The shared synthetic insolation raster: cols 0-1 = 1000, cols 2-3 = 2000, one nodata."""
    nodata = -9999.0
    values = np.array(
        [[nodata, 1000.0, 2000.0, 2000.0], [1000.0, 1000.0, 2000.0, 2000.0]],
        dtype="float64",
    )
    transform = from_origin(0.0, 20.0, 10.0, 10.0)
    path = tmp_path / "insol.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=2,
        width=4,
        count=1,
        dtype="float64",
        nodata=nodata,
        crs=config.WORKING_CRS,
        transform=transform,
    ) as dst:
        dst.write(values, 1)
    return path


def test_zonal_insolation_spanning_and_out_of_bounds(tmp_path):
    # Characterization of the pixel-centre + nodata + no-coverage semantics the optimized
    # windowed implementation must preserve exactly.
    raster_path = _insol_raster(tmp_path)
    footprints = gpd.GeoDataFrame(
        {"id": ["spanning", "outside"]},
        geometry=[
            box(10.0, 0.0, 30.0, 20.0),  # cols 1-2 -> mean of 1000 and 2000 = 1500
            box(1000.0, 1000.0, 1010.0, 1010.0),  # entirely off the raster -> NaN
        ],
        crs=config.WORKING_CRS,
    )

    out = radiation.zonal_insolation(footprints, raster_path)

    vals = out["poa_clear_sky_kwh_m2"].to_numpy()
    assert vals[0] == 1500.0
    assert np.isnan(vals[1])


def test_zonal_insolation_windowed_matches_full_raster_reference(tmp_path):
    # Equivalence proof for the windowed optimization: on many varied random footprints the
    # windowed result must equal a brute-force full-raster geometry_mask (the pre-optimization
    # logic, computed independently here) for every roof, edge cases included.
    rng = np.random.default_rng(0)
    n_rows, n_cols, res = 40, 60, 2.0
    ox, oy = 320_000.0, 4_307_000.0
    nodata = -9999.0
    raster_vals = rng.uniform(500.0, 2500.0, size=(n_rows, n_cols))
    raster_vals[5:10, 5:10] = nodata  # a nodata patch some roofs will straddle
    transform = from_origin(ox, oy, res, res)
    path = tmp_path / "r.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=n_rows,
        width=n_cols,
        count=1,
        dtype="float64",
        nodata=nodata,
        crs=config.WORKING_CRS,
        transform=transform,
    ) as dst:
        dst.write(raster_vals, 1)

    geoms = []
    for _ in range(40):
        w, h = rng.uniform(2.0, 30.0), rng.uniform(2.0, 30.0)
        x = rng.uniform(ox, ox + n_cols * res - 30.0)
        y = rng.uniform(oy - n_rows * res + 30.0, oy)
        geoms.append(box(x, y - h, x + w, y))
    geoms.append(box(999_999.0, 999_999.0, 1_000_009.0, 1_000_009.0))  # out of bounds
    footprints = gpd.GeoDataFrame(geometry=geoms, crs=config.WORKING_CRS)

    got = radiation.zonal_insolation(footprints, path)[radiation.POA_COLUMN].to_numpy()

    # Independent reference: mask the FULL raster per roof (the original implementation).
    ref_raster = raster_vals.copy()
    ref_raster[ref_raster == nodata] = np.nan
    reference = []
    for geom in geoms:
        inside = ~geometry_mask(
            [mapping(geom)], out_shape=(n_rows, n_cols), transform=transform, invert=False
        )
        v = ref_raster[inside]
        v = v[~np.isnan(v)]
        reference.append(float(np.mean(v)) if v.size else np.nan)

    np.testing.assert_allclose(got, reference, equal_nan=True)
