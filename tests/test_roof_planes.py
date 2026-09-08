"""Unit tests for the roof-plane pure core (`fit_roof_plane`).

Seam under test: given a roof's point cloud (X, z), return its plane summary
(tilt/aspect/fit-quality). Expected values are derived from the *geometry* of a
synthetic plane, independently of the fitting formula — a plane whose surface rises
toward the north physically faces south, so its aspect must read ~180, whatever
arctan2 sign convention the implementation happens to use.

Fast: pure numpy + sklearn, no DSM, no network, no GRASS.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pytest

from rooftop_solar import roof_planes

# Compass azimuth (deg) of the direction each synthetic roof *faces* (downslope).
_FACING_AZIMUTH = {"north": 0.0, "east": 90.0, "south": 180.0, "west": 270.0}


def synthetic_roof(
    pitch_deg: float, faces: str, n: int = 20
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Build a perfect n*n planar roof of `pitch_deg`, facing compass direction `faces`.

    A roof "faces" the direction it slopes *down* toward, so its surface *rises* toward
    the opposite compass point. Grid coords are integer pixel indices (tilt is a ratio,
    so the pixel size is irrelevant). `faces="flat"` returns a level roof.
    """
    north_idx, east_idx = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    east = east_idx.ravel().astype(np.float64)  # +x
    north = north_idx.ravel().astype(np.float64)  # +y
    slope = float(np.tan(np.radians(pitch_deg)))
    rise = {
        "south": slope * north,  # rises north -> faces south
        "north": -slope * north,  # rises south -> faces north
        "west": slope * east,  # rises east  -> faces west
        "east": -slope * east,  # rises west  -> faces east
        "flat": np.zeros_like(east),
    }[faces]
    X = np.column_stack([east, north])
    return X, rise.astype(np.float64)


def test_south_facing_roof_sign_convention():
    # A 20-deg roof rising to the north faces due south: aspect ~180, tilt ~20.
    X, z = synthetic_roof(pitch_deg=20.0, faces="south")

    fit = roof_planes.fit_roof_plane(X, z)

    assert abs(fit["aspect_deg"] - _FACING_AZIMUTH["south"]) < 1.0
    assert abs(fit["tilt_deg"] - 20.0) < 1.0
    # A perfect noiseless plane is a fully confident fit.
    assert fit["low_confidence"] is False
    assert fit["inlier_ratio"] > 0.99


@pytest.mark.parametrize("faces", ["north", "east", "west"])
def test_aspect_matches_facing_direction(faces):
    # South is covered above; check the other three axes to pin the full compass.
    X, z = synthetic_roof(pitch_deg=25.0, faces=faces)

    fit = roof_planes.fit_roof_plane(X, z)

    expected = _FACING_AZIMUTH[faces]
    # aspect wraps at 360, so compare on the circle (north -> 0 could read ~360).
    delta = abs((fit["aspect_deg"] - expected + 180.0) % 360.0 - 180.0)
    assert delta < 1.0
    assert abs(fit["tilt_deg"] - 25.0) < 1.0


def test_flat_roof_has_near_zero_tilt():
    # A level roof has no meaningful aspect; only tilt is asserted (aspect is degenerate).
    X, z = synthetic_roof(pitch_deg=0.0, faces="flat")

    fit = roof_planes.fit_roof_plane(X, z)

    assert fit["tilt_deg"] < 1.0


def test_multiplane_single_planar_roof_returns_one_plane():
    # A single clean tilted plane has nothing left to peel off after the first fit --
    # sequential RANSAC must stop at len==1, matching fit_roof_plane's tilt/aspect.
    X, z = synthetic_roof(pitch_deg=20.0, faces="south")

    planes = roof_planes.fit_planes_multi(X, z)

    assert len(planes) == 1
    assert abs(planes[0]["tilt_deg"] - 20.0) < 1.0
    assert abs(planes[0]["aspect_deg"] - _FACING_AZIMUTH["south"]) < 1.0
    assert planes[0]["n_px"] == len(z)


def _synthetic_gable(
    pitch_deg: float, n_side: int = 20, n_east: int = 20
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Build a symmetric gable roof: two facets of known pitch meeting at a ridge.

    The south half (smaller `north` index) rises toward the ridge -- physically faces
    south (rises north, per `synthetic_roof`'s convention); the north half falls away
    from the ridge as `north` increases further -- physically faces north. `n_side` is
    the pixel run of *each* facet (so the roof is `2*n_side` px in the north direction).
    """
    east_idx, north_idx = np.meshgrid(np.arange(n_east), np.arange(2 * n_side), indexing="ij")
    east = east_idx.ravel().astype(np.float64)
    north = north_idx.ravel().astype(np.float64)
    slope = float(np.tan(np.radians(pitch_deg)))
    # A tent: rises from both edges to the ridge at north == n_side - 0.5.
    south_of_ridge = north < n_side
    z = np.where(south_of_ridge, slope * north, slope * (2 * n_side - 1 - north))
    X = np.column_stack([east, north])
    return X, z


def test_multiplane_recovers_two_facets():
    # A gable roof: two known-pitch facets, one facing south, one facing north, meeting
    # at a ridge. Sequential RANSAC should peel off each facet as its own plane.
    pitch_deg = 20.0
    X, z = _synthetic_gable(pitch_deg)

    planes = roof_planes.fit_planes_multi(X, z)

    assert len(planes) == 2
    for plane in planes:
        assert abs(plane["tilt_deg"] - pitch_deg) < 2.0
    aspects = [plane["aspect_deg"] for plane in planes]
    # one facet faces south (~180), the other faces north (~0/360) -- order unspecified.
    assert any(abs(a - 180.0) < 3.0 for a in aspects)
    assert any(min(abs(a - 0.0), abs(a - 360.0)) < 3.0 for a in aspects)
    # disjoint membership covering (almost) every pixel of this perfect synthetic roof.
    idx0, idx1 = set(planes[0]["inlier_idx"]), set(planes[1]["inlier_idx"])
    assert idx0.isdisjoint(idx1)
    assert len(idx0 | idx1) >= 0.95 * len(z)


def test_multiplane_drops_below_min_plane_px():
    # A clean 400-pixel roof plane plus 7 stray points whose elevation is wildly
    # inconsistent with any plane through the roof. After the roof is peeled off, only
    # 7 points remain -- strictly fewer than MIN_PLANE_PX (8), so no fit on them can
    # ever be recorded as a second plane, however RANSAC's internal sampling falls.
    X_roof, z_roof = synthetic_roof(pitch_deg=20.0, faces="south")
    n_stray = 7
    assert n_stray < roof_planes.MIN_PLANE_PX
    stray_east = np.arange(n_stray, dtype=np.float64)
    stray_north = np.full(n_stray, 1000.0)  # far outside the roof's coordinate range
    X_stray = np.column_stack([stray_east, stray_north])
    z_stray = np.zeros(n_stray)  # the roof's plane would predict ~364 m here

    X = np.vstack([X_roof, X_stray])
    z = np.concatenate([z_roof, z_stray])

    planes = roof_planes.fit_planes_multi(X, z)

    assert len(planes) == 1
    assert planes[0]["n_px"] == len(z_roof)


def test_too_few_pixels_are_flagged_not_fitted():
    # Below MIN_PX pixels, no plane is trustworthy: return NaN geometry, flagged low-confidence
    # (ADR-0002) rather than a bogus tilt/aspect from a handful of points.
    X = np.array([[0.0, 0.0], [2.0, 0.0], [0.0, 2.0], [2.0, 2.0]])
    z = np.array([10.0, 10.2, 9.8, 10.1])
    assert len(z) < roof_planes.MIN_PX

    fit = roof_planes.fit_roof_plane(X, z)

    assert np.isnan(fit["tilt_deg"])
    assert np.isnan(fit["aspect_deg"])
    assert fit["n_px"] == 4
    assert fit["low_confidence"] is True
