# Stage 2 · Part 2-3 plan (tentative) — Deployed web app

> **STATUS: grounded, ready to grill (2026-09-08)** — the tentative hypothesis (written 2026-09-07)
> now folds in Part 2-2's **real city dataset** from a full-DC run (numbers in §2a below), per the
> living-plans protocol ([`stage-2-overview.md`](stage-2-overview.md) §4). Format and scale are no
> longer guesses; the framework / host / tiling-toolchain choices are still open — that's the
> `/grill-with-docs` job. Remaining guesses stay flagged `ASSUMPTION:`.

- **Phase:** 2, Part 3 of 5 · **Branch:** `stage-2` · **Overview:** [stage-2-overview.md](stage-2-overview.md)
- **Prerequisite:** Part 2-2 built.

## 1. What this part is

An interactive, hosted web map of Part 2-2's district-wide per-roof data + tract choropleth + equity
overlay. Per the overview (§3), this **reaches the end goal as a complete product** — at RANSAC
baseline accuracy — before 2-4/2-5 improve accuracy behind stable interfaces. This is the piece with
the least in common with the rest of the pipeline: delivery/frontend, not analytics.

## 2. Where it continues from

- Part 2-2's district-wide per-roof dataset (GeoParquet — **CONFIRMED**, see §2a) and district-wide
  tract GeoPackage/choropleth (from Part 2-1's aggregation, reused unmodified).
- The equity classification (`equity_class`, `is_priority`) from Part 2-1 (ADR-0007) — this is the
  overlay layer, not a new computation.
- Architecture §11 (stack-neutral delivery): publish as hosted layers, wrap in a lightweight,
  framework-agnostic web map; the Esri/ArcGIS route stays a supported, decoupled option.

## 2a. Grounded against Part 2-2's real dataset (2026-09-08)

Part 2-2's roof side ran full-DC (a 1-day, uncalibrated pass — building counts and geometry are
day-count-independent, so these figures also hold for the pending 12-day calibrated run):

| Layer | Scale (measured) | Serving implication |
|---|---|---|
| **Per-roof** (`dc_roofs.parquet`) | **98,471 roofs** (57,140 usable), **18.1 MB** GeoParquet, ~676k vertices (mean **6.9/roof** — simple footprints) | Too heavy for raw client-side GeoJSON (~50–100 MB); **vector tiles are required, not optional** (§3's ASSUMPTION → a requirement). Simple geometries tile cheaply. |
| **Tract equity** (~206 DC tracts) | Tiny (hundreds of polygons) | The **primary** choropleth — trivially served as inline GeoJSON, **no tiling needed**. |

**What this settles for 2-3:**
- **Format CONFIRMED = GeoParquet** — Part 2-2's output does *not* need to change (§6's "absorbs an
  extra ETL step" risk is retired): the ETL is GeoParquet → (GeoJSON/FlatGeobuf) → `tippecanoe` →
  **PMTiles/MBTiles** for the roof layer only; the tract layer stays a small GeoJSON.
- **Two layers, two strategies** — build the small tract-equity map first (near-static; it *is* the
  finding), add the 98k-roof vector-tile layer as a zoom-in drill-down. Directly de-risks §4's "how
  much interactivity is worth it".

**City-scale quirks observed (feed §6):**
- **Transient Planetary Computer auth/SAS-token failures** on a long run: ~1 tile/run failed (e.g.
  `tile 11_3`) with `ClientAuthenticationError`; **skip-and-continue** caught it and **resume retries**
  it — so a "100% complete" dataset may need one resume pass. (Candidate 2-2 hardening: re-sign/retry
  on auth error.)
- **0 empty in-DC tiles** — all 66 grid tiles have buildings (dense urban), so the empty-tile
  short-circuit rarely fires for DC.
- **Absolute energy/capacity from the 1-day run are NOT usable** (single-day extrapolation → ~3.6 GW /
  6 TWh/yr, ~2–4× NREL's ~1.3 GW / ~1.6 TWh/yr); the map's numbers must come from the **12-day
  calibrated** run. Roof *counts* / geometry are exact regardless.

## 3. Likely approach (tentative)

- ASSUMPTION: generate vector tiles from the district GeoParquet → **CONFIRMED necessary for the
  per-roof layer** (98k features / 18 MB — see §2a), but **only** that layer; the ~206-tract equity
  layer is tiny and served as inline GeoJSON, no tiling.
- Route options to choose between (stack-neutral, per architecture §11), roughly in ascending
  effort/control:
  1. `folium` — already declared in `environment.yml` but currently unused; a quick static/served-map
     route, likely the fastest path to *something* deployed.
  2. A JS map (MapLibre GL / Leaflet) over the generated vector tiles — more interactivity/control,
     more build surface.
  3. The supported optional Esri/ArcGIS route (ArcGIS Online / Experience Builder / `arcgis.learn`,
     architecture §11) — first-class supported, but a vendor-coupled deployment target.
- ASSUMPTION: layer toggles for suitability, per-household potential, and the equity quadrant
  (ADR-0007's 2×2 classes / priority flag) as the core interactive surface — this is the minimum that
  makes the map more than a static choropleth.

## 4. Key questions to grill when we reach this part

- Framework choice — resolve the folium-vs-JS-map-vs-Esri tradeoff above against actual effort
  available and what "deployed" needs to mean for a portfolio piece.
- Hosting + free-tier/quota limits (risks §5: "cloud/free-tier limits are real... batch publishes and
  confirm caps before committing a platform as the final target") — pick a host only after checking
  this.
- Vector-tiling at city scale — what generates the tiles, and does Part 2-2's output format need to
  change to make that easy?
- How (and how much) to test a deliverable that is mostly not test-first — what's the honest testing
  story for a frontend/map layer in a TDD-leaning repo?
- How much interactivity is actually worth building vs a good static/near-static map — scope this
  against time remaining, not ambition.

## 5. Tentative deliverables

- A deployed, publicly reachable (or at least demoable) interactive map covering the whole District.
- Layer toggles: suitability, per-household potential, equity quadrant/priority.
- A short note on hosting choice + quota/cost constraints actually hit.

## 6. Risks / dependencies

- Quota/credit metering on whatever host is chosen (risks §5) — could force a platform change or a
  scope cut late.
- This is honestly **the least test-first part of Stage 2** — say so plainly rather than retrofitting
  a TDD story that doesn't fit a mostly-visual, mostly-frontend deliverable.
- Depends on Part 2-2's output format/scale being map-service-friendly; if not, this part absorbs an
  extra ETL step it didn't plan for.

## 7. Downstream review checkpoint

When this part is built, re-read the remaining downstream plans (2-4, 2-5) and revise them before
starting the next — in particular, note whether the deployed map's data format constrains how 2-4/2-5
must publish their re-run output to propagate without rebuilding the app.
