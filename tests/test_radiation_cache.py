"""Unit tests for `radiation.surface_irradiance`'s self-cache (tier 2, ADR-0014).

A matching `<out_path>.key` sidecar (written by `cache.write_key_sidecar`) must short-circuit
`surface_irradiance` before it ever opens the DSM or enters a GRASS session -- so a cache hit
runs no `r.sun` at all. A missing/mismatched sidecar, or `out_path`/`cache_key` given alone,
must proceed into the real computation.

These tests never touch GRASS: `radiation.grass_session` (the context manager wrapping every
`r.sun` call) is monkeypatched to either raise (proving it was never entered -- a cache hit) or
raise a sentinel `RuntimeError` (proving it WAS entered -- a cache miss), so the assertion is
about which code path ran, not about real `r.sun` output. Marked `integration` to match this
module's existing `test_radiation_integration.py` suite (all of this file's tests exercise
`surface_irradiance`'s r.sun-skip plumbing, even though none needs GRASS on PATH); the default
unit tier stays green because they're deselected along with it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from rooftop_solar import cache, radiation

WORKING_CRS = "EPSG:6347"  # NAD83(2011) / UTM 18N -- matches test_radiation_integration.py


def _write_tiny_dsm(path) -> None:
    """A minimal valid, readable 4x4 float32 DSM -- just enough to pass the CRS/shape read."""
    dsm = np.full((4, 4), 10.0, dtype="float32")
    transform = from_origin(500_000.0, 4_310_000.0, 2.0, 2.0)
    profile = {
        "driver": "GTiff",
        "height": 4,
        "width": 4,
        "count": 1,
        "dtype": "float32",
        "crs": CRS.from_string(WORKING_CRS),
        "transform": transform,
        "nodata": -9999.0,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(dsm, 1)


def _raise_on_call(*_args, **_kwargs):
    raise AssertionError("r.sun ran on a cache hit")


def _raise_sentinel(*_args, **_kwargs):
    raise RuntimeError("RSUN_ATTEMPTED")


@pytest.mark.integration
def test_cache_hit_skips_r_sun(tmp_path, monkeypatch):
    """A matching sidecar returns out_path without entering grass_session or reading the DSM."""
    p = tmp_path / "tile_insolation.tif"
    p.write_bytes(b"dummy raster bytes")
    cache.write_key_sidecar(p, "rad-key-v1")

    monkeypatch.setattr(radiation, "grass_session", _raise_on_call)

    # dsm_path doesn't even need to exist -- a hit returns before the DSM is ever opened.
    result = radiation.surface_irradiance("any_dsm.tif", out_path=p, cache_key="rad-key-v1")

    assert result == Path(p)


@pytest.mark.integration
def test_cache_mismatch_attempts_r_sun(tmp_path, monkeypatch):
    """A sidecar holding a different key must fall through into the real computation."""
    p = tmp_path / "tile_insolation.tif"
    p.write_bytes(b"dummy raster bytes")
    cache.write_key_sidecar(p, "old-key")

    dsm_path = tmp_path / "dsm.tif"
    _write_tiny_dsm(dsm_path)

    monkeypatch.setattr(radiation, "grass_session", _raise_sentinel)

    with pytest.raises(RuntimeError, match="RSUN_ATTEMPTED"):
        radiation.surface_irradiance(dsm_path, out_path=p, cache_key="rad-key-v1")


@pytest.mark.integration
def test_no_cache_key_never_skips(tmp_path, monkeypatch):
    """`out_path` alone (no `cache_key`) must never skip, even with a matching-looking sidecar."""
    p = tmp_path / "tile_insolation.tif"
    p.write_bytes(b"dummy raster bytes")
    cache.write_key_sidecar(p, "rad-key-v1")  # present, but cache_key=None below

    dsm_path = tmp_path / "dsm.tif"
    _write_tiny_dsm(dsm_path)

    monkeypatch.setattr(radiation, "grass_session", _raise_sentinel)

    with pytest.raises(RuntimeError, match="RSUN_ATTEMPTED"):
        radiation.surface_irradiance(dsm_path, out_path=p, cache_key=None)


@pytest.mark.integration
def test_no_out_path_never_skips(tmp_path, monkeypatch):
    """`cache_key` alone (no `out_path`) must never skip -- both are required for a hit."""
    dsm_path = tmp_path / "dsm.tif"
    _write_tiny_dsm(dsm_path)

    monkeypatch.setattr(radiation, "grass_session", _raise_sentinel)

    with pytest.raises(RuntimeError, match="RSUN_ATTEMPTED"):
        radiation.surface_irradiance(dsm_path, out_path=None, cache_key="rad-key-v1")
