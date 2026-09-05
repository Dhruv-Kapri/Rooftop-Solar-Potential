"""Phase 1 vertical slice — fetch real footprints + DSM for Glover Park and render a
static check map, so a human can eyeball that the two layers line up.

De-risks the two data-fetch pipeline stages (footprints.py, dsm.py) before roof-plane
extraction / radiation modelling get built on top of them.

Run:  python scripts/fetch_aoi_data.py
"""

from __future__ import annotations

import geopandas as gpd
import matplotlib.pyplot as plt
import rasterio
from rasterio.plot import show as rio_show

from rooftop_solar import aoi, config, dsm, footprints


def main() -> None:
    core = aoi.core_aoi_wgs84()
    buffered = aoi.buffered_aoi()
    print(f"Core AOI (unbuffered):     {core.bounds}")
    print(f"Buffered AOI (+{config.AOI_BUFFER_M:.0f} m): {buffered.bounds}")

    # --- footprints: try Microsoft Global ML Building Footprints, fall back to OSM ---
    try:
        footprints_gdf = footprints.load_footprints(buffered, source="ms-buildings")
        footprints_source = "ms-buildings"
    except Exception as exc:  # deliberately broad: any ms-buildings failure -> OSM fallback
        print(f"[ms-buildings failed: {exc!r}] falling back to source=osm")
        footprints_gdf = footprints.load_footprints(buffered, source="osm")
        footprints_source = "osm"

    footprints_path = config.DATA_DIR / "footprints" / "glover_park_footprints.gpkg"
    footprints_path.parent.mkdir(parents=True, exist_ok=True)
    footprints_gdf.to_file(footprints_path, driver="GPKG")
    print(f"Footprints: {len(footprints_gdf)} building(s) from source={footprints_source!r}")
    print(f"  -> {footprints_path}")

    # --- DSM: pre-derived 2 m Planetary Computer 3dep-lidar-dsm collection ---
    dsm_path = dsm.build_dsm(buffered)
    with rasterio.open(dsm_path) as dsm_src:
        print(f"DSM: shape={dsm_src.shape} crs={dsm_src.crs} res={dsm_src.res}")
        print(f"  -> {dsm_path}")

    # --- static check map ---
    outputs_dir = config.OUTPUTS_DIR
    outputs_dir.mkdir(parents=True, exist_ok=True)
    map_path = outputs_dir / "glover_park_aoi.png"
    _render_check_map(dsm_path, footprints_gdf, core, map_path)
    print(f"Map: -> {map_path}")

    print("\nDone.")


def _render_check_map(dsm_path, footprints_gdf, core_aoi_wgs84, out_path) -> None:
    """DSM as a terrain background, footprints overlaid, core (unbuffered) AOI outlined.

    Everything is drawn in the DSM's CRS (config.WORKING_CRS) so footprints and the core
    AOI box (both fetched/defined in EPSG:4326) are reprojected to match before plotting.
    """
    with rasterio.open(dsm_path) as dsm_src:
        crs_label = dsm_src.crs.to_string()
        fig, ax = plt.subplots(figsize=(10, 12))
        rio_show(
            dsm_src, ax=ax, cmap="terrain", title="Glover Park, DC — DSM + building footprints"
        )

        footprints_working = footprints_gdf.to_crs(dsm_src.crs)
        footprints_working.boundary.plot(ax=ax, edgecolor="black", linewidth=0.4)

        core_gdf = gpd.GeoDataFrame(geometry=[core_aoi_wgs84], crs="EPSG:4326").to_crs(dsm_src.crs)
        core_gdf.boundary.plot(ax=ax, edgecolor="red", linewidth=2.0)

        ax.set_xlabel(f"Easting ({crs_label}, m)")
        ax.set_ylabel("Northing (m)")
        fig.tight_layout()
        fig.savefig(out_path, dpi=150)
        plt.close(fig)


if __name__ == "__main__":
    main()
