# ADR-0010: Web delivery — static MapLibre GL JS + PMTiles on GitHub Pages

- **Status:** Accepted
- **Date:** 2026-09-08
- **Phase:** 2 (Part 2-3 — deployed interactive web map)
- **Context tags:** delivery, frontend, web-map, vector-tiles, static-hosting

## Context
Part 2-3 ships Part 2-2's District-wide per-roof data + the tract equity overlay as an interactive,
publicly-hosted web map — the product capstone at RANSAC-baseline accuracy (overview §3), before
Parts 2-4/2-5 improve accuracy behind stable interfaces. `architecture.md §11` commits to **stack-neutral
delivery**: hosted vector-tile layers, a lightweight *framework-agnostic* web app, with the Esri/ArcGIS
route a **decoupled optional** target, not a dependency. Decided in a grilling session, 2026-09-08.

Facts that shaped it (verified against the Part 2-2 output + the environment):
- The data is a **static batch output** — `dc_roofs.parquet` (100,064 roofs, 18.1 MB) + `dc_tracts.gpkg`
  (~206 tracts) — regenerated only when the pipeline re-runs, **never live-queried**.
- **Two layers at very different scales:** ~206 tracts (tiny — trivially raw GeoJSON) vs 100k roofs /
  ~676k vertices (**too heavy** for raw client GeoJSON, ~50–100 MB → must be vector tiles).
- Tooling present: `node`/`npm`, GDAL (`ogr2ogr`). **Not** present: `tippecanoe`, the `pmtiles` CLI.
- The repo is a Python geospatial pipeline with **no existing frontend**; GitHub Pages is available as a
  **project site** for the existing repo.

## Decision
1. **Static / serverless — no backend.** The map is static files; PMTiles is served off static hosting
   via HTTP **range requests**. (The data is static, so a tile server/API would add infra + quota for
   nothing.)
2. **MapLibre GL JS, buildless.** A plain `web/index.html` + `main.js` load `maplibre-gl` and the
   `pmtiles` protocol from a **pinned CDN version** — no bundler, no `node_modules`. MapLibre renders
   **both** layers: the tract layer as inline GeoJSON, the roof layer as PMTiles.
3. **Two layers, two strategies.** Tract equity = a small **GeoJSON** (no tiling). Roof layer =
   **PMTiles** built by `tippecanoe`, **usable roofs only**, a minimal 3-field payload, visible at
   **zoom ≥ 13** (below that, only the tract layer).
4. **ETL in `scripts/build_web.py`** (repo-script pattern, like `run_stage1`/`run_city`):
   `dc_roofs.parquet` / `dc_tracts.gpkg` → reproject **EPSG:6347 → 4326** → `web/assets/tracts.geojson`
   + (`ogr2ogr` → FlatGeobuf →) `tippecanoe` → `web/assets/roofs.pmtiles`. **`tippecanoe` is installed as
   a system dependency** (the GRASS pattern — a non-pip tool called on PATH).
5. **GitHub Pages, in-repo project site** (public repo; **relative** asset paths because the site lives
   under the `/<repo>/` subpath). Deploy via a **GitHub Actions Pages workflow that publishes the
   committed `web/` dir** — **no CI tiling** (the source dataset is gitignored and needs the hours-long
   GRASS pipeline, so CI can only *publish*, not rebuild). The built `roofs.pmtiles` is therefore a
   **committed deployable** under `web/assets/`.
6. **Basemap = CARTO Positron** (light, attribution) — the only residual metered dependency, with a
   self-contained minimal background as the fallback.
7. **Esri/ArcGIS stays a documented, decoupled optional route** (§11) — not the target.

## Consequences
- **Zero server cost/quota** — retires the plan §4 hosting/quota risk. The one residual metered
  dependency is the CARTO basemap (attribution-only, generous free tier).
- **The testing boundary is honest** (plan §6): the **deterministic ETL** (`build_web`) is unit-TDD'd
  (feature counts, the property schema the map reads, zero-padded `GEOID` strings, the 6347→4326
  reprojection); the map itself is a **light Playwright smoke** (loads, both layers render, no console
  errors, screenshot) + committed screenshots — **not** faked unit tests on rendering. The heavier
  `verifier-setup`/per-PR `e2e-setup` machinery is deliberately **not** adopted (over-scoped for a
  static map built once).
- **A committed build artifact.** `roofs.pmtiles` (~5–15 MB) lives in history, regenerated only on a
  pipeline re-run. Escalation if it bloats: **Git LFS** the `*.pmtiles`, or a `gh-pages` orphan branch.
  The raw `outputs/` stays gitignored; only the *curated deployable* is committed under `web/`.
- **Part 2-2's output format does not change** (GeoParquet confirmed) — Part 2-3 owns the tiling ETL.
- **Downstream (2-4/2-5):** after an accuracy re-run, re-running `build_web` + committing the new
  `web/assets/` re-propagates to the live map **without rebuilding the app**.

## Alternatives considered
- **folium (Leaflet)** — rejected: cannot natively render the PMTiles roof layer / 100k vector features
  (needs plugins, chokes at scale); would force dropping the roof drill-down that motivates tiling.
- **Server-backed tile service / API** — rejected: the data is static; a backend adds infra, quota, and
  something to keep running for no live-query benefit.
- **GDAL MVT driver (zero-install) → MBTiles** — rejected: cannot cleanly produce a single,
  static-hostable **PMTiles** without the (uninstalled) `pmtiles` CLI, and `tippecanoe`'s low-zoom
  feature-dropping is materially better for 100k features.
- **npm/vite bundler** — rejected: a 2-layer static map does not earn a build step; buildless CDN keeps
  CI a pure publish and matches §11's "lightweight, framework-agnostic".
- **Commit the source parquet + tile in CI** — rejected: the 18 MB source is gitignored and
  regenerating it in CI needs the hours-long GRASS pipeline; committing the smaller built tiles is
  simpler and sufficient.
- **Esri/ArcGIS as the target** — kept as the decoupled optional route (§11), not the portfolio target
  (vendor-coupled, account/quota).
