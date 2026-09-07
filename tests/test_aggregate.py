"""Unit tests for the census-tract aggregation core (Phase 2, Part 2-1; ADR-0006/0007).

Three independent pure transforms, tested separately on synthetic tracts/buildings — no
network, no real census data:

  - `aggregate_to_tracts` — centroid point-in-polygon roll-up. *Conservation* invariant (§6):
    every roof lands in exactly one tract by its centroid, extensive sums add back to the AOI
    totals, unusable roofs contribute 0 and are never dropped.
  - `attach_equity` — GEOID (zero-padded string) join of ACS + LEAD, per-household/per-capita
    normalization, and the `equity_data_missing` flag (a missing match is flagged, never 0).
  - `classify_equity` — 2×2 median split into potential/burden levels + the priority flag,
    with `unknown` for flagged-missing rows excluded from the medians.

Expected values are worked by hand from the ADR contracts, never read back from the code.
Fast: pure pandas/geopandas, no DSM, no GRASS, no network.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

from rooftop_solar import aggregate, config

# Two adjacent 100×100 m tracts sharing the x=100 edge, in the metric working CRS.
_GEOID_A = "11001000100"
_GEOID_B = "11001000200"
_TRACT_A = box(0.0, 0.0, 100.0, 100.0)
_TRACT_B = box(100.0, 0.0, 200.0, 100.0)


def _tracts_gdf():
    return gpd.GeoDataFrame(
        {"GEOID": [_GEOID_A, _GEOID_B]},
        geometry=[_TRACT_A, _TRACT_B],
        crs=config.WORKING_CRS,
    )


def _buildings_gdf():
    """Four synthetic roofs with hand-chosen centroids and per-roof yields.

    b3 is a boundary straddler: its polygon spans the x=100 tract edge, but its centroid
    (x=102) sits in tract B — so a *centroid* assignment gives it to B exactly once, while a
    naive polygon-intersect join would double-count it into both tracts.
    """
    rows = [
        # geometry, usable, u_area, capacity, energy, co2, suit
        (box(40.0, 40.0, 60.0, 60.0), True, 70.0, 14.0, 1000.0, 350.0, 80.0),   # →A
        (box(10.0, 10.0, 30.0, 30.0), False, 0.0, 0.0, 0.0, 0.0, 0.0),          # →A unusable
        (box(140.0, 40.0, 160.0, 60.0), True, 30.0, 6.0, 500.0, 175.0, 60.0),   # →B
        (box(95.0, 40.0, 109.0, 60.0), True, 20.0, 4.0, 300.0, 105.0, 40.0),    # →B straddler
    ]
    return gpd.GeoDataFrame(
        {
            "usable": [r[1] for r in rows],
            "usable_area_m2": [r[2] for r in rows],
            "capacity_kw": [r[3] for r in rows],
            "annual_energy_kwh": [r[4] for r in rows],
            "annual_co2_kg": [r[5] for r in rows],
            "suitability": [r[6] for r in rows],
        },
        geometry=[r[0] for r in rows],
        crs=config.WORKING_CRS,
    )


# --------------------------------------------------------------------------- #
# aggregate_to_tracts — centroid roll-up + conservation                       #
# --------------------------------------------------------------------------- #

def test_aggregate_to_tracts_conserves_extensive_totals():
    buildings = _buildings_gdf()
    out = aggregate.aggregate_to_tracts(buildings, _tracts_gdf())

    # Every extensive quantity summed over tracts equals the AOI total (nothing dropped or
    # double-counted — the straddler lands in exactly one tract). ± float tolerance.
    for col in ("usable_area_m2", "capacity_kw", "annual_energy_kwh", "annual_co2_kg"):
        np.testing.assert_allclose(out[col].sum(), buildings[col].sum())
    assert out["n_buildings"].sum() == len(buildings)


def test_aggregate_to_tracts_per_tract_sums_and_summaries():
    out = aggregate.aggregate_to_tracts(_buildings_gdf(), _tracts_gdf()).set_index("GEOID")

    # Tract A holds b0 (usable) + b1 (unusable, contributes 0 but is counted, not dropped).
    a = out.loc[_GEOID_A]
    assert a["n_buildings"] == 2
    assert a["n_usable"] == 1
    np.testing.assert_allclose(a["usable_area_m2"], 70.0)
    np.testing.assert_allclose(a["annual_energy_kwh"], 1000.0)
    np.testing.assert_allclose(a["capacity_kw"], 14.0)
    np.testing.assert_allclose(a["annual_co2_kg"], 350.0)
    np.testing.assert_allclose(a["median_suitability"], 40.0)  # median([80, 0]) over ALL roofs
    np.testing.assert_allclose(a["pct_usable"], 50.0)  # 1 of 2 usable, as a percentage

    # Tract B holds b2 + the straddler b3 — assigned to B by centroid, exactly once.
    b = out.loc[_GEOID_B]
    assert b["n_buildings"] == 2
    assert b["n_usable"] == 2
    np.testing.assert_allclose(b["annual_energy_kwh"], 800.0)  # 500 + 300, NOT A's
    np.testing.assert_allclose(b["usable_area_m2"], 50.0)
    np.testing.assert_allclose(b["pct_usable"], 100.0)


def test_aggregate_to_tracts_preserves_tract_geometry_and_geoid_string():
    out = aggregate.aggregate_to_tracts(_buildings_gdf(), _tracts_gdf())

    assert isinstance(out, gpd.GeoDataFrame)
    assert out.crs == config.WORKING_CRS
    # One row per tract that contains ≥1 building; tract geometry preserved (100×100 = 1e4 m²).
    assert set(out["GEOID"]) == {_GEOID_A, _GEOID_B}
    np.testing.assert_allclose(out.geometry.area.to_numpy(), 10000.0)
    # GEOID stays a string — never coerced to int (§6, §11).
    assert out["GEOID"].map(type).eq(str).all()


def test_aggregate_to_tracts_drops_tracts_with_no_buildings():
    # A third empty tract far from every building must not appear in the output.
    tracts = gpd.GeoDataFrame(
        {"GEOID": [_GEOID_A, _GEOID_B, "11001009900"]},
        geometry=[_TRACT_A, _TRACT_B, box(500.0, 500.0, 600.0, 600.0)],
        crs=config.WORKING_CRS,
    )
    out = aggregate.aggregate_to_tracts(_buildings_gdf(), tracts)
    assert "11001009900" not in set(out["GEOID"])


def test_aggregate_to_tracts_boundary_centroid_assigned_once():
    # The §8 "boundary-straddler" case, done properly: a roof whose CENTROID lands exactly on
    # the shared tract edge (x=100). It must be assigned to exactly one tract — never dropped
    # (conservation, §6). A `within` predicate excludes the boundary and would silently lose it;
    # `intersects` + dedup keeps it once.
    buildings = gpd.GeoDataFrame(
        {
            "usable": [True],
            "usable_area_m2": [10.0],
            "capacity_kw": [2.0],
            "annual_energy_kwh": [100.0],
            "annual_co2_kg": [35.0],
            "suitability": [50.0],
        },
        geometry=[box(90.0, 40.0, 110.0, 60.0)],  # centroid = (100, 50), on the A|B edge
        crs=config.WORKING_CRS,
    )
    out = aggregate.aggregate_to_tracts(buildings, _tracts_gdf())

    assert out["n_buildings"].sum() == 1  # assigned once — not zero (dropped), not two
    np.testing.assert_allclose(out["annual_energy_kwh"].sum(), 100.0)  # conserved, §6


# --------------------------------------------------------------------------- #
# attach_equity — GEOID string join + normalization + missing flag            #
# --------------------------------------------------------------------------- #

def _tracts_for_equity():
    """Minimal post-aggregation tracts: GEOID (str) + extensive energy + geometry."""
    return gpd.GeoDataFrame(
        {
            "GEOID": ["11001000100", "11001000200", "11001000300"],
            "annual_energy_kwh": [1000.0, 800.0, 600.0],
        },
        geometry=[_TRACT_A, _TRACT_B, box(200.0, 0.0, 300.0, 100.0)],
        crs=config.WORKING_CRS,
    )


def test_attach_equity_joins_on_geoid_string_and_normalizes():
    tracts = _tracts_for_equity()
    acs = pd.DataFrame(
        {
            "GEOID": ["11001000100", "11001000200"],
            "population": [200.0, 100.0],
            "households": [100.0, 50.0],
            "median_income": [50000.0, 40000.0],
        }
    )
    energy_burden = pd.DataFrame(
        {"GEOID": ["11001000100", "11001000200"], "energy_burden": [0.03, 0.05]}
    )

    out = aggregate.attach_equity(tracts, acs, energy_burden).set_index("GEOID")

    t1 = out.loc["11001000100"]
    np.testing.assert_allclose(t1["households"], 100.0)  # the string join actually matched
    np.testing.assert_allclose(t1["potential_per_household"], 10.0)  # 1000 / 100
    np.testing.assert_allclose(t1["potential_per_capita"], 5.0)  # 1000 / 200
    np.testing.assert_allclose(t1["energy_burden"], 0.03)
    assert not bool(t1["equity_data_missing"])

    t2 = out.loc["11001000200"]
    np.testing.assert_allclose(t2["potential_per_household"], 16.0)  # 800 / 50
    assert not bool(t2["equity_data_missing"])


def test_attach_equity_flags_unmatched_tract_never_zeroes_it():
    tracts = _tracts_for_equity()
    acs = pd.DataFrame(
        {
            "GEOID": ["11001000100", "11001000200"],
            "population": [200.0, 100.0],
            "households": [100.0, 50.0],
            "median_income": [50000.0, 40000.0],
        }
    )
    energy_burden = pd.DataFrame(
        {"GEOID": ["11001000100", "11001000200"], "energy_burden": [0.03, 0.05]}
    )

    out = aggregate.attach_equity(tracts, acs, energy_burden).set_index("GEOID")

    # 11001000300 matched no ACS/LEAD row → flagged, and normalized values are NaN, NOT 0
    # (a spurious 0 would corrupt the medians in classify_equity — ADR-0007).
    t3 = out.loc["11001000300"]
    assert bool(t3["equity_data_missing"])
    assert np.isnan(t3["potential_per_household"])
    assert np.isnan(t3["energy_burden"])
    assert not (t3["potential_per_household"] == 0)


def test_attach_equity_guards_zero_households():
    # A real tract can have 0 occupied housing units (industrial/park). Per-household must be
    # NaN (not inf/0) and the tract flagged, so it never enters the medians.
    tracts = gpd.GeoDataFrame(
        {"GEOID": ["11001000100"], "annual_energy_kwh": [400.0]},
        geometry=[_TRACT_A],
        crs=config.WORKING_CRS,
    )
    acs = pd.DataFrame(
        {
            "GEOID": ["11001000100"],
            "population": [100.0],
            "households": [0.0],
            "median_income": [40000.0],
        }
    )
    energy_burden = pd.DataFrame({"GEOID": ["11001000100"], "energy_burden": [0.04]})

    out = aggregate.attach_equity(tracts, acs, energy_burden).set_index("GEOID")
    t = out.loc["11001000100"]
    assert np.isnan(t["potential_per_household"])
    assert bool(t["equity_data_missing"])


# --------------------------------------------------------------------------- #
# classify_equity — 2×2 median split + priority + unknown                     #
# --------------------------------------------------------------------------- #

def _classify_input():
    """Five classifiable tracts spanning all four quadrants (one sitting exactly on both
    medians, to pin tie handling) + one flagged-missing tract that must not move the medians.

    per_household medians over [50,40,10,20,30] → 30; energy_burden over [5,1,5,1,3] → 3.
    """
    return gpd.GeoDataFrame(
        {
            "GEOID": ["r1", "r2", "r3", "r4", "r5", "r6"],
            "potential_per_household": [50.0, 40.0, 10.0, 20.0, 30.0, np.nan],
            "energy_burden": [5.0, 1.0, 5.0, 1.0, 3.0, np.nan],
            "equity_data_missing": [False, False, False, False, False, True],
        },
        geometry=[box(i, 0.0, i + 1.0, 1.0) for i in range(6)],
        crs=config.WORKING_CRS,
    )


def test_classify_equity_quadrants_priority_and_ties():
    out = aggregate.classify_equity(_classify_input()).set_index("GEOID")

    # Tie convention: value == median → "high" (≥, matching the boundary-inclusive style of
    # the Stage-1 cutoffs). r5 sits on both medians → high/high → priority.
    assert out.loc["r5", "potential_level"] == "high"
    assert out.loc["r5", "burden_level"] == "high"
    assert bool(out.loc["r5", "is_priority"])

    # All four quadrants are represented and named consistently.
    assert out.loc["r1", "equity_class"] == "high_potential_high_burden"  # priority
    assert out.loc["r2", "equity_class"] == "high_potential_low_burden"
    assert out.loc["r3", "equity_class"] == "low_potential_high_burden"
    assert out.loc["r4", "equity_class"] == "low_potential_low_burden"
    assert bool(out.loc["r1", "is_priority"])
    assert not bool(out.loc["r2", "is_priority"])


def test_classify_equity_marks_missing_unknown_and_excludes_from_medians():
    out = aggregate.classify_equity(_classify_input()).set_index("GEOID")

    # The flagged-missing row is classified "unknown" and never a priority tract.
    assert out.loc["r6", "equity_class"] == "unknown"
    assert not bool(out.loc["r6", "is_priority"])

    # Medians were taken over the 5 classifiable rows only. If r6's NaN had leaked in it would
    # not change a nanmedian, but a spurious 0 would drop the median — guard by re-deriving:
    # with the median at 30, exactly the tracts ≥30 are "high".
    highs = {g for g in ("r1", "r2", "r3", "r4", "r5") if out.loc[g, "potential_level"] == "high"}
    assert highs == {"r1", "r2", "r5"}  # 50, 40, 30 ≥ 30


def test_classify_equity_all_missing_yields_no_priority():
    # Degenerate: no classifiable tract → no medians → everything "unknown", nothing priority
    # (the thin-AOI limitation taken to its extreme; must not raise).
    gdf = gpd.GeoDataFrame(
        {
            "GEOID": ["r1", "r2"],
            "potential_per_household": [np.nan, np.nan],
            "energy_burden": [np.nan, np.nan],
            "equity_data_missing": [True, True],
        },
        geometry=[box(0.0, 0.0, 1.0, 1.0), box(1.0, 0.0, 2.0, 1.0)],
        crs=config.WORKING_CRS,
    )
    out = aggregate.classify_equity(gdf)
    assert (out["equity_class"] == "unknown").all()
    assert not out["is_priority"].any()
