"""End-to-end integration smoke for the city runner `run_city` (needs network + GRASS).

Runs the real Stage-1 spine (footprints + 3DEP DSM fetch + a real GRASS r.sun pass) over a
small multi-tile patch of DC, for a single day (fast, not calibrated), and asserts Part 2-2's
two headline invariants (stage-2-part2-plan.md §5/§6/§7): **seam conservation** on real data
(Σ per-tile roof counts == the merged city count) and **idempotency** (a second resumable run
skips the cached tiles and reproduces an identical result). The full-DC 12-day run is the
documented manual batch job, not a test. Deselected by default; run with `pytest -m integration`.
"""

from __future__ import annotations

import json
import shutil

import pytest
from pyproj import Transformer
from shapely.geometry import box
from shapely.ops import transform as shp_transform

from rooftop_solar import config, dsm, pipeline, radiation

# The Stage-1 smoke box (known 3DEP + building coverage), ~550 m x ~670 m — big enough to
# straddle several cells of a deliberately small 300 m test grid, so the run is genuinely
# multi-tile (the point of this smoke). Reprojected to the metric working CRS that run_city
# tiles in.
_AOI_WGS84 = box(-77.0782, 38.9170, -77.0718, 38.9230)
_TO_WORKING = Transformer.from_crs("EPSG:4326", config.WORKING_CRS, always_xy=True).transform
_AOI_WORKING = shp_transform(_TO_WORKING, _AOI_WGS84)

_TILE_SIZE_M = 300.0
_BUFFER_M = 100.0


def _tile_cache_mtimes(tiles_dir) -> dict[str, float]:
    return {p.name: p.stat().st_mtime_ns for p in tiles_dir.glob("tile_*_roofs.parquet")}


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("grass") is None, reason="GRASS not on PATH")
def test_run_city_conserves_and_is_idempotent(tmp_path):
    tiles_dir = tmp_path / "tiles"

    kwargs = dict(
        aoi=_AOI_WORKING,
        tile_size_m=_TILE_SIZE_M,
        buffer_m=_BUFFER_M,
        origin=config.GRID_ORIGIN,
        day_range=[172],  # single summer day — fast smoke, not a calibrated annual sum
        tiles_dir=tiles_dir,
        output_dir=tmp_path,
    )

    # --- first run: cold cache ---
    first = pipeline.run_city(**kwargs)

    # Genuinely multi-tile, and it produced some roofs.
    manifest = json.loads((tiles_dir / pipeline.MANIFEST_NAME).read_text())
    done = {k: v for k, v in manifest.items() if v["status"] == "done"}
    assert len(done) >= 2, "smoke AOI should span more than one tile"
    assert len(first) > 0

    # Seam conservation on real data: Σ per-tile roof counts == the merged city roof count —
    # no building dropped on a seam, none double-counted (§5, the floor-partition guarantee).
    assert sum(entry["n_roofs"] for entry in done.values()) == len(first)

    # The city per-roof GeoParquet deliverable was written.
    assert (tmp_path / pipeline.CITY_ROOFS_PARQUET_NAME).exists()

    # --- second run: warm cache, resume=True ---
    mtimes_before = _tile_cache_mtimes(tiles_dir)
    second = pipeline.run_city(**kwargs)
    mtimes_after = _tile_cache_mtimes(tiles_dir)

    # Idempotency: the cached tiles were skipped (not recomputed → parquet files untouched)...
    assert mtimes_after == mtimes_before
    # ...and the city result is identical (deterministic; order-independent).
    assert len(second) == len(first)
    assert second["capacity_kw"].sum() == first["capacity_kw"].sum()
    assert second["annual_energy_kwh"].sum() == first["annual_energy_kwh"].sum()
    assert second["suitability"].tolist() == first["suitability"].tolist()


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("grass") is None, reason="GRASS not on PATH")
def test_run_city_scoring_bump_reuses_dsm_and_radiation(tmp_path, monkeypatch):
    """Seam A2: a scoring-tier-only version bump reuses the cached DSM + r.sun raster.

    ADR-0014 / spec #4 user-story 1 — the whole point of the tiered cache is that a
    downstream-only change (e.g. a roof-plane/usable-area/yield tweak, bumping
    `SCORING_VERSION`) must NOT force the DSM to be re-fetched or `r.sun` to be rerun: only
    `dsm_key`/`radiation_key` invalidate those tiers, and neither changes here. We simulate the
    bump by monkeypatching `pipeline.scoring_key` itself (cheaper than a real version bump, same
    effect on the manifest stamp) and assert the expensive DSM/r.sun paths are never touched.
    """
    tiles_dir = tmp_path / "tiles"

    kwargs = dict(
        aoi=_AOI_WORKING,
        tile_size_m=_TILE_SIZE_M,
        buffer_m=_BUFFER_M,
        origin=config.GRID_ORIGIN,
        day_range=[172],
        tiles_dir=tiles_dir,
        output_dir=tmp_path,
    )

    # --- cold run: real network + GRASS, populates the DSM/insolation cache tiers ---
    first = pipeline.run_city(**kwargs)

    manifest = json.loads((tiles_dir / pipeline.MANIFEST_NAME).read_text())
    done_nonempty = {
        k: v for k, v in manifest.items() if v["status"] == "done" and v["n_roofs"] > 0
    }
    assert done_nonempty, "smoke AOI should produce at least one non-empty tile"

    dsm_mtimes_before: dict[str, int] = {}
    insol_mtimes_before: dict[str, int] = {}
    for key in done_nonempty:
        row, col = key.split("_")
        dsm_tif = tiles_dir / f"tile_{row}_{col}_dsm.tif"
        insol_tif = tiles_dir / f"tile_{row}_{col}_insolation.tif"
        assert dsm_tif.exists()
        assert (tiles_dir / f"tile_{row}_{col}_dsm.tif.key").exists()
        assert insol_tif.exists()
        assert (tiles_dir / f"tile_{row}_{col}_insolation.tif.key").exists()
        dsm_mtimes_before[key] = dsm_tif.stat().st_mtime_ns
        insol_mtimes_before[key] = insol_tif.stat().st_mtime_ns

    # --- simulate a scoring-only version bump: only the manifest stamp changes; dsm_key/
    # radiation_key (and therefore the DSM/insolation sidecars) are untouched. ---
    real_scoring_key = pipeline.scoring_key
    monkeypatch.setattr(
        pipeline, "scoring_key", lambda **kw: real_scoring_key(**kw) + "-scorebump"
    )

    # Guard against the expensive work: neither a DSM network fetch nor a real r.sun pass may
    # run on a scoring-only rerun (footprints ARE legitimately re-fetched — cheap, not a cache
    # tier — so we deliberately do NOT monkeypatch footprints here).
    def _no_dsm_fetch(*_a, **_k):
        raise AssertionError("DSM network fetch on a scoring-only rerun")

    def _no_grass(*_a, **_k):
        raise AssertionError("r.sun ran on a scoring-only rerun")

    monkeypatch.setattr(dsm, "_search_dsm_hrefs", _no_dsm_fetch)
    monkeypatch.setattr(radiation, "grass_session", _no_grass)

    # --- warm run: must complete without raising, proving the DSM + insolation caches were
    # reused (only scoring reran) ---
    second = pipeline.run_city(**kwargs)

    for key in done_nonempty:
        row, col = key.split("_")
        dsm_tif = tiles_dir / f"tile_{row}_{col}_dsm.tif"
        insol_tif = tiles_dir / f"tile_{row}_{col}_insolation.tif"
        assert dsm_tif.stat().st_mtime_ns == dsm_mtimes_before[key], "DSM was rebuilt, not reused"
        assert insol_tif.stat().st_mtime_ns == insol_mtimes_before[key], (
            "insolation raster was rebuilt, not reused"
        )

    assert len(second) == len(first)
    assert second["capacity_kw"].sum() == first["capacity_kw"].sum()
    assert second["annual_energy_kwh"].sum() == first["annual_energy_kwh"].sum()
