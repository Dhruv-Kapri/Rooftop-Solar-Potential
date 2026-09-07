"""Smoke test that the package imports and config resolves. Real tests land per phase."""

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
