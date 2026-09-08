"""ETL transforms for the deployed web map (Phase 2, Part 2-3; plan §5, ADR-0010).

Two pure `GeoDataFrame in -> GeoDataFrame out` transforms — the `build_web` ↔ map boundary
(stage-2-part3-plan.md §5): they take the pipeline's per-tract / per-roof results in
`config.WORKING_CRS` (EPSG:6347) and produce the minimal, web-map-ready property schema in
EPSG:4326 (lon/lat, what MapLibre/GeoJSON/PMTiles need). File writing (GeoJSON / FlatGeobuf)
and the `tippecanoe` PMTiles build are orchestration, not transforms, and live in the later
`scripts/build_web.py` CLI (plan §4/§7) — not built here.

  - `tracts_to_web` — per-tract map schema: `GEOID`, `equity_class`, `is_priority`,
    `potential_per_household`, `energy_burden`, `geometry`.
  - `roofs_to_web` — usable-only roofs, minimal per-roof map schema: `suitability`,
    `capacity_kw`, `annual_energy_kwh`, `geometry` (properties x 100k roofs bloats vector
    tiles, plan §4).

Same idiom as `aggregate.py`: no hidden globals, no file I/O, no network.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import geopandas as gpd

from rooftop_solar import config

_WEB_CRS = "EPSG:4326"

_TRACT_COLUMNS = [
    "GEOID",
    "equity_class",
    "is_priority",
    "potential_per_household",
    "energy_burden",
    "geometry",
]

_ROOF_COLUMNS = ["suitability", "capacity_kw", "annual_energy_kwh", "geometry"]


def tracts_to_web(tracts_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reproject per-tract results to EPSG:4326 and trim to the map property schema (plan §5).

    Keeps exactly `GEOID` (zero-padded string, never coerced to int — DC GEOIDs start "11…"),
    `equity_class`, `is_priority`, `potential_per_household`, `energy_burden`, and `geometry`;
    all other input columns are dropped. One output feature per input tract.
    """
    return tracts_gdf[_TRACT_COLUMNS].to_crs(_WEB_CRS)


def roofs_to_web(roofs_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Filter to usable roofs, reproject to EPSG:4326, and trim to the minimal map schema.

    Keeps only rows with `usable == True`, then keeps exactly `suitability`, `capacity_kw`,
    `annual_energy_kwh`, and `geometry` — all other input columns, including `usable` itself,
    are dropped. Minimal payload: properties x ~100k roofs bloats the vector tiles (plan §4).
    """
    usable = roofs_gdf[roofs_gdf["usable"]]
    return usable[_ROOF_COLUMNS].to_crs(_WEB_CRS)


def write_tract_geojson(tracts_gdf: gpd.GeoDataFrame, path: str | Path) -> Path:
    """Write the web-ready tract layer (`tracts_to_web`) to GeoJSON (EPSG:4326); return the path.

    The tract layer is tiny (~206 DC tracts), so it is served inline as GeoJSON — no tiling
    (plan §3/§4). Composition + file I/O only; the schema/reprojection contract lives in
    `tracts_to_web`.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tracts_to_web(tracts_gdf).to_file(path, driver="GeoJSON")
    return path


def write_roof_pmtiles(
    roofs_gdf: gpd.GeoDataFrame,
    path: str | Path,
    *,
    layer: str = "roofs",
    minzoom: int = 13,
    maxzoom: int = 16,
) -> Path:
    """Build usable-roof vector tiles as PMTiles via `tippecanoe`; return the path.

    The 100k-roof layer is too heavy for client-side GeoJSON, so it is served as PMTiles vector
    tiles (plan §2a/§3). Writes `roofs_to_web` to an intermediate FlatGeobuf, then shells
    `tippecanoe` to build the tiles: a single `layer`, zoom `minzoom`..`maxzoom` — roofs are a
    z>=`minzoom` drill-down (plan §5), so nothing is tiled below `minzoom`.
    `--drop-densest-as-needed` keeps dense downtown tiles under the size limit.

    Requires `tippecanoe` on PATH (a system dep, ADR-0010).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    web = roofs_to_web(roofs_gdf)
    with tempfile.TemporaryDirectory() as tmp:
        fgb = Path(tmp) / "roofs.fgb"
        web.to_file(fgb, driver="FlatGeobuf")
        subprocess.run(
            [
                "tippecanoe",
                "-o", str(path),
                "-l", layer,
                f"--minimum-zoom={minzoom}",
                f"--maximum-zoom={maxzoom}",
                "--drop-densest-as-needed",
                "--force",
                "--quiet",
                str(fgb),
            ],
            check=True,
        )
    return path


def build_web(
    *,
    input_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    tracts_name: str = "dc_tracts.gpkg",
    roofs_name: str = "dc_roofs.parquet",
    scatter_name: str = "dc_tract_scatter.png",
) -> dict[str, Path]:
    """Build the three web assets from the pipeline's `dc_*` deliverables (plan §4).

    Reads the aggregation tract GeoPackage (layer ``tracts``) + the city roof GeoParquet from
    ``input_dir`` and writes ``tracts.geojson`` (inline layer), ``roofs.pmtiles`` (vector tiles)
    and ``scatter.png`` (the about-panel chart, copied) into ``output_dir``. Point ``input_dir``
    at ``outputs/1day/`` for a dev build or ``outputs/`` for the calibrated run (plan §4/§7).

    Returns the written paths keyed by role (``tracts`` / ``roofs`` / ``scatter``).
    """
    in_dir = Path(input_dir) if input_dir is not None else config.OUTPUTS_DIR
    out_dir = Path(output_dir) if output_dir is not None else config.REPO_ROOT / "web" / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)

    tracts_gdf = gpd.read_file(in_dir / tracts_name, layer="tracts")
    roofs_gdf = gpd.read_parquet(in_dir / roofs_name)

    return {
        "tracts": write_tract_geojson(tracts_gdf, out_dir / "tracts.geojson"),
        "roofs": write_roof_pmtiles(roofs_gdf, out_dir / "roofs.pmtiles"),
        "scatter": Path(shutil.copy(in_dir / scatter_name, out_dir / "scatter.png")),
    }
