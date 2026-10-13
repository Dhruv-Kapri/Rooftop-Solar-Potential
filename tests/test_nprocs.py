"""Unit tests for the `nprocs` (r.sun thread count) plumbing (issue #16).

`nprocs` flows run_city -> _score_tile -> _score_roofs -> surface_irradiance -> run_r_sun ->
GRASS `r.sun`. Each hop is tested on its own so a test fails if any one of them drops it. It
only changes speed, never results, so it must stay out of every cache key and the manifest.
Offline: GRASS, the network and the per-tile spine are all faked.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pytest
from shapely.geometry import box

from rooftop_solar import config, pipeline, radiation

REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakeGrass:
    """Records `run_command` calls; stands in for the `grass.script` module."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def run_command(self, name, **kwargs):
        self.calls.append((name, kwargs))
        raise RuntimeError("STOP_BEFORE_EXPORT")  # skip the r.out.gdal/rasterio half


def _r_sun_kwargs(**call_kwargs) -> dict:
    gs = _FakeGrass()
    with pytest.raises(RuntimeError, match="STOP_BEFORE_EXPORT"):
        radiation.run_r_sun(gs, 172, "/tmp/unused", **call_kwargs)
    return gs.calls[0][1]


def test_run_r_sun_forwards_nprocs_to_grass():
    assert _r_sun_kwargs(nprocs=4)["nprocs"] == 4


def test_run_r_sun_defaults_to_one_thread():
    assert _r_sun_kwargs()["nprocs"] == 1


def test_surface_irradiance_rejects_nprocs_below_one(tmp_path):
    with pytest.raises(ValueError, match="nprocs"):
        radiation.surface_irradiance(tmp_path / "dsm.tif", nprocs=0)


def test_surface_irradiance_forwards_nprocs_to_run_r_sun(tmp_path, monkeypatch):
    import contextlib

    import numpy as np
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_origin

    dsm_path = tmp_path / "dsm.tif"
    profile = {
        "driver": "GTiff",
        "height": 4,
        "width": 4,
        "count": 1,
        "dtype": "float32",
        "crs": CRS.from_epsg(6347),
        "transform": from_origin(500_000.0, 4_310_000.0, 2.0, 2.0),
    }
    with rasterio.open(dsm_path, "w", **profile) as dst:
        dst.write(np.full((4, 4), 10.0, dtype="float32"), 1)

    seen: list[int] = []

    def _fake_run_r_sun(gs, day, workdir, nprocs=1):
        seen.append(nprocs)
        raise RuntimeError("STOP")

    monkeypatch.setattr(
        radiation, "grass_session", lambda *a, **k: contextlib.nullcontext(_FakeGrass2())
    )
    monkeypatch.setattr(radiation, "run_r_sun", _fake_run_r_sun)
    with pytest.raises(RuntimeError, match="STOP"):
        radiation.surface_irradiance(dsm_path, day_range=[172], nprocs=3)
    assert seen == [3]


class _FakeGrass2:
    def run_command(self, *_a, **_k):
        return None


def test_score_roofs_forwards_nprocs_to_surface_irradiance(monkeypatch):
    seen = {}

    def _fake_surface_irradiance(dsm_path, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("STOP")

    monkeypatch.setattr(pipeline.dsm, "build_dsm", lambda *a, **k: "dsm.tif")
    monkeypatch.setattr(pipeline.radiation, "surface_irradiance", _fake_surface_irradiance)
    with pytest.raises(RuntimeError, match="STOP"):
        pipeline._score_roofs(None, None, day_range=None, nprocs=5)
    assert seen["nprocs"] == 5


def test_score_tile_forwards_nprocs_to_score_roofs(monkeypatch):
    seen = {}
    tile = SimpleNamespace(buffered=box(0, 0, 10, 10))
    monkeypatch.setattr(pipeline, "_working_to_wgs84", lambda g: g)
    monkeypatch.setattr(
        pipeline.footprints, "load_footprints", lambda *a, **k: pipeline._empty_roofs()
    )
    monkeypatch.setattr(pipeline, "select_by_membership", lambda fp, mask: [object()])
    monkeypatch.setattr(pipeline.tiling, "tile_membership", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_score_roofs", lambda *a, **k: seen.update(k) or "scored")

    out = pipeline._score_tile(
        tile,
        tile_size_m=1.0,
        origin=(0.0, 0.0),
        footprints_source="x",
        day_range=None,
        dsm_out_path="d.tif",
        nprocs=6,
    )
    assert out == "scored"
    assert seen["nprocs"] == 6


def _run_city_fake(tmp_path, monkeypatch, nprocs):
    """run_city over a tiny AOI with a fake per-tile spine; returns (nprocs seen, key args)."""
    seen_nprocs: list[int] = []
    keys: list[tuple[str, str]] = []

    def _fake_score_tile(tile, **kwargs):
        seen_nprocs.append(kwargs["nprocs"])
        keys.append((kwargs["dsm_cache_key"], kwargs["radiation_cache_key"]))
        return pipeline._empty_roofs()

    monkeypatch.setattr(pipeline, "_score_tile", _fake_score_tile)
    pipeline.run_city(
        aoi=box(*config.GRID_ORIGIN, config.GRID_ORIGIN[0] + 3000, config.GRID_ORIGIN[1] + 3000),
        tiles_dir=tmp_path,
        write_outputs=False,
        nprocs=nprocs,
    )
    manifest = json.loads((tmp_path / pipeline.MANIFEST_NAME).read_text())
    return seen_nprocs, keys, manifest


def test_run_city_forwards_nprocs_to_every_tile(tmp_path, monkeypatch):
    seen, _keys, _manifest = _run_city_fake(tmp_path, monkeypatch, nprocs=8)
    assert len(seen) > 1
    assert set(seen) == {8}


def test_nprocs_is_not_part_of_any_cache_key_or_manifest(tmp_path, monkeypatch):
    _, keys_1, manifest_1 = _run_city_fake(tmp_path / "a", monkeypatch, nprocs=1)
    _, keys_8, manifest_8 = _run_city_fake(tmp_path / "b", monkeypatch, nprocs=8)
    assert keys_1 == keys_8
    assert manifest_1 == manifest_8


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
