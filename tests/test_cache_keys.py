"""Unit tests for the tiered per-tile cache's key-derivation + sidecar surface (Seam A1,
ADR-0014 — "three chained component-version keys + a `.key` sidecar").

`pipeline.dsm_key` / `radiation_key` / `scoring_key` chain into each other (dsm_key ->
radiation_key -> scoring_key) so bumping one tier's component version invalidates that tier
and everything downstream, never upstream. `cache.py` (stdlib-only, no package imports —
avoids the dsm/radiation -> pipeline import cycle later tickets introduce) holds the sidecar
read/write/match helpers a producer uses to record an artifact's validity key next to it.

This ticket builds ONLY the pure key/sidecar surface — nothing here is wired into dsm.py,
radiation.py, or run_city yet (later tickets #10/#11/#12).

Expected values are behavioural comparisons (equal/unequal between two calls), never a
hand-rebuilt `json.dumps(...)` re-derivation of the impl's own formula (tautological — see
the task brief / tdd skill's anti-pattern list). Pure/offline: tmp_path only, no network/GRASS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rooftop_solar import cache, pipeline


def _dsm_kwargs(**overrides) -> dict:
    base = dict(
        origin=(315000.0, 4295000.0),
        tile_size_m=2000.0,
        buffer_m=300.0,
        row=0,
        col=0,
        resolution_m=2.0,
        dsm_source="pc-2m",
    )
    base.update(overrides)
    return base


def _rad_kwargs(dsm_key: str, **overrides) -> dict:
    base = dict(
        dsm_key=dsm_key,
        day_range=[172],
        grass_version="8.5.0",
    )
    base.update(overrides)
    return base


def _score_kwargs(radiation_key: str, **overrides) -> dict:
    base = dict(
        radiation_key=radiation_key,
        footprints_source="ms-buildings",
        method="multiplane",
    )
    base.update(overrides)
    return base


def _full_chain(**overrides) -> tuple[str, str, str]:
    """Build a dsm_key -> radiation_key -> scoring_key chain, with overrides applied to
    whichever kwargs dict(s) they belong in (by key name)."""
    dsm_over = {k: v for k, v in overrides.items() if k in _dsm_kwargs()}
    rad_over = {k: v for k, v in overrides.items() if k in (*_rad_kwargs(""), "horizon_params")}
    score_over = {k: v for k, v in overrides.items() if k in ("footprints_source", "method")}

    dk = pipeline.dsm_key(
        **_dsm_kwargs(**dsm_over),
        version=overrides.get("dsm_version", pipeline.DSM_VERSION),
    )
    rk = pipeline.radiation_key(
        **_rad_kwargs(dk, **rad_over),
        version=overrides.get("radiation_version", pipeline.RADIATION_VERSION),
    )
    sk = pipeline.scoring_key(
        **_score_kwargs(rk, **score_over),
        version=overrides.get("scoring_version", pipeline.SCORING_VERSION),
    )
    return dk, rk, sk


# --------------------------------------------------------------------------- #
# Stability                                                                   #
# --------------------------------------------------------------------------- #


def test_dsm_key_is_stable_for_identical_kwargs():
    a = pipeline.dsm_key(**_dsm_kwargs())
    b = pipeline.dsm_key(**_dsm_kwargs())

    assert a == b


def test_radiation_key_is_stable_for_identical_kwargs():
    dk = pipeline.dsm_key(**_dsm_kwargs())
    a = pipeline.radiation_key(**_rad_kwargs(dk))
    b = pipeline.radiation_key(**_rad_kwargs(dk))

    assert a == b


def test_scoring_key_is_stable_for_identical_kwargs():
    dk = pipeline.dsm_key(**_dsm_kwargs())
    rk = pipeline.radiation_key(**_rad_kwargs(dk))
    a = pipeline.scoring_key(**_score_kwargs(rk))
    b = pipeline.scoring_key(**_score_kwargs(rk))

    assert a == b


def test_radiation_key_ignores_day_range_order():
    dk = pipeline.dsm_key(**_dsm_kwargs())
    a = pipeline.radiation_key(**_rad_kwargs(dk, day_range=[200, 172]))
    b = pipeline.radiation_key(**_rad_kwargs(dk, day_range=[172, 200]))

    assert a == b


# --------------------------------------------------------------------------- #
# Invalidation matrix                                                         #
# --------------------------------------------------------------------------- #


def test_bumping_dsm_version_cascades_through_radiation_and_scoring():
    dk1, rk1, sk1 = _full_chain(dsm_version="1")
    dk2, rk2, sk2 = _full_chain(dsm_version="2")

    assert dk1 != dk2
    assert rk1 != rk2
    assert sk1 != sk2


def test_bumping_radiation_version_leaves_dsm_key_unchanged():
    dk1, rk1, sk1 = _full_chain(radiation_version="1")
    dk2, rk2, sk2 = _full_chain(radiation_version="2")

    assert dk1 == dk2
    assert rk1 != rk2
    assert sk1 != sk2


def test_bumping_scoring_version_leaves_dsm_and_radiation_keys_unchanged():
    dk1, rk1, sk1 = _full_chain(scoring_version="1")
    dk2, rk2, sk2 = _full_chain(scoring_version="2")

    assert dk1 == dk2
    assert rk1 == rk2
    assert sk1 != sk2


def test_changing_day_range_cascades_into_scoring_but_not_dsm():
    dk1, rk1, sk1 = _full_chain(day_range=[172])
    dk2, rk2, sk2 = _full_chain(day_range=[172, 200])

    assert dk1 == dk2
    assert rk1 != rk2
    assert sk1 != sk2


def test_changing_method_only_changes_scoring_key():
    dk1, rk1, sk1 = _full_chain(method="multiplane")
    dk2, rk2, sk2 = _full_chain(method="ransac")

    assert dk1 == dk2
    assert rk1 == rk2
    assert sk1 != sk2


def test_changing_footprints_source_only_changes_scoring_key():
    dk1, rk1, sk1 = _full_chain(footprints_source="ms-buildings")
    dk2, rk2, sk2 = _full_chain(footprints_source="osm")

    assert dk1 == dk2
    assert rk1 == rk2
    assert sk1 != sk2


def test_changing_dsm_geometry_field_cascades_through_everything():
    dk1, rk1, sk1 = _full_chain(row=0)
    dk2, rk2, sk2 = _full_chain(row=1)

    assert dk1 != dk2
    assert rk1 != rk2
    assert sk1 != sk2


# --------------------------------------------------------------------------- #
# Reserved horizon-params slot (radiation_key)                                #
# --------------------------------------------------------------------------- #


def test_horizon_params_default_none_is_stable():
    dk = pipeline.dsm_key(**_dsm_kwargs())
    a = pipeline.radiation_key(**_rad_kwargs(dk))
    b = pipeline.radiation_key(**_rad_kwargs(dk), horizon_params=None)

    assert a == b


def test_horizon_params_changes_radiation_key():
    dk = pipeline.dsm_key(**_dsm_kwargs())
    a = pipeline.radiation_key(**_rad_kwargs(dk), horizon_params=None)
    b = pipeline.radiation_key(**_rad_kwargs(dk), horizon_params={"maxdistance": 500})

    assert a != b


# --------------------------------------------------------------------------- #
# Sidecar (cache.py) — stdlib only, tmp_path                                  #
# --------------------------------------------------------------------------- #


def test_sidecar_path_appends_key_suffix(tmp_path: Path):
    artifact = tmp_path / "tile_0_0.tif"

    assert cache.sidecar_path(artifact) == Path(f"{artifact}.key")


def test_write_then_read_key_sidecar_round_trips(tmp_path: Path):
    artifact = tmp_path / "tile_0_0.tif"
    artifact.write_bytes(b"fake raster")

    cache.write_key_sidecar(artifact, "key-1")

    assert cache.read_key_sidecar(artifact) == "key-1"


def test_read_key_sidecar_missing_returns_none(tmp_path: Path):
    artifact = tmp_path / "tile_0_0.tif"

    assert cache.read_key_sidecar(artifact) is None


def test_sidecar_matches_true_when_artifact_and_sidecar_agree(tmp_path: Path):
    artifact = tmp_path / "tile_0_0.tif"
    artifact.write_bytes(b"fake raster")
    cache.write_key_sidecar(artifact, "key-1")

    assert cache.sidecar_matches(artifact, "key-1") is True


def test_sidecar_matches_false_for_mismatched_key(tmp_path: Path):
    artifact = tmp_path / "tile_0_0.tif"
    artifact.write_bytes(b"fake raster")
    cache.write_key_sidecar(artifact, "key-1")

    assert cache.sidecar_matches(artifact, "key-2") is False


def test_sidecar_matches_false_when_sidecar_missing(tmp_path: Path):
    artifact = tmp_path / "tile_0_0.tif"
    artifact.write_bytes(b"fake raster")

    assert cache.sidecar_matches(artifact, "key-1") is False


def test_sidecar_matches_false_when_artifact_deleted_after_sidecar_written(tmp_path: Path):
    artifact = tmp_path / "tile_0_0.tif"
    artifact.write_bytes(b"fake raster")
    cache.write_key_sidecar(artifact, "key-1")
    artifact.unlink()

    assert cache.sidecar_matches(artifact, "key-1") is False


def test_write_key_sidecar_raises_for_missing_artifact(tmp_path: Path):
    artifact = tmp_path / "tile_0_0.tif"

    with pytest.raises(FileNotFoundError, match="tile_0_0.tif"):
        cache.write_key_sidecar(artifact, "key-1")


# --- cross-machine handoff integrity (issue #18): the sidecar also pins the artifact bytes ---


def test_sidecar_matches_false_for_truncated_artifact(tmp_path: Path):
    artifact = tmp_path / "tile_0_0.tif"
    artifact.write_bytes(b"fake raster bytes")
    cache.write_key_sidecar(artifact, "key-1")

    artifact.write_bytes(b"fake raster")  # truncated; sidecar left intact

    assert cache.sidecar_matches(artifact, "key-1") is False


def test_sidecar_matches_false_for_same_size_replaced_artifact(tmp_path: Path):
    artifact = tmp_path / "tile_0_0.tif"
    artifact.write_bytes(b"old raster!")
    cache.write_key_sidecar(artifact, "key-1")

    artifact.write_bytes(b"new raster!")  # same length, different content

    assert cache.sidecar_matches(artifact, "key-1") is False


def test_sidecar_matches_false_for_legacy_plain_text_sidecar(tmp_path: Path):
    artifact = tmp_path / "tile_0_0.tif"
    artifact.write_bytes(b"fake raster")
    cache.sidecar_path(artifact).write_text("key-1")  # pre-#18 format: the bare key

    assert cache.read_key_sidecar(artifact) is None
    assert cache.sidecar_matches(artifact, "key-1") is False


# --------------------------------------------------------------------------- #
# GRASS engine version in the radiation key (issue #14, ADR-0014 addendum)
# --------------------------------------------------------------------------- #


def test_radiation_key_is_stable_for_same_grass_version():
    dk, rk_a, _ = _full_chain(grass_version="8.5.0")
    _, rk_b, _ = _full_chain(grass_version="8.5.0")
    assert rk_a == rk_b


def test_changing_grass_version_cascades_into_scoring_but_not_dsm():
    dk_a, rk_a, sk_a = _full_chain(grass_version="8.5.0")
    dk_b, rk_b, sk_b = _full_chain(grass_version="8.6.0")
    assert dk_a == dk_b
    assert rk_a != rk_b
    assert sk_a != sk_b


def test_radiation_key_requires_grass_version():
    dk = pipeline.dsm_key(**_dsm_kwargs())
    with pytest.raises(TypeError):
        pipeline.radiation_key(dsm_key=dk, day_range=[172])  # type: ignore[call-arg]
