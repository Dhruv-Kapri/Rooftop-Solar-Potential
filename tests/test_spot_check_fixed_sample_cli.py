"""`scripts/spot_check.py --sample` — reuse an earlier spot-check dir's fixed sample so every
tuning round is scored against the same hand-labelled roofs (#23). Offline: a tiny synthetic
spot dir in tmp_path; no GRASS, no network."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

REPO_ROOT = Path(__file__).resolve().parents[1]
CRS = "EPSG:6347"


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location(
        "spot_check_script", REPO_ROOT / "scripts/spot_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _spot_dir(tmp_path: Path, buildings_ids=("7_2_103", "3_1_5")) -> Path:
    """Two sample rows (7_2_103 first); buildings.parquet has both methods per id, with the
    multiplane footprint distinct from the ransac one so the right rows are provably picked."""
    pd.DataFrame(
        {
            "building_id": ["7_2_103", "3_1_5"],
            "area": ["glover_park", "downtown"],
            "roof_type": ["gable", "flat"],
            "tile_row": [7, 3],
            "tile_col": [2, 1],
            "tile_building_id": [103, 5],
            "n_planes": [2, 1],
            "roof_class": ["gable", "flat"],
            "footprint_area_m2": [100.0, 400.0],
        }
    ).to_csv(tmp_path / "sample.csv", index=False)
    rows = []
    for i, bid in enumerate(buildings_ids):
        rows.append({"building_id": bid, "method": "multiplane", "geometry": box(i, 0, i + 1, 1)})
        rows.append({"building_id": bid, "method": "ransac", "geometry": box(i, 5, i + 1, 6)})
    gpd.GeoDataFrame(rows, crs=CRS).to_parquet(tmp_path / "buildings.parquet")
    return tmp_path


def test_load_fixed_sample_keeps_csv_order_columns_and_multiplane_geometry(script, tmp_path):
    sample = script.load_fixed_sample(_spot_dir(tmp_path))

    assert isinstance(sample, gpd.GeoDataFrame)
    assert list(sample["building_id"]) == ["7_2_103", "3_1_5"]
    assert list(sample["tile_row"]) == [7, 3]
    assert list(sample["tile_col"]) == [2, 1]
    assert sample.crs == CRS
    assert sample.geometry.iloc[0].equals(box(0, 0, 1, 1))
    assert sample.geometry.iloc[1].equals(box(1, 0, 2, 1))


def test_load_fixed_sample_raises_naming_ids_without_geometry(script, tmp_path):
    spot = _spot_dir(tmp_path, buildings_ids=("7_2_103",))

    with pytest.raises(ValueError, match="3_1_5"):
        script.load_fixed_sample(spot)
