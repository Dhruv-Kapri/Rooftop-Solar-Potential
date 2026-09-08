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


def test_plane_yields_composer_adds_per_plane_yield_columns():
    # Same worked example as pv_yield's hand calc, applied per plane row.
    planes = gpd.GeoDataFrame(
        {
            "building_id": [0, 0],
            "plane_id": [0, 1],
            "poa_clear_sky_kwh_m2": [1000.0, 1000.0],
            "usable_area_m2": [100.0, 0.0],
        },
        geometry=[box(0.0, 0.0, 10.0, 10.0), box(50.0, 0.0, 60.0, 10.0)],
        crs=config.WORKING_CRS,
    )

    out = yield_pv.plane_yields(planes)

    row0 = out.iloc[0]
    assert row0["poa_real_kwh_m2"] == 750.0
    assert row0["capacity_kw"] == 20.0
    assert row0["annual_energy_kwh"] == 12000.0
    assert row0["annual_co2_kg"] == 4200.0
    # unusable plane (0 area) -> no yield.
    row1 = out.iloc[1]
    assert row1["capacity_kw"] == 0.0
    assert row1["annual_energy_kwh"] == 0.0
    assert row1["annual_co2_kg"] == 0.0
    assert len(out) == len(planes)


def test_plane_yields_zero_area_nan_poa_is_zero_not_nan():
    # An unusable plane can carry a NaN POA (e.g. no valid raster pixel under it). Without
    # the guard, 0 (area) * NaN (poa_real) = NaN -- the guard must force a clean 0 instead.
    planes = gpd.GeoDataFrame(
        {
            "building_id": [0],
            "plane_id": [0],
            "poa_clear_sky_kwh_m2": [np.nan],
            "usable_area_m2": [0.0],
        },
        geometry=[box(0.0, 0.0, 10.0, 10.0)],
        crs=config.WORKING_CRS,
    )

    out = yield_pv.plane_yields(planes)

    row = out.iloc[0]
    assert row["capacity_kw"] == 0.0
    assert not np.isnan(row["capacity_kw"])
    assert row["annual_energy_kwh"] == 0.0
    assert not np.isnan(row["annual_energy_kwh"])
    assert row["annual_co2_kg"] == 0.0
    assert not np.isnan(row["annual_co2_kg"])


def _plane_yields_fixture():
    """Two buildings: building 0 has two usable planes (pv_yield hand-worked below);
    building 1 is a single all-unusable plane. Mirrors the shape `plane_yields` emits.

    Building 0:
      plane0: usable_area=100, poa_clear=1000 -> pv_yield(100,1000)=(750, 20, 12000, 4200).
      plane1: usable_area=50,  poa_clear=800  -> pv_yield(50, 800) =(600, 10, 4800,  1680).
      -> collapse: capacity=30, annual_energy=16800, co2=5880, usable_area=150, dominant=plane0
         (100 > 50 usable area) so tilt/aspect/roof_class/poa_real from plane0.
    Building 1: one unusable plane -> everything sums to 0, usable=False.
    """
    footprint0 = box(0.0, 0.0, 10.0, 10.0)  # 100 m2 footprint (independent of usable area)
    footprint1 = box(50.0, 0.0, 60.0, 10.0)
    return gpd.GeoDataFrame(
        {
            "building_id": [0, 0, 1],
            "plane_id": [0, 1, 0],
            "n_planes": [2, 2, 1],
            "low_confidence": [False, False, True],
            "tilt_deg": [20.0, 35.0, np.nan],
            "aspect_deg": [180.0, 190.0, np.nan],
            "roof_class": ["pitched_sun_facing", "pitched_sun_facing", "pitched_north_facing"],
            "usable": [True, True, False],
            "usable_area_m2": [100.0, 50.0, 0.0],
            "poa_real_kwh_m2": [750.0, 600.0, np.nan],
            "capacity_kw": [20.0, 10.0, 0.0],
            "annual_energy_kwh": [12000.0, 4800.0, 0.0],
            "annual_co2_kg": [4200.0, 1680.0, 0.0],
        },
        geometry=[footprint0, footprint0, footprint1],
        crs=config.WORKING_CRS,
    )


def test_collapse_to_buildings_sums_extensive_and_picks_dominant_plane():
    out = yield_pv.collapse_to_buildings(_plane_yields_fixture()).set_index("building_id")

    b0 = out.loc[0]
    assert b0["capacity_kw"] == 30.0
    assert b0["annual_energy_kwh"] == 16800.0
    assert b0["annual_co2_kg"] == 5880.0
    assert b0["usable_area_m2"] == 150.0
    assert b0["usable"]
    assert b0["n_planes"] == 2
    # dominant = plane0 (larger usable area: 100 > 50).
    assert b0["tilt_deg"] == 20.0
    assert b0["aspect_deg"] == 180.0
    assert b0["roof_class"] == "pitched_sun_facing"
    assert b0["poa_real_kwh_m2"] == 750.0
    assert b0["suitability"] == 100.0  # lone usable building among the two

    b1 = out.loc[1]
    assert b1["capacity_kw"] == 0.0
    assert b1["annual_energy_kwh"] == 0.0
    assert b1["annual_co2_kg"] == 0.0
    assert b1["usable_area_m2"] == 0.0
    assert not b1["usable"]
    assert b1["suitability"] == 0.0


def test_collapse_to_buildings_output_columns_and_crs():
    out = yield_pv.collapse_to_buildings(_plane_yields_fixture())

    assert set(out.columns) == {
        "geometry",
        "building_id",
        "n_planes",
        "low_confidence",
        "tilt_deg",
        "aspect_deg",
        "roof_class",
        "footprint_area_m2",
        "usable",
        "usable_area_m2",
        "poa_real_kwh_m2",
        "capacity_kw",
        "annual_energy_kwh",
        "annual_co2_kg",
        "energy_density_kwh_m2",
        "suitability",
    }
    assert out.crs == config.WORKING_CRS
    assert len(out) == 2  # one row per building, not per plane


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
