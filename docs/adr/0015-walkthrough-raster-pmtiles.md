# ADR-0015: Glover Park walkthrough — raster PMTiles alongside ADR-0010's vector stack

- **Status:** Accepted
- **Date:** 2026-11-14
- **Phase:** 2 (delivery extension of Part 2-3; issue #41)
- **Context tags:** delivery, web-map, raster, pmtiles, static-hosting
- **Extends:** [ADR-0010](0010-web-delivery-static-maplibre-pmtiles.md)

## Context
#41 adds a step-by-step walkthrough of how a roof's result is produced. It mirrors Esri's "Estimate
solar power potential" tutorial (the ADR-0005 benchmark): DSM/hillshade → solar radiation → slope →
aspect → cutoffs → suitable buildings → MWh. Several of those stages are **rasters**, and ADR-0010's
static stack serves vectors only (inline GeoJSON + `tippecanoe` vector PMTiles). Scope is **Glover
Park only** (user, 2026-11-14), so the rasters are small: the GP DSM is ~275 KB at 2 m.

## Decision
1. **Raster stages ship as raster PMTiles** (PNG tiles with alpha), one archive per stage, served
   off GitHub Pages by HTTP range requests, the same mechanism as the roof layer. MapLibre reads them
   as `raster` sources through the already-loaded `pmtiles` protocol.
2. **Built with tools already in the env, no new system dependency:** values are colour-mapped in
   Python to an RGBA GeoTIFF, GDAL writes raster MBTiles (`gdal.Translate(format="MBTiles")` plus
   `BuildOverviews`), and the `pmtiles` pip package converts it (`convert.mbtiles_to_pmtiles`). The env's
   GDAL has no PMTiles write driver, and `tippecanoe` is vector-only.
3. **Colour ramps are baked in at build time.** The page draws legends from value ranges the ETL
   writes to a manifest. Pixel values aren't inspectable on click, which is fine for an explanatory
   walkthrough.
4. **Vector stages stay GeoJSON** (facets, obstructions, buildings): at Glover Park scale they're
   small, as in ADR-0010's tract-layer reasoning.
5. **Swipe/compare** uses `@maplibre/maplibre-gl-compare` from a pinned CDN version, following
   ADR-0010's buildless pattern.

## Consequences
- The walkthrough adds committed deployables under `web/assets/walkthrough/` (a few MB in total,
  checked at build). ADR-0010's escalation path (Git LFS / `gh-pages`) still applies if they grow.
- City-wide rasters stay out of scope. Scaling this to the District would need a separate decision on
  size and hosting.

## Alternatives considered
- **Pre-rendered PNG `image` sources:** simplest, but a single image per stage pixelates when zoomed
  and doesn't reuse the range-request tiling. Not chosen (user, 2026-11-14).
- **COG + client-side rendering** (geotiff.js / a COG protocol): allows inspecting pixel values, but
  brings the most new JS and a new CDN dependency for an explanatory page.
- **`rio-pmtiles` / `gdal2tiles`:** would work, but add a dependency that GDAL MBTiles plus the
  existing `pmtiles` package already cover.
