"""Unit tests for the city-scale idempotency manifest (Part 2-2, ADR-0009 — "idempotency
= a tile manifest + a params stamp").

`params_stamp` fingerprints the params that invalidate a cached tile; `TileManifest` records
per-tile status + stamp, persisted as JSON, so a crashed or accuracy-swapped multi-hour run
resumes only what it must (stage-2-part2-plan.md §3, §5, §7). Pure/offline: tmp_path, no
network/GRASS.

Expected values are behavioural comparisons (equal/unequal between two calls), never a
hand-rebuilt `json.dumps(...)` re-derivation of the impl's own formula (tautological — see
the task brief / tdd skill's anti-pattern list).
"""

from __future__ import annotations

from pathlib import Path

from rooftop_solar import pipeline, tiling

_TILE_A = tiling.Tile(row=0, col=0, core=None, buffered=None)
_TILE_B = tiling.Tile(row=0, col=1, core=None, buffered=None)


def _kwargs(**overrides) -> dict:
    base = dict(
        day_range=[172],
        buffer_m=300.0,
        tile_size_m=2000.0,
        origin=(315000.0, 4295000.0),
        dsm_source="pc-2m",
        footprints_source="ms-buildings",
    )
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# params_stamp                                                                #
# --------------------------------------------------------------------------- #


def test_params_stamp_is_stable_for_identical_kwargs():
    a = pipeline.params_stamp(**_kwargs())
    b = pipeline.params_stamp(**_kwargs())

    assert a == b


def test_params_stamp_changes_when_day_range_changes():
    a = pipeline.params_stamp(**_kwargs(day_range=[172]))
    b = pipeline.params_stamp(**_kwargs(day_range=[172, 200]))

    assert a != b


def test_params_stamp_changes_when_pipeline_version_bumps():
    a = pipeline.params_stamp(**_kwargs(), pipeline_version="2-2.0")
    b = pipeline.params_stamp(**_kwargs(), pipeline_version="2-2.1")

    assert a != b


def test_params_stamp_ignores_day_range_order():
    # Sorted for the stamp (plan: "day_range sorted") — reordering the same set of days is
    # not a params change worth invalidating a cache over.
    a = pipeline.params_stamp(**_kwargs(day_range=[200, 172]))
    b = pipeline.params_stamp(**_kwargs(day_range=[172, 200]))

    assert a == b


# --------------------------------------------------------------------------- #
# TileManifest                                                                #
# --------------------------------------------------------------------------- #


def test_is_current_false_for_unknown_tile(tmp_path: Path):
    manifest = pipeline.TileManifest.load(tmp_path / "manifest.json")

    assert manifest.is_current(_TILE_A, "stamp-1") is False


def test_is_current_true_only_when_done_and_stamp_matches(tmp_path: Path):
    manifest = pipeline.TileManifest.load(tmp_path / "manifest.json")

    manifest.mark_done(_TILE_A, "stamp-1", n_roofs=5)

    assert manifest.is_current(_TILE_A, "stamp-1") is True
    assert manifest.is_current(_TILE_A, "stamp-2") is False


def test_is_current_false_for_failed_tile(tmp_path: Path):
    manifest = pipeline.TileManifest.load(tmp_path / "manifest.json")

    manifest.mark_failed(_TILE_A, "stamp-1", error="boom")

    assert manifest.is_current(_TILE_A, "stamp-1") is False


def test_save_then_load_round_trips_is_current(tmp_path: Path):
    path = tmp_path / "nested" / "manifest.json"
    manifest = pipeline.TileManifest.load(path)
    manifest.mark_done(_TILE_A, "stamp-1", n_roofs=3)
    manifest.mark_failed(_TILE_B, "stamp-1", error="oops")
    manifest.save()

    reloaded = pipeline.TileManifest.load(path)

    assert reloaded.is_current(_TILE_A, "stamp-1") is True
    assert reloaded.is_current(_TILE_B, "stamp-1") is False
    assert reloaded.is_current(_TILE_A, "stamp-2") is False
