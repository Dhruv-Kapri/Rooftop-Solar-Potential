"""Smoke test that the package imports and config resolves. Real tests land per phase."""

from rooftop_solar import config


def test_paths_under_repo_root():
    assert config.DATA_DIR.parent == config.REPO_ROOT
    assert config.OUTPUTS_DIR.parent == config.REPO_ROOT


def test_study_area_defaults():
    assert config.STUDY_AREA == "Washington, DC"
    assert config.NLR_API_BASE == "https://developer.nlr.gov"
