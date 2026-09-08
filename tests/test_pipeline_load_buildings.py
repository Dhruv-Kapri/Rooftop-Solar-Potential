"""Unit tests for `pipeline._load_buildings` — resolving the per-roof input by format.

Part 2-2's city output is **GeoParquet** (ADR-0009), while Part 2-1's Stage-1 output is a
**GeoPackage** (layer ``roofs``); `_load_buildings` must read either from a path (and pass a
GeoDataFrame straight through), so `run_aggregation` composes with both scale paths. Offline.
"""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import box

from rooftop_solar import config, pipeline


def _roofs() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"capacity_kw": [1.0, 2.0]},
        geometry=[box(0.0, 0.0, 1.0, 1.0), box(2.0, 2.0, 3.0, 3.0)],
        crs=config.WORKING_CRS,
    )


def test_reads_geoparquet(tmp_path):
    path = tmp_path / "dc_roofs.parquet"
    _roofs().to_parquet(path)

    out = pipeline._load_buildings(path)

    assert len(out) == 2
    assert out.crs == config.WORKING_CRS
    assert out["capacity_kw"].tolist() == [1.0, 2.0]


def test_reads_geopackage_roofs_layer(tmp_path):
    path = tmp_path / "roofs.gpkg"
    _roofs().to_file(path, driver="GPKG", layer="roofs")

    out = pipeline._load_buildings(path)

    assert len(out) == 2
    assert set(out["capacity_kw"]) == {1.0, 2.0}


def test_passes_geodataframe_through():
    g = _roofs()
    assert pipeline._load_buildings(g) is g
