# Stage 2 · Part 2-3 — web delivery note

- **Date:** 2026-09-08 · **Phase:** 2 (Part 2-3) · **Plan:** [`stage-2-part3-plan.md`](../plans/stage-2-part3-plan.md) · **ADR:** [0010](../adr/0010-web-delivery-static-maplibre-pmtiles.md)
- **Live:** <https://dhruv-kapri.github.io/Rooftop-Solar-Potential/>
- **Reproduced by:** `python scripts/build_web.py` (writes `web/assets/`) → commit → the Pages workflow publishes `web/`.

The short §8 note the plan asks for: **where it's hosted, and the one build-time unknown resolved.**

## Host

A **static / serverless** site — no backend. A **GitHub Pages project site** (public repo) serves the
committed `web/`: the buildless MapLibre GL JS + PMTiles map (CDN-pinned libs, relative asset paths).
Publishing is a GitHub Actions workflow ([`.github/workflows/deploy-pages.yml`](../../.github/workflows/deploy-pages.yml))
that uploads the already-committed `web/` — **no tiling in CI** (the source dataset is gitignored and
needs the hours-long GRASS pipeline; tiles are built locally with `scripts/build_web.py` and committed).
Basemap: CARTO Positron (attribution-only free tier) — the only residual metered dependency.

## PMTiles range-requests on GitHub Pages — CONFIRMED

This was the one build-time unknown (plan §9 / ADR-0010): PMTiles serves a single file and reads tiles
by HTTP **byte-range**, so the host must honour `Range`. **Confirmed on the live deploy (2026-09-08):**

```
$ curl -s -D - -o /dev/null -r 0-99 \
    https://dhruv-kapri.github.io/Rooftop-Solar-Potential/assets/roofs.pmtiles
HTTP/2 206
accept-ranges: bytes
content-range: bytes 0-99/13486219
content-length: 100
```

`206 Partial Content` + `accept-ranges: bytes` — Pages' Fastly CDN serves ranges. A live headless
render (Playwright) additionally drew **both layers** (tract choropleth + roof PMTiles) with **zero
console errors**. So the **Cloudflare Pages fallback is not needed**.

> Note: a **local** preview needs `python scripts/serve_web.py` — stock `python -m http.server` does
> *not* honour Range, so the roof layer breaks under it (the tract layer still works). This is a
> correction to the plan's original §4 wording.

## What's served

| Asset | Shape | Serving |
|---|---|---|
| `tracts.geojson` | 206 DC tracts, EPSG:4326, 5 map fields (`GEOID`, `equity_class`, `is_priority`, `potential_per_household`, `energy_burden`) | inline GeoJSON, **1.72 MB** — no tiling (small) |
| `roofs.pmtiles` | **57,648** usable roofs (of 100,064), 3 fields (`suitability`, `capacity_kw`, `annual_energy_kwh`) | PMTiles vector tiles, **13.49 MB**, layer `roofs`, zoom **13–16** (MVT) |
| `scatter.png` | potential-vs-burden quadrant chart | about-panel image |

## Caveats & refresh

- **Preliminary numbers.** The committed tiles are from the **1-day** uncalibrated run — roof
  counts/geometry/relative suitability are exact, absolute energy/capacity read ~2–4× high. The
  calibrated 12-day swap is Part 2-3 **step 6**: re-run `scripts/build_web.py` against the calibrated
  `outputs/` and commit the refreshed `web/assets/` — **no `web/` app rebuild** (the map reads whatever
  tiles are committed; Part 2-3 §10). The map's colour breaks are computed at runtime from the data, so
  they auto-adapt.
- **Committed-tile churn** (~13.5 MB per rebuild in history; plan §9) — escalate to Git LFS / a
  `gh-pages` orphan branch if it grows.

## Testing (all green)

ETL unit tests (`tests/test_web_build.py` — schema, zero-padded `GEOID`, real 6347→4326 reprojection,
usable-only filter) · build smoke (`tests/test_web_build_integration.py` — real `tippecanoe` → valid
PMTiles, layer + minzoom) · browser smoke (`tests/test_web_smoke.py` — Playwright: both layers render,
range requests work, no console errors, screenshots).
