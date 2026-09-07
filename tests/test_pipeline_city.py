"""Unit tests for the city-runner's pure pieces (Part 2-2, ADR-0009).

The full `run_city` spine (footprints -> DSM -> r.sun -> ... per tile) is exercised by the
marked integration smoke (`test_pipeline_city_integration.py`; needs network + GRASS). Here we
only test the pure transform that `run_city` adds on top of the reused stages: recomputing the
ADR-0004 suitability percentile **city-wide** after the tiles are merged. Offline, synthetic.
"""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import box

from rooftop_solar import config, pipeline


def _merged_city_roofs() -> gpd.GeoDataFrame:
    """A merged city frame standing in for two tiles' worth of scored roofs.

    Four usable roofs with KNOWN energy densities + one unusable. The ``suitability`` column
    carries STALE per-tile values (every usable roof at 100, as if each had topped its own
    tile) so the test can prove the recompute overwrote them with the city-wide rank.
    """
    return gpd.GeoDataFrame(
        {
            "energy_density_kwh_m2": [10.0, 20.0, 30.0, 40.0, 5.0],
            "usable_area_m2": [50.0, 50.0, 50.0, 50.0, 0.0],  # last roof unusable
            "suitability": [100.0, 100.0, 100.0, 100.0, 0.0],  # stale within-tile values
        },
        geometry=[box(float(i), 0.0, float(i) + 1.0, 1.0) for i in range(5)],
        crs=config.WORKING_CRS,
    )


def test_recompute_city_suitability_ranks_over_the_whole_city_not_per_tile():
    city = _merged_city_roofs()

    out = pipeline.recompute_city_suitability(city)

    # ADR-0004 percentile rank (rank/n_usable * 100) over ALL usable roofs city-wide:
    #   density 10 -> 25, 20 -> 50, 30 -> 75, 40 -> 100 (the city max); unusable -> exactly 0.
    # The density-20 roof was 100 in its stale per-tile value; city-wide it is 50 — the whole
    # point of recomputing over the merged set rather than trusting per-tile percentiles.
    assert out["suitability"].tolist() == [25.0, 50.0, 75.0, 100.0, 0.0]


def test_recompute_city_suitability_leaves_other_columns_untouched():
    city = _merged_city_roofs()

    out = pipeline.recompute_city_suitability(city)

    assert out["energy_density_kwh_m2"].tolist() == city["energy_density_kwh_m2"].tolist()
    assert out.crs == config.WORKING_CRS
    assert len(out) == len(city)


def test_recompute_city_suitability_handles_empty_frame():
    empty = _merged_city_roofs().iloc[0:0]

    out = pipeline.recompute_city_suitability(empty)

    assert len(out) == 0
