"""`scripts/run_city.py` CLI — run-label output dir + the on-disk run log (issue #19)."""

from __future__ import annotations

import importlib.util
import json
import platform
import sys
from pathlib import Path

import geopandas as gpd
import pytest

from rooftop_solar import config, pipeline

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location(
        "run_city_script", REPO_ROOT / "scripts/run_city.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def run_cli(script, tmp_path, monkeypatch):
    """Run `main()` with a faked `run_city`; returns the kwargs it received."""
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")

    def run(*extra: str) -> dict:
        recorded: dict = {}

        def fake_run_city(**kwargs):
            recorded.update(kwargs)
            return gpd.GeoDataFrame(geometry=[], crs=config.WORKING_CRS)

        monkeypatch.setattr(script.pipeline, "run_city", fake_run_city)
        monkeypatch.setattr(script, "_report_runlog", lambda *a, **k: None)
        argv = ["run_city.py", "--no-aggregate", "--tiles-dir", str(tmp_path / "tiles"), *extra]
        monkeypatch.setattr(sys, "argv", argv)
        script.main()
        return recorded

    return run


def test_run_label_sets_output_dir(run_cli):
    recorded = run_cli("--run-label", "foo")
    assert recorded["output_dir"] == config.outputs_run_dir("dc", "foo")


def test_default_label_is_calibrated_tuned_run(run_cli):
    recorded = run_cli()
    assert recorded["output_dir"] == config.outputs_run_dir("dc", "12day_tuned")


def test_output_dir_beats_run_label(run_cli, tmp_path):
    recorded = run_cli("--run-label", "foo", "--output-dir", str(tmp_path / "explicit"))
    assert recorded["output_dir"] == tmp_path / "explicit"


@pytest.mark.parametrize("label", ["", "a/b", ".", "..", "a\\b"])
def test_bad_run_label_rejected(run_cli, label):
    with pytest.raises(SystemExit):
        run_cli("--run-label", label)


def test_report_runlog_writes_file(script, tmp_path, capsys):
    tiles_dir = tmp_path / "tiles"
    tiles_dir.mkdir()
    manifest = {"0_0": {"status": "done", "n_roofs": 3}, "0_1": {"status": "done", "n_roofs": 0}}
    (tiles_dir / pipeline.MANIFEST_NAME).write_text(json.dumps(manifest))
    city = gpd.GeoDataFrame(
        {"annual_energy_kwh": [1000.0, 2000.0], "capacity_kw": [5.0, 10.0]},
        geometry=[None, None],
        crs=config.WORKING_CRS,
    )
    output_dir = tmp_path / "out" / "run"

    script._report_runlog(tiles_dir, city, 123.4, 7, 3, output_dir)

    text = (output_dir / "run_log.txt").read_text()
    assert platform.node() in text
    assert "r.sun threads        : 7" in text
    assert "tile workers         : 3" in text
    assert "123.4" in text
    assert text.strip() == capsys.readouterr().out.strip()


def test_report_runlog_appends_each_segment(script, tmp_path):
    """A resumed / handed-off run (Mac <-> VM) appends a timestamped segment, so earlier
    segments' machine and wall-clock survive (#19)."""
    tiles_dir = tmp_path / "tiles"
    tiles_dir.mkdir()
    (tiles_dir / pipeline.MANIFEST_NAME).write_text(json.dumps({}))
    city = gpd.GeoDataFrame(geometry=[], crs=config.WORKING_CRS)
    output_dir = tmp_path / "out"

    script._report_runlog(tiles_dir, city, 111.1, 1, 1, output_dir)
    script._report_runlog(tiles_dir, city, 222.2, 8, 2, output_dir)

    text = (output_dir / "run_log.txt").read_text()
    assert "111.1" in text and "222.2" in text
    assert text.count("logged (UTC)") == 2
