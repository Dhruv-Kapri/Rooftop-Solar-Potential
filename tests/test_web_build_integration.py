"""Build smoke for the web ETL (needs `tippecanoe`) — Phase 2, Part 2-3; plan §6, ADR-0010.

The offline unit tier (`test_web_build.py`) covers the pure transforms + the tract GeoJSON. This
tier is the honest *build* smoke: it actually runs `tippecanoe` on synthetic roofs and asserts a
**valid PMTiles** with the expected **layer** + **minzoom**, and that `build_web` wires the whole
ETL together (tract GeoJSON + roof PMTiles + the scatter PNG copy). No network — synthetic frames
only. Deselected by default; run with `pytest -m integration`. `tippecanoe` on PATH is required;
the `pmtiles` reader validates the output.
"""

from __future__ import annotations

import shutil

import geopandas as gpd
import pytest
from pyproj import Transformer
from shapely.geometry import box

from rooftop_solar import config, web_build

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("tippecanoe") is None, reason="tippecanoe not on PATH"),
]

# Synthetic roofs anchored at a known DC lon/lat, built in the metric working CRS (what the
# pipeline emits) so `roofs_to_web` reprojects them into valid lon/lat tippecanoe can tile.
_TO_WORKING = Transformer.from_crs("EPSG:4326", config.WORKING_CRS, always_xy=True).transform
_CX, _CY = _TO_WORKING(-77.03, 38.90)


def _roofs_gdf() -> gpd.GeoDataFrame:
    """Three roofs (two usable, one not) — small 2 m boxes near the DC anchor point."""
    return gpd.GeoDataFrame(
        {
            "usable": [True, False, True],
            "suitability": [80.0, 0.0, 60.0],
            "capacity_kw": [4.0, 0.0, 3.0],
            "annual_energy_kwh": [1200.0, 0.0, 900.0],
        },
        geometry=[
            box(_CX, _CY, _CX + 2.0, _CY + 2.0),
            box(_CX + 5.0, _CY, _CX + 7.0, _CY + 2.0),
            box(_CX + 10.0, _CY, _CX + 12.0, _CY + 2.0),
        ],
        crs=config.WORKING_CRS,
    )


def _pmtiles_header_and_layers(path):
    """Read a PMTiles file's header + vector-layer ids via the `pmtiles` reader."""
    from pmtiles.reader import MmapSource, Reader

    with open(path, "rb") as f:
        reader = Reader(MmapSource(f))
        return reader.header(), [layer["id"] for layer in reader.metadata()["vector_layers"]]


def test_write_roof_pmtiles_emits_valid_pmtiles_with_layer_and_minzoom(tmp_path):
    out = web_build.write_roof_pmtiles(_roofs_gdf(), tmp_path / "roofs.pmtiles")

    assert out == tmp_path / "roofs.pmtiles"
    assert out.exists() and out.stat().st_size > 0

    header, layer_ids = _pmtiles_header_and_layers(out)
    assert header["min_zoom"] == 13  # roofs are a z>=13 drill-down (plan §5), nothing below
    assert "roofs" in layer_ids  # the single expected vector layer


def _tracts_gdf() -> gpd.GeoDataFrame:
    """Two tracts near the DC anchor, in the working CRS, with the full aggregation schema."""
    return gpd.GeoDataFrame(
        {
            "GEOID": ["11001000100", "11001000200"],
            "equity_class": ["high_potential_high_burden", "low_potential_low_burden"],
            "is_priority": [True, False],
            "potential_per_household": [10.0, 16.0],
            "energy_burden": [0.03, 0.05],
            "n_buildings": [12, 8],  # extra column that must not reach the web layer
        },
        geometry=[
            box(_CX, _CY, _CX + 100.0, _CY + 100.0),
            box(_CX + 100.0, _CY, _CX + 200.0, _CY + 100.0),
        ],
        crs=config.WORKING_CRS,
    )


def test_build_web_wires_geojson_pmtiles_and_scatter(tmp_path):
    # `build_web` reads the pipeline's `dc_*` deliverables from an input dir and emits the three
    # web assets to an output dir — the whole ETL wired together (plan §4).
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    _tracts_gdf().to_file(in_dir / "dc_tracts.gpkg", driver="GPKG", layer="tracts")
    _roofs_gdf().to_parquet(in_dir / "dc_roofs.parquet")
    (in_dir / "dc_tract_scatter.png").write_bytes(b"\x89PNG\r\n\x1a\n synthetic")

    out_dir = tmp_path / "assets"
    outputs = web_build.build_web(input_dir=in_dir, output_dir=out_dir)

    # All three assets land, keyed by role, at the plan's §4 filenames.
    assert (out_dir / "tracts.geojson").exists()
    assert (out_dir / "roofs.pmtiles").exists()
    assert (out_dir / "scatter.png").exists()
    assert outputs["scatter"].read_bytes().startswith(b"\x89PNG")  # copied, not corrupted

    # The tract GeoJSON is valid + web-schema'd; the roof PMTiles is a valid tiled `roofs` layer.
    back = gpd.read_file(outputs["tracts"])
    assert back.crs == "EPSG:4326" and len(back) == 2
    assert "n_buildings" not in back.columns  # trimmed to the map schema
    header, layer_ids = _pmtiles_header_and_layers(outputs["roofs"])
    assert header["min_zoom"] == 13 and "roofs" in layer_ids
