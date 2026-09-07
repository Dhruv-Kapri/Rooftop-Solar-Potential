# Stage 2 · Part 2-3 plan (tentative) — Deployed web app

> **STATUS: tentative** — a hypothesis written 2026-09-07, before Part 2-2 exists. Per the
> living-plans protocol ([`stage-2-overview.md`](stage-2-overview.md) §4), **re-read and revise this
> before starting it**, against what the earlier parts actually taught. Guesses are flagged `ASSUMPTION:`.

- **Phase:** 2, Part 3 of 5 · **Branch:** `stage-2` · **Overview:** [stage-2-overview.md](stage-2-overview.md)
- **Prerequisite:** Part 2-2 built.

## 1. What this part is

An interactive, hosted web map of Part 2-2's district-wide per-roof data + tract choropleth + equity
overlay. Per the overview (§3), this **reaches the end goal as a complete product** — at RANSAC
baseline accuracy — before 2-4/2-5 improve accuracy behind stable interfaces. This is the piece with
the least in common with the rest of the pipeline: delivery/frontend, not analytics.

## 2. Where it continues from

- Part 2-2's district-wide per-roof dataset (ASSUMPTION there: GeoParquet) and district-wide tract
  GeoPackage/choropleth (from Part 2-1's aggregation, reused unmodified).
- The equity classification (`equity_class`, `is_priority`) from Part 2-1 (ADR-0007) — this is the
  overlay layer, not a new computation.
- Architecture §11 (stack-neutral delivery): publish as hosted layers, wrap in a lightweight,
  framework-agnostic web map; the Esri/ArcGIS route stays a supported, decoupled option.

## 3. Likely approach (tentative)

- ASSUMPTION: generate vector tiles from the district GeoParquet (per-roof + tract layers) rather than
  serving raw vector data to the client at city scale.
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
