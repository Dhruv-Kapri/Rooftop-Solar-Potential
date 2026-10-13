"""run_city scores tiles in worker processes (issue #27, ADR-0009).

`workers` only changes speed: the merged city frame and the manifest must be identical at any
worker count, and `workers` must stay out of every cache key. Offline: no GRASS, no network —
`radiation.grass_version` is faked in the coordinator, and the per-tile spine is replaced by the
module-level fakes below. They MUST be module-level: a spawned worker unpickles `_score_tile` by
reference, so the monkeypatched fake has to be importable by name in the child (pytest puts
`tests/` on `sys.path` and spawn forwards it).

Worker processes can't update coordinator-side counters, so the fakes leave one marker file per
call under `<tiles_dir>/calls/` instead.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import geopandas as gpd
import pytest
from geopandas.testing import assert_geodataframe_equal
from shapely.geometry import Point, box

from rooftop_solar import config, pipeline, radiation

REPO_ROOT = Path(__file__).resolve().parents[1]

# A 3 km square from the fixed origin = 2 x 2 tiles at the default 2 km tile size.
_AOI = box(*config.GRID_ORIGIN, config.GRID_ORIGIN[0] + 3000, config.GRID_ORIGIN[1] + 3000)
_N_TILES = 4
_BAD_TILE = (0, 1)  # (row, col) the failing fake raises for


def _mark_call(tile, dsm_out_path) -> None:
    calls = Path(dsm_out_path).parent / "calls"
    calls.mkdir(exist_ok=True)
    (calls / f"{tile.row}_{tile.col}").touch()


def _roofs_for(tile) -> gpd.GeoDataFrame:
    """Two deterministic roofs per tile, with the columns the city merge/suitability need."""
    x0, y0, _, _ = tile.core.bounds
    return gpd.GeoDataFrame(
        {
            "tile": [f"{tile.row}_{tile.col}"] * 2,
            "usable_area_m2": [50.0, 80.0],
            "energy_density_kwh_m2": [100.0 + 7 * tile.row + 3 * tile.col, 120.0 - tile.row],
        },
        geometry=[Point(x0 + 10, y0 + 10), Point(x0 + 20, y0 + 20)],
        crs=config.WORKING_CRS,
    )


def fake_score_tile(tile, *, dsm_out_path, **_kw):
    # Later grid tiles finish first, so completion order differs from grid order.
    time.sleep(0.5 / (1 + tile.col + 2 * tile.row))
    _mark_call(tile, dsm_out_path)
    return _roofs_for(tile)


def fake_score_tile_one_bad(tile, *, dsm_out_path, **kw):
    if (tile.row, tile.col) == _BAD_TILE:
        _mark_call(tile, dsm_out_path)
        raise ValueError("boom: no DSM coverage")
    return fake_score_tile(tile, dsm_out_path=dsm_out_path, **kw)


def fake_score_tile_one_crash(tile, *, dsm_out_path, **kw):
    # The worker PROCESS dies (as if OOM-killed) — no Python exception to carry back.
    if (tile.row, tile.col) == _BAD_TILE:
        os._exit(1)
    return fake_score_tile(tile, dsm_out_path=dsm_out_path, **kw)


def _run(tiles_dir, monkeypatch, fake, **kwargs):
    monkeypatch.setattr(pipeline, "_score_tile", fake)
    monkeypatch.setattr(radiation, "grass_version", lambda: "8.5.0")  # no GRASS needed
    city = pipeline.run_city(aoi=_AOI, tiles_dir=tiles_dir, write_outputs=False, **kwargs)
    manifest = json.loads((tiles_dir / pipeline.MANIFEST_NAME).read_text())
    return city, manifest


def _called(tiles_dir) -> set[str]:
    return {p.name for p in (tiles_dir / "calls").glob("*")}


def _reset_calls(tiles_dir) -> None:
    for p in (tiles_dir / "calls").glob("*"):
        p.unlink()


@pytest.fixture(scope="module")
def good_runs(tmp_path_factory):
    """One workers=1 and one workers=3 run, each in its own tiles_dir (spawn is slow-ish)."""
    mp = pytest.MonkeyPatch()
    try:
        out = {}
        for workers in (1, 3):
            d = tmp_path_factory.mktemp(f"w{workers}")
            out[workers] = _run(d, mp, fake_score_tile, workers=workers)
    finally:
        mp.undo()
    return out


def test_pooled_city_frame_equals_single_worker(good_runs):
    city_1, _ = good_runs[1]
    city_3, _ = good_runs[3]
    assert len(city_1) == 2 * _N_TILES
    assert_geodataframe_equal(city_3, city_1)


def test_pooled_manifest_equals_single_worker(good_runs):
    # Identical stamps also show `workers` is in no cache key (ADR-0014): a run resumes
    # identically at any worker count.
    _, manifest_1 = good_runs[1]
    _, manifest_3 = good_runs[3]
    assert len(manifest_1) == _N_TILES
    assert manifest_3 == manifest_1


def _assert_one_bad_tile(city, manifest):
    bad_key = f"{_BAD_TILE[0]}_{_BAD_TILE[1]}"
    assert manifest[bad_key]["status"] == "failed"
    assert "ValueError" in manifest[bad_key]["error"]
    assert "no DSM coverage" in manifest[bad_key]["error"]
    others = {k: v for k, v in manifest.items() if k != bad_key}
    assert len(others) == _N_TILES - 1
    assert all(v["status"] == "done" for v in others.values())
    assert len(city) == 2 * (_N_TILES - 1)
    assert bad_key not in set(city["tile"])


def test_bad_tile_is_recorded_failed_and_batch_continues(tmp_path, monkeypatch):
    _assert_one_bad_tile(*_run(tmp_path, monkeypatch, fake_score_tile_one_bad, workers=1))


def test_pooled_bad_tile_then_resume_skips_done_tiles_and_retries_failed(tmp_path, monkeypatch):
    # Pooled runs spawn fresh interpreters (slow to import), so failure + resume share one test.
    _assert_one_bad_tile(*_run(tmp_path, monkeypatch, fake_score_tile_one_bad, workers=2))
    assert _called(tmp_path) == {f"{r}_{c}" for r in (0, 1) for c in (0, 1)}
    _reset_calls(tmp_path)

    city, manifest = _run(tmp_path, monkeypatch, fake_score_tile, workers=2)

    assert _called(tmp_path) == {f"{_BAD_TILE[0]}_{_BAD_TILE[1]}"}  # only the failed tile reran
    assert all(v["status"] == "done" for v in manifest.values())
    assert len(city) == 2 * _N_TILES
    assert list(city["tile"][::2]) == ["0_0", "0_1", "1_0", "1_1"]  # grid order, not finish order


def test_worker_process_crash_does_not_abort_batch_and_resume_recovers(tmp_path, monkeypatch):
    # A dead worker breaks the whole pool: the crashed tile (and any tile not yet finished) is
    # recorded failed, the batch still returns, and a resumed run completes the city.
    _, manifest = _run(tmp_path, monkeypatch, fake_score_tile_one_crash, workers=2)

    bad_key = f"{_BAD_TILE[0]}_{_BAD_TILE[1]}"
    assert len(manifest) == _N_TILES
    assert manifest[bad_key]["status"] == "failed"
    assert "BrokenProcessPool" in manifest[bad_key]["error"]

    city, manifest = _run(tmp_path, monkeypatch, fake_score_tile, workers=2)

    assert all(v["status"] == "done" for v in manifest.values())
    assert len(city) == 2 * _N_TILES


def test_workers_does_not_leak_into_scoring_kwargs(tmp_path, monkeypatch):
    seen = []

    def _spy(tile, **kw):
        seen.append(kw)
        return _roofs_for(tile)

    _run(tmp_path, monkeypatch, _spy, workers=1)
    assert seen and all("workers" not in kw for kw in seen)


@pytest.mark.parametrize("bad", [0, -1, True, 2.5, "2"])
def test_run_city_rejects_invalid_workers_before_any_tile_work(tmp_path, monkeypatch, bad):
    def _boom(*a, **k):
        raise AssertionError("tile work started")

    monkeypatch.setattr(pipeline, "_score_tile", _boom)
    monkeypatch.setattr(radiation, "grass_version", _boom)
    with pytest.raises(ValueError, match="workers"):
        pipeline.run_city(aoi=_AOI, tiles_dir=tmp_path, write_outputs=False, workers=bad)


def test_cli_forwards_workers(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "run_city_script", REPO_ROOT / "scripts/run_city.py"
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    recorded: dict = {}

    def fake_run_city(**kwargs):
        recorded.update(kwargs)
        return gpd.GeoDataFrame(geometry=[], crs=config.WORKING_CRS)

    monkeypatch.setattr(script.pipeline, "run_city", fake_run_city)
    monkeypatch.setattr(script, "_report_runlog", lambda *a, **k: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_city.py",
            "--workers",
            "3",
            "--no-aggregate",
            "--tiles-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path),
        ],
    )

    script.main()

    assert recorded["workers"] == 3
