# Data sources — US v1 (all public, all verified)

From §6 of the plan. All layers are public and free; some need a free API key.

| Layer | Recommended source | Access & notes |
|---|---|---|
| **Building footprints** | Microsoft Global ML Building Footprints (fallback: OSM via `osmnx`) | Bulk GeoJSON via GitHub `dataset-links.csv`, or STAC `ms-buildings` on Planetary Computer. License CDLA-Permissive-2.0. OSM is ODbL, coverage uneven. |
| **Surface model (DSM)** | USGS 3DEP LiDAR → derive DSM | Raw point cloud on AWS `usgs-lidar-public` (EPT/LAZ), read with PDAL `readers.ept`; rasterize via `writers.gdal`, `output_type: max`. Shortcut: Planetary Computer hosts a pre-derived `3dep-lidar-dsm` STAC collection — use it where it covers your city. |
| **Imagery** | NAIP | Planetary Computer `naip` STAC, Cloud-Optimized GeoTIFF, 0.3–1 m, RGB+NIR, public domain. Revisit every 2–3 yrs (not for change detection). |
| **Solar irradiance** | NSRDB + PVWatts v8 (NREL, now NLR) | Free API key at `developer.nlr.gov/signup`. PVWatts converts tilt/azimuth/area → annual kWh. NSRDB irradiance at ~4 km (2 km for some GOES products). |
| **Admin boundaries** | US Census TIGER/Line | Free, no auth, `www2.census.gov/geo/tiger/`. Census tracts for aggregation; join via GeoPandas. |

## Naming note (important)

The **National Renewable Energy Laboratory** was renamed the **National Laboratory of the Rockies
(NLR)** on **2025-12-01**. Its developer API moved from `developer.nrel.gov` → `developer.nlr.gov`
(old keys still valid). Use the new domain so the work reads current. Historical NREL reports keep
their original technical-report numbers (e.g. NREL/TP-6A20-65298).

## The DSM-vs-DTM trap (critical for shading)

Use the **DSM** (full elevation surface — every building at real height + trees + terrain), **never
the DTM** (bare earth), for the radiation pass. The DTM has no buildings, so it zeroes out all
inter-building shading. See `docs/risks.md`.
