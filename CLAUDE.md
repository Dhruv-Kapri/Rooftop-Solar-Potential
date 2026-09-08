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

**Phase 1 MVP complete (2026-09-07); Phase 2 in progress — Parts 2-1 (aggregation + equity) and 2-2
(city-scale tiling) built + tested (2026-09-07).** The full spine — footprints → DSM → shaded `r.sun`
radiation → RANSAC roof planes → usable area → PV yield + suitability — runs end-to-end for Glover Park
(`scripts/run_stage1.py`) and passes the ADR-0005 benchmark gate. Part 2-1 fills the last stub
(`aggregate.py`), adds the census/equity data-access module (`tracts.py`), and rolls per-roof
results up to census tracts with an equity overlay (`scripts/run_aggregation.py`; ADR-0006/0007/0008).
Part 2-2 scales the spine over the **whole District** by tiling: `tiling.py` (fixed-origin grid + `floor`
membership) + `pipeline.run_city` (per-tile GeoParquet cache, resumable manifest/params-stamp, city-wide
merge + suitability recompute; `scripts/run_city.py`; ADR-0009). Both test tiers are green (unit +
network/GRASS integration smoke). The **expensive full-DC batch run and its real deliverables (the
populated equity map, sanity write-up, compute-cost run-log) are deferred** — 66 tiles @ 2 km, a ~10–15 h
job, run when ready via `scripts/run_city.py`.
**Phase 2 is planned in `docs/plans/stage-2-overview.md`** (5 parts; **Part 2-3 web app is next**).
See `docs/roadmap.md` for phase boundaries/exit criteria and `docs/benchmarks/` for the benchmark
write-up.

## Source of truth

- `docs/reference/Rooftop_Solar_Project_Plan.pdf` — **the full engineering plan; section numbers (§N)
  are cited throughout the docs and code.** When in doubt, this wins.
- `docs/architecture.md` — two modelling modes, provider interface, delivery.
- `docs/data-sources.md` — every data layer, source, access notes (US v1).
- `docs/risks.md` — honest risks + the inter-building shading traps + reference benchmarks.
- `docs/roadmap.md` — phased roadmap.
- `docs/glossary.md` — project-specific domain terms (POA, specific yield, census tract, energy burden, …).
- `docs/plans/` — per-stage implementation plans; `stage-2-overview.md` is the current work map.

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

**LiDAR per-roof** (v1 default, DC) vs **block-model / LOD-1** (heritage + India/Phase-3 fallback,
the mode the SIH prototype ran on). The geometry front-end is swappable; radiation → yield →
aggregation is identical downstream. Detail: `docs/architecture.md §5`.

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
| Census / equity data access | `tracts.py` (TIGER · ACS · DOE LEAD) |
| City-scale tiling grid | `tiling.py` (fixed-origin grid · `floor` membership) |
| City runner (tile → cache → merge) | `pipeline.run_city` (+ `scripts/run_city.py`) |
| Paths / keys / study-area config | `config.py` |

All modules above are implemented and tested. Part 2-1 (Phase 2) filled `aggregate.py` and added
`tracts.py`; `pipeline.run_aggregation` rolls per-roof results up to census tracts with an equity
overlay (see `docs/plans/stage-2-part1-plan.md`, ADR-0006/0007/0008). Part 2-2 added `tiling.py` and
`pipeline.run_city` — the same spine tiled over the whole District, idempotent/resumable per-tile
(ADR-0009). Both tiers green; the full-DC batch + real deliverables are deferred (see Status).
**Part 2-3 (deployed web app) is next.**

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
- **tippecanoe** installed separately (e.g. `brew install tippecanoe`) and callable on PATH — the
  Part 2-3 web build (`scripts/build_web.py`) tiles roofs into PMTiles with it; the `pmtiles` pip
  reader (in `environment.yml`) validates the output in the build smoke. Only needed to (re)build
  the web map's `roofs.pmtiles`, not for the analytics pipeline.
- Lint/format: **ruff**. Tests: **pytest** — the unit tier runs by default; GRASS/network tests are
  marked `integration` and **deselected by default** (`pytest -m integration` to include).
- **Never commit data** — `data/`, `outputs/`, and all `*.tif/*.laz/*.las/*.copc.laz` are
  gitignored. LiDAR/DSM/imagery are huge.
- Secrets in `.env` (gitignored); copy `.env.example` to start.

## Working style

Follow the user's global CLAUDE.md: ask before assuming, simplest solution that fits, don't touch
unrelated code (surface smells separately), flag uncertainty. Cite plan §-numbers when a decision
traces to the plan.
