"""Unit tests for the pure PARSE half of the census data-access module (Phase 2, Part 2-1).

`tracts.py` is split into a network *fetch* half (integration-marked, tested for reachability
elsewhere) and a pure *parse/normalize* half tested here, offline, against tiny fixtures that
mimic the real source shapes:

  - `parse_tracts`   — a raw TIGER/Line tract GeoDataFrame → GEOID(str) + geometry, reprojected
                       to WORKING_CRS, extra columns dropped.
  - `parse_acs`      — the Census Data API's array-of-arrays JSON → GEOID(str), population,
                       households, median_income, with ACS negative sentinels → NaN.
  - `aggregate_lead_burden` — DOE LEAD stratum microdata → one overall burden per tract (GEOID str).

The single most likely silent bug here is **GEOID integer coercion** (§6, §11), which drops the
zero-pad — so every parser must yield a zero-padded string GEOID. Expected values are worked by
hand from the source formats, not read back from the code.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

from rooftop_solar import config, tracts

# --------------------------------------------------------------------------- #
# parse_tracts — TIGER geometry → GEOID(str) + geometry in WORKING_CRS         #
# --------------------------------------------------------------------------- #

def _raw_tiger_gdf():
    """A minimal stand-in for a TIGER/Line tract shapefile read: zero-padded string GEOID,
    the usual extra attribute columns, geometry in NAD83 lon/lat (EPSG:4269)."""
    return gpd.GeoDataFrame(
        {
            "STATEFP": ["11", "11"],
            "COUNTYFP": ["001", "001"],
            "TRACTCE": ["000100", "000200"],
            "GEOID": ["11001000100", "11001000200"],
            "NAMELSAD": ["Census Tract 1", "Census Tract 2"],
            "ALAND": [123456, 234567],
        },
        geometry=[box(-77.08, 38.91, -77.07, 38.92), box(-77.07, 38.91, -77.06, 38.92)],
        crs="EPSG:4269",
    )


def test_parse_tracts_keeps_geoid_geometry_and_reprojects():
    out = tracts.parse_tracts(_raw_tiger_gdf())

    assert isinstance(out, gpd.GeoDataFrame)
    assert out.crs == config.WORKING_CRS  # reprojected to the metric working CRS (§6, CRS trap)
    assert set(out.columns) == {"GEOID", "geometry"}  # attribute clutter dropped
    assert out["GEOID"].tolist() == ["11001000100", "11001000200"]
    assert out["GEOID"].map(type).eq(str).all()


def test_parse_tracts_zero_pads_a_coerced_geoid():
    # Defensive: if an upstream read hands GEOID back as an int (dropping the leading pad — the
    # classic AL "01…" hazard, §11), the parser must restore the 11-char zero-padded string.
    raw = gpd.GeoDataFrame(
        {"GEOID": [1001020100]},  # int64 — a hypothetical Alabama tract missing its leading 0
        geometry=[box(-86.8, 33.5, -86.7, 33.6)],
        crs="EPSG:4269",
    )
    out = tracts.parse_tracts(raw)
    assert out["GEOID"].tolist() == ["01001020100"]
    assert out["GEOID"].map(type).eq(str).all()


# --------------------------------------------------------------------------- #
# parse_acs — Census Data API array-of-arrays → tidy demographics             #
# --------------------------------------------------------------------------- #

def _raw_acs_rows():
    """The Census Data API returns a JSON array of arrays: a header row, then data rows whose
    trailing columns are the geography components (state, county, tract). GEOID must be built
    by concatenating those zero-padded components — never inferred as an int."""
    return [
        ["NAME", "B01003_001E", "B25003_001E", "B19013_001E", "state", "county", "tract"],
        ["Census Tract 1", "3000", "1200", "85000", "11", "001", "000100"],
        # -666666666 is an ACS annotation/sentinel for a suppressed estimate → must become NaN.
        ["Census Tract 2", "1500", "600", "-666666666", "11", "001", "000200"],
    ]


def test_parse_acs_builds_string_geoid_and_numeric_columns():
    out = tracts.parse_acs(_raw_acs_rows()).set_index("GEOID")

    assert list(out.index) == ["11001000100", "11001000200"]
    assert all(isinstance(g, str) for g in out.index)  # GEOID = state+county+tract, zero-pad intact
    np.testing.assert_allclose(out.loc["11001000100", "population"], 3000.0)
    np.testing.assert_allclose(out.loc["11001000100", "households"], 1200.0)
    np.testing.assert_allclose(out.loc["11001000100", "median_income"], 85000.0)


def test_parse_acs_maps_negative_sentinel_to_nan():
    out = tracts.parse_acs(_raw_acs_rows()).set_index("GEOID")
    # A spurious -666666666 income would wreck the equity read if kept — it must be NaN, not
    # a real value (and definitely not 0).
    assert np.isnan(out.loc["11001000200", "median_income"])


# --------------------------------------------------------------------------- #
# aggregate_lead_burden — LEAD stratum microdata → overall per-tract burden    #
# --------------------------------------------------------------------------- #

def _raw_lead_strata():
    """Row-per-stratum LEAD microdata (the real DC file's shape): a tract key `FIP` (int in the
    CSV — coercion hazard) plus household-weighted `*UNITS` income/energy-cost totals. The
    overall tract burden is Σ(energy cost×units) / Σ(income×units) over a tract's strata
    (ADR-0008)."""
    return pd.DataFrame(
        {
            "FIP": [11001000100, 11001000100, 11001000200, 11001000300],
            "HINCP*UNITS": [100000.0, 50000.0, 200000.0, 0.0],  # income × units
            "ELEP*UNITS": [2000.0, 1500.0, 3000.0, 0.0],  # electricity × units
            "GASP*UNITS": [1000.0, 500.0, 1000.0, 0.0],  # gas × units
            "FULP*UNITS": [0.0, 500.0, 0.0, 0.0],  # other fuel × units
        }
    )


def test_aggregate_lead_burden_overall_ratio_per_tract():
    out = tracts.aggregate_lead_burden(_raw_lead_strata()).set_index("GEOID")

    assert all(isinstance(g, str) for g in out.index)  # FIP → zero-padded string GEOID (§11)
    # Tract 100: Σcost=(2000+1000+0)+(1500+500+500)=5500, Σincome=150000 → 5500/150000.
    np.testing.assert_allclose(out.loc["11001000100", "energy_burden"], 5500.0 / 150000.0)
    # Tract 200: Σcost=4000, Σincome=200000 → 0.02.
    np.testing.assert_allclose(out.loc["11001000200", "energy_burden"], 0.02)


def test_aggregate_lead_burden_zero_income_is_nan_not_zero():
    out = tracts.aggregate_lead_burden(_raw_lead_strata()).set_index("GEOID")
    # A tract with zero total income → NaN burden (never 0/inf), so it flags as missing
    # downstream instead of poisoning the median (ADR-0007/0008).
    assert np.isnan(out.loc["11001000300", "energy_burden"])
