# CLAUDE.md — Rooftop Solar Potential

Orientation for Claude Code sessions in this repo. Read this first, then the plan docs.

## What this is

A city-scale pipeline that scores **every rooftop** for solar suitability: per-roof geometry →
orientation/tilt/shading → irradiance → **PV capacity, annual energy, CO₂ offset**, aggregated to
census tracts with an equity lens. Built from **public data + open-source Python**, delivered as an
**interactive web map**. It is the engineering evolution of a 2024 Grasshopper + Ladybug hackathon
prototype (SIH1739) into a reproducible, city-scale pipeline.

**Framing:** this is a general geospatial-analysis project. The Esri / ArcGIS stack is *one
supported deployment target, not a dependency*. Keep language stack-neutral — don't over-index on
Esri.

## Status

**Phase 0 (scoping / pre-development).** Until now the repo held only the plan + docs; code starts
in Phase 1. See `docs/roadmap.md` for phase boundaries and exit criteria.

## Source of truth

- `docs/Rooftop_Solar_Project_Plan.pdf` — **the full engineering plan; section numbers (§N) are
  cited throughout the docs and code.** When in doubt, this wins.
- `docs/architecture.md` — two modelling modes, provider interface, delivery.
- `docs/data-sources.md` — every data layer, source, access notes (US v1).
- `docs/risks.md` — honest risks + the inter-building shading traps + reference benchmarks.
- `docs/roadmap.md` — phased roadmap.

## Locked decisions (don't re-litigate without the user)

- **Radiation engine: GRASS GIS `r.sun`** (no pure-Python raster solar model exists; GRASS is a
  system dependency, installed/called separately — see risks §14.2).
- **Study area: Washington, DC**; **Phase 1 neighbourhood: Glover Park**, benchmarked against
  Esri's "Estimate solar power potential" tutorial.
- **Irradiance API: NREL → renamed NLR** (National Laboratory of the Rockies, 2025-12-01). Use
  `developer.nlr.gov`; key lives in `.env` as `NLR_API_KEY` (old NREL keys still valid).
- **Provider adapters are deferred to Phase 3.** Do **not** build the
  `FootprintSource / ElevationSource / ImagerySource / IrradianceSource` abstractions now — the plan
  says premature at one US city. They land when a second city (India) creates the real need.

## Two modelling modes (one interface)

Geometry front-end is swappable; radiation → yield → aggregation is identical downstream.

- **LiDAR per-roof** — v1 default, high-res. Clip LiDAR per footprint, fit roof planes (RANSAC
  baseline; ML route is Phase 2). Washington DC.
- **Block-model / LOD-1** — heritage + fallback. Extrude footprints to one height/building. The
  mode the SIH prototype ran on; the India (Phase 3) path where LiDAR is unavailable.

## Pipeline → code map

`src/rooftop_solar/` mirrors the pipeline (README mermaid):

| Stage | Module |
|---|---|
| Building footprints | `footprints.py` |
| DSM from LiDAR | `dsm.py` |
| Solar radiation (with shading) | `radiation.py` (GRASS `r.sun`) |
| Roof-plane / tilt / aspect | `roof_planes.py` |
| Filter usable area | `usable_area.py` |
| PV capacity & annual energy | `yield_pv.py` (PVWatts) |
| Aggregate to census tracts | `aggregate.py` |
| Paths / keys / study-area config | `config.py` |

Modules are currently stubs (raise `NotImplementedError`) — fill them in per phase.

## Correctness invariants — the shading traps (risks §8; easy to get wrong, hard to notice)

1. **Use the DSM, never the DTM** for the radiation pass — the DTM has no buildings, so it silently
   zeroes all inter-building shading.
2. **Buffer the AOI** — buildings on the south edge are shaded by taller ones just outside it.
3. **Set an adequate shadow search distance** in `r.sun`/`r.horizon` — downtown, far tall towers
   still matter.
4. **Don't let PVWatts undo the shading** — it assumes an unshaded horizon; feed it the
   shading-adjusted per-roof insolation (or a shading-loss factor), not the raw NSRDB value.

Roof-plane extraction is the weakest link (errors propagate into kWh/CO₂) — report uncertainty,
don't over-claim precision.

## Dev environment

- **Conda** (geospatial stack: GDAL/PDAL/GRASS-adjacent): `conda env create -f environment.yml`,
  then `pip install -e .` for the `rooftop_solar` package (src layout).
- **GRASS GIS** installed separately and callable on PATH (Phase 0 exit criterion).
- Lint/format: **ruff**. Tests: **pytest**.
- **Never commit data** — `data/`, `outputs/`, and all `*.tif/*.laz/*.las/*.copc.laz` are
  gitignored. LiDAR/DSM/imagery are huge.
- Secrets in `.env` (gitignored); copy `.env.example` to start.

## Working style

Follow the user's global CLAUDE.md: ask before assuming, simplest solution that fits, don't touch
unrelated code (surface smells separately), flag uncertainty. Cite plan §-numbers when a decision
traces to the plan.
