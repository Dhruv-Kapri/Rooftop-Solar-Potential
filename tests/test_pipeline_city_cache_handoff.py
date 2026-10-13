"""run_city reuses a tile's cached roofs parquet only when its sidecar vouches for the bytes
(issue #18, ADR-0014: the tile cache is rsynced between machines, so a truncated, swapped or
sidecar-less parquet must be recomputed, never reused and never crash the run). Offline: no
network, no GRASS — `_score_tile` and `radiation.grass_version` are faked; the tile grid is real."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import box

from rooftop_solar import cache, config, pipeline, radiation

# Spans 2 tiles at tile_size_m=2000 (a 3 km wide strip), fixed origin.
_AOI = box(
    config.GRID_ORIGIN[0] + 100,
    config.GRID_ORIGIN[1] + 100,
    config.GRID_ORIGIN[0] + 3100,
    config.GRID_ORIGIN[1] + 900,
)


def _run_city(tmp_path, monkeypatch) -> list[tuple[int, int]]:
    """One run_city over `_AOI` with a fake scorer; return the tiles the scorer was called for."""
    scored: list[tuple[int, int]] = []

    def fake_score_tile(tile, **kw):
        scored.append((tile.row, tile.col))
        return gpd.GeoDataFrame(
            {"energy_density_kwh_m2": [10.0], "usable_area_m2": [1.0], "suitability": [100.0]},
            geometry=[box(0, 0, 1, 1)],
            crs=config.WORKING_CRS,
        )

    monkeypatch.setattr(radiation, "grass_version", lambda: "8.5.0")
    monkeypatch.setattr(pipeline, "_score_tile", fake_score_tile)
    pipeline.run_city(aoi=_AOI, tiles_dir=tmp_path, write_outputs=False)
    return scored


def _roofs_parquets(tmp_path):
    return sorted(tmp_path.glob("tile_*_roofs.parquet"))


def test_second_run_with_intact_cache_reuses_every_tile(tmp_path, monkeypatch):
    first = _run_city(tmp_path, monkeypatch)
    assert len(first) >= 2

    assert _run_city(tmp_path, monkeypatch) == []


def test_truncated_roofs_parquet_recomputes_only_that_tile(tmp_path, monkeypatch):
    first = _run_city(tmp_path, monkeypatch)
    victim = _roofs_parquets(tmp_path)[0]
    victim.write_bytes(victim.read_bytes()[:20])  # truncated; sidecar left intact

    second = _run_city(tmp_path, monkeypatch)  # must not crash in gpd.read_parquet

    assert len(second) == 1
    assert second[0] in first
    assert victim.name == f"tile_{second[0][0]}_{second[0][1]}_roofs.parquet"
    # the recomputed parquet is whole again, so the next run reuses everything
    assert _run_city(tmp_path, monkeypatch) == []


def test_done_tile_whose_parquet_has_no_sidecar_is_recomputed(tmp_path, monkeypatch):
    _run_city(tmp_path, monkeypatch)
    victim = _roofs_parquets(tmp_path)[0]
    cache.sidecar_path(victim).unlink()  # e.g. a cache written before sidecars existed

    second = _run_city(tmp_path, monkeypatch)

    assert len(second) == 1
    assert victim.name == f"tile_{second[0][0]}_{second[0][1]}_roofs.parquet"
