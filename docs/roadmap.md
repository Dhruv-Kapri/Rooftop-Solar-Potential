# Phased roadmap (§12)

## Phase 0 · Scoping & de-risking — a few evenings  ✅ COMPLETE (2026-09-01)

Pick the study area (recommended: **Washington, DC** — dense open LiDAR, and a well-documented Esri
reference workflow for a DC neighbourhood to benchmark against). Confirm 3DEP coverage/vintage, get
the NLR API key, stand up the repo and a data-access smoke test per source. Decide GRASS-vs-ArcGIS
for the radiation step now — **decided: GRASS `r.sun`.**

**Exit criteria:**
- [x] Study area + neighbourhood confirmed (DC / Glover Park); 3DEP LiDAR coverage/vintage verified
      (see findings below)
- [x] NLR API key obtained — in `.env` (kept as a placeholder between sessions for safety)
- [x] GRASS GIS installed and callable — verified via `setup_phase0.sh` stage 4 (`grass --version`)
- [x] A data-access check confirmed for each source — `scripts/check_data_access.py` (stdlib-only,
      runs before the conda env). All five verified live ([ok]) during `setup_phase0.sh` stage 6:
      footprints, DSM, imagery, boundaries, and irradiance (PVWatts, with the live key)
- [x] Reviewers get a descriptive entry point — the `README.md` (lineage + prototype figures,
      pipeline diagram, modelling modes, roadmap) links the full plan PDF as source of truth.
      **Decision (2026-09-01):** dropped the separate styled-HTML + GitHub Pages step; the README
      does the job, so there's nothing extra to build or host.

**Phase 0 findings (data-access smoke test, verified):**
- The only `3dep-lidar-dsm` item covering the Glover Park bbox is
  `USGS_LPC_VA_Fairfax_County_2018-dsm-2m-...` — **vintage 2018**, and the id indicates a **2 m**
  derived DSM. Provenance is a project *named* for Fairfax County, VA whose tiles extend north into
  DC. Two things to decide before Phase 1: (a) 2 m is coarser than the ~1 m the plan assumes and
  bounds inter-building-shading fidelity (risks §8, trap 6) — if that ceiling is too low we derive a
  finer DSM ourselves from the raw 3DEP EPT point cloud via PDAL (risks §1); (b) 2018 is ~8 years
  old — confirm no major construction in the AOI since, or note it as a documented bias.

## Phase 1 · MVP — one neighbourhood, end-to-end · 2–3 weekends  ✅ COMPLETE (2026-09-07)

> **Detailed implementation plan:** [`docs/plans/stage-1-plan.md`](plans/stage-1-plan.md) — module
> contracts, sequencing, acceptance criteria, and the decisions in [`docs/adr/`](adr/).

One DC neighbourhood. Footprints + DSM + radiation (with shading) + RANSAC roof planes + PVWatts →
a per-building suitability score and a clean static map. **Explicit goal:** reproduce and
sanity-check against a published reference (Esri's Glover Park "Estimate solar power potential"
tutorial is the Phase-1 template). Ship it even if rough — it proves the whole spine works.

**Phase 1 outcome (2026-09-07):** spine delivered end-to-end (`scripts/run_stage1.py`). Benchmark
**passes** the revised ADR-0005 gate — specific yield 1162 (MS) / 1186 (OSM) vs Esri 1150 — and the
OSM run reproduces notebook 05 (median per-building 14.35 MWh) exactly, confirming faithful
promotion. Key finding: MS ML Buildings *merges* Glover Park's rowhouses (771 footprints vs OSM's
2,885), inflating *per-building* energy 3.3× while leaving specific yield / aggregates intact — so
the gate now keys on specific yield, and median-per-building is demoted to context (ADR-0005
revised). Full write-up: [`docs/benchmarks/stage-1-glover-park-esri.md`](benchmarks/stage-1-glover-park-esri.md).
Deferred to Phase 2: census aggregation, hosted map, multi-plane/ML segmentation, 365-day sum,
explicit `r.horizon` shadow distance (trap 3).

**Phase 1 finding — DSM vintage (verified):** the buffered Glover Park AOI mosaics **three
`3dep-lidar-dsm` tiles spanning two LiDAR vintages** — one `USGS_LPC_VA_Fairfax_County_2018`
(2018) plus two `USGS_LPC_MD_VA_Sandy_NCR_2014_LAS_2015` (2014/15) tiles. Phase 0 saw only the
2018 tile because it checked the *core* bbox; the 300 m shading buffer (risks §8, trap 2)
reaches into the 2014/15 tiles. Both are 2 m with the same processing, so the seam risk is low
— but a mixed-vintage surface is a documented bias to keep in mind when interpreting kWh/CO₂
(and to re-check if construction post-dates a tile). The `notebooks/01_footprints_and_dsm.ipynb`
DSM-vs-DTM check visualises the surface these tiles produce.

## Phase 2 · Full — city-scale + ML + deployed app · multi-week  ← IN PROGRESS

Scale to the full District: tiling/batching for the whole point cloud, classical multi-plane +
obstruction roof-geometry refinement (ML demoted to a bounded inference-only demo — ADR-0011),
census-tract aggregation, an equity overlay, and the deployed web app. The portfolio centrepiece. Facade
BIPV (§10) enters here as a stretch.

- **Part 2-1 — census-tract aggregation + equity overlay — complete (2026-09-07).** `aggregate.py`
  (roll-up · equity join · quadrant classification) + `tracts.py` (TIGER/ACS/DOE LEAD) +
  `pipeline.run_aggregation` + `scripts/run_aggregation.py`; ADR-0006/0007/0008. Conservation holds
  on the real Glover Park run (all 771 roofs → 7 DC tracts, extensive sums preserved). Glover Park is
  a machinery smoke, not the equity finding — that arrives at Part 2-2.
- **Part 2-2 — city-scale tiling — built + tested (2026-09-07).** `tiling.py` (fixed-origin grid +
  `floor` membership) + `pipeline.run_city` (per-tile GeoParquet cache · resumable manifest/params-stamp
  · city-wide merge + suitability recompute) + `scripts/run_city.py`; ADR-0009. Both tiers green — seam
  conservation + idempotency verified on a real multi-tile DC run. DC = 66 tiles @ 2 km. The **full-DC
  batch run has since run** (Part 2-4 step 6, #19; ~3.9 h cold on the VM with the ADR-0014 tiered
  cache) and its populated equity map is what the live map serves.
- **Part 2-3 — deployed web app — built + live (2026-09-08).** A static MapLibre GL JS + PMTiles map on
  GitHub Pages ([live](https://dhruv-kapri.github.io/Rooftop-Solar-Potential/); `web/`,
  `scripts/build_web.py`; ADR-0010) — tract equity choropleth + 100k-roof drill-down. ETL unit-tested,
  build + browser smokes green, PMTiles range-requests on Pages confirmed. Serves the tuned Part 2-4
  city run since #25 (2026-10-18).
- **Part 2-4 — roof-geometry refinement (classical multi-plane + obstructions) — steps 1–7 done
  (2026-10-18).** Reframed from "ML segmentation" to a classical, training-free upgrade
  (ADR-0011/0012/0013): multi-plane sequential RANSAC + per-plane POA + obstruction-aware usable area +
  per-building collapse, across `roof_planes.py` / `radiation.py` / `usable_area.py` / `yield_pv.py`, wired
  into `pipeline._score_roofs` (`method="multiplane"` default; `"ransac"` kept as the baseline). City
  re-run with flat-roof GCR (#19), spot-check tuning (#23, `SCORING_VERSION` `2-4.2`), validation (#24,
  [write-up](benchmarks/stage-2-part4-validation.md)) and the map refresh (#25). The structural
  facet-vs-obstruction rule (#30, `SCORING_VERSION` `2-4.3`) re-scored the city, and #32 moved the map
  to it (run `12day_2-4.3`; figures in the write-up's
  [published map run](benchmarks/stage-2-part4-validation.md#published-map-run)). #33 (`2-4.4`) restores
  `low_confidence` to fit quality and adds `superstructure_frac`, with yield unchanged (the map stays on
  `12day_2-4.3`).
  **Remaining:** the **bounded inference-only ML demo** (step 8;
  `method="ml"`). `stage-2` merged into `main` and Pages deploys from `main` only (#26).
  **Part 2-4 step 8 + Part 2-5 (fidelity) next** — Part 2-5 starts from `main`, after its tentative
  plan is revised.

## Phase 3 · India — re-version via modular adapters · stretch

Swap data adapters behind the common interface for an Indian city (§13). Deliberately showcases
modular, provider-abstracted design — and the block-model path (§5) that traces straight back to the
SIH prototype. **Honest boundary:** DSM and imagery are resolution-limited, not source-limited, so
this drops to the block-model/LOD-1 path with a documented accuracy caveat on mutual shading.
