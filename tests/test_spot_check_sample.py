"""Spot-check sampling + labels template (Part 2-4 step 7b; plan §6)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from rooftop_solar import validation as v

REPO_ROOT = Path(__file__).resolve().parents[1]
CRS = "EPSG:6347"
ORIGIN = (1000.0, 2000.0)
TILE = 100.0
KW = {"tile_size_m": TILE, "origin": ORIGIN}


def _city(rows):
    """rows: (x, y, n_planes, roof_class, tilt, area) -> 10 m footprints at (x, y)."""
    recs = []
    for i, (x, y, n, cls, tilt, area) in enumerate(rows):
        recs.append(
            {
                "geometry": box(x, y, x + 10, y + 10),
                "building_id": i,
                "n_planes": n,
                "roof_class": cls,
                "tilt_deg": tilt,
                "footprint_area_m2": area,
            }
        )
    return gpd.GeoDataFrame(recs, crs="EPSG:6347")


def _two_area_city():
    rows = []
    for k in range(6):  # area A (x 1000-1100): 6 flat, 2 gable
        rows.append((1000 + k * 12, 2000, 1, "flat", 0.0, 100.0))
    for k in range(2):
        rows.append((1000 + k * 12, 2020, 1, "pitched_sun_facing", 30.0, 100.0))
    for k in range(4):  # area B (x 1200-1300): 4 hip
        rows.append((1200 + k * 12, 2000, 3, "pitched_north_facing", 30.0, 100.0))
    return _city(rows)


AREAS = {
    "A": box(1000, 2000, 1100, 2100),
    "B": box(1200, 2000, 1300, 2100),
}


@pytest.mark.parametrize(
    ("n", "cls", "expected"),
    [
        (1, "flat", "flat"),
        (2, "flat", "flat"),
        (1, "pitched_sun_facing", "gable"),
        (2, "pitched_north_facing", "gable"),
        (3, "pitched_sun_facing", "hip"),
        (4, "pitched_north_facing", "hip"),
        (3, "flat", "complex"),
        (4, "flat", "complex"),
    ],
)
def test_roof_type_truth_table(n, cls, expected):
    assert v.roof_type(n, cls) == expected


def test_spot_check_id_format():
    assert v.spot_check_id(3, 7, 12) == "3_7_12"


def test_stratification_takes_min_of_per_cell_and_available():
    s = v.select_spot_check_sample(_two_area_city(), AREAS, per_cell=3, **KW)
    counts = s.groupby(["area", "roof_type"]).size().to_dict()
    assert counts == {("A", "flat"): 3, ("A", "gable"): 2, ("B", "hip"): 3}
    assert list(s.columns) == [
        "building_id",
        "area",
        "roof_type",
        "tile_row",
        "tile_col",
        "tile_building_id",
        "n_planes",
        "roof_class",
        "footprint_area_m2",
        "geometry",
    ]
    assert s.crs == "EPSG:6347"
    assert s.index.tolist() == list(range(len(s)))


def test_sorted_by_area_then_type_then_id():
    s = v.select_spot_check_sample(_two_area_city(), AREAS, per_cell=3, **KW)
    assert s["area"].tolist() == ["A"] * 5 + ["B"] * 3
    assert s["roof_type"].tolist()[:5] == ["flat"] * 3 + ["gable"] * 2


def test_deterministic_and_row_order_independent():
    city = _two_area_city()
    a = v.select_spot_check_sample(city, AREAS, per_cell=2, **KW)
    b = v.select_spot_check_sample(city, AREAS, per_cell=2, **KW)
    shuffled = city.sample(frac=1, random_state=np.random.RandomState(1))
    c = v.select_spot_check_sample(shuffled, AREAS, per_cell=2, **KW)
    assert a["building_id"].tolist() == b["building_id"].tolist()
    assert a["building_id"].tolist() == c["building_id"].tolist()


def test_excludes_outside_nan_tilt_and_small_roofs():
    city = _city(
        [
            (1000, 2000, 1, "flat", 0.0, 100.0),  # ok
            (1020, 2000, 1, "flat", np.nan, 100.0),  # unfittable
            (1040, 2000, 1, "flat", 0.0, 10.0),  # too small
            (5000, 5000, 1, "flat", 0.0, 100.0),  # outside every area
        ]
    )
    s = v.select_spot_check_sample(city, AREAS, per_cell=10, **KW)
    assert s["tile_building_id"].tolist() == [0]


def test_building_in_two_areas_goes_to_first():
    city = _city([(1000, 2000, 1, "flat", 0.0, 100.0)])
    areas = {"first": box(990, 1990, 1100, 2100), "second": box(990, 1990, 1100, 2100)}
    s = v.select_spot_check_sample(city, areas, per_cell=3, **KW)
    assert s["area"].tolist() == ["first"]


def test_ids_decode_to_tile():
    # rep point (1255, 2335) -> col 2, row 3 for origin (1000, 2000), tile 100
    city = _city([(1250, 2330, 1, "flat", 0.0, 100.0)])
    areas = {"A": box(1200, 2300, 1300, 2400)}
    s = v.select_spot_check_sample(city, areas, per_cell=3, **KW)
    r = s.iloc[0]
    assert (r["tile_row"], r["tile_col"], r["tile_building_id"]) == (3, 2, 0)
    assert r["building_id"] == "3_2_0"


def test_empty_result_keeps_columns():
    s = v.select_spot_check_sample(_two_area_city(), {"X": box(0, 0, 1, 1)}, **KW)
    assert len(s) == 0
    assert "building_id" in s.columns and "roof_type" in s.columns


def test_write_labels_template(tmp_path):
    s = v.select_spot_check_sample(_two_area_city(), AREAS, per_cell=3, **KW)
    path = v.write_labels_template(s, tmp_path / "sub" / "labels.csv")
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert tuple(df.columns) == v.LABEL_COLUMNS
    assert df["building_id"].tolist() == s["building_id"].tolist()
    for col in v.LABEL_COLUMNS[1:]:
        assert (df[col] == "").all()


def test_write_labels_template_refuses_overwrite(tmp_path):
    s = v.select_spot_check_sample(_two_area_city(), AREAS, **KW)
    path = tmp_path / "labels.csv"
    v.write_labels_template(s, path)
    path.write_text("hand entered")
    with pytest.raises(FileExistsError):
        v.write_labels_template(s, path)
    assert path.read_text() == "hand entered"


def _spot_dir(tmp_path: Path, building_ids=("7_2_103", "3_1_5")) -> Path:
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
    for i, bid in enumerate(building_ids):
        rows.append({"building_id": bid, "method": "multiplane", "geometry": box(i, 0, i + 1, 1)})
        rows.append({"building_id": bid, "method": "ransac", "geometry": box(i, 5, i + 1, 6)})
    gpd.GeoDataFrame(rows, crs=CRS).to_parquet(tmp_path / "buildings.parquet")
    return tmp_path


def test_load_spot_check_sample_keeps_csv_order_columns_and_multiplane_geometry(tmp_path):
    sample = v.load_spot_check_sample(_spot_dir(tmp_path))

    assert isinstance(sample, gpd.GeoDataFrame)
    assert list(sample["building_id"]) == ["7_2_103", "3_1_5"]
    assert list(sample["tile_row"]) == [7, 3]
    assert list(sample["tile_col"]) == [2, 1]
    assert sample.crs == CRS
    assert sample.geometry.iloc[0].equals(box(0, 0, 1, 1))
    assert sample.geometry.iloc[1].equals(box(1, 0, 2, 1))


def test_load_spot_check_sample_raises_naming_ids_without_geometry(tmp_path):
    spot = _spot_dir(tmp_path, building_ids=("7_2_103",))

    with pytest.raises(ValueError, match="3_1_5"):
        v.load_spot_check_sample(spot)


def test_spot_check_cli_sample_without_out_dir_is_an_argparse_error(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "spot_check_script", REPO_ROOT / "scripts/spot_check.py"
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setattr(sys, "argv", ["spot_check.py", "--sample", str(tmp_path)])

    with pytest.raises(SystemExit) as exc:
        script.main()

    assert exc.value.code == 2
