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

from rooftop_solar import config, pipeline

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
