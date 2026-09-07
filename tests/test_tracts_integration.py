"""Network integration smoke for the census data-access loaders (Phase 2, Part 2-1).

Deselected by default (`integration` marker) — needs network, and ACS needs a CENSUS_API_KEY.
Mirrors the Stage-1 integration smoke: asserts the loaders are REACHABLE for DC and that the
GEOID-string / 2020-vintage / working-CRS invariants (§6) hold on real data — not specific
magnitudes. Run with `pytest -m integration`.

Includes the end-to-end `run_aggregation` real-data smoke (conservation end-to-end, §7.1); it
needs a `CENSUS_API_KEY` for the ACS leg, so it is skipped when the key is absent.
"""

from __future__ import annotations

import pytest

from rooftop_solar import config, pipeline, tracts


@pytest.mark.integration
def test_load_tracts_reachable_and_invariants_dc():
    gdf = tracts.load_tracts()  # DC 2020 tracts, no API key needed (public TIGER/Line)

    assert len(gdf) > 100  # DC has ~200 tracts — a non-empty, plausible count
    assert set(gdf.columns) == {"GEOID", "geometry"}
    assert gdf.crs == config.WORKING_CRS  # reprojected to the metric working CRS (§6 CRS trap)
    # GEOID: zero-padded 11-char strings, all in the DC state FIPS (§6, §11).
    assert all(isinstance(g, str) for g in gdf["GEOID"])
    assert gdf["GEOID"].map(len).eq(11).all()
    assert gdf["GEOID"].str.startswith(config.DC_STATE_FIPS).all()


@pytest.mark.integration
@pytest.mark.skipif(not config.CENSUS_API_KEY, reason="needs CENSUS_API_KEY in .env")
def test_load_acs_reachable_and_invariants_dc():
    df = tracts.load_acs()  # DC 2023 ACS 5-year

    assert len(df) > 100
    assert set(df.columns) == {"GEOID", "population", "households", "median_income"}
    assert all(isinstance(g, str) for g in df["GEOID"])
    assert df["GEOID"].map(len).eq(11).all()
    assert df["GEOID"].str.startswith(config.DC_STATE_FIPS).all()
    assert df["households"].notna().any()  # at least some tracts have a households estimate


@pytest.mark.integration
def test_load_energy_burden_reachable_and_invariants_dc():
    df = tracts.load_energy_burden()  # DC LEAD 2022 (public OpenEI bundle, no key)

    assert len(df) > 100
    assert set(df.columns) == {"GEOID", "energy_burden"}
    assert all(isinstance(g, str) for g in df["GEOID"])
    assert df["GEOID"].map(len).eq(11).all()
    assert df["GEOID"].str.startswith(config.DC_STATE_FIPS).all()
    # Burden is a fraction of income; an overall (all-households) aggregate sits well below 1
    # (ADR-0008 — real DC lands ~0.5–5.6%). This is a sanity band, not a magnitude gate.
    finite = df["energy_burden"].dropna()
    assert (finite > 0).all()
    assert finite.median() < 0.5


@pytest.mark.integration
@pytest.mark.skipif(not config.CENSUS_API_KEY, reason="needs CENSUS_API_KEY in .env")
def test_run_aggregation_end_to_end_glover_park(tmp_path):
    """Full real-data smoke: fetch DC tracts + ACS + LEAD, roll up the Stage-1 Glover Park
    roofs, assert conservation end-to-end (§7.1) and that the deliverables are written."""
    import geopandas as gpd

    roofs_path = config.OUTPUTS_DIR / pipeline.GEOPACKAGE_NAME
    if not roofs_path.exists():
        pytest.skip("Stage-1 output missing; run scripts/run_stage1.py first")

    result = pipeline.run_aggregation(output_dir=tmp_path)  # real tracts + ACS + LEAD
    roofs = gpd.read_file(roofs_path, layer="roofs")

    # Conservation: total scored energy is preserved through the roll-up (every roof that lands
    # in a DC tract contributes once; Glover Park is fully inside DC so nothing is lost).
    import numpy as np
    assert np.isclose(result["annual_energy_kwh"].sum(), roofs["annual_energy_kwh"].sum())

    assert "equity_class" in result.columns
    assert (tmp_path / pipeline.TRACT_GEOPACKAGE_NAME).exists()
    assert (tmp_path / pipeline.POTENTIAL_CHOROPLETH_NAME).exists()
    assert (tmp_path / pipeline.EQUITY_CHOROPLETH_NAME).exists()
    assert (tmp_path / pipeline.EQUITY_SCATTER_NAME).exists()
