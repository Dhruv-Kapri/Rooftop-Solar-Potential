# Stage 2 · Glover Park step-by-step walkthrough plan (#41)

- **Phase:** 2 (delivery extension of Part 2-3) · **Branch:** `issue-41-gp-walkthrough` off `stage-2`
- **Issue:** [#41](https://github.com/Dhruv-Kapri/Rooftop-Solar-Potential/issues/41) ·
  **Depends on:** #40 (per-plane `reason` + 30 m² gate) · **ADR:** [ADR-0015](../adr/0015-walkthrough-raster-pmtiles.md)

## 1. What this is

A separate page, `web/walkthrough.html`, that explains *how* a roof's result is produced, one stage at
a time. It mirrors the stages of Esri's "Estimate solar power potential" tutorial (the ADR-0005
benchmark) and adds the stages our pipeline has but the tutorial doesn't. **Glover Park only.** The
city map (`web/index.html`) is unchanged apart from a link to the walkthrough. City-wide rasters are
out of scope.

## 2. Decisions (user, 2026-11-14)

| Question | Decision |
|---|---|
| Raster delivery | **Raster PMTiles** (ADR-0015, extends ADR-0010) |
| Stages | **Tutorial + ours**: tutorial-parity pixel rasters, then our fitted planes, obstructions, per-plane cutoffs and kWh (§3) |
| UX | **Stepper + swipe**: step list, Prev/Next, per-step narrative, swipe comparing the previous and current step |
| #40 dependency | **Build now, wire #40 later**: everything except the final asset build lands now; the cutoff steps read #40's per-plane reason column through one constant |

Decided during implementation (recorded assumptions, cheap to revisit):
- **Seam:** the multiplane branch of `pipeline._score_roofs` becomes `pipeline.score_stages(...)`,
  which returns a `ScoringStages` record (DSM path, insolation path, per-plane frame, obstructions,
  per-plane usable frame, per-building result). `_score_roofs` returns `.buildings`, so its behaviour
  is unchanged. The walkthrough runs **the same spine** rather than a parallel copy.
- **Vectors:** facets, obstructions and buildings are small at Glover Park scale, so they ship as
  GeoJSON, as the tract layer already does in ADR-0010. Only the rasters need tiling.
- **Narrative:** short per-step copy in `walkthrough.js`, adapted from notebooks 01–05.

## 3. Stages

| # | Stage | Kind | Source | Tutorial equivalent |
|---|---|---|---|---|
| 1 | DSM hillshade | raster | `dsm.build_dsm` (GP DSM) | DSM + hillshade |
| 2 | Shaded `r.sun` insolation, clipped to footprints | raster | `radiation.surface_irradiance` (12-day, ADR-0001) | Solar_Rad |
| 3 | Pixel slope, clipped to footprints | raster | `numpy` gradient of the DSM | Slope_DSM |
| 4 | Pixel aspect, clipped (flat ≤10° shown as flat) | raster | same | Aspect_DSM |
| 5 | Fitted roof planes, coloured by aspect | vector | `fit_roof_planes("multiplane")` `inlier_xy` → pixel-square union | — (ours, ADR-0011) |
| 6 | Obstructions removed | vector | `detect_obstructions` geometry | — (ours, ADR-0013) |
| 7a–d | Cutoffs, applied one at a time: too steep (>45°) → north-facing → low insolation (<800 kWh/m²) → too small (<30 m²); #40's precedence, so a plane failing several cutoffs carries (and is removed at) the first | vector filter | #40's per-plane reason | remove >45° / north / <800 / Suitable_Buildings ≥30 m² |
| 8 | Suitable buildings by annual MWh, plus the ADR-0005 benchmark line | vector | `score_stages(...).buildings` | Elec_Prod_MWh |

Stages 3–4 are **context, not inputs**: the pipeline applies its cutoffs to fitted planes, not
pixels. The narrative says so, which is the honest difference from the tutorial.

## 4. Seams and modules

- `pipeline.score_stages` / `ScoringStages`: the refactor in §2. `run_stage1`, `run_city` and
  `_score_roofs` callers are unchanged.
- `walkthrough.py` (new): pure ETL.
  - Raster: `hillshade`, `slope_aspect` (numpy, unit-tested on synthetic planes), `mask_to_footprints`,
    `colorize` (value → RGBA via a matplotlib colormap, nodata transparent), and
    `write_raster_pmtiles` (RGBA GeoTIFF → GDAL MBTiles + overviews → `pmtiles.convert.mbtiles_to_pmtiles`).
  - Vector: `facet_polygons` (planes → one polygon per plane, plus tilt/aspect/reason),
    `obstruction_polygons`, `buildings_to_web`, and `walkthrough_manifest` (per-step stats, raster
    value ranges for legends, the GP benchmark numbers).
  - `build_walkthrough(...)`: runs `score_stages` over Glover Park with the DSM and insolation
    rasters persisted under `data/walkthrough/` (tiered-cache keys, ADR-0014, so a rebuild skips
    `r.sun`) and writes `web/assets/walkthrough/`.
- `scripts/build_walkthrough.py`: thin CLI.
- `web/walkthrough.html` + `walkthrough.js` (+ `style.css` additions): MapLibre, pmtiles,
  `@maplibre/maplibre-gl-compare` (pinned CDN). Two synced maps; Prev/Next moves the right map to
  step *n* and the left map to step *n−1*.

## 5. Tests (quality over count)

- `score_stages(...).buildings` equals the old `_score_roofs` output on a synthetic fixture (pins the
  no-behaviour-change refactor).
- `slope_aspect` on known synthetic planes (a south-facing 30° plane and a flat plane): exact slope,
  aspect convention matching `roof_planes` (0 = N, clockwise).
- `colorize`: nodata is transparent, values are clamped to the range.
- `facet_polygons`: one polygon per plane, area ≈ n_px × pixel area, the reason carried through, and an
  unfittable building → no facet.
- The manifest schema the page reads; `write_raster_pmtiles` produces a readable PMTiles with the
  expected bounds and zooms (integration: needs GDAL).
- Browser smoke (`tests/test_walkthrough_smoke.py`, integration): the page loads, stepping through all
  steps throws no console errors, the raster and vector layers render, screenshot.

## 6. Steps

1. Plan + ADR-0015 (this doc).
2. `score_stages` refactor (TDD, pinned by the equality test).
3. Raster ETL + PMTiles writer.
4. Vector ETL + manifest (reason column via one constant, `walkthrough.REASON_COLUMN`, reconciled
   with #40's name at rebase).
5. Page + stepper + swipe + build script + smoke.
6. **After #40 lands:** rebase, run the GP walkthrough build (12-day `r.sun` over GP), commit
   `web/assets/walkthrough/`, check asset sizes, and run the browser smoke.

## 7. Risks / open

- #40's reason-column name and collapse rule aren't fixed yet, so step 7 is wired by one constant.
- The swipe plugin adds a third CDN dependency (pinned), same pattern as ADR-0010.
- Raster PMTiles at z13–z17 for a ~2 km area: expected to be well under 5 MB in total; checked at step 6.
