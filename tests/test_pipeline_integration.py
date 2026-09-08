"""End-to-end integration smoke for the Stage-1 pipeline (needs network + GRASS).

Runs the WHOLE spine — real footprints + 3DEP DSM fetch, a real GRASS r.sun pass, multi-plane
roof geometry, per-plane POA, obstruction-aware usable area, per-building yield — on a tiny
fixed DC sub-AOI, for a single day (fast, not calibrated). This is the Part 2-4 default
(`method="multiplane"`, ADR-0011/0012/0013), so the result is one row per building with
`n_planes`/`low_confidence` (facets collapsed) — the per-plane `inlier_ratio`/`n_px`/
`poa_clear_sky_kwh_m2` columns live only inside the collapse. It asserts the plumbing and
structural invariants (columns, CRS, score range, usable->positive-energy), NOT specific
magnitudes — the full-scale, 12-day, calibrated run is the documented manual Glover Park
benchmark, not a test. Deselected by default; run with `pytest -m integration`.
"""

from __future__ import annotations

import shutil

import pytest
from shapely.geometry import box

from rooftop_solar import config, pipeline

# A tiny DC box with known 3DEP + building coverage (the notebooks' exploration AOI), and a
# slightly larger buffered frame so the core-selection actually filters the buffer ring.
_CORE = box(-77.0770, 38.9182, -77.0730, 38.9218)
_BUFFERED = box(-77.0782, 38.9170, -77.0718, 38.9230)

# The multiplane collapsed per-building schema (yield_pv.collapse_to_buildings) — the per-plane
# uncertainty columns (inlier_ratio/n_px/poa_clear_sky_kwh_m2) are consumed inside the collapse.
_EXPECTED_COLUMNS = {
    "geometry", "building_id", "n_planes", "low_confidence", "tilt_deg", "aspect_deg",
    "roof_class", "footprint_area_m2", "usable", "usable_area_m2", "poa_real_kwh_m2",
    "capacity_kw", "annual_energy_kwh", "annual_co2_kg", "energy_density_kwh_m2", "suitability",
}


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("grass") is None, reason="GRASS not on PATH")
def test_pipeline_runs_end_to_end(tmp_path):
    result = pipeline.run_stage1(
        core=_CORE,
        buffered=_BUFFERED,
        day_range=[172],  # single summer day — fast smoke, not a calibrated annual sum
        output_dir=tmp_path,
    )

    # Produced some roofs, on the working CRS, with the full column contract.
    assert len(result) > 0
    assert result.crs == config.WORKING_CRS
    assert _EXPECTED_COLUMNS.issubset(result.columns)

    # Multiplane: one row per building, each with >= 1 fitted facet.
    assert (result["n_planes"] >= 1).all()
    assert result["building_id"].is_unique  # collapse produced one row per building

    # Suitability is a 0-100 within-AOI percentile.
    assert result["suitability"].min() >= 0.0
    assert result["suitability"].max() <= 100.0

    # Usable roofs carry positive capacity/energy; unusable roofs are zeroed (ADR-0003/0004).
    usable = result[result["usable"]]
    unusable = result[~result["usable"]]
    if len(usable):
        assert (usable["capacity_kw"] > 0).all()
        assert (usable["annual_energy_kwh"] > 0).all()
    if len(unusable):
        assert (unusable["usable_area_m2"] == 0).all()
        assert (unusable["suitability"] == 0).all()

    # The deliverables were written.
    assert (tmp_path / pipeline.GEOPACKAGE_NAME).exists()
    assert (tmp_path / pipeline.CHOROPLETH_NAME).exists()
