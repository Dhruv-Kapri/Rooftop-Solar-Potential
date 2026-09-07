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
  trap 3).
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
