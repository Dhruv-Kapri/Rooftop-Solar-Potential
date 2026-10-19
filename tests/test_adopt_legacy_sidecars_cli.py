"""`scripts/adopt_legacy_sidecars.py` — one-off upgrade of the #19 city cache (pre-#18 sidecars)
so `run_city` and the spot-check re-score reuse it instead of recomputing (ADR-0014 addendum).
Offline: a tmp tile dir with fake artifacts; no GRASS, no network."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from rooftop_solar import cache, pipeline

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location(
        "adopt_legacy_sidecars_script", REPO_ROOT / "scripts/adopt_legacy_sidecars.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_artifact(path: Path, key: str | None) -> Path:
    path.write_bytes(b"fake artifact")
    if key is not None:
        cache.sidecar_path(path).write_text(key)  # pre-#18 format: the bare key
    return path


def test_adopts_rasters_and_roofs_so_every_reuse_check_passes(script, tmp_path):
    dsm = _legacy_artifact(pipeline.tile_cache_path(tmp_path, 0, 1, "dsm"), "dsm-key")
    insol = _legacy_artifact(pipeline.tile_cache_path(tmp_path, 0, 1, "insolation"), "rad-key")
    roofs = _legacy_artifact(pipeline.tile_cache_path(tmp_path, 0, 1, "roofs"), None)
    (tmp_path / pipeline.MANIFEST_NAME).write_text(
        json.dumps({"0_1": {"status": "done", "stamp": "score-key", "n_roofs": 7}})
    )

    counts = script.adopt_tile_cache(tmp_path)

    assert counts == script.Adopted(rasters=2, roofs=1)
    assert cache.sidecar_matches(dsm, "dsm-key")
    assert cache.sidecar_matches(insol, "rad-key")
    assert cache.sidecar_matches(roofs, "score-key")


def test_leaves_failed_and_missing_tiles_alone_and_is_idempotent(script, tmp_path):
    failed_roofs = _legacy_artifact(pipeline.tile_cache_path(tmp_path, 1, 1, "roofs"), None)
    (tmp_path / pipeline.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "1_1": {"status": "failed", "stamp": "score-key", "error": "boom"},
                "2_2": {"status": "done", "stamp": "score-key", "n_roofs": 3},  # no parquet
            }
        )
    )

    assert script.adopt_tile_cache(tmp_path) == script.Adopted(rasters=0, roofs=0)
    assert cache.read_key_sidecar(failed_roofs) is None

    _legacy_artifact(pipeline.tile_cache_path(tmp_path, 3, 3, "dsm"), "dsm-key")
    assert script.adopt_tile_cache(tmp_path).rasters == 1
    assert script.adopt_tile_cache(tmp_path).rasters == 0  # already current format
