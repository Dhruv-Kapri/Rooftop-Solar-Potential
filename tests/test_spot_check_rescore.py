"""Unit tests for the spot-check detailed re-score (Part 2-4 step 7b; plan §6, ADR-0011/0013).

Seam under test: `validation.detailed_score` / `rescore_sample` re-run the multiplane chain
(and the single-plane baseline through the same fitter) on given footprints and keep the
per-plane, obstruction and per-pixel-membership detail the production collapse discards.
Expected values come from the constructed geometry (pitch, box pixel count), never from
re-running the code under test.

Fixtures: synthetic GeoTIFFs in EPSG:6347 at 1 m pixels -- a clean N/S gable and a flat roof
carrying a small raised box -- plus a constant insolation raster on the same grid.

Fast: numpy + rasterio + geopandas, no network, no GRASS.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from geopandas.testing import assert_geodataframe_equal
from rasterio.transform import from_origin
from shapely.geometry import box

from rooftop_solar import cache, config, pipeline, roof_planes, validation

ORIGIN_X = 320_000.0
ORIGIN_Y = 4_307_100.0  # top edge of the grid
WIDTH_PX = 60
HEIGHT_PX = 60
BASE_ELEV_M = 300.0
PITCH_DEG = 20.0
INSOLATION_KWH_M2 = 1200.0  # above the 800 cutoff, so sun-facing planes are usable
NODATA = -9999.0

# Gable: 20 m wide (E-W) x 40 m tall (N-S), ridge along E-W at its mid-line -> a south-facing
# and a north-facing facet of 400 px each. Flat roof: 20 x 20 m, with a 2 x 3 px box raised
# 3 m (6 px: deliberately < roof_planes.MIN_PLANE_PX, else multiplane fits it as its own
# facet and it escapes subtraction -- the ADR-0013 deferred finding).
GABLE = box(ORIGIN_X + 5, ORIGIN_Y - 45, ORIGIN_X + 25, ORIGIN_Y - 5)
FLAT = box(ORIGIN_X + 35, ORIGIN_Y - 25, ORIGIN_X + 55, ORIGIN_Y - 5)
BOX_ROWS, BOX_COLS, BOX_RISE_M = (8, 10), (42, 45), 3.0  # raster rows [8,10) x cols [42,45)


def _write_raster(path, band):
    profile = {
        "driver": "GTiff",
        "height": HEIGHT_PX,
        "width": WIDTH_PX,
        "count": 1,
        "dtype": "float32",
        "crs": config.WORKING_CRS,
        "transform": from_origin(ORIGIN_X, ORIGIN_Y, 1.0, 1.0),
        "nodata": NODATA,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(band.astype(np.float32), 1)
    return path


def _dsm_band():
    band = np.full((HEIGHT_PX, WIDTH_PX), BASE_ELEV_M, dtype=np.float64)
    slope = np.tan(np.radians(PITCH_DEG))
    # gable occupies raster rows 5..44, cols 5..24; the ridge sits between rows 24 and 25
    for row in range(5, 45):
        height = slope * (min(row - 5, 44 - row) + 0.5)
        band[row, 5:25] = BASE_ELEV_M + height
    band[BOX_ROWS[0] : BOX_ROWS[1], BOX_COLS[0] : BOX_COLS[1]] += BOX_RISE_M
    return band


@pytest.fixture
def rasters(tmp_path):
    dsm = _write_raster(tmp_path / "dsm.tif", _dsm_band())
    insol = _write_raster(tmp_path / "insol.tif", np.full((HEIGHT_PX, WIDTH_PX), INSOLATION_KWH_M2))
    return dsm, insol


@pytest.fixture
def footprints():
    return gpd.GeoDataFrame(
        {"building_id": ["gable", "flat"], "geometry": [GABLE, FLAT]}, crs=config.WORKING_CRS
    )


@pytest.fixture
def detailed(rasters, footprints):
    return validation.detailed_score(footprints, *rasters)


def _rows(df, method, building_id):
    return df[(df["method"] == method) & (df["building_id"] == building_id)]


def test_spot_check_methods_labels():
    assert validation.SPOT_CHECK_METHODS == {"multiplane": roof_planes.MAX_PLANES, "ransac": 1}


def test_fit_roof_planes_max_planes_one_gives_one_plane_per_building(rasters, footprints):
    planes = roof_planes.fit_roof_planes(footprints, rasters[0], method="multiplane", max_planes=1)

    assert list(planes["building_id"]) == [0, 1]
    assert (planes["n_planes"] == 1).all()


def test_multiplane_recovers_gable_as_two_planes(detailed):
    planes = _rows(detailed.planes, "multiplane", "gable").sort_values("plane_id")

    assert list(planes["n_planes"]) == [2, 2]
    assert planes["tilt_deg"].to_numpy() == pytest.approx([PITCH_DEG, PITCH_DEG], abs=0.5)
    # compass aspect wraps at 360, so compare on the circle (north is ~0 or ~360)
    aspects = planes["aspect_deg"].to_numpy()
    south, north = (
        (aspects[0], aspects[1]) if abs(aspects[0] - 180) < 90 else (aspects[1], aspects[0])
    )
    assert south == pytest.approx(180.0, abs=1.0)
    assert min(north, 360.0 - north) == pytest.approx(0.0, abs=1.0)
    # the 0.5 m inlier band lets the first facet absorb a few ridge pixels of the other
    assert planes["n_px"].sum() == 800
    assert planes["n_px"].between(350, 450).all()


def test_single_plane_baseline_has_one_plane(detailed):
    planes = _rows(detailed.planes, "ransac", "gable")

    assert list(planes["n_planes"]) == [1]
    assert len(planes) == 1


def test_obstruction_area_on_flat_roof_matches_box_pixels(detailed):
    box_area_m2 = (BOX_ROWS[1] - BOX_ROWS[0]) * (BOX_COLS[1] - BOX_COLS[0]) * 1.0

    planes = _rows(detailed.planes, "multiplane", "flat")
    obs = _rows(detailed.obstructions, "multiplane", "flat")

    assert planes["obstruction_area_m2"].sum() == pytest.approx(box_area_m2)
    assert len(obs) == 1
    assert obs.iloc[0]["obstruction_area_m2"] == pytest.approx(box_area_m2)
    assert obs.iloc[0]["geometry"].area == pytest.approx(box_area_m2)
    assert list(obs.columns) == [
        "building_id",
        "method",
        "plane_id",
        "obstruction_area_m2",
        "geometry",
    ]
    assert detailed.obstructions.crs == config.WORKING_CRS


def test_clean_gable_has_no_obstruction_under_multiplane(detailed):
    assert _rows(detailed.obstructions, "multiplane", "gable").empty


def test_planes_frame_is_parquet_writable_and_keeps_footprint(detailed, tmp_path):
    assert "coef" not in detailed.planes.columns
    assert "inlier_xy" not in detailed.planes.columns
    assert detailed.planes.crs == config.WORKING_CRS
    assert detailed.planes.geometry.iloc[0].equals(GABLE)
    detailed.planes.to_parquet(tmp_path / "planes.parquet")


def test_membership_has_one_row_per_footprint_pixel_per_method(detailed):
    assert list(detailed.membership.columns) == [
        "building_id",
        "method",
        "x",
        "y",
        "z",
        "plane_id",
        "is_obstruction",
    ]
    for method in validation.SPOT_CHECK_METHODS:
        assert len(_rows(detailed.membership, method, "gable")) == 800
        assert len(_rows(detailed.membership, method, "flat")) == 400


def test_membership_gable_splits_into_two_halves_and_flags_obstruction(detailed):
    gable = _rows(detailed.membership, "multiplane", "gable")
    south = gable[gable["y"] < ORIGIN_Y - 25]  # south of the ridge
    north = gable[gable["y"] > ORIGIN_Y - 25]
    assert south["plane_id"].nunique() == 1
    assert north["plane_id"].nunique() == 1
    assert south["plane_id"].iloc[0] != north["plane_id"].iloc[0]
    assert not gable["is_obstruction"].any()

    flat = _rows(detailed.membership, "multiplane", "flat")
    assert flat["is_obstruction"].sum() == 6


def test_buildings_frame_has_caller_ids_and_method(detailed):
    for method in validation.SPOT_CHECK_METHODS:
        got = detailed.buildings[detailed.buildings["method"] == method]
        assert sorted(got["building_id"]) == ["flat", "gable"]
    assert detailed.buildings.crs == config.WORKING_CRS


def test_multiplane_buildings_match_production_score_roofs(
    detailed, rasters, footprints, monkeypatch
):
    dsm_path, insol_path = rasters
    monkeypatch.setattr(pipeline.dsm, "build_dsm", lambda *a, **k: dsm_path)
    monkeypatch.setattr(pipeline.radiation, "surface_irradiance", lambda *a, **k: insol_path)

    production = pipeline._score_roofs(footprints, None, day_range=None, method="multiplane")

    ours = detailed.buildings[detailed.buildings["method"] == "multiplane"].drop(columns="method")
    ours = ours.assign(building_id=ours["building_id"].map({"gable": 0, "flat": 1}))
    ours = ours.sort_values("building_id").reset_index(drop=True)
    assert_geodataframe_equal(ours, production.sort_values("building_id").reset_index(drop=True))


def _cache_raster(tiles, name, src):
    """Copy `src` into the tile cache as `name`, with a current-format sidecar (ADR-0014)."""
    path = tiles / name
    path.write_bytes(src.read_bytes())
    cache.write_key_sidecar(path, "any-key")
    return path


def _tiles_dir(tmp_path, rasters, *, with_insol=True):
    tiles = tmp_path / "tiles"
    tiles.mkdir()
    _cache_raster(tiles, "tile_0_0_dsm.tif", rasters[0])
    if with_insol:
        _cache_raster(tiles, "tile_0_0_insolation.tif", rasters[1])
    return tiles


def _sample(footprints):
    return footprints.assign(tile_row=0, tile_col=0)


def test_rescore_sample_matches_detailed_score(tmp_path, rasters, footprints, detailed):
    got = validation.rescore_sample(_sample(footprints), _tiles_dir(tmp_path, rasters))

    assert_geodataframe_equal(got.planes, detailed.planes)
    assert_geodataframe_equal(got.obstructions, detailed.obstructions)
    assert_geodataframe_equal(got.buildings, detailed.buildings)
    pd.testing.assert_frame_equal(got.membership, detailed.membership)


def test_rescore_sample_concatenates_tiles(tmp_path, rasters, footprints):
    tiles = _tiles_dir(tmp_path, rasters)
    _cache_raster(tiles, "tile_1_2_dsm.tif", rasters[0])
    _cache_raster(tiles, "tile_1_2_insolation.tif", rasters[1])
    sample = footprints.assign(tile_row=[0, 1], tile_col=[0, 2])

    got = validation.rescore_sample(sample, tiles)

    assert sorted(set(got.buildings["building_id"])) == ["flat", "gable"]
    assert len(got.buildings) == 4  # 2 buildings x 2 methods
    assert got.planes.crs == config.WORKING_CRS


def test_rescore_sample_missing_raster_names_the_file(tmp_path, rasters, footprints):
    tiles = _tiles_dir(tmp_path, rasters, with_insol=False)

    with pytest.raises(FileNotFoundError, match=r"tile_0_0_insolation\.tif.*run_city"):
        validation.rescore_sample(_sample(footprints), tiles)


def test_rescore_sample_truncated_raster_is_refused(tmp_path, rasters, footprints):
    tiles = _tiles_dir(tmp_path, rasters)
    dsm = tiles / "tile_0_0_dsm.tif"
    dsm.write_bytes(dsm.read_bytes()[:100])  # e.g. an rsync that died mid-file; sidecar intact

    with pytest.raises(ValueError, match=r"tile_0_0_dsm\.tif.*run_city"):
        validation.rescore_sample(_sample(footprints), tiles)


def test_rescore_sample_sidecarless_raster_is_refused(tmp_path, rasters, footprints):
    tiles = _tiles_dir(tmp_path, rasters)
    cache.sidecar_path(tiles / "tile_0_0_insolation.tif").unlink()

    with pytest.raises(ValueError, match=r"tile_0_0_insolation\.tif.*run_city"):
        validation.rescore_sample(_sample(footprints), tiles)


def test_rescore_sample_empty_sample_raises(tmp_path):
    empty = gpd.GeoDataFrame(
        {"building_id": [], "tile_row": [], "tile_col": []},
        geometry=[],
        crs=config.WORKING_CRS,
    )
    with pytest.raises(ValueError, match="empty"):
        validation.rescore_sample(empty, tmp_path)
