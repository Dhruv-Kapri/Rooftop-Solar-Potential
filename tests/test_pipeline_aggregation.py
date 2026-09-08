"""Offline wiring test for `pipeline.run_aggregation` (Phase 2, Part 2-1).

Exercises the whole composed aggregation path — `aggregate_to_tracts` → `attach_equity` →
`classify_equity` → GeoPackage + choropleth export — on synthetic, INJECTED census frames, so
it runs with no network and no dependency on the DOE LEAD burden-methodology question (§11).
The real-data equivalent (fetching TIGER/ACS/LEAD for Glover Park) is the `integration`-marked
smoke; this proves the plumbing and the conservation invariant (§7.1) end-to-end offline.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

from rooftop_solar import config, pipeline

_TRACT_A = box(0.0, 0.0, 100.0, 100.0)
_TRACT_B = box(100.0, 0.0, 200.0, 100.0)


def _buildings():
    rows = [
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


def _tracts():
    return gpd.GeoDataFrame(
        {"GEOID": ["11001000100", "11001000200"]},
        geometry=[_TRACT_A, _TRACT_B],
        crs=config.WORKING_CRS,
    )


def _acs():
    return pd.DataFrame(
        {
            "GEOID": ["11001000100", "11001000200"],
            "population": [200.0, 100.0],
            "households": [100.0, 50.0],
            "median_income": [50000.0, 40000.0],
        }
    )


def _energy_burden():
    return pd.DataFrame(
        {"GEOID": ["11001000100", "11001000200"], "energy_burden": [0.03, 0.05]}
    )


def test_run_aggregation_composes_conserves_and_writes(tmp_path):
    buildings = _buildings()
    result = pipeline.run_aggregation(
        buildings=buildings,
        tracts_gdf=_tracts(),
        acs=_acs(),
        energy_burden=_energy_burden(),
        output_dir=tmp_path,
        write_outputs=True,
    )

    # The full equity contract is present on the output tracts.
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.crs == config.WORKING_CRS
    for col in ("GEOID", "annual_energy_kwh", "potential_per_household", "energy_burden",
                "equity_class", "is_priority"):
        assert col in result.columns

    # Conservation end-to-end through the wired path (§7.1): every roof's energy survives the
    # roll-up (nothing dropped or double-counted).
    np.testing.assert_allclose(
        result["annual_energy_kwh"].sum(), buildings["annual_energy_kwh"].sum()
    )

    # Deliverables written: the tract GeoPackage (layer 'tracts') + both choropleths + scatter.
    gpkg = tmp_path / pipeline.TRACT_GEOPACKAGE_NAME
    assert gpkg.exists()
    import fiona
    assert "tracts" in fiona.listlayers(gpkg)
    assert (tmp_path / pipeline.POTENTIAL_CHOROPLETH_NAME).exists()
    assert (tmp_path / pipeline.EQUITY_CHOROPLETH_NAME).exists()
    assert (tmp_path / pipeline.EQUITY_SCATTER_NAME).exists()


def test_run_aggregation_names_outputs_by_prefix(tmp_path):
    # A city run aggregates ALL of DC, so its deliverables must NOT carry the Glover-Park name
    # (Part 2-2). The area name/file prefix are parameters; the city path passes DC values.
    pipeline.run_aggregation(
        buildings=_buildings(),
        tracts_gdf=_tracts(),
        acs=_acs(),
        energy_burden=_energy_burden(),
        output_dir=tmp_path,
        area_name="Washington, DC",
        file_prefix="dc",
    )

    for name in (
        "dc_tracts.gpkg",
        "dc_tract_potential.png",
        "dc_tract_equity.png",
        "dc_tract_scatter.png",
    ):
        assert (tmp_path / name).exists()
    assert not list(tmp_path.glob("glover_park_*"))  # no Glover-Park name leaks through


def test_run_aggregation_write_outputs_false_writes_nothing(tmp_path):
    result = pipeline.run_aggregation(
        buildings=_buildings(),
        tracts_gdf=_tracts(),
        acs=_acs(),
        energy_burden=_energy_burden(),
        output_dir=tmp_path,
        write_outputs=False,
    )
    assert len(result) == 2
    assert not any(tmp_path.iterdir())  # nothing written when write_outputs is False
