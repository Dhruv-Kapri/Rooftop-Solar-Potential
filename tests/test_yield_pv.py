"""Unit tests for the PV-yield pure core (ADR-0003 constants, ADR-0004 score).

Seams:
  - `pv_yield` — usable area + shaded clear-sky insolation -> real-sky POA, capacity,
    annual energy, annual CO2. Expected numbers are hand-evaluated from the ADR-0003
    constants for chosen inputs (a worked example, not the code's own formula).
  - `suitability_score` — within-AOI percentile rank of energy density, unusable pinned to 0
    (ADR-0004). Expected ranks are worked by hand for a small set.

Fast: pure numpy/scipy, no DSM, no network, no GRASS.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from shapely.geometry import box

from rooftop_solar import config, yield_pv


def test_pv_yield_hand_worked():
    # usable_area = 100 m2, clear-sky POA = 1000 kWh/m2/yr.
    #   poa_real  = 1000 * 0.75 (real-sky)               = 750
    #   capacity  = 100 * 0.20 kW/m2                      = 20
    #   energy    = 100 * 750 * 0.20 (eff) * 0.80 (PR)    = 12000
    #   co2       = 12000 * 0.35 kg/kWh                   = 4200
    poa_real, capacity_kw, energy_kwh, co2_kg = yield_pv.pv_yield(100.0, 1000.0)

    assert poa_real == 750.0
    assert capacity_kw == 20.0
    assert energy_kwh == 12000.0
    assert co2_kg == 4200.0


def test_pv_yield_zero_usable_area_yields_nothing():
    # An unusable roof (0 m2) produces no capacity, energy, or CO2.
    _poa_real, capacity_kw, energy_kwh, co2_kg = yield_pv.pv_yield(0.0, 1000.0)

    assert capacity_kw == 0.0
    assert energy_kwh == 0.0
    assert co2_kg == 0.0


def test_suitability_score_percentile_rank_and_pinning():
    # 4 usable roofs with distinct densities -> ranks 1..4 -> 100*rank/4 = 25,50,75,100.
    # The 5th roof is unusable (0 m2): pinned to exactly 0 whatever its density.
    density = np.array([10.0, 20.0, 30.0, 40.0, 999.0])
    usable_area_m2 = np.array([1.0, 1.0, 1.0, 1.0, 0.0])

    scores = yield_pv.suitability_score(density, usable_area_m2)

    np.testing.assert_allclose(scores, [25.0, 50.0, 75.0, 100.0, 0.0])


def test_suitability_score_averages_ties():
    # Tied densities share the averaged rank: ranks [1.5, 1.5, 3] over n=3 -> [50, 50, 100].
    density = np.array([10.0, 10.0, 20.0])
    usable_area_m2 = np.array([1.0, 1.0, 1.0])

    scores = yield_pv.suitability_score(density, usable_area_m2)

    np.testing.assert_allclose(scores, [50.0, 50.0, 100.0])


def test_suitability_score_single_usable_roof_tops_out():
    scores = yield_pv.suitability_score(
        np.array([42.0, 5.0]), np.array([1.0, 0.0])
    )
    assert scores[0] == 100.0  # the lone usable roof
    assert scores[1] == 0.0  # unusable, pinned


def test_estimate_yield_composer_adds_yield_columns():
    # Two roofs: a qualifying 100 m2 roof (fully usable) and an unusable one (0 m2 usable).
    roofs = gpd.GeoDataFrame(
        {
            "poa_clear_sky_kwh_m2": [1000.0, 1000.0],
            "footprint_area_m2": [100.0, 100.0],
            "usable_area_m2": [100.0, 0.0],
        },
        geometry=[box(0.0, 0.0, 10.0, 10.0), box(50.0, 0.0, 60.0, 10.0)],
        crs=config.WORKING_CRS,
    )

    out = yield_pv.estimate_yield(roofs)

    row0 = out.iloc[0]
    assert row0["poa_real_kwh_m2"] == 750.0
    assert row0["capacity_kw"] == 20.0
    assert row0["annual_energy_kwh"] == 12000.0
    assert row0["annual_co2_kg"] == 4200.0
    # energy density is per footprint m2: 12000 / 100 = 120; unusable roof -> 0.
    np.testing.assert_allclose(out["energy_density_kwh_m2"].to_numpy(), [120.0, 0.0])
    # lone usable roof tops the score; unusable pinned to 0.
    np.testing.assert_allclose(out["suitability"].to_numpy(), [100.0, 0.0])
    assert len(out) == len(roofs)
