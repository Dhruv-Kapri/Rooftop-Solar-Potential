"""Unit tests for the spot-check label comparison (Part 2-4 step 7b; plan §6, ADR-0013).

Seam under test: `validation.compare_labels` joins hand labels (plane count, obstruction y/n)
to a `DetailedScore` and scores each (building, method); `summarize_spot_check` rolls that
to the per-method write-up table. Every expected value below is worked out by hand from the
small tables in `_score` / `_labels`, never by re-running the code's own arithmetic.

Fast: pandas + geopandas on hand-made frames, no rasters, no network.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from rooftop_solar import validation

FOOT = box(0, 0, 10, 10)


def _buildings(rows):
    """rows: (building_id, method, n_planes, tilt_deg)."""
    return gpd.GeoDataFrame(
        {
            "building_id": [r[0] for r in rows],
            "method": [r[1] for r in rows],
            "n_planes": [r[2] for r in rows],
            "tilt_deg": [r[3] for r in rows],
        },
        geometry=[FOOT] * len(rows),
    )


def _score():
    """Five labelled roofs (a-e) plus an unlabelled one (z), both methods each."""
    nan = float("nan")
    buildings = _buildings(
        [
            ("a", "multiplane", 2, 20.0), ("a", "ransac", 1, 20.0),
            ("b", "multiplane", 4, 20.0), ("b", "ransac", 1, 20.0),
            ("c", "multiplane", 3, 20.0), ("c", "ransac", 1, 20.0),
            ("d", "multiplane", 2, 20.0), ("d", "ransac", 1, 20.0),
            ("e", "multiplane", 1, nan), ("e", "ransac", 1, nan),  # unfittable placeholder
            ("z", "multiplane", 9, 20.0), ("z", "ransac", 1, 20.0),
        ]
    )  # fmt: skip
    # b / multiplane: four planes; planes 2 and 3 tie on area (10 m2), tie-break on plane_id.
    plane_rows = [
        ("b", "multiplane", 0, 100.0, 60.0),
        ("b", "multiplane", 1, 50.0, 30.0),
        ("b", "multiplane", 2, 10.0, 4.0),
        ("b", "multiplane", 3, 10.0, 3.0),
    ]
    planes = gpd.GeoDataFrame(
        {
            "building_id": [r[0] for r in plane_rows],
            "method": [r[1] for r in plane_rows],
            "plane_id": [r[2] for r in plane_rows],
            "plane_area_m2": [r[3] for r in plane_rows],
            "usable_area_m2": [r[4] for r in plane_rows],
        },
        geometry=[FOOT] * len(plane_rows),
    )
    obs_rows = [
        ("b", "multiplane", 3, 5.0),
        ("b", "ransac", 0, 2.0),
        ("c", "multiplane", 0, 1.5),
        ("d", "ransac", 0, 0.0),  # zero area does not count as an obstruction
    ]
    obstructions = gpd.GeoDataFrame(
        {
            "building_id": [r[0] for r in obs_rows],
            "method": [r[1] for r in obs_rows],
            "plane_id": [r[2] for r in obs_rows],
            "obstruction_area_m2": [r[3] for r in obs_rows],
        },
        geometry=[FOOT] * len(obs_rows),
    )
    return validation.DetailedScore(
        planes=planes, obstructions=obstructions, membership=pd.DataFrame(), buildings=buildings
    )


def _labels():
    return pd.DataFrame(
        {
            "building_id": ["a", "b", "c", "d", "e"],
            "n_major_planes": ["2", "3", "1", "2", "1"],
            "has_obstruction": ["n", "Y", " no ", "yes", "FALSE"],
            "notes": ["", "", "", "", ""],
        }
    )


def _row(comparison, building_id, method):
    sel = comparison[(comparison["building_id"] == building_id) & (comparison["method"] == method)]
    assert len(sel) == 1
    return sel.iloc[0]


def test_one_row_per_labelled_building_and_method_in_order():
    comparison = validation.compare_labels(_labels(), _score())
    assert list(comparison["building_id"]) == ["a", "a", "b", "b", "c", "c", "d", "d", "e", "e"]
    assert list(comparison["method"]) == ["multiplane", "ransac"] * 5


def test_plane_counts_diff_and_tolerance():
    c = validation.compare_labels(_labels(), _score())
    # a: label 2 -> multiplane 2 (diff 0), ransac 1 (diff -1): both within +-1
    assert (
        _row(c, "a", "multiplane")["model_planes"],
        _row(c, "a", "multiplane")["plane_diff"],
    ) == (2, 0)
    assert _row(c, "a", "ransac")["plane_diff"] == -1 and _row(c, "a", "ransac")["plane_agree"]
    # b: label 3 -> multiplane 4 (+1, agrees at the boundary), ransac 1 (-2, does not)
    assert (
        _row(c, "b", "multiplane")["plane_diff"] == 1 and _row(c, "b", "multiplane")["plane_agree"]
    )
    assert _row(c, "b", "ransac")["plane_diff"] == -2 and not _row(c, "b", "ransac")["plane_agree"]
    # c: label 1 -> multiplane 3 (+2, disagrees)
    assert (
        _row(c, "c", "multiplane")["plane_diff"] == 2
        and not _row(c, "c", "multiplane")["plane_agree"]
    )
    assert _row(c, "a", "multiplane")["label_planes"] == 2


def test_unfittable_roof_counts_zero_model_planes():
    c = validation.compare_labels(_labels(), _score())
    for method in ("multiplane", "ransac"):
        r = _row(c, "e", method)
        assert r["model_planes"] == 0  # placeholder n_planes=1 with NaN tilt
        assert r["plane_diff"] == -1 and r["plane_agree"]


def test_obstruction_outcomes():
    c = validation.compare_labels(_labels(), _score())
    expected = {
        ("a", "multiplane"): "true_negative", ("a", "ransac"): "true_negative",
        ("b", "multiplane"): "hit", ("b", "ransac"): "hit",
        ("c", "multiplane"): "false_positive", ("c", "ransac"): "true_negative",
        ("d", "multiplane"): "miss", ("d", "ransac"): "miss",  # ransac: 0 m2 row is no obstruction
        ("e", "multiplane"): "true_negative", ("e", "ransac"): "true_negative",
    }  # fmt: skip
    for (bid, method), outcome in expected.items():
        assert _row(c, bid, method)["obstruction_outcome"] == outcome, (bid, method)
    r = _row(c, "b", "multiplane")
    assert r["label_obstruction"] and r["model_obstruction"]
    assert not _row(c, "d", "ransac")["model_obstruction"]


def test_large_planar_obstruction_flag_and_escaped_area():
    c = validation.compare_labels(_labels(), _score())
    flagged = c[c["large_planar_obstruction"]]
    assert list(zip(flagged["building_id"], flagged["method"], strict=True)) == [
        ("b", "multiplane")
    ]
    r = _row(c, "b", "multiplane")
    assert r["extra_planes"] == 1
    # 1 surplus plane = the smallest; planes 2 and 3 tie at 10 m2 -> plane_id 2 -> 4.0 m2
    assert r["escaped_usable_m2"] == pytest.approx(4.0)
    # c/multiplane over-counts planes but is labelled obstruction-free: not the ADR-0013 case
    r = _row(c, "c", "multiplane")
    assert not r["large_planar_obstruction"] and r["extra_planes"] == 0
    assert r["escaped_usable_m2"] == 0.0


def test_escaped_area_sums_extra_smallest_planes():
    labels = _labels()
    labels.loc[labels["building_id"] == "b", "n_major_planes"] = "2"  # 4 planes vs 2 -> extra 2
    r = _row(validation.compare_labels(labels, _score()), "b", "multiplane")
    assert r["extra_planes"] == 2
    assert r["escaped_usable_m2"] == pytest.approx(7.0)  # planes 2 + 3: 4.0 + 3.0


def test_unlabelled_buildings_are_ignored():
    c = validation.compare_labels(_labels(), _score())
    assert "z" not in set(c["building_id"])


def test_notes_starting_exclude_drop_the_roof_even_if_unlabelled():
    labels = _labels()
    labels.loc[labels["building_id"] == "c", "notes"] = " exclude (likely not a roof)"
    labels.loc[labels["building_id"] == "d", ["n_major_planes", "has_obstruction", "notes"]] = [
        "", "", "EXCLUDE: can't tell",
    ]  # fmt: skip
    labels.loc[labels["building_id"] == "e", "notes"] = "not EXCLUDE-prefixed"
    c = validation.compare_labels(labels, _score())
    assert list(c["building_id"].unique()) == ["a", "b", "e"]
    assert validation.excluded_labels(labels) == ["c", "d"]


def test_label_parsing_accepts_bools_ints_and_ints_as_text():
    labels = pd.DataFrame(
        {
            "building_id": ["a", "b", "c"],
            "n_major_planes": [2, 3.0, "1"],
            "has_obstruction": [False, True, 0],
        }
    )
    c = validation.compare_labels(labels, _score())
    assert list(c[c["method"] == "multiplane"]["label_obstruction"]) == [False, True, False]
    assert list(c[c["method"] == "multiplane"]["label_planes"]) == [2, 3, 1]


@pytest.mark.parametrize(
    "column, bad",
    [
        ("n_major_planes", ""),  # blank (unfinished file)
        ("n_major_planes", "0"),
        ("n_major_planes", "2.5"),
        ("n_major_planes", "two"),
        ("n_major_planes", "inf"),
        ("has_obstruction", ""),
        ("has_obstruction", "maybe"),
    ],
)
def test_invalid_or_blank_label_names_the_building(column, bad):
    labels = _labels()
    labels.loc[labels["building_id"] == "c", column] = bad
    with pytest.raises(ValueError, match="c"):
        validation.compare_labels(labels, _score())


def test_blank_labels_file_names_every_unfinished_building():
    labels = _labels().assign(n_major_planes="", has_obstruction="")
    with pytest.raises(ValueError) as exc:
        validation.compare_labels(labels, _score())
    for bid in "abcde":
        assert f"'{bid}'" in str(exc.value)


def test_duplicate_building_id_raises():
    labels = pd.concat([_labels(), _labels().iloc[[1]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate.*'b'"):
        validation.compare_labels(labels, _score())


def test_label_for_unscored_building_raises():
    labels = pd.concat(
        [_labels(), pd.DataFrame({"building_id": ["nope"], "n_major_planes": ["1"],
                                  "has_obstruction": ["n"]})],
        ignore_index=True,
    )  # fmt: skip
    with pytest.raises(ValueError, match="'nope'"):
        validation.compare_labels(labels, _score())


def test_summarize_spot_check_by_method():
    summary = validation.summarize_spot_check(validation.compare_labels(_labels(), _score()))
    assert list(summary.index) == ["multiplane", "ransac"]
    mp, rs = summary.loc["multiplane"], summary.loc["ransac"]
    # multiplane: agree a,b,d,e (c is +2) -> 4/5; hit b, miss d, FP c, TN a+e; one flagged, 4.0 m2
    assert (mp["n_roofs"], mp["plane_agree"], mp["plane_agree_pct"]) == (5, 4, 80.0)
    assert (mp["obstruction_hits"], mp["obstruction_misses"]) == (1, 1)
    assert (mp["obstruction_false_positives"], mp["obstruction_true_negatives"]) == (1, 2)
    assert mp["large_planar_obstructions"] == 1
    assert mp["escaped_usable_m2"] == pytest.approx(4.0)
    # ransac: agree a,c,d,e (b is -2) -> 4/5; hit b, miss d, no FP, TN a+c+e; none flagged
    assert (rs["plane_agree"], rs["plane_agree_pct"]) == (4, 80.0)
    assert (rs["obstruction_hits"], rs["obstruction_misses"]) == (1, 1)
    assert (rs["obstruction_false_positives"], rs["obstruction_true_negatives"]) == (0, 3)
    assert rs["large_planar_obstructions"] == 0 and rs["escaped_usable_m2"] == 0.0
