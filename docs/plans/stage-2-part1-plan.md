# Stage 2 · Part 2-1 plan — census-tract aggregation + equity overlay

- **Phase:** 2, Part 1 of 5 · **Branch:** `stage-2` · **Date:** 2026-09-07
- **Overview / part map:** [`stage-2-overview.md`](stage-2-overview.md)
- **Source of truth:** `docs/reference/Rooftop_Solar_Project_Plan.pdf` (§-numbers); `docs/roadmap.md`
- **Decisions locked this session:** [ADR-0006](../adr/0006-equity-data-source-and-dimension.md),
  [ADR-0007](../adr/0007-equity-metric-per-household-quadrant.md)

## 1. What Part 2-1 is

Fill the one remaining stub (`aggregate.py`) so the pipeline **rolls per-roof results up to US Census
tracts** and lays an **equity overlay** (energy burden × per-household solar potential) over them —
completing the project's core framing: *"score every roof → roll up to tracts with an equity lens"*
(architecture.md §3). It runs on Stage 1's existing Glover Park per-roof output.

This is a **promotion-and-extend** phase, not research: aggregation is standard geopandas, and every
decision that had a real trade-off is already an ADR. The work is clean module boundaries + a census
data join + a transparent equity classification, all test-first.

### In scope
- Fill `aggregate.py` as **decomposed pure functions** (roll-up · equity join · classification).
- A new **data-access module** for tract geometry + equity attributes (TIGER / ACS / DOE LEAD).
- A **pipeline wiring entry** + thin CLI → tract GeoPackage + tract choropleth(s).
- Two-tier tests (unit on synthetic tracts + one network-marked integration smoke on Glover Park).
- ADR-0006/0007 + glossary (done this session).

### Explicitly NOT in scope (→ later parts)
- City-scale run across all DC tracts (**Part 2-2** — Part 2-1 is Glover Park only).
- Interactive/hosted web map (**Part 2-3** — static choropleth + GeoPackage this part).
- ML segmentation, 365-day sum, `r.horizon` fidelity (Parts 2-4 / 2-5).
- Any CEJST/EJScreen composite overlay (optional, caveated, deferred — ADR-0006).

## 2. Current state

| Concern | State |
|---|---|
| `aggregate.py` | **Stub** — `aggregate_to_tracts(buildings, tracts)` raises `NotImplementedError("Phase 2")`. |
| Per-roof result | **Exists** — `pipeline.run_stage1` → `outputs/glover_park_roofs.gpkg` (layer `roofs`), working CRS EPSG:6347, with `usable`, `usable_area_m2`, `capacity_kw`, `energy_kwh`, `co2_*`, `suitability`. |
| Tract geometry / equity data | **None in code.** `data-sources.md` scopes TIGER/Line; nothing fetches it, ACS, or DOE LEAD yet. |
| Config | Glover Park AOI + `WORKING_CRS` only; no DC-wide / census constants. |

> Verify the exact per-roof column names against the real GeoPackage during step 1 — this plan uses
> the names the Stage-1 modules write; treat any mismatch as the source of truth, not this doc.

## 3. Decisions locked this session

| Decision | Where |
|---|---|
| Equity dimension = **energy burden** (headline) + median income (secondary); no composite index. | ADR-0006 |
| Data spine = **TIGER 2020** geometry + **ACS 5-year (2023)** + **DOE LEAD 2022**, on **2020 tracts**, joined on zero-padded string `GEOID`. | ADR-0006 |
| Equity metric = **per-household potential × energy burden**, 2×2 quadrant (**priority tract** = high×high); scatter as support. | ADR-0007 |
| Roof→tract assignment = **centroid** point-in-polygon (one roof → one tract). | this session |
| `aggregate.py` = **decomposed pure functions** + a pipeline wiring entry; **scale-agnostic** (Part 2-2 reuses untouched). | this session |
| Part 2-1 AOI = **Glover Park** — machinery + smoke, **not** the analytical payoff (that's Part 2-2). | this session |

## 4. Target architecture — module contracts

Same idiom as the Stage-1 spine: **impure data access** in its own module (like `footprints.py` /
`dsm.py`), **pure transforms** as small composable functions (like `usable_area.py`), and one **wiring
entry** in `pipeline.py` (like `run_stage1`). Each transform is GeoDataFrame/DataFrame in → out, no
hidden globals.

```
scripts/run_aggregation.py                 # NEW thin CLI → pipeline.run_aggregation(...)
src/rooftop_solar/tracts.py                # NEW data access (network): TIGER + ACS + DOE LEAD
    load_tracts(state_fips="11", year=2020)      -> GeoDataFrame  # geometry, GEOID(str), WORKING_CRS
    load_acs(state_fips="11", year=2023)         -> DataFrame     # GEOID, pop, households, med_income
    load_energy_burden(state_fips="11")          -> DataFrame     # GEOID, energy_burden
src/rooftop_solar/aggregate.py             # FILL (pure transforms)
    aggregate_to_tracts(buildings, tracts)       -> GeoDataFrame  # centroid join + per-tract sums/summaries
    attach_equity(tracts_gdf, acs, energy_burden) -> GeoDataFrame # join equity attrs + per-household/per-capita
    classify_equity(tracts_gdf)                  -> GeoDataFrame  # 2x2 quadrant + priority flag
src/rooftop_solar/pipeline.py              # EXTEND
    run_aggregation(buildings=None, ...)         -> GeoDataFrame  # load tracts+equity, compose, export
    write_tract_geopackage(...) / render_tract_choropleth(...)   # mirror the Stage-1 helpers
src/rooftop_solar/config.py                # EXTEND: DC_STATE_FIPS, TIGER/ACS/LEAD vintages, CENSUS_API_KEY, cache paths
```

**`aggregate_to_tracts(buildings, tracts)`** — keep the stub signature. Assign each building to a tract
by **centroid** point-in-polygon (both in `WORKING_CRS`), then per tract compute **extensive** sums
(`usable_area_m2`, `capacity_kw`, `energy_kwh`, CO₂, `n_buildings`, `n_usable`) and **intensive**
summaries (`median_suitability`, `pct_usable`). Unusable roofs are **kept and contribute 0** (they
arrive with `usable_area_m2 = 0` from Stage 1) — never dropped. Returns one row per tract that
contains ≥1 building, tract geometry + `GEOID` preserved.

**`attach_equity(tracts_gdf, acs, energy_burden)`** — left-join `acs` and `energy_burden` on string
`GEOID`; add `population`, `households`, `median_income`, `energy_burden`, and the normalized
`potential_per_household` (= `energy_kwh / households`) + `potential_per_capita`. A tract with **no
match is flagged** (`equity_data_missing = True`), **never defaulted to 0** (ADR-0007 — a spurious 0
corrupts the medians).

**`classify_equity(tracts_gdf)`** — split the tract set at the **medians** of `potential_per_household`
and `energy_burden` → `potential_level` / `burden_level` ∈ {high, low}, a combined `equity_class` (4
classes), and `is_priority` (high potential × high burden). Rows flagged `equity_data_missing` are
excluded from the medians and classified `unknown`.

**`pipeline.run_aggregation(...)`** — mirror `run_stage1`: default `buildings` to the Stage-1
GeoPackage (or accept the in-memory result GDF), load tracts+equity via `tracts.py`, compose the three
transforms, write a tract **GeoPackage** (layer `tracts`) + render choropleth(s). Keyword-only args, a
`write_outputs` flag, lazy Agg matplotlib — same conventions as the existing helpers.

## 5. Data spine — concrete (ADR-0006)

| Layer | Source | Access | Vintage / key |
|---|---|---|---|
| Tract geometry | TIGER/Line (or cartographic-boundary for display) | `www2.census.gov/geo/tiger/…tl_2020_11_tract.zip`, no auth | **2020 tracts**, DC FIPS **11** |
| Demographics | ACS 5-year, Census Data API | `api.census.gov/data/2023/acs/acs5?…&in=state:11` | **2023**, needs free `CENSUS_API_KEY` |
| Energy burden | DOE LEAD 2022 | OpenEI/Zenodo DC ZIP → CSV, no auth | **2022**, 2020-tract-aligned |

Fetch once, **cache under `data/`** (gitignored). `CENSUS_API_KEY` goes in `.env`/`.env.example`
alongside `NLR_API_KEY`. ACS table hints: `B19013_001E` median income, `B01003_001E` population,
`B25003_001E`/`B11001_001E` households. **`GEOID` is a zero-padded string everywhere** (DC = `11001…`).

## 6. Correctness invariants — Part 2-1's "shading traps"

Each silently corrupts the result if wrong; verify explicitly in review + tests:

- [ ] **Conservation.** Every scored roof lands in exactly one tract; per-tract extensive sums add
      back to the AOI totals (± float tolerance). Unusable roofs contribute 0, are not dropped.
- [ ] **GEOID is a zero-padded string** through every load and join — no integer coercion (drops the
      leading `11`).
- [ ] **Tract-vintage alignment.** Geometry, ACS, and LEAD are all **2020 tracts** — no 2010/2020 mix.
- [ ] **CRS.** Centroids + point-in-polygon happen in `WORKING_CRS` (metric); tract geometry is
      reprojected to it before the join.
- [ ] **Missing equity data is flagged, not zeroed** — an unmatched tract must not enter the medians as
      a 0 (ADR-0007).

## 7. Acceptance

No external per-tract benchmark exists (unlike Stage 1's Esri tutorial), so Part 2-1 is accepted on
**invariants + sanity + interpretability**, not a numeric gate:

1. **Conservation invariant** holds (unit test on synthetic data **and** asserted on the real Glover
   Park run).
2. **Sanity:** per-household potential sits in a plausible range against NREL/NLR rooftop
   technical-potential figures (risks §15) — a smell test, not a gate.
3. **Interpretability:** `classify_equity` produces the four classes and identifies priority tracts on
   a run with enough tracts.
4. **Expectation set in writing:** the Glover Park choropleth spans ~2 tracts and is a **smoke test of
   the machinery**, not an equity finding — the populated priority-tract map arrives at Part 2-2.

## 8. Testing strategy (two tiers — matches Stage 1)

1. **Unit (fast, no network):**
   - `aggregate_to_tracts`: synthetic buildings placed in known synthetic tract polygons →
     conservation, correct per-tract sums, centroid-assigns-boundary-straddler-once, unusable
     contributes 0.
   - `attach_equity`: GEOID string join, per-household/per-capita arithmetic, `equity_data_missing`
     flag on an unmatched tract (not 0).
   - `classify_equity`: median-split logic, priority flag, tie handling, `unknown` for missing rows.
2. **Integration (marked `integration`, network):** `tracts.py` loaders reachable for DC; end-to-end
   `run_aggregation` on the real Glover Park per-roof output → conservation holds, deliverables written.

Mark network tests so `pytest` stays green offline (the repo already deselects `integration` by
default and skips GRASS/network — reuse the same marker).

## 9. Deliverables

- New `tracts.py`; filled `aggregate.py`; extended `pipeline.py` + `config.py`;
  `scripts/run_aggregation.py`; `CENSUS_API_KEY` in `.env.example`.
- Per-**tract** GeoPackage (layer `tracts`: geometry + extensive/intensive + equity + `equity_class`).
- Tract **choropleth PNG(s)**: per-household potential, and the equity-quadrant classification;
  optionally the potential-vs-burden scatter.
- Tests (both tiers) green.

## 10. Sequencing (TDD order)

1. **`config.py` + `.env.example`** — census constants + key (tiny, unblocks the rest).
2. **`tracts.py` loaders** — start with a small saved fixture for unit tests; real loaders behind the
   `integration` marker. *Verify GEOID-string + vintage invariants here.*
3. **`aggregate_to_tracts`** — pure, red-green-refactor against synthetic tracts. *Conservation.*
4. **`attach_equity`** — pure. *GEOID join + missing-flag invariants.*
5. **`classify_equity`** — pure. *Quadrant/priority logic.*
6. **`pipeline.run_aggregation` + exports + CLI** — wire it; tract GeoPackage + choropleth(s).
7. **Integration smoke** on Glover Park; assert conservation end-to-end.
8. **Housekeeping** — update roadmap Phase-2 status; note Part 2-1 done in the overview.

**Delegation note (orchestration style):** steps 2–5 are well-scoped, notebook-free pure/data tasks
with clear contracts — good Sonnet subagent tasks, one module per task, reviewed against the invariant
checklist (§6). Steps 6–7 (wiring, the real-data smoke, sanity judgement) stay closer to hand.

## 11. Open risks carried into implementation

- **DOE LEAD / mirror availability.** LEAD is the least Census-native source; if the DC file is
  awkward, income (ACS) alone still gives a defensible equity lens (ADR-0006 keeps it as the
  secondary). Confirm the DC download early (step 2).
- **Thin AOI.** ~2 tracts means the quadrant medians are near-degenerate — a *known* smoke-only
  limitation, not a bug (ADR-0007). Don't over-read the Glover Park map.
- **GEOID coercion** is the single most likely silent bug — pandas/geopandas love to read `11001…`
  as an int. Assert dtype in the loaders and after every join.
- **Downstream review checkpoint (living-plans protocol):** when Part 2-1 is built, re-read the
  tentative Part 2-2…2-5 plans against what it taught (real column names, the scale-agnostic contract,
  cache layout) and revise before starting Part 2-2.
