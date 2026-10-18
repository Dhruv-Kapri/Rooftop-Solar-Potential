"""Unit tests for the city-cache DSM self-consistency run (Part 2-4 step 7a; plan §6, #24).

Seam under test: `validation.tile_self_consistency` (one cached tile -> per-roof rows tagged
with roof type) and `validation.summarize_self_consistency` (per-roof rows -> per-type
medians), plus the `scripts/self_consistency.py` CLI over a synthetic one-tile cache.
Expected values are hand-computed from the constructed table / geometry.

Fast: numpy + rasterio + geopandas, no network, no GRASS.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from rooftop_solar import cache, config, validation

REPO_ROOT = Path(__file__).resolve().parents[1]
ORIGIN_X, ORIGIN_Y = 320_000.0, 4_307_100.0
SIZE_PX = 60
GABLE = box(ORIGIN_X + 5, ORIGIN_Y - 45, ORIGIN_X + 25, ORIGIN_Y - 5)  # N/S gable, 20 x 40 m
EMPTY = box(ORIGIN_X + 40, ORIGIN_Y - 20, ORIGIN_X + 50, ORIGIN_Y - 5)  # nodata -> unfittable
NODATA = -9999.0


def _write_gable_dsm(path: Path) -> None:
    band = np.full((SIZE_PX, SIZE_PX), NODATA, dtype=np.float32)
    slope = np.tan(np.radians(20.0))
    for row in range(5, 45):
        band[row, 5:25] = 300.0 + slope * (min(row - 5, 44 - row) + 0.5)
    profile = {
        "driver": "GTiff", "height": SIZE_PX, "width": SIZE_PX, "count": 1, "dtype": "float32",
        "crs": config.WORKING_CRS, "transform": from_origin(ORIGIN_X, ORIGIN_Y, 1.0, 1.0),
        "nodata": NODATA,
    }  # fmt: skip
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(band, 1)


def _roofs() -> gpd.GeoDataFrame:
    # building_ids are the production parquet's own, deliberately not positional
    return gpd.GeoDataFrame(
        {"building_id": [7, 3], "n_planes": [2, 0], "roof_class": ["pitched", "flat"],
         "geometry": [GABLE, EMPTY]},
        crs=config.WORKING_CRS,
    )  # fmt: skip


@pytest.fixture
def tiles(tmp_path: Path) -> Path:
    tiles = tmp_path / "tiles"
    tiles.mkdir()
    dsm = tiles / "tile_2_5_dsm.tif"
    _write_gable_dsm(dsm)
    cache.write_key_sidecar(dsm, "any-key")
    _roofs().to_parquet(tiles / "tile_2_5_roofs.parquet")
    return tiles


def test_tile_self_consistency_tags_rows_and_keeps_compare_columns(tiles):
    got = validation.tile_self_consistency(tiles, 2, 5)

    assert list(got["roof_id"]) == ["2_5_7", "2_5_3"]  # parquet's own building_id, parquet order
    assert list(got["building_id"]) == [7, 3]
    assert (got["tile_row"] == 2).all() and (got["tile_col"] == 5).all()
    assert list(got["roof_class"]) == ["pitched", "flat"]
    assert list(got["n_planes"]) == [2, 0]
    assert list(got["roof_type"]) == ["gable", "flat"]  # from compare's n_planes, not the parquet's
    assert {"rmse_multiplane", "rmse_ransac", "rmse_improvement"} <= set(got.columns)
    assert got.crs == config.WORKING_CRS
    assert got["rmse_improvement"].iloc[0] > 0.5  # one plane cannot reconstruct a gable
    assert np.isnan(got["rmse_multiplane"].iloc[1])


def test_tile_self_consistency_missing_roofs_raises(tiles):
    (tiles / "tile_2_5_roofs.parquet").unlink()
    with pytest.raises(FileNotFoundError, match="roofs"):
        validation.tile_self_consistency(tiles, 2, 5)


def test_tile_self_consistency_missing_dsm_raises(tiles):
    (tiles / "tile_2_5_dsm.tif").unlink()
    with pytest.raises(FileNotFoundError, match="dsm"):
        validation.tile_self_consistency(tiles, 2, 5)


def test_tile_self_consistency_truncated_dsm_refused(tiles):
    path = tiles / "tile_2_5_dsm.tif"
    path.write_bytes(path.read_bytes()[:-10])
    with pytest.raises(ValueError, match="sidecar"):
        validation.tile_self_consistency(tiles, 2, 5)


def test_tile_self_consistency_sidecarless_dsm_refused(tiles):
    cache.sidecar_path(tiles / "tile_2_5_dsm.tif").unlink()
    with pytest.raises(ValueError, match="sidecar"):
        validation.tile_self_consistency(tiles, 2, 5)


def _per_roof() -> pd.DataFrame:
    nan = np.nan
    return pd.DataFrame(
        {
            "roof_type": ["gable", "gable", "gable", "hip", "flat"],
            "rmse_multiplane": [0.1, 0.3, nan, 0.2, 0.05],
            "rmse_ransac": [0.5, 0.9, nan, 0.4, 0.05],
            "rmse_improvement": [0.4, 0.6, nan, 0.2, 0.0],
            "pct_within_multiplane": [90.0, 70.0, nan, 80.0, 100.0],
            "pct_within_ransac": [50.0, 30.0, nan, 60.0, 100.0],
        }
    )


def test_summarize_self_consistency_hand_computed():
    got = validation.summarize_self_consistency(_per_roof())

    assert got.index.name == "roof_type"
    assert list(got.index) == ["flat", "gable", "hip", "complex", "all"]
    gable = got.loc["gable"]
    assert (gable["n_roofs"], gable["n_unfittable"]) == (3, 1)
    assert gable["rmse_multiplane_median"] == pytest.approx(0.2)
    assert gable["rmse_ransac_median"] == pytest.approx(0.7)
    assert gable["rmse_improvement_median"] == pytest.approx(0.5)
    assert gable["rmse_improvement_p90"] == pytest.approx(0.58)  # 0.4 + 0.9 * (0.6 - 0.4)
    assert gable["pct_within_multiplane_median"] == pytest.approx(80.0)
    assert gable["pct_within_ransac_median"] == pytest.approx(40.0)
    everything = got.loc["all"]
    assert (everything["n_roofs"], everything["n_unfittable"]) == (5, 1)
    assert everything["rmse_multiplane_median"] == pytest.approx(0.15)  # [.05,.1,.2,.3]


def test_summarize_self_consistency_empty_type_has_nan_metrics_and_no_warning(recwarn):
    got = validation.summarize_self_consistency(_per_roof())

    complex_row = got.loc["complex"]
    assert (complex_row["n_roofs"], complex_row["n_unfittable"]) == (0, 0)
    assert complex_row.drop(["n_roofs", "n_unfittable"]).isna().all()
    assert not [w for w in recwarn if issubclass(w.category, RuntimeWarning)]


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location(
        "self_consistency_script", REPO_ROOT / "scripts/self_consistency.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_writes_roofs_and_summary(script, tiles, tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    monkeypatch.setattr(
        sys, "argv", ["self_consistency.py", "--tiles-dir", str(tiles), "--out-dir", str(out)]
    )
    script.main()

    per_roof = gpd.read_parquet(out / "self_consistency_roofs.parquet")
    summary = pd.read_csv(out / "self_consistency_summary.csv", index_col="roof_type")
    assert list(per_roof["roof_id"]) == ["2_5_7", "2_5_3"]
    assert summary.loc["all", "n_roofs"] == 2
    assert "gable" in capsys.readouterr().out


def test_cli_tiles_option_selects_tiles(script, tiles, tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setattr(
        sys,
        "argv",
        ["self_consistency.py", "--tiles-dir", str(tiles), "--out-dir", str(out), "--tiles", "9_9"],
    )
    with pytest.raises(FileNotFoundError):
        script.main()
