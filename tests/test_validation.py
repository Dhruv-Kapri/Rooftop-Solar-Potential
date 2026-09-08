"""Unit tests for the validation module (Part 2-4 step 7a; plan §6, risks §14.4).

Seam under test: reconstruct a roof's DSM surface from its fitted plane(s), measure the
per-pixel residual, and roll that up into a self-consistency summary (RMSE, % within
tolerance) -- multiplane vs single-plane ransac. Expected values are hand-computed from
the spec/geometry, never recomputed the way the code does.

Fixture helpers (`synthetic_roof`, `_synthetic_gable`) are local copies of
`test_roof_planes.py`'s -- same idiom, kept self-contained per this repo's test-file
convention (no cross-test-file imports elsewhere).

Fast: pure numpy + rasterio + geopandas, no network, no GRASS.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from rooftop_solar import config, roof_planes, validation

# --- synthetic gable DSM geometry, same grid/pitch as test_roof_planes_composer.py's
# gable_dsm_path fixture -- an independent-of-the-code-under-test "tent" over the rows.
PIXEL_M = 2.0
GRID_SIZE_PX = 100
ORIGIN_EASTING = 320_000.0
ORIGIN_NORTHING = 4_307_200.0
PITCH_DEG = 20.0
BASE_ELEV_M = 300.0
NODATA = -9999.0


def synthetic_roof(
    pitch_deg: float, faces: str, n: int = 20
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Build a perfect n*n planar roof of `pitch_deg`, facing compass direction `faces`.

    Local copy of `test_roof_planes.py`'s helper of the same name -- see there for the
    full docstring.
    """
    north_idx, east_idx = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    east = east_idx.ravel().astype(np.float64)
    north = north_idx.ravel().astype(np.float64)
    slope = float(np.tan(np.radians(pitch_deg)))
    rise = {
        "south": slope * north,
        "north": -slope * north,
        "west": slope * east,
        "east": -slope * east,
        "flat": np.zeros_like(east),
    }[faces]
    X = np.column_stack([east, north])
    return X, rise.astype(np.float64)


def _synthetic_gable(
    pitch_deg: float, n_side: int = 20, n_east: int = 20
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Build a symmetric gable roof: two facets of known pitch meeting at a ridge.

    Local copy of `test_roof_planes.py`'s helper of the same name -- see there for the
    full docstring.
    """
    east_idx, north_idx = np.meshgrid(np.arange(n_east), np.arange(2 * n_side), indexing="ij")
    east = east_idx.ravel().astype(np.float64)
    north = north_idx.ravel().astype(np.float64)
    slope = float(np.tan(np.radians(pitch_deg)))
    south_of_ridge = north < n_side
    z = np.where(south_of_ridge, slope * north, slope * (2 * n_side - 1 - north))
    X = np.column_stack([east, north])
    return X, z


def test_reconstruct_residuals_single_plane_near_zero():
    # A perfect synthetic plane, reconstructed with its OWN fitted coef, must read back
    # (almost) exactly -- the residual is the fit's own leftover noise, near machine zero.
    X, z = synthetic_roof(pitch_deg=20.0, faces="south")
    plane = roof_planes.fit_planes_multi(X, z)[0]

    residuals = validation.reconstruct_residuals(X, z, [plane["coef"]])

    assert residuals.shape == (len(z),)
    assert np.all(np.abs(residuals) < 1e-6)


def test_reconstruct_residuals_gable_two_planes_near_zero_but_one_plane_large():
    # The crux test: a gable's two true facets reconstruct it almost exactly, while a
    # single averaged plane cannot -- proving multi-plane reconstructs a gable and a
    # single plane does not. Asserted as a structural inequality (several-x), not an
    # exact magnitude (the spec's instruction: never a tautological/snapshot assert).
    X, z = _synthetic_gable(pitch_deg=20.0)

    two_plane_coefs = [p["coef"] for p in roof_planes.fit_planes_multi(X, z)]
    one_plane_coefs = [p["coef"] for p in roof_planes.fit_planes_multi(X, z, max_planes=1)]
    assert len(two_plane_coefs) == 2
    assert len(one_plane_coefs) == 1

    two_plane_residuals = validation.reconstruct_residuals(X, z, two_plane_coefs)
    one_plane_residuals = validation.reconstruct_residuals(X, z, one_plane_coefs)

    two_plane_rms = float(np.sqrt(np.mean(two_plane_residuals**2)))
    one_plane_rms = float(np.sqrt(np.mean(one_plane_residuals**2)))
    # The two true facets reconstruct the gable almost exactly (well within the 0.5 m
    # self-consistency tolerance); RANSAC's loose inlier band around the ridge leaves a
    # small non-zero residual there, so "near zero" is bounded loosely, not to machine
    # precision. The single averaged plane, unable to follow either slope, is materially
    # worse -- asserted as a large structural ratio, not an exact magnitude.
    assert two_plane_rms < 0.1
    assert one_plane_rms > 0.5
    assert one_plane_rms > 10.0 * two_plane_rms


def test_reconstruct_residuals_unfittable_is_nan():
    # No planes to reconstruct against -- an unfittable roof (fit_planes_multi returned
    # []) has nothing to compare its DSM pixels to, so every residual is NaN.
    z = np.array([1.0, 2.0, 3.0])
    X = np.column_stack([np.arange(3.0), np.zeros(3)])

    residuals = validation.reconstruct_residuals(X, z, [])

    assert residuals.shape == (3,)
    assert np.all(np.isnan(residuals))


def test_self_consistency_hand_worked():
    # Hand-worked, independent of the implementation:
    # rmse = sqrt((0^2 + 0^2 + 1^2 + (-1)^2) / 4) = sqrt(0.5) ~= 0.7071.
    # pct_within_tol (tol=0.5): 2 of 4 residuals (the two zeros) satisfy |r| <= 0.5 -> 50.0.
    residuals = np.array([0.0, 0.0, 1.0, -1.0])

    result = validation.self_consistency(residuals, tol=0.5)

    assert result["n_px"] == 4
    np.testing.assert_allclose(result["rmse"], np.sqrt(0.5))
    np.testing.assert_allclose(result["pct_within_tol"], 50.0)


def test_self_consistency_empty_or_all_nan():
    for residuals in (np.array([]), np.array([np.nan, np.nan])):
        result = validation.self_consistency(residuals)

        assert result["n_px"] == 0
        assert np.isnan(result["rmse"])
        assert np.isnan(result["pct_within_tol"])


@pytest.fixture
def gable_dsm_path(tmp_path: Path) -> Path:
    """A synthetic gable-ridge DSM -- same construction as
    test_roof_planes_composer.py's fixture of the same name (see there for the full
    docstring): a "tent" over the northing (row) axis, two known-pitch facets meeting
    at a central ridge.
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


@pytest.fixture
def gable_footprint() -> gpd.GeoDataFrame:
    """One footprint straddling the gable ridge -- big enough to cover both facets."""
    geom = box(320_060.0, 4_307_060.0, 320_140.0, 4_307_140.0)  # 80x80 m -> 1600 px @ 2 m
    return gpd.GeoDataFrame({"id": ["gable"]}, geometry=[geom], crs=config.WORKING_CRS)


def test_compare_self_consistency_multiplane_beats_single_on_gable(gable_dsm_path, gable_footprint):
    # Multi-plane models both facets; the single plane averages them -- so its
    # reconstruction residual against the real DSM must be materially worse.
    out = validation.compare_self_consistency(gable_footprint, gable_dsm_path)
    row = out.iloc[0]

    assert row["n_planes"] == 2
    assert row["rmse_multiplane"] < row["rmse_ransac"]
    assert row["rmse_improvement"] > 0.0


def test_compare_self_consistency_columns_and_crs(gable_dsm_path, gable_footprint):
    out = validation.compare_self_consistency(gable_footprint, gable_dsm_path)

    assert set(out.columns) == {
        "geometry",
        "building_id",
        "n_planes",
        "rmse_multiplane",
        "pct_within_multiplane",
        "rmse_ransac",
        "pct_within_ransac",
        "rmse_improvement",
    }
    assert out.crs == gable_footprint.crs
    assert len(out) == len(gable_footprint)
