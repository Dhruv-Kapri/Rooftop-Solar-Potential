# Glossary

Project-specific terms as used in this pipeline, not textbook-general definitions. Cross-reference
`docs/roadmap.md`, `docs/risks.md`, and the ADRs in `docs/adr/` where noted.

## Geometry & data

- **AOI (area of interest)** — the study boundary for a run; Stage 1's AOI is Glover Park, DC.
- **Buffered AOI / shadow buffer** — the AOI padded outward (300 m in Stage 1) before clipping the
  DSM, so buildings just outside the AOI's edge can still cast shadows onto roofs just inside it.
  Without this, south-edge buildings look falsely unshaded (risks §8, trap 2).
- **DSM vs DTM** — DSM (Digital Surface Model) includes buildings, trees, everything above bare
  ground; DTM (Digital Terrain Model) is bare-earth only. The radiation pass must use the **DSM** —
  the DTM has no buildings, so it silently zeroes all inter-building shading (risks §8, trap 1).
- **Height-above-ground** — a building's height derived from DSM minus DTM at its footprint; used to
  judge whether a building is tall enough to matter as a shadow-caster for its neighbours.
- **Building footprint** — a 2D polygon outlining one building; the unit every downstream stage
  (DSM clip, roof plane, yield, score) is computed per instance of.
- **LiDAR / point cloud** — laser-scanned 3D points (x, y, z + intensity/classification) the DSM is
  derived from; the source of truth for building and terrain height.
- **EPT (Entwine Point Tiles)** — the tiled, indexed point-cloud format 3DEP LiDAR is served in for
  streaming access without downloading whole tiles.
- **3DEP** — USGS's 3D Elevation Program; the national LiDAR/DSM source this pipeline draws from.
- **MS ML Building Footprints** — Microsoft's ML-generated building footprint dataset; the canonical
  Stage-1 footprint source (the exploration notebooks used raw OSM instead — see ADR-0005).
- **NAIP** — National Agriculture Imagery Program; aerial RGB imagery, one candidate imagery source.

## Radiation

- **GRASS `r.sun`** — the GRASS GIS module that computes surface solar radiation per raster cell,
  including horizon/shadow-casting from a DSM. The locked radiation engine (no pure-Python
  equivalent exists — risks §14.2).
- **`r.horizon`** — the GRASS module `r.sun` calls internally to compute the horizon angle (and
  hence shadow-casting) per cell from the DSM.
- **Shadow search distance** — how far `r.horizon`/`r.sun` look outward for a shadow-casting obstacle.
  Too short and a distant tall tower stops mattering even though it still shades the roof (risks §8,
  trap 3). In the current pipeline this is **not an explicit `r.sun` parameter** — the effective reach
  is bounded by the DSM/GRASS-region extent (i.e. the buffered AOI), so the **shadow buffer *is* the
  search distance** until an explicit `maxdistance` is wired in Part 2-5.
- **Insolation** — solar energy received per unit area over a period (e.g. kWh/m²/yr); the raster
  quantity `r.sun` produces per cell.
- **POA (plane-of-array)** — irradiance measured or modelled in the plane of the tilted roof/panel
  surface, as opposed to a horizontal or normal-incidence measurement.
- **Global radiation / `glob_rad`** — `r.sun`'s combined direct + diffuse + reflected radiation
  output for a cell; the primary insolation quantity used downstream.
- **Clear-sky vs real-sky** — clear-sky assumes no clouds/atmosphere loss; real-sky applies an
  empirical derate for actual atmospheric conditions. Stage 1's PV yield constants include a
  real-sky derate (ADR-0003).
- **Representative-day sampling (12-day scheme)** — Stage 1's approximation of an annual insolation
  sum using 12 mid-month days (one per month, weighted by days-in-month) instead of all 365 days —
  ~30× cheaper, shading stays on throughout (ADR-0001).

## Yield & scoring

- **Roof plane** — the single fitted plane (z = a·x + b·y + c) approximating a roof's surface within
  one footprint; Stage 1 fits exactly one per building (ADR-0002).
- **Tilt** — the roof plane's angle from horizontal, derived from the fitted plane's slope.
- **Aspect / azimuth** — the compass direction the roof plane faces, derived from the fitted plane's
  downslope direction; used to classify sun-facing vs. north-facing.
- **RANSAC** — Random Sample Consensus; a robust plane-fitting method that repeatedly samples subsets
  of points and keeps the fit with the most inliers, tolerating outliers (obstructions, noise).
- **Inlier ratio** — the fraction of a footprint's DSM points that fit the chosen RANSAC plane within
  tolerance; low ratios flag a poorly-fit (often multi-facet) roof.
- **Low-confidence flag** — a per-roof boolean set when `inlier_ratio < 0.5` or fewer than 20 inlier
  pixels back the fit; carried through to output, not dropped (ADR-0002). ~55% of Glover Park roofs
  carry this flag.
- **Utilization fraction** — the fraction (0.70 in Stage 1) of a qualifying roof's area assumed
  actually packable with panels, after setbacks/spacing/obstructions (ADR-0003).
- **Usable area** — the roof area that both (a) passes the plan's hard cutoffs (slope ≤ 45°,
  irradiance ≥ 800 kWh/m²/yr, not north-facing) and (b) is then derated by the utilization fraction
  (ADR-0003).
- **PV capacity (kW)** — the nameplate DC power a roof's usable area could hold, from usable area ×
  module power density (0.20 kW/m² in Stage 1).
- **Performance ratio (PR)** — the fraction of theoretical PV output actually delivered after
  real-world losses (wiring, temperature, inverter, soiling); 0.80 in Stage 1's constants.
- **Specific yield (kWh/kWp)** — annual energy produced per kW of installed capacity; the standard
  size-independent measure used to compare a roof's productivity regardless of its area, and the
  quantity ADR-0005's benchmark check gates on.
- **Capacity factor** — specific yield expressed as a fraction of the theoretical maximum
  (nameplate kW running 8760 h/yr); a normalized productivity measure.
- **CO₂ offset / grid-emissions factor** — annual energy × a grid-emissions factor (0.35 kg CO₂/kWh
  in Stage 1) gives the estimated CO₂ displaced by generating that energy locally instead of from
  the grid.
- **Suitability score** — Stage 1's per-roof score: the within-AOI percentile rank of annual energy
  density (energy per usable m²), with unusable roofs pinned to 0 (ADR-0004).
- **Percentile rank** — a value's rank among a set, expressed as the fraction of the set it exceeds;
  robust to outliers but only meaningful relative to the set it was computed over (ADR-0004).

## Aggregation & delivery

- **Census tract** — the standard US Census geography Stage 2 aggregates per-building results up to,
  for the equity-lens analysis (roadmap Phase 2).
- **Choropleth** — a map shaded by a per-region value (e.g. per-tract suitability); the intended
  Stage 1/2 output visualization.
- **NLR** — National Laboratory of the Rockies; NREL's name as of 2025-12-01. Old NREL API keys still
  work against `developer.nlr.gov`.
- **PVWatts** — NREL/NLR's PV energy production model; Stage 1 uses its constants as explicit local
  values rather than calling its API directly (ADR-0003); a direct API call is a Phase-2 option.
- **Esri benchmark** — Esri's "Estimate solar power potential" tutorial for Glover Park; the
  published reference Stage 1 sanity-checks against (ADR-0005).
- **LOD-1 / block model** — the fallback modelling mode: extrude each footprint to a single height,
  no per-roof plane fitting. Used where LiDAR is unavailable (the India/Phase-3 path), not for DC.

### Stage 2 · census aggregation & equity (Part 2-1)

- **GEOID** — the US Census geographic identifier; for a tract an 11-digit code (2 state + 3 county +
  6 tract). DC tracts begin `11001…`. Must be kept a **zero-padded string** end-to-end — integer
  coercion drops the leading `11` and silently breaks every join (ADR-0006).
- **TIGER/Line** — the Census's boundary-geometry files; Stage 2 uses the **2020-vintage** DC tract
  polygons as the aggregation geography (data-sources.md; ADR-0006).
- **ACS (American Community Survey)** — the Census's rolling demographic survey; Stage 2 pulls its
  **5-year** tract estimates for population, occupied housing units, and median household income via
  the Census Data API (needs a free `CENSUS_API_KEY`; ADR-0006).
- **Energy burden** — the share of household income spent on home energy; Stage 2's **headline equity
  dimension** — a high-burden tract is where rooftop-solar bill savings matter most. Sourced from DOE
  LEAD (ADR-0006).
- **DOE LEAD** — the US DOE's *Low-Income Energy Affordability Data*; the tract-level energy-burden
  source (2022 update, 2020-tract-aligned, free file download; ADR-0006).
- **Disadvantage (this project)** — tract disadvantage is operationalized by **energy burden**
  (headline) + **median household income** (legible secondary), *not* a composite index. The
  de-hosted federal composites (CEJST, EPA EJScreen) are an optional, pinned, caveated overlay only —
  never the core (ADR-0006).
- **Extensive vs intensive (tract) quantities** — *extensive* quantities **sum** over a tract's roofs
  (total usable area, PV capacity, energy, CO₂, building/usable-roof counts); *intensive* quantities
  are per-unit summaries that don't scale with tract size (median suitability, % roofs usable). The
  equity lens uses a per-household normalization of the extensive potential.
- **Per-household potential** — a tract's total rooftop-solar potential ÷ its occupied housing units;
  the equity-relevant normalizer (benefit accrues to *dwellings*, and energy burden is already a
  per-household metric, so the two equity axes share a denominator). Per-capita is reported as a
  legible secondary (ADR-0007).
- **Equity quadrant / priority tract** — Stage 2's equity overlay classifies each tract into a 2×2 of
  (per-household potential, high/low) × (energy burden, high/low), split at the tract-set medians. A
  **priority tract** is high-potential + high-burden — the biggest equity win from rooftop solar.
  Median-relative, like the ADR-0004 percentile, so meaningful only across many tracts — i.e.
  city-wide (Part 2-2), not Glover Park's ~7 tracts (ADR-0007).

### Stage 2 · city-scale tiling (Part 2-2)

- **Tile** — one cell of Part 2-2's fixed grid over the District. It carries a **core** cell (the area
  it is responsible for scoring) and a **buffered** cell (core grown by `TILE_BUFFER_M`) that the
  DSM + `r.sun` pass actually cover, so a core-edge roof is still shaded by casters just outside the
  core — the inter-building/AOI shadow trap, one scale up (ADR-0009).
- **Fixed grid origin** — the tile grid is anchored to a **fixed origin**, so a cell's `(row, col)` —
  and therefore its cache key — is identical on every run, independent of how the AOI is framed. The
  precondition for idempotent caching (ADR-0009).
- **`floor` membership / seam conservation** — each building is assigned to exactly one tile by
  flooring its representative point against the grid origin: `(row, col) = ⌊(rep_point − origin)/tile_size⌋`.
  A building on a **seam** (a shared cell edge) therefore lands in exactly one cell *by construction* —
  never dropped (as a `.within` test would) nor double-counted (as a footprint-intersect would). **Seam
  conservation**: every District building is scored exactly once (ADR-0009).
- **Params-stamp** — a per-tile fingerprint (radiation days, DSM source, buffer, tile size, a pipeline
  version) recorded in the **tile manifest**. A resumed run recomputes only tiles whose stamp changed,
  so an accuracy swap (Parts 2-4/2-5) is incremental, not from-scratch, and a stale cache is never
  silently served (ADR-0009).
- **GeoParquet** — the columnar, partition-friendly geospatial format Part 2-2 writes the city-scale
  per-roof dataset in (partitioned per tile) — better suited than a single GeoPackage to a ~10⁵-building
  dataset and incremental per-tile writes. The small per-tract output stays GeoPackage (ADR-0009).

### Stage 2 · roof-geometry refinement (Part 2-4)

- **Multi-plane (sequential) RANSAC** — Part 2-4's upgrade to ADR-0002's single-plane fit: fit the best
  RANSAC plane on a roof's DSM pixels, remove its inliers, refit on the remainder, repeat (≤ ~4 planes,
  dropping planes below a min area/inlier count). Recovers gable/hip/complex roofs the single-plane
  baseline averaged into one tilt/aspect. Classical, training-free (ADR-0011). Exposed as
  `fit_roof_planes(method="multiplane")`; `"ransac"` is kept for the baseline comparison.
- **Pixel–plane membership** — which DSM pixels are inliers of which fitted plane; a byproduct of
  sequential RANSAC. It is what makes per-plane POA and DSM-residual obstruction detection possible
  (ADR-0011/0012/0013).
- **Dominant plane** — the largest usable plane on a roof; its tilt/aspect/roof_class are what the
  collapsed per-building row reports (ADR-0011).
- **Per-building collapse** — Part 2-4 fits multiple planes internally but emits **one row per
  building** (extensive quantities summed over usable planes), so the downstream contract (aggregate,
  web_build, the map) is unchanged — no schema change, no map rebuild (ADR-0011; Part 2-3 §10).
- **Per-plane POA** — insolation taken as the `r.sun` zonal mean over each **usable** plane's pixels,
  not over the whole footprint. The footprint mean dilutes sun-facing planes with north-face/obstruction
  pixels; per-plane POA removes that dilution — the biggest honest energy-accuracy gain, nearly free
  given pixel–plane membership (ADR-0012). Energy is driven by this insolation, not by tilt/aspect
  (which only feed the usable/not gate).
- **Rooftop obstruction / superstructure** — a chimney, vent, HVAC unit, or bulkhead that eats usable
  panel area. Detected as a **DSM residual**: pixels sitting more than a threshold (≈0.5–1.0 m) above
  the fitted plane (the RANSAC outliers-above). Classical — no training or labels (ADR-0013).
- **Obstruction-aware usable area** — Part 2-4 replaces ADR-0003's flat `× 0.70` utilization fraction
  with **measured** obstruction subtraction plus a smaller principled setback factor (~0.85–0.90,
  edge/access only): `usable = (plane_area − obstruction_area) × setback_factor` (ADR-0013).
- **DSM self-consistency** — Part 2-4's ground-truth-free accuracy proxy: reconstruct the DSM from the
  fitted planes and measure the per-roof residual (RMSE / % pixels within tolerance), multi-plane vs
  single-plane. Multi-plane should reconstruct complex roofs with materially lower residual (ADR-0011).
- **Spot-check (hand-labelled sample)** — ~20 DC roofs, stratified across archetypes (flat/gable/hip/
  complex) over Glover Park + a downtown block, hand-labelled for **major-plane count** and
  **obstruction presence**, compared against the algorithm (plane-count agreement ±1 vs single-plane;
  obstruction hit/false-positive). An indicative sanity check, not a statistical accuracy claim — the
  honest stand-in for the DC ground truth that doesn't exist (risks §14.4).
- **ML demo (`method="ml"`)** — a bounded, **inference-only** demonstration: an existing NYC-trained
  RoofN3D point-cloud segmenter run on a handful of DC roofs behind the pre-wired
  `fit_roof_planes(method="ml")` seam, plus a comparison notebook. Framed as a NYC-trained *prior*
  applied to DC (domain gap noted), **not** a validated pipeline swap — the shipped accuracy path is the
  classical `"multiplane"` (ADR-0011).
