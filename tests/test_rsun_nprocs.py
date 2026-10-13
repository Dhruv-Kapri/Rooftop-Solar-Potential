"""Unit tests for threading `nprocs` (r.sun's OpenMP thread count) from the CLI / `run_city`
down to each tile's `r.sun` call (issue #16).

`nprocs` doesn't change results, so it must never enter a cache key. Everything here is faked
-- no GRASS, no network.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import box

from rooftop_solar import config, pipeline, radiation, tiling

REPO_ROOT = Path(__file__).resolve().parents[1]


class _Stop(Exception):
    pass


class _RecordingGs:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def run_command(self, module, **kwargs):
        self.calls.append((module, kwargs))
        if module == "r.sun":
            raise _Stop


def test_run_r_sun_forwards_nprocs(tmp_path):
    gs = _RecordingGs()
    with pytest.raises(_Stop):
        radiation.run_r_sun(gs, 172, str(tmp_path), nprocs=8)
    assert gs.calls[-1][0] == "r.sun"
    assert gs.calls[-1][1]["nprocs"] == 8


def test_run_r_sun_default_nprocs_is_one(tmp_path):
    gs = _RecordingGs()
    with pytest.raises(_Stop):
        radiation.run_r_sun(gs, 172, str(tmp_path))
    assert gs.calls[-1][1]["nprocs"] == 1


def _write_tiny_dsm(path) -> None:
    profile = {
        "driver": "GTiff",
        "height": 4,
        "width": 4,
        "count": 1,
        "dtype": "float32",
        "crs": CRS.from_string("EPSG:6347"),
        "transform": from_origin(500_000.0, 4_310_000.0, 2.0, 2.0),
        "nodata": -9999.0,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.full((4, 4), 10.0, dtype="float32"), 1)


def test_surface_irradiance_forwards_nprocs_to_every_day(tmp_path, monkeypatch):
    dsm_path = tmp_path / "dsm.tif"
    _write_tiny_dsm(dsm_path)
    out_path = tmp_path / "insol.tif"

    class _NoopGs:
        def run_command(self, *_a, **_k):
            pass

    @contextlib.contextmanager
    def fake_session(*_a, **_k):
        yield _NoopGs()

    seen: list[int] = []

    def fake_run_r_sun(_gs, _day, _workdir, nprocs=1):
        seen.append(nprocs)
        return np.full((4, 4), 1000.0), None

    monkeypatch.setattr(radiation, "grass_session", fake_session)
    monkeypatch.setattr(radiation, "run_r_sun", fake_run_r_sun)

    radiation.surface_irradiance(dsm_path, day_range=[172, 173], nprocs=4, out_path=out_path)

    assert seen == [4, 4]
    assert out_path.exists()


def test_surface_irradiance_rejects_nonpositive_nprocs(tmp_path):
    with pytest.raises(ValueError, match="nprocs"):
        radiation.surface_irradiance(tmp_path / "missing.tif", nprocs=0)


def _run_city_recording(monkeypatch, tiles_dir, nprocs):
    calls: list[dict] = []

    def fake_score_tile(tile, **kwargs):
        calls.append(kwargs)
        return pipeline._empty_roofs()

    monkeypatch.setattr(pipeline, "_score_tile", fake_score_tile)
    ox, oy = config.GRID_ORIGIN
    aoi = box(ox + 100, oy + 100, ox + 2 * config.TILE_SIZE_M - 100, oy + 1000)  # 2 tiles
    pipeline.run_city(aoi=aoi, tiles_dir=tiles_dir, write_outputs=False, nprocs=nprocs)
    return calls


def test_run_city_forwards_nprocs_and_keys_ignore_it(tmp_path, monkeypatch):
    calls_1 = _run_city_recording(monkeypatch, tmp_path / "a", nprocs=1)
    calls_8 = _run_city_recording(monkeypatch, tmp_path / "b", nprocs=8)

    assert len(calls_1) == len(calls_8) >= 2
    assert all(c["nprocs"] == 1 for c in calls_1)
    assert all(c["nprocs"] == 8 for c in calls_8)
    assert [c["radiation_cache_key"] for c in calls_1] == [
        c["radiation_cache_key"] for c in calls_8
    ]
    assert [c["dsm_cache_key"] for c in calls_1] == [c["dsm_cache_key"] for c in calls_8]
    m1 = json.loads((tmp_path / "a" / pipeline.MANIFEST_NAME).read_text())
    m8 = json.loads((tmp_path / "b" / pipeline.MANIFEST_NAME).read_text())
    assert m1 == m8


def test_cli_forwards_nprocs(tmp_path, monkeypatch):
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
            "--nprocs",
            "6",
            "--no-aggregate",
            "--tiles-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path),
        ],
    )

    script.main()

    assert recorded["nprocs"] == 6


def test_score_tile_forwards_nprocs_to_surface_irradiance(tmp_path, monkeypatch):
    """Covers the middle hops `_score_tile` -> `_score_roofs` -> `surface_irradiance`."""
    ox, oy = config.GRID_ORIGIN
    size = config.TILE_SIZE_M
    (tile,) = tiling.make_grid(box(ox + 10, oy + 10, ox + 20, oy + 20), size, 100.0, (ox, oy))
    building = gpd.GeoDataFrame(
        geometry=[box(ox + 50, oy + 50, ox + 60, oy + 60)], crs=config.WORKING_CRS
    ).to_crs("EPSG:4326")

    seen: list[int] = []

    def fake_surface_irradiance(_dsm, **kwargs):
        seen.append(kwargs["nprocs"])
        raise _Stop

    monkeypatch.setattr(pipeline.footprints, "load_footprints", lambda *_a, **_k: building)
    monkeypatch.setattr(pipeline.dsm, "build_dsm", lambda *_a, **_k: tmp_path / "dsm.tif")
    monkeypatch.setattr(pipeline.radiation, "surface_irradiance", fake_surface_irradiance)

    with pytest.raises(_Stop):
        pipeline._score_tile(
            tile,
            tile_size_m=size,
            origin=(ox, oy),
            footprints_source="ms-buildings",
            day_range=[172],
            dsm_out_path=tmp_path / "dsm.tif",
            nprocs=3,
        )
    assert seen == [3]
