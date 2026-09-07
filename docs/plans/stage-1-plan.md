# Stage 1 plan — MVP, Glover Park, end-to-end

- **Phase:** 1 (MVP — one DC neighbourhood) · **Branch:** `stage-1` · **Date:** 2026-09-07
- **Source of truth:** `docs/reference/Rooftop_Solar_Project_Plan.pdf` (§-numbers cited below);
  roadmap in `docs/roadmap.md`
- **Decisions locked this session:** ADR-0001 … ADR-0005 in `docs/adr/`

## 1. What Stage 1 is

Turn the exploration spine — already proven end-to-end in the walkthrough notebooks — into real
`src/rooftop_solar/` code that runs the **whole pipeline for Glover Park** and produces a
**per-building suitability score, a static choropleth, and an exported GeoPackage**, sanity-checked
against Esri's "Estimate solar power potential" Glover Park tutorial.

The plan's Phase-1 bar (§12): *"reproduce and sanity-check against a published reference — ship it
even if rough, it proves the whole spine works."* This is a **promotion-and-wiring** phase, not a
research phase: the maths is settled in notebooks `02`–`05`; the work is to move it behind clean
module boundaries, apply the plan's explicit thresholds, and run it at full neighbourhood scale on
the production footprint source.

### In scope
- Fill the four Phase-1 stub modules: `radiation`, `roof_planes`, `usable_area`, `yield_pv`.
- A single end-to-end runner (`pipeline.py` + a thin CLI) that produces the per-building result.
- Static choropleth PNG + per-roof GeoPackage export.
- Re-run the Esri benchmark on the **canonical footprint source** (Microsoft ML Buildings).
- Two-tier tests (unit + one small-AOI integration smoke).

### Explicitly NOT in scope (→ Phase 2)
- Census-tract aggregation (`aggregate.py` stays a Phase-2 stub).
- Interactive / hosted web map (static map + GeoPackage only this phase).
- Multi-plane / iterative RANSAC and ML roof/obstruction segmentation.
- Full 365-day radiation sum.
- Absolute (cross-city) suitability scoring.

## 2. Current state

| Stage | Module | State | Notebook prototype |
|---|---|---|---|
| Footprints | `footprints.py` | **Done** — MS ML Buildings + OSM fallback | `01` (OSM demo only) |
| DSM | `dsm.py` | **Done** — 3DEP `3dep-lidar-dsm`, 2 m, mosaic→reproject→clip | `01` |
| AOI / config | `aoi.py`, `config.py` | **Done** — buffered AOI (300 m), EPSG:6347 | `01` |
| Radiation (shaded) | `radiation.py` | **Stub** → fill | `02`, `04`, `05` (`run_r_sun`, GRASS session) |
| Roof planes | `roof_planes.py` | **Stub** → fill | `03`, `04`, `05` (`roof_points`, `fit_roof_plane`) |
| Usable area | `usable_area.py` | **Stub** → fill | `04` (`classify_roof`, `utilization_fraction`) |
| PV yield + score | `yield_pv.py` | **Stub** → fill | `04` (`pv_yield`, `suitability_score`) |
| Aggregate | `aggregate.py` | **Phase-2 stub** — leave | — (prose only) |

The mature, fully type-hinted versions of every function live in notebooks `04` and `05`; `05`
already ran the full pipeline at neighbourhood scale (4391 footprints) and produced the benchmark
numbers. **Notebook `05` is the regression anchor** — the ported `src/` code should reproduce its
numbers (on the same footprint source) within tolerance.

## 3. Decisions locked this session

Each is recorded as an ADR; the one-liners here are the load-bearing summary.

| ADR | Decision |
|---|---|
| [0001](../adr/0001-radiation-fidelity-12-day-sample.md) | Radiation = **12-day** monthly representative-day sample (shaded), not 365-day. |
| [0002](../adr/0002-roof-plane-single-ransac-baseline.md) | Roof planes = **single-plane RANSAC** baseline; carry `inlier_ratio` / `low_confidence` through to output. |
| [0003](../adr/0003-usable-area-filter-and-pv-constants.md) | Usable area = plan's **hard cutoffs** (slope >45°, irradiance <800, north-facing) **+** 0.70 utilization; explicit PV constants. |
| [0004](../adr/0004-suitability-score-percentile-rank.md) | Suitability = **within-AOI percentile rank** of annual energy density; unusable pinned to 0. |
| [0005](../adr/0005-benchmark-acceptance-intensive.md) | "Pass" = intensive quantities within **~±15%** of Esri; totals reported but **not gated**. |

Also settled: canonical footprints = **Microsoft ML Buildings** (OSM = documented fallback);
deliverable = **static map + GeoPackage** (no web app); porting philosophy = **faithful promotion
first** (reproduce `05`'s numbers), harden second.

## 4. Target architecture — runner + module contracts

One orchestrator, pure stage modules, a single GRASS session owned by the radiation stage.

```
scripts/run_stage1.py            # thin CLI → pipeline.run_stage1(aoi)
src/rooftop_solar/pipeline.py    # NEW orchestrator
    fp        = footprints.load_footprints(aoi)          # done
    dsm_path  = dsm.build_dsm(aoi)                        # done
    insol     = radiation.surface_irradiance(dsm_path)    # NEW: shaded annual insolation raster
    planes    = roof_planes.fit_roof_planes(fp, dsm_path) # NEW: per-roof tilt/aspect + uncertainty
    planes    = radiation.zonal_insolation(planes, insol) # NEW helper: mean shaded insolation per roof
    usable    = usable_area.usable_area(planes)           # NEW: cutoffs + utilization → usable_area_m2
    result    = yield_pv.estimate_yield(usable)           # NEW: capacity/energy/CO₂ + suitability
    → export GeoPackage + render static choropleth
```

**Stage contracts** (keep existing stub signatures where sensible; each stage is
GeoDataFrame/Path in → out, no hidden globals):

- `radiation.surface_irradiance(dsm_path, day_range=None, shadow_search_distance_m=500.0) -> Path`
  — 12 mid-month `r.sun` mode-2 runs (shading ON), summed/scaled to an **annual shaded insolation**
  raster on the shared target grid. Opens **one** GRASS session via a context manager; writes a
  GeoTIFF. Port: `start_grass_session`, `write_geotiff`, `run_r_sun`, the 12-day loop.
- `radiation.zonal_insolation(footprints, insol_path) -> GeoDataFrame` — attach mean shaded
  insolation (kWh/m²/yr) per roof. Port: `zonal_mean_insolation`. (Lives in `radiation.py` because
  it consumes the radiation raster; revisit placement during hardening.)
- `roof_planes.fit_roof_planes(footprints, dsm_path, method="ransac") -> GeoDataFrame` — per-roof
  `tilt_deg`, `aspect_deg`, `inlier_ratio`, `n_px`, `low_confidence`. Port: `roof_points`,
  `fit_roof_plane`. **Must not drop the uncertainty columns** (ADR-0002).
- `usable_area.usable_area(roof_planes, obstructions=None) -> GeoDataFrame` — apply ADR-0003:
  qualify roofs by the three hard cutoffs, then derate qualifying area by the 0.70 utilization
  fraction → `usable_area_m2`. Port: `classify_roof`, `utilization_fraction` (+ add the <800 cutoff).
- `yield_pv.estimate_yield(usable) -> GeoDataFrame` — capacity (kW), annual energy (kWh), CO₂ (kg),
  and the within-AOI percentile suitability score. Port: `pv_yield`, `suitability_score`.

`aggregate.py` is untouched this phase.

## 5. Usable-area filter — concrete spec (ADR-0003)

Two complementary steps; do not conflate them:

1. **Qualify (hard drops, plan §7 step 5):** a roof is unusable if `tilt_deg > 45`, **or** mean
   shaded insolation `< 800 kWh/m²/yr`, **or** it is north-facing (aspect in the northern sector,
   N. hemisphere). Unusable roofs get `usable_area_m2 = 0` and suitability `= 0`.
   > Note: the notebook's "flat < 10°" is a **classification label**, not a drop threshold. The only
   > drop thresholds are the plan's three above. The `<800` cutoff was absent in the notebook — add it.
2. **Derate (packing realism):** for qualifying roofs, `usable_area_m2 = footprint_area × 0.70`
   (utilization fraction — accounts for setbacks, spacing, unsurveyed obstructions).

**PV constants (Stage-1 baselines, documented):** real-sky derate `0.75`, module efficiency `0.20`,
performance ratio `0.80`, `0.20 kW/m²`, CO₂ factor `0.35 kg/kWh`. (A direct PVWatts-v8 API call that
lets NLR own efficiency/PR is a Phase-2 option.)

## 6. Suitability score (ADR-0004)

`suitability_score` = within-AOI **percentile rank** of each roof's annual **energy density**
(kWh per usable m²), scaled 0–100; unusable roofs pinned to 0. Robust to outliers, good for the
single-neighbourhood choropleth. Relative within the AOI — not cross-city comparable (that's Phase 2).

## 7. Acceptance / benchmark (ADR-0005)

Re-run the Esri comparison on **MS ML Buildings** (the canonical source), reproducing notebook `05`'s
method. Stage 1 **passes** iff:

- **median per-building annual energy** within **~±15%** of Esri's tutorial value, **and**
- **specific yield** (kWh/kWp/yr) within **~±15%** of Esri's, **and**
- every larger divergence (especially totals) is **explained in writing** (single-plane RANSAC vs
  Esri per-pixel; residual footprint-source differences).

Totals are reported for context but are **not** a pass/fail gate — they are method-dependent
(notebook `05`: 1.9–3.1×) and not directly comparable. Expect the footprint-source divergence to
**shrink** vs `05` now that we use MS footprints instead of raw OSM (which included sheds/garages).

National sanity check (§15): aggregate intensity should sit in a plausible range against
NREL/NLR rooftop-technical-potential figures — a smell test, not a gate.

## 8. Correctness checklist — the four shading traps (risks §8)

Verify each explicitly in code review; each one silently zeroes inter-building shading if wrong:

- [ ] **Trap 1 — DSM not DTM.** Radiation runs on the DSM (`dsm.build_dsm`), never the bare-earth DTM.
- [ ] **Trap 2 — buffer the AOI.** Radiation uses the **buffered** AOI (`AOI_BUFFER_M = 300`) so
      south-edge roofs see their off-AOI shadow-casters; results clipped back to the core AOI.
- [ ] **Trap 3 — shadow search distance.** `r.sun`/`r.horizon` `maxdistance` set explicitly
      (≥ `shadow_search_distance_m`), not left at a small default.
- [ ] **Trap 4 — PVWatts can't undo shading.** Yield consumes the **shaded** per-roof insolation
      (from `zonal_insolation`), never a raw NSRDB location value.

## 9. Testing strategy

Two tiers (tests currently = one config smoke test):

1. **Unit — pure functions, known answers.** Sign-convention check from notebook `03`
   (synthetic 20° south-facing roof → `aspect_deg ≈ 180`); `classify_roof` / cutoff logic;
   `pv_yield` on a hand-worked roof; `suitability_score` ordering. Fast, no network, no GRASS.
2. **Integration — one small fixed sub-AOI smoke.** Runs the spine end-to-end and asserts the
   headline numbers within tolerance. Marked/skippable (needs GRASS + network); **not** the
   full-scale run.

Full-scale Glover Park stays a **documented manual run** (the benchmark), not a CI job. Mark
network- and GRASS-dependent tests so `pytest` is green offline.

## 10. Deliverables

- Filled `radiation.py`, `roof_planes.py`, `usable_area.py`, `yield_pv.py`; new `pipeline.py` +
  `scripts/run_stage1.py`.
- Per-roof **GeoPackage** (geometry + tilt/aspect/uncertainty/usable area/capacity/energy/CO₂/score).
- Static **choropleth PNG** (suitability) + a benchmark comparison table vs Esri.
- Tests (two tiers above) green.
- ADR-0001…0005 + glossary (this session).

## 11. Sequencing

Ordered so each step unblocks the next and the benchmark closes it out:

1. **`radiation.py`** — `surface_irradiance` (12-day shaded) + `zonal_insolation`; GRASS session
   context manager. *Verify traps 1–3.*
2. **`roof_planes.py`** — `fit_roof_planes` with uncertainty columns.
3. **`usable_area.py`** — cutoffs + utilization (ADR-0003).
4. **`yield_pv.py`** — capacity/energy/CO₂ + suitability. *Verify trap 4.*
5. **`pipeline.py` + `scripts/run_stage1.py`** — wire the runner; GeoPackage + static map export.
6. **Tests** — unit tier alongside each module; integration smoke after step 5.
7. **Benchmark** — full Glover Park run on MS footprints; fill the comparison table; apply the
   ADR-0005 gate; write up divergences.

**Delegation note (per orchestration style):** steps 1–4 are well-scoped ports with a notebook
reference each — good Sonnet subagent tasks, one module per task, reviewed against the notebook
source and the trap checklist. Steps 5–7 (wiring, benchmark judgement) stay closer to hand.

## 12. Open risks carried into implementation

- **Roof-plane weakest link (risks §14.4).** Single-plane fit averages gable/hip facets; 55%
  low-confidence in `05`. Mitigation: uncertainty columns surfaced per roof; don't over-claim.
- **Mixed DSM vintage.** Buffered AOI mosaics 2018 + 2014/15 tiles (roadmap Phase-1 finding) — a
  documented bias; re-check if construction post-dates a tile.
- **GRASS session overhead (risks §14.2).** System dependency, scripted per run; the context-manager
  session is the main integration risk in step 1.
- **12-day approximation (ADR-0001).** Monthly sampling, not a true annual sum — stated plainly in
  outputs; 365-day is the Phase-2 upgrade.
