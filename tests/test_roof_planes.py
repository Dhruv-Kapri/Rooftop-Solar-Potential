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
