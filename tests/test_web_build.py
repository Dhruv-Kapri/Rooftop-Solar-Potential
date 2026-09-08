"""Unit tests for the `build_web` ETL transforms (Phase 2, Part 2-3; ADR-0010).

Two independent pure transforms, tested on synthetic tract/roof frames — no network, no real
outputs:

  - `tracts_to_web` — EPSG:6347 -> EPSG:4326 reprojection + exact map-property schema (plan
    §5), zero-padded `GEOID` string preserved, one feature per input tract.
  - `roofs_to_web` — usable-only filter + exact minimal-payload schema (plan §5), same
    reprojection.

Expected values are hand-worked or independently re-derived (e.g. via a manual pyproj
round-trip), never read back from `web_build`'s own output. Fast: pure geopandas, no files, no
network.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from shapely.geometry import Point, box

from rooftop_solar import config, web_build

# A known DC lon/lat, reprojected into WORKING_CRS to build synthetic input geometry — the
# round-trip through `tracts_to_web`/`roofs_to_web` must land back on the same lon/lat, proving
# a real 6347->4326 transform ran (not just a CRS relabel).
_DC_LON, _DC_LAT = -77.03, 38.90
_DC_POINT_6347 = (
    gpd.GeoSeries([Point(_DC_LON, _DC_LAT)], crs="EPSG:4326").to_crs(config.WORKING_CRS).iloc[0]
)

_GEOID_A = "11001000100"
_GEOID_B = "11001000200"
_TRACT_A = box(0.0, 0.0, 100.0, 100.0)
_TRACT_B = box(100.0, 0.0, 200.0, 100.0)


def _tracts_gdf():
    return gpd.GeoDataFrame(
        {
            "GEOID": [_GEOID_A, _GEOID_B],
            "equity_class": ["high_potential_high_burden", "low_potential_low_burden"],
            "is_priority": [True, False],
            "potential_per_household": [10.0, 16.0],
            "energy_burden": [0.03, 0.05],
            # Extra columns present on the real dc_tracts.gpkg that must be dropped.
            "n_buildings": [12, 8],
            "median_income": [50000.0, 40000.0],
            "population": [200.0, 100.0],
        },
        geometry=[_TRACT_A, _TRACT_B],
        crs=config.WORKING_CRS,
    )


def test_tracts_to_web_exact_schema():
    out = web_build.tracts_to_web(_tracts_gdf())
    assert set(out.columns) == {
        "GEOID",
        "equity_class",
        "is_priority",
        "potential_per_household",
        "energy_burden",
        "geometry",
    }


def test_tracts_to_web_geoid_stays_zero_padded_string():
    out = web_build.tracts_to_web(_tracts_gdf())
    out = out.set_index("GEOID")
    assert _GEOID_A in out.index  # "11001000100" — leading "11" intact, not coerced to int
    assert out.reset_index()["GEOID"].map(type).eq(str).all()


def test_tracts_to_web_reprojects_to_4326():
    # A single tract centred exactly on the known DC point, built in WORKING_CRS.
    cx, cy = _DC_POINT_6347.x, _DC_POINT_6347.y
    tract = gpd.GeoDataFrame(
        {
            "GEOID": [_GEOID_A],
            "equity_class": ["high_potential_high_burden"],
            "is_priority": [True],
            "potential_per_household": [10.0],
            "energy_burden": [0.03],
        },
        geometry=[box(cx - 1.0, cy - 1.0, cx + 1.0, cy + 1.0)],
        crs=config.WORKING_CRS,
    )

    out = web_build.tracts_to_web(tract)

    assert out.crs == "EPSG:4326"
    centroid = out.geometry.iloc[0].centroid
    np.testing.assert_allclose([centroid.x, centroid.y], [_DC_LON, _DC_LAT], atol=1e-6)


def test_tracts_to_web_preserves_feature_count():
    out = web_build.tracts_to_web(_tracts_gdf())
    assert len(out) == 2


def test_write_tract_geojson_writes_valid_4326_with_schema(tmp_path):
    # Offline (no tippecanoe): the tract layer is written as GeoJSON and must round-trip as a
    # valid EPSG:4326 layer carrying exactly the map schema, one feature per input tract.
    out_path = tmp_path / "tracts.geojson"
    returned = web_build.write_tract_geojson(_tracts_gdf(), out_path)

    assert returned == out_path
    assert out_path.exists()

    back = gpd.read_file(out_path)
    assert back.crs == "EPSG:4326"  # GeoJSON must be lon/lat (RFC 7946) — a real reprojection
    assert len(back) == 2  # one feature per input tract
    assert set(back.columns) == {
        "GEOID",
        "equity_class",
        "is_priority",
        "potential_per_household",
        "energy_burden",
        "geometry",
    }
    # GEOID round-trips through GeoJSON as the zero-padded string, never coerced to int.
    assert set(back["GEOID"]) == {_GEOID_A, _GEOID_B}
    assert back["GEOID"].map(type).eq(str).all()


# --------------------------------------------------------------------------- #
# roofs_to_web — usable-only filter + minimal schema + reprojection           #
# --------------------------------------------------------------------------- #

_ROOF_A = box(10.0, 10.0, 20.0, 20.0)
_ROOF_B = box(30.0, 10.0, 40.0, 20.0)
_ROOF_C = box(50.0, 10.0, 60.0, 20.0)


def _roofs_gdf():
    return gpd.GeoDataFrame(
        {
            "usable": [True, False, True],
            "suitability": [80.0, 0.0, 60.0],
            "capacity_kw": [4.0, 0.0, 3.0],
            "annual_energy_kwh": [1200.0, 0.0, 900.0],
            # Extra columns present on the real dc_roofs.parquet that must be dropped.
            "tilt_deg": [20.0, 5.0, 25.0],
            "poa_real_kwh_m2": [1400.0, 900.0, 1300.0],
        },
        geometry=[_ROOF_A, _ROOF_B, _ROOF_C],
        crs=config.WORKING_CRS,
    )


def test_roofs_to_web_keeps_only_usable_roofs():
    out = web_build.roofs_to_web(_roofs_gdf())
    # Two usable roofs (suitability 80, 60); the unusable one (suitability 0) is dropped.
    assert len(out) == 2
    assert set(out["suitability"]) == {80.0, 60.0}


def test_roofs_to_web_exact_schema():
    out = web_build.roofs_to_web(_roofs_gdf())
    # `usable` itself is dropped along with every other extra input column.
    assert set(out.columns) == {"suitability", "capacity_kw", "annual_energy_kwh", "geometry"}


def test_roofs_to_web_reprojects_to_4326():
    cx, cy = _DC_POINT_6347.x, _DC_POINT_6347.y
    roof = gpd.GeoDataFrame(
        {
            "usable": [True],
            "suitability": [80.0],
            "capacity_kw": [4.0],
            "annual_energy_kwh": [1200.0],
        },
        geometry=[box(cx - 1.0, cy - 1.0, cx + 1.0, cy + 1.0)],
        crs=config.WORKING_CRS,
    )

    out = web_build.roofs_to_web(roof)

    assert out.crs == "EPSG:4326"
    centroid = out.geometry.iloc[0].centroid
    np.testing.assert_allclose([centroid.x, centroid.y], [_DC_LON, _DC_LAT], atol=1e-6)
