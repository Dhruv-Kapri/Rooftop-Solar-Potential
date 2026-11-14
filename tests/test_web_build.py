"""Unit tests for the `build_web` ETL transforms (Phase 2, Part 2-3; ADR-0010).

Two independent pure transforms, tested on synthetic tract/roof frames — no network, no real
outputs:

  - `tracts_to_web` — EPSG:6347 -> EPSG:4326 reprojection + exact map-property schema (plan
    §5), zero-padded `GEOID` string preserved, one feature per input tract.
  - `roofs_to_web` — all roofs kept (#40) + exact popup schema, display-precision rounding,
    same reprojection.

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
            # Extra columns present on the real tracts.gpkg that must be dropped.
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
# roofs_to_web — all roofs + popup schema + rounding + reprojection           #
# --------------------------------------------------------------------------- #

_ROOF_WEB_COLUMNS = {
    "usable",
    "unsuitable_reason",
    "suitability",
    "capacity_kw",
    "annual_energy_kwh",
    "tilt_deg",
    "aspect_deg",
    "footprint_area_m2",
    "usable_area_m2",
    "roof_class",
    "poa_real_kwh_m2",
    "n_planes",
    "low_confidence",
    "geometry",
}


def _roofs_gdf() -> gpd.GeoDataFrame:
    """A suitable roof, a too-steep roof, and a no-plane-fit roof (NaN geometry-derived fields)."""
    return gpd.GeoDataFrame(
        {
            "usable": [True, False, False],
            "unsuitable_reason": [None, "too_steep", "no_plane_fit"],
            "suitability": [80.123, 0.0, 0.0],
            "capacity_kw": [4.456, 0.0, 0.0],
            "annual_energy_kwh": [1200.6, 0.0, 0.0],
            "tilt_deg": [20.26, 55.0, np.nan],
            "aspect_deg": [183.7, 10.0, np.nan],
            "footprint_area_m2": [100.4, 80.0, 50.0],
            "usable_area_m2": [60.5, 0.0, 0.0],
            "roof_class": ["pitched_sun_facing", "pitched_north_facing", "flat"],
            "poa_real_kwh_m2": [1400.49, 900.0, np.nan],
            "n_planes": [2, 1, 0],
            "low_confidence": [False, True, True],
            # Extra columns present on the real roofs.parquet that must be dropped.
            "height_m": [8.0, 6.0, 4.0],
        },
        geometry=[
            box(10.0, 10.0, 20.0, 20.0),
            box(30.0, 10.0, 40.0, 20.0),
            box(50.0, 10.0, 60.0, 20.0),
        ],
        crs=config.WORKING_CRS,
    )


def test_roofs_to_web_keeps_unsuitable_roofs_with_exact_schema() -> None:
    out = web_build.roofs_to_web(_roofs_gdf())
    assert len(out) == 3  # nothing filtered: unsuitable roofs are drawn grey (#40)
    assert out["usable"].tolist() == [True, False, False]
    assert out["unsuitable_reason"].isna().tolist() == [True, False, False]
    assert out["unsuitable_reason"].iloc[1:].tolist() == ["too_steep", "no_plane_fit"]
    assert set(out.columns) == _ROOF_WEB_COLUMNS  # `height_m` dropped


def test_roofs_to_web_rounds_to_display_precision_and_keeps_nan() -> None:
    out = web_build.roofs_to_web(_roofs_gdf())
    row = out.iloc[0]
    assert (row["suitability"], row["capacity_kw"], row["tilt_deg"]) == (80.1, 4.5, 20.3)
    assert row["aspect_deg"] == 184.0
    assert row["annual_energy_kwh"] == 1201.0
    assert row["poa_real_kwh_m2"] == 1400.0
    # NaN must survive as NaN (tippecanoe then omits it -> null), not become a string/zero.
    assert np.isnan(out.iloc[2]["tilt_deg"]) and np.isnan(out.iloc[2]["poa_real_kwh_m2"])
    # n_planes is float: tippecanoe turns FlatGeobuf integer columns into strings.
    assert out["n_planes"].dtype == "float64"


def test_roofs_to_web_reprojects_to_4326() -> None:
    cx, cy = _DC_POINT_6347.x, _DC_POINT_6347.y
    roofs = _roofs_gdf().iloc[:1]
    roofs = roofs.set_geometry(
        [box(cx - 1.0, cy - 1.0, cx + 1.0, cy + 1.0)], crs=config.WORKING_CRS
    )

    out = web_build.roofs_to_web(roofs)

    assert out.crs == "EPSG:4326"
    centroid = out.geometry.iloc[0].centroid
    np.testing.assert_allclose([centroid.x, centroid.y], [_DC_LON, _DC_LAT], atol=1e-6)
