"""Unit tests for `dsm.build_dsm`'s self-cache (tier 1 of the tiered per-tile cache, ADR-0014).

A matching `<out_path>.key` sidecar (written by `cache.write_key_sidecar`) must short-circuit
`build_dsm` before it ever calls `_search_dsm_hrefs` (the network entry point — a STAC search
against Planetary Computer) — so a cache hit performs no network fetch and no rebuild. A
missing/mismatched sidecar, or no `cache_key` at all, must proceed into the fetch.

These tests never touch the network or GRASS: `dsm._search_dsm_hrefs` is monkeypatched to
either raise (proving it was never called) or raise a sentinel (proving it WAS called), so
the assertion is about which code path ran, not about real STAC/mosaic/reproject behavior.
Offline despite the "network fetch" framing — see the `integration` marker note below.
"""

from __future__ import annotations

import pytest
from shapely.geometry import box

from rooftop_solar import cache, dsm

_AOI = box(0, 0, 1, 1)


@pytest.mark.integration
def test_cache_hit_skips_network_and_rebuild(tmp_path, monkeypatch):
    """A matching sidecar returns out_path without calling `_search_dsm_hrefs`."""
    p = tmp_path / "tile_dsm.tif"
    p.write_bytes(b"dummy dsm bytes")
    cache.write_key_sidecar(p, "dsm-key-v1")

    def _no_network(aoi):
        raise AssertionError("network fetch happened on a cache hit")

    monkeypatch.setattr(dsm, "_search_dsm_hrefs", _no_network)

    result = dsm.build_dsm(_AOI, out_path=p, cache_key="dsm-key-v1")

    assert result == p


def test_cache_mismatch_attempts_rebuild(tmp_path, monkeypatch):
    """A sidecar holding a different key must fall through into the fetch (recompute)."""
    p = tmp_path / "tile_dsm.tif"
    p.write_bytes(b"dummy dsm bytes")
    cache.write_key_sidecar(p, "old-key")

    def _fetch(aoi):
        raise RuntimeError("FETCH_ATTEMPTED")

    monkeypatch.setattr(dsm, "_search_dsm_hrefs", _fetch)

    with pytest.raises(RuntimeError, match="FETCH_ATTEMPTED"):
        dsm.build_dsm(_AOI, out_path=p, cache_key="dsm-key-v1")


def test_no_cache_key_always_attempts_rebuild(tmp_path, monkeypatch):
    """With no `cache_key`, build_dsm must always proceed to the fetch — no sidecar, ever."""
    out_path = tmp_path / "x.tif"

    def _fetch(aoi):
        raise RuntimeError("FETCH_ATTEMPTED")

    monkeypatch.setattr(dsm, "_search_dsm_hrefs", _fetch)

    with pytest.raises(RuntimeError, match="FETCH_ATTEMPTED"):
        dsm.build_dsm(_AOI, out_path=out_path)

    assert not cache.sidecar_path(out_path).exists()
