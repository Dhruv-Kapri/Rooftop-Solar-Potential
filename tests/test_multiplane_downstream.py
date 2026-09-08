"""Downstream regression: the multiplane collapse still feeds aggregate.py / web_build.py.

Part 2-4 replaces the ransac path's one-row-per-footprint `estimate_yield` output with
`yield_pv.collapse_to_buildings`'s one-row-per-building output for the multiplane path. Both
downstream consumers (`aggregate.aggregate_to_tracts`, `web_build.roofs_to_web`) were built and
tested against the ransac schema; this test proves the collapsed frame satisfies the same
contract — same column names, same semantics (extensive sums, usable-only filter) — so nothing
downstream needs to change for Part 2-4.

Fast: pure geopandas, no DSM, no GRASS, no network.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from shapely.geometry import box

from rooftop_solar import aggregate, config, web_build

# One 100x100 m tract holding all three synthetic buildings, in the working CRS (see
# test_aggregate.py's fixture style).
_GEOID = "11001000100"
_TRACT = box(0.0, 0.0, 100.0, 100.0)


def _collapsed_buildings_gdf() -> gpd.GeoDataFrame:
    """Three collapsed (post `collapse_to_buildings`) buildings, hand-chosen values.

    b0: usable, 10 m2 usable area, 2 kW, 100 kWh, 35 kg CO2, suitability 80.
    b1: usable,  5 m2 usable area, 1 kW,  50 kWh, 17.5 kg CO2, suitability 60.
    b2: unusable, 0 usable area/energy/capacity/co2, suitability 0 (dropped by roofs_to_web,
        still counted by aggregate_to_tracts).
    All centroids fall inside `_TRACT`.
    """
    rows = [
        (box(10.0, 10.0, 20.0, 20.0), True, 10.0, 2.0, 100.0, 35.0, 80.0),
        (box(30.0, 30.0, 40.0, 40.0), True, 5.0, 1.0, 50.0, 17.5, 60.0),
        (box(60.0, 60.0, 70.0, 70.0), False, 0.0, 0.0, 0.0, 0.0, 0.0),
    ]
    return gpd.GeoDataFrame(
        {
            "building_id": [0, 1, 2],
            "n_planes": [2, 1, 1],
            "low_confidence": [False, False, True],
            "tilt_deg": [20.0, 25.0, np.nan],
            "aspect_deg": [180.0, 190.0, np.nan],
            "roof_class": [
                "pitched_sun_facing",
                "pitched_sun_facing",
                "pitched_north_facing",
            ],
            "footprint_area_m2": [100.0, 100.0, 100.0],
            "usable": [r[1] for r in rows],
            "usable_area_m2": [r[2] for r in rows],
            "poa_real_kwh_m2": [750.0, 700.0, np.nan],
            "capacity_kw": [r[3] for r in rows],
            "annual_energy_kwh": [r[4] for r in rows],
            "annual_co2_kg": [r[5] for r in rows],
            "energy_density_kwh_m2": [1.0, 0.5, 0.0],
            "suitability": [r[6] for r in rows],
        },
        geometry=[r[0] for r in rows],
        crs=config.WORKING_CRS,
    )


def _tracts_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"GEOID": [_GEOID]}, geometry=[_TRACT], crs=config.WORKING_CRS)


def test_collapsed_rows_feed_aggregate_and_web():
    collapsed = _collapsed_buildings_gdf()

    # --- aggregate_to_tracts: the extensive sums and summary stats over the one tract ---
    out = aggregate.aggregate_to_tracts(collapsed, _tracts_gdf()).set_index("GEOID")
    tract = out.loc[_GEOID]

    assert tract["n_buildings"] == 3
    assert tract["n_usable"] == 2
    np.testing.assert_allclose(tract["usable_area_m2"], 15.0)  # 10 + 5 + 0
    np.testing.assert_allclose(tract["capacity_kw"], 3.0)  # 2 + 1 + 0
    np.testing.assert_allclose(tract["annual_energy_kwh"], 150.0)  # 100 + 50 + 0
    np.testing.assert_allclose(tract["annual_co2_kg"], 52.5)  # 35 + 17.5 + 0
    np.testing.assert_allclose(tract["median_suitability"], 60.0)  # median([80, 60, 0])
    np.testing.assert_allclose(tract["pct_usable"], 200.0 / 3.0)  # 2 of 3, as a percentage

    # --- roofs_to_web: usable-only, minimal schema, reprojected ---
    web = web_build.roofs_to_web(collapsed)

    assert len(web) == 2  # the unusable building is dropped
    assert set(web.columns) == {"suitability", "capacity_kw", "annual_energy_kwh", "geometry"}
    assert web.crs.to_string() == "EPSG:4326"
    np.testing.assert_allclose(sorted(web["capacity_kw"]), [1.0, 2.0])
    np.testing.assert_allclose(sorted(web["annual_energy_kwh"]), [50.0, 100.0])
