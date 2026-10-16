"""Smoke test that the package imports and config resolves. Real tests land per phase."""

import pytest

from rooftop_solar import config


def test_paths_under_repo_root():
    assert config.DATA_DIR.parent == config.REPO_ROOT
    assert config.OUTPUTS_DIR.parent == config.REPO_ROOT


def test_study_area_defaults():
    assert config.STUDY_AREA == "Washington, DC"
    assert config.NLR_API_BASE == "https://developer.nlr.gov"


def test_census_fips_is_zero_padded_string():
    # GEOID/FIPS integer coercion is the single most likely silent bug (§11): "11" must
    # never become int 11, which would drop the leading zero-pad on downstream joins.
    assert config.DC_STATE_FIPS == "11"
    assert isinstance(config.DC_STATE_FIPS, str)


def test_equity_sources_share_2020_tract_vintage():
    # Geometry, ACS, and LEAD are all keyed on 2020 tracts (ADR-0006) — no vintage mix.
    assert config.TIGER_TRACT_YEAR == 2020


# --- city-scale tiling (Phase 2, Part 2-2 — ADR-0009) ---


def test_tile_buffer_tracks_aoi_buffer():
    # ADR-0009: the tile buffer IS the effective shadow reach, and there's no separate wired
    # search distance — so it must stay equal to the Stage-1 AOI buffer. If someone retunes
    # AOI_BUFFER_M for shadow reach, the tile buffer must move with it, not silently diverge.
    assert config.TILE_BUFFER_M == config.AOI_BUFFER_M


def test_grid_origin_is_a_fixed_metric_anchor_sw_of_dc():
    # A FIXED origin is what makes cell (row, col) — and therefore the per-tile cache key —
    # stable across runs (ADR-0009). It lives in WORKING_CRS (metres) and sits SW of the whole
    # District so every floor index is non-negative (DC easting >= ~315875, northing >= ~4295204
    # in EPSG:6347).
    ox, oy = config.GRID_ORIGIN
    assert isinstance(ox, float) and isinstance(oy, float)
    assert ox <= 315875.0 and oy <= 4295204.0


def test_tile_size_is_a_positive_metric_length():
    assert isinstance(config.TILE_SIZE_M, float)
    assert config.TILE_SIZE_M > 0.0


def test_tiles_cache_dir_under_data_dir():
    assert config.TILES_CACHE_DIR.parent == config.DATA_DIR


# --- output-path helpers (outputs-dir reorg, spec #3 / ticket #6) ---


def test_outputs_area_dir_is_a_subdir_of_outputs():
    assert config.outputs_area_dir("glover_park") == config.OUTPUTS_DIR / "glover_park"


@pytest.mark.parametrize("label", ["", ".", "..", "a/b", "a\\b"])
def test_outputs_run_dir_rejects_unsafe_label(label):
    with pytest.raises(ValueError, match="label"):
        config.outputs_run_dir("dc", label)


@pytest.mark.parametrize("label", ["", ".", "..", "a/b", "a\\b"])
def test_validate_run_label_rejects_unsafe_label(label):
    with pytest.raises(ValueError, match="label"):
        config.validate_run_label(label)


def test_validate_run_label_accepts_plain_name():
    assert config.validate_run_label("12day") is None


def test_outputs_run_dir_nests_label_under_area():
    assert config.outputs_run_dir("dc", "12day") == config.OUTPUTS_DIR / "dc" / "12day"


def test_calibrated_city_label_is_12day():
    assert config.CALIBRATED_CITY_LABEL == "12day"


def test_calibrated_city_outputs_dir_resolves_via_helper():
    assert config.CALIBRATED_CITY_OUTPUTS_DIR == config.outputs_run_dir("dc", "12day")
