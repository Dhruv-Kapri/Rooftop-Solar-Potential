"""run_city asks GRASS for its version ONCE per run and feeds it into every tile's radiation
key (issue #14, ADR-0014 addendum). Offline: no network, no GRASS — `_score_tile` and
`radiation.grass_version` are faked; the tile grid is real."""

from __future__ import annotations

import json

import geopandas as gpd
from shapely.geometry import box

from rooftop_solar import config, pipeline, radiation

# Spans 2 tiles at tile_size_m=2000 (a 3 km wide strip), fixed origin.
_AOI = box(
    config.GRID_ORIGIN[0] + 100,
    config.GRID_ORIGIN[1] + 100,
    config.GRID_ORIGIN[0] + 3100,
    config.GRID_ORIGIN[1] + 900,
)


def _run(tmp_path, monkeypatch, version):
    calls = []

    def fake_version():
        calls.append(1)
        return version

    monkeypatch.setattr(radiation, "grass_version", fake_version)
    monkeypatch.setattr(
        pipeline,
        "_score_tile",
        lambda tile, **kw: gpd.GeoDataFrame(geometry=[], crs=config.WORKING_CRS),
    )
    pipeline.run_city(aoi=_AOI, tiles_dir=tmp_path, write_outputs=False)
    return calls, json.loads((tmp_path / pipeline.MANIFEST_NAME).read_text())


def _stamps(manifest):
    return {k: v["stamp"] for k, v in manifest.items()}


def test_grass_version_is_queried_once_per_multi_tile_run(tmp_path, monkeypatch):
    calls, manifest = _run(tmp_path, monkeypatch, "8.5.0")
    assert len(_stamps(manifest)) >= 2
    assert len(calls) == 1


def test_different_grass_version_changes_manifest_stamps(tmp_path, monkeypatch):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _, m_a = _run(tmp_path / "a", monkeypatch, "8.5.0")
    _, m_b = _run(tmp_path / "b", monkeypatch, "8.6.0")
    sa, sb = _stamps(m_a), _stamps(m_b)
    assert sa.keys() == sb.keys()
    assert all(sa[k] != sb[k] for k in sa)
