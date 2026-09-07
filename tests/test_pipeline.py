"""Offline tests for the Stage-1 deliverable writers (GeoPackage + choropleth PNG).

The full pipeline (footprints -> DSM -> r.sun -> ...) is exercised by the marked
integration smoke (needs network + GRASS). Here we only check the export helpers on a
synthetic result GeoDataFrame — fast, no network, no GRASS — since a GeoPackage has to
carry the tricky dtypes the pipeline produces (bool flags, NaN geometry columns, string
labels) without silently mangling them.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from shapely.geometry import box

from rooftop_solar import config, pipeline


def _synthetic_result() -> gpd.GeoDataFrame:
    """A minimal stand-in for estimate_yield's output: two roofs, mixed dtypes, one NaN."""
    return gpd.GeoDataFrame(
        {
            "tilt_deg": [20.0, np.nan],  # low-confidence roof -> NaN
            "low_confidence": [False, True],  # bool column
            "roof_class": ["pitched_sun_facing", "flat"],  # object/string column
            "n_px": [120, 3],  # int column
            "usable": [True, False],
            "suitability": [100.0, 0.0],
        },
        geometry=[box(0.0, 0.0, 10.0, 10.0), box(50.0, 0.0, 60.0, 10.0)],
        crs=config.WORKING_CRS,
    )


def test_write_geopackage_roundtrips(tmp_path):
    gdf = _synthetic_result()

    out = pipeline.write_geopackage(gdf, tmp_path / "roofs.gpkg")

    assert out.exists()
    reloaded = gpd.read_file(out, layer="roofs")
    assert len(reloaded) == 2
    assert {"tilt_deg", "roof_class", "suitability", "geometry"}.issubset(reloaded.columns)
    assert reloaded["suitability"].tolist() == [100.0, 0.0]
    assert reloaded["roof_class"].tolist() == ["pitched_sun_facing", "flat"]
    assert reloaded.crs == gdf.crs


def test_render_choropleth_writes_png(tmp_path):
    gdf = _synthetic_result()

    out = pipeline.render_choropleth(gdf, tmp_path / "suitability.png")

    assert out.exists()
    assert out.stat().st_size > 0
