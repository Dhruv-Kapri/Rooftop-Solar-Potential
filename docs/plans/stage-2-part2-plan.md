# Stage 2 · Part 2-2 plan — city-scale run over the whole District

- **Phase:** 2, Part 2 of 5 · **Branch:** `stage-2` · **Date:** 2026-09-07
- **Overview / part map:** [`stage-2-overview.md`](stage-2-overview.md)
- **Source of truth:** `docs/reference/Rooftop_Solar_Project_Plan.pdf` (§-numbers); `docs/roadmap.md`
- **Builds on:** Part 2-1 ([`stage-2-part1-plan.md`](stage-2-part1-plan.md)) — the scale-agnostic aggregation.
- **Decisions locked this session:** tiling = **fixed buffered grid**; orchestration = **sequential +
  per-tile cache (resumable/idempotent)**; radiation = **calibrated 12-day per tile** (ADR-0001).
  **Grilled + hardened 2026-09-07** — the seam/idempotency/failure contracts in §3/§5 are the result.

> **STATUS: built + tested (2026-09-07)** — `tiling.py` + `pipeline.run_city` + `scripts/run_city.py`
> land the tiling / per-tile cache / merge / manifest spine; unit **and** network/GRASS integration
> tiers are green (seam conservation + idempotency verified on a real multi-tile DC run). **As-built
> facts (measured):** DC splits into **66 tiles @ 2 km** (`GRID_ORIGIN=(315000, 4295000)`); the
> integration smoke ran ~6 single-day tiles ×2 in ~31 min (≈4–5 min/tile, DSM-fetch + `r.sun` bound —
> so the calibrated full-DC batch is a ~10–15 h job). **Two additions beyond this plan:** the ADR-0004
> suitability percentile is **recomputed city-wide after merge** (`pipeline.recompute_city_suitability`
> — per-tile would silently be *within-tile*; a gap this plan missed, settled with the user), and an
> **empty-tile short-circuit** skips the DSM/`r.sun` work when no footprint floors into a tile's core
> (the common case — DC is 43% of its bbox). **Deferred:** the expensive full-DC batch run (step 7) and
> its real deliverables (populated equity map, sanity write-up, compute-cost run-log) — run when ready
> via `scripts/run_city.py`. Original planning history below.

## 1. What Part 2-2 is

Scale the **Stage-1 per-roof spine** (footprints → DSM → shaded `r.sun` → roof planes → usable area →
yield) from one Glover Park bounding box to the **entire District of Columbia**, by running it over a
**fixed buffered grid of tiles**, then feeding the merged city-wide per-roof result into the
**already-built, scale-agnostic Part 2-1 aggregation** to produce the **real city-wide equity map**.

This is **compute + orchestration, not new analytics** (overview §2). The aggregation side
(`aggregate.py` / `tracts.py`) is reused **untouched** — it already ran over all 206 DC tracts during
Part 2-1's real conservation check. Part 2-2's whole job is the **roof side at scale**: tile, cache,
merge, resume — and thereby resolve Part 2-1's two smoke limitations (thin AOI, partial-tract
coverage) so the equity quadrant becomes **meaningful** (many tracts, whole-tract coverage).

It is also **the expensive re-run** that Parts 2-4/2-5 pay for again later (overview §3): an accuracy
swap (ML segmentation, `r.horizon`) re-runs this whole city pass. So **build quality matters more than
logic here** — get idempotency/caching wrong and every later accuracy pass becomes a from-scratch
re-run instead of an incremental one. That is the reason sequential-but-resumable is a deliberate
choice, not a shortcut.

### In scope
- A **tiling module** (`tiling.py`): split the DC AOI into a regular grid of **buffered** tiles.
- A **city runner** (`pipeline.run_city`): run the Stage-1 spine per tile, **cache each tile's roof
  GeoParquet**, skip already-done tiles (manifest params-stamp), merge → city per-roof result.
- **Reuse `run_aggregation`** over the merged city roofs → the populated tract equity map.
- Idempotency + a **tile manifest** (per-tile status).
- Two-tier tests (unit on grid/merge + conservation; integration smoke on a small tile patch).
- ADR-0009 (tiling + inter-tile shading + idempotency).

### Explicitly NOT in scope (→ later parts)
- Hosted interactive web map (**Part 2-3**).
- ML roof/obstruction segmentation (**Part 2-4**).
- 365-day radiation sum, explicit `r.horizon` (**Part 2-5**).
- **Parallelism** — sequential first (deferred; the documented escape hatch if runtime bites).

## 2. Current state

| Concern | State |
|---|---|
| Stage-1 spine | **Runs per bbox AOI** — `run_stage1(core=…, buffered=…)` already takes the two AOIs and reports only whole footprints whose representative point lies in the core (the seam rule Part 2-2 reuses). |
| Aggregation | **Scale-agnostic + done** — `aggregate_to_tracts/attach_equity/classify_equity` + `run_aggregation` ran over all 206 DC tracts in Part 2-1. Reused untouched. |
| Tiling / city runner | **None.** No grid, no per-tile cache, no resumable orchestration. |
| Config | Glover Park AOI only; no DC-wide extent, no tile constants. |
| DC data volume | **Unknown — confirm in step 1** (point-cloud size, tile count, fetch/compute time). |

## 3. Decisions locked this session

| Decision | Rationale |
|---|---|
| Tiling = **fixed buffered grid** (regular ~1–2 km cells; buffer each ≥ the r.sun shadow search distance, clip roofs to the core cell). | Directly reuses the Stage-1 AOI buffer/core-clip pattern for inter-tile shading (risks §8 trap 2, one scale up) — predictable, simple, proven. |
| Orchestration = **sequential + per-tile cache**, resumable/idempotent. | Simplest thing that satisfies the overview §3 idempotency requirement; a re-run resumes. Slow (batch job, not CI); parallelism deferred. |
| Radiation = **calibrated 12-day per tile** (ADR-0001, unchanged). | Consistency with the Esri-benchmarked Stage-1 model; the honest choice. |
| AOI = **whole DC** = union of the TIGER 2020 tracts (`tracts.load_tracts().union_all()`). | Reuses the census geometry already loaded; a clean, reproducible District boundary. |
| City per-roof dataset = **GeoParquet** (partitioned by tile); the final **tract** output stays **GeoPackage** (small, matches Part 2-1, good for the Part 2-3 web-map handoff). | Columnar/partitioned format suits a ~city-scale (~10⁵-building) dataset and incremental per-tile writes far better than one monolithic GeoPackage. GeoParquet confirmed available (geopandas 1.1.4 + pyarrow). |
| **Tile membership = `floor`-arithmetic** on the representative point against a **fixed grid origin** — `col = ⌊(x−oₓ)/size⌋`, `row = ⌊(y−oᵧ)/size⌋` — **not** `.within`. | A regular grid's cell index is arithmetic; `floor` is total → every building lands in **exactly one** tile *by construction* (no drops, no doubles, no reconciliation pass). Avoids `run_stage1`'s `.within` boundary-drop hazard (the Part 2-1 bug) at ~90× the seam length. |
| `Tile` = frozen dataclass `(row, col, core, buffered)`; grid **anchored to a fixed origin**. | Stable `(row, col)` ⇒ stable cache keys across runs — idempotency depends on it. |
| Idempotency = **tile manifest + a params stamp** (radiation days, DSM source, buffer, tile_size, a pipeline version); `resume` skips only tiles whose stamp matches. | Makes the §1 re-run promise real: a 2-4/2-5 accuracy swap bumps the stamp → incremental recompute, not silent staleness (a bare file-exists check would serve stale results). |
| Per-tile failure = **skip-and-continue + retry-on-rerun** (recorded in the manifest); empty/no-data tiles = "done, 0 roofs". | A ~52-tile, multi-hour batch can't abort on one bad tile; empty tiles are the *common* case (DC is 43% of its bbox). |
| DSM = **reuse Stage-1's 2 m PC DSM unchanged**; the finer 3DEP/PDAL DSM is **deferred to Part 2-5** (recorded there). | Part 2-2 is orchestration, not fidelity. |
| `build_dsm` gains an **`out_path` param** (default unchanged, so Stage-1 is untouched); `run_city` passes a per-tile path — the DSM stays **transient** (the roof parquet is the resume cache). | Otherwise the hardcoded `glover_park_dsm.tif` is overwritten every tile under a Glover-Park name. |
| `TILE_BUFFER_M = 300 m` (= `AOI_BUFFER_M`); one city-wide value, no downtown special-casing. | The buffer **is** the effective shadow reach — `surface_irradiance`'s `shadow_search_distance_m` is *not wired* to r.sun, so reach = the buffered-tile GRASS region. DC's Height-of-Buildings Act caps buildings ~40–50 m, so 300 m is generous. |

Formalized in **[ADR-0009](../adr/0009-city-scale-tiling-and-idempotency.md)** (tiling grid +
fixed-origin `floor` membership + inter-tile shading buffer + idempotent manifest/params-stamp cache +
GeoParquet city dataset). One GRASS session is owned per tile (GRASS locations are not trivially shared
across runs), so tiles are independent units of work.

## 4. Target architecture — module contracts

Same idiom as the Stage-1 spine: a **pure** grid module, a **wiring** runner in `pipeline.py`, reusing
every existing stage per tile.

```
scripts/run_city.py                        # NEW thin CLI → pipeline.run_city(...)
src/rooftop_solar/tiling.py                # NEW pure geometry
    @dataclass(frozen=True) Tile(row, col, core: Polygon, buffered: Polygon)
    make_grid(aoi, tile_size_m, buffer_m, origin) -> list[Tile]  # fixed-origin cells covering aoi
    tile_index(point, tile_size_m, origin)        -> (row, col)  # floor-arithmetic membership
src/rooftop_solar/dsm.py                    # EXTEND: build_dsm(aoi, …, out_path=None)  # default unchanged
src/rooftop_solar/pipeline.py              # EXTEND
    run_city(aoi=None, tile_size_m=…, output_dir=…, resume=True) -> GeoDataFrame
        # for each tile (skip if manifest params-stamp matches):
        #   score its core footprints — those whose floor(rep_point)==(row,col) — via the
        #   Stage-1 stages over the BUFFERED extent; cache → data/tiles/tile_<r>_<c>_roofs.parquet
        # merge all tile roofs → city per-roof GeoDataFrame (exactly-once by floor membership)
    run_aggregation(buildings=<city roofs>, …)             # REUSED unchanged → equity map
src/rooftop_solar/config.py                # EXTEND: DC_AOI source, TILE_SIZE_M, TILE_BUFFER_M=300, GRID_ORIGIN, tile cache dir
```

**`make_grid(aoi, tile_size_m, buffer_m, origin)`** — a pure function returning `Tile`s (frozen
dataclasses) that **cover** `aoi` from a **fixed origin** with **no gaps and non-overlapping cores**;
each tile carries a `core` cell and a `buffered` cell (`core` grown by `buffer_m`). Tiles whose core
does not intersect `aoi` are dropped. In `WORKING_CRS` (metric), so `tile_size_m`/`buffer_m` are
metres. `tile_index(point, …)` is the companion membership function — `⌊(pt−origin)/tile_size⌋` — that
maps any point to exactly one `(row, col)`; `make_grid` and `tile_index` share the same `origin`, so
the grid and the membership rule can never disagree.

**`run_city(...)`** — mirror `run_stage1`'s conventions (keyword-only, lazy Agg, `output_dir`). For
each tile whose manifest params-stamp does not already match: run the Stage-1 stages over the
`buffered` extent, but select **core footprints by `floor` membership** — those whose
`tile_index(rep_point) == (row, col)` — **not** `run_stage1`'s inline `.within` (which would drop a
rep-point on a shared seam). This means a **small extract** of `run_stage1`'s inline core-filter
(`pipeline.py:83-86`) into a shared step both paths use (Stage-1 keeps `.within` for its arbitrary
bbox; tiling passes `floor` membership). Cache the tile's roofs to
`data/tiles/tile_<r>_<c>_roofs.parquet`; pass a per-tile `out_path` to `build_dsm` (DSM transient).
Merge all tile roofs into one city GeoParquet — exactly-once holds by construction — then call the
existing `run_aggregation` on it. A **tile manifest** records per-tile status (pending/done/failed) +
params-stamp so a crashed or accuracy-swapped run resumes/recomputes only what it must.

## 5. Correctness invariants — Part 2-2's "shading traps"

Each silently corrupts the city result if wrong; verify in review + tests:

- [ ] **Inter-tile shading.** DSM + `r.sun` cover the **buffered** tile, so tall casters just outside a
      tile still shade its edge roofs — the inter-building/AOI trap (risks §8 traps 2–3), one scale up.
      The **buffer *is* the shadow reach** (`surface_irradiance`'s `shadow_search_distance_m` is not
      wired to r.sun; reach = the buffered-tile GRASS region), so `TILE_BUFFER_M = 300 m` must exceed
      the longest relevant shadow — generous given DC's Height-Act building heights (~40–50 m).
- [ ] **Seam conservation — every DC building scored exactly once.** Membership is `floor`-arithmetic
      on the representative point against the fixed grid origin (`tile_index`), so every building maps
      to **exactly one** `(row, col)` *by construction* — no seam double-count, none dropped. **Not**
      `.within` (which drops a rep-point exactly on a shared cell edge — the Part 2-1 bug). The merge
      asserts Σ per-tile roof counts == the city roof count == the footprints-in-DC count.
- [ ] **Idempotency / determinism.** A re-run skips cached tiles and reproduces an **identical** city
      result; tile processing order does not change the output.
- [ ] **CRS.** Every tile's roofs are in `WORKING_CRS`; the merge does not mix CRSs.
- [ ] **Conservation into aggregation.** City extensive totals = Σ over tiles = Σ over tracts (the
      Part 2-1 invariant, now city-wide).

## 6. Acceptance

No external city-wide benchmark exists; accepted on **invariants + sanity + the payoff**, like Part 2-1:

1. **Seam conservation** holds — unit test on a synthetic grid (a seam-straddling building scored once)
   **and** asserted on a real multi-tile run.
2. **Idempotency** — running twice yields an identical city result; cached tiles are skipped (test).
3. **The payoff:** the real city run produces the **populated** tract equity map — many tracts, whole
   tracts covered, so per-household potential is meaningful (Part 2-1's partial-coverage artifact
   resolved). The quadrant medians and priority tracts are now a real finding, not a smoke.
4. **Sanity:** DC totals (capacity/energy/CO₂, per-household potential) sit in a plausible range vs
   NREL/NLR DC rooftop technical-potential figures (risks §15) — a smell test, not a gate.
5. **Runtime documented** — the full-DC run is a batch job (not CI), with tile count + wall-clock noted.

## 7. Testing strategy (two tiers — matches Stage 1 / Part 2-1)

1. **Unit (fast, no network):**
   - `make_grid`: covers the AOI, non-overlapping cores, buffer applied, off-AOI tiles dropped, **fixed
     origin** (same cells regardless of how the AOI is framed).
   - `tile_index` (floor membership): a point strictly inside → its cell; a point **exactly on a shared
     seam / corner** → exactly one deterministic cell — the exactly-once guarantee, the case `.within`
     would drop.
   - merge/conservation: synthetic per-tile roofs across a seam → each building counted once, city
     total = Σ tiles.
   - manifest params-stamp: a matching stamp skips a tile; a changed stamp (e.g. radiation days) forces
     recompute (idempotency + correct invalidation).
2. **Integration (marked `integration`, GRASS + network):** a small **2×2 tile patch** of DC
   end-to-end → seam conservation + **idempotency** (run twice, identical), deliverables written.

Full-DC is a documented manual batch run (like Stage-1's 12-day benchmark), not a CI job.

## 8. Deliverables

- New `tiling.py`; extended `pipeline.py` (`run_city` + tile cache/manifest) + `config.py`;
  `scripts/run_city.py`; ADR-0009.
- **City-wide** per-roof **GeoParquet** + the **real** tract equity GeoPackage + choropleths + scatter
  (the actual Phase-2 payoff — the populated priority-tract map).
- A short sanity write-up vs NREL DC rooftop technical potential (`docs/benchmarks/`), **plus a
  compute-cost run-log** (tile count, wall-clock, per-tile cost) — Parts 2-4/2-5 budget re-runs against
  it.
- Tests (both tiers) green.

## 9. Sequencing (TDD order)

1. **config** — `DC_AOI` (tract union), `TILE_SIZE_M`, `TILE_BUFFER_M = 300`, `GRID_ORIGIN`, tile cache
   dir. *Confirm real DC point-cloud volume + a sensible `TILE_SIZE_M` with a quick experiment here
   (~52 tiles @2 km is the starting point).*
2. **`tiling.make_grid` + `tile_index`** — pure, red-green against synthetic AOIs. *Coverage /
   non-overlap / fixed origin / floor membership incl the on-seam case.*
3. **Small enabling refactors** — extract `run_stage1`'s inline core-filter (`pipeline.py:83-86`) into
   a shared step taking a membership rule (Stage-1 `.within`, tiling `floor`); add `build_dsm(out_path=…)`
   (default unchanged). *These touch Stage-1-tested code — red-green carefully, keep Stage-1 green.*
4. **Per-tile cache + merge** — floor-membership core selection + the manifest/params-stamp; seam
   conservation red-green on synthetic tiles.
5. **`pipeline.run_city`** — wire sequential + resumable + skip-and-continue; integration smoke on a
   2×2 DC tile patch.
6. **Idempotency** — run-twice test (identical result, stamp-matched cache hits; a changed stamp
   recomputes).
7. **Full-DC batch run** → the real per-roof + equity deliverables; capture runtime + tile count.
8. **Sanity + housekeeping** — NREL cross-check; write ADR-0009; update roadmap/overview/README + the
   real city map.

**Delegation note:** steps 2–4 (pure grid + membership + merge, clear contracts + invariants) are good
Sonnet subagent tasks, reviewed against §5; step 3's Stage-1 refactor stays closer to hand (it touches
tested code). Steps 5–7 (wiring, the real batch run, sanity judgement) stay closer to hand.

## 10. Open risks carried into implementation

- **DC point-cloud volume / 3DEP availability + fetch time** — the biggest unknown; confirm size and a
  workable tile size in step 1. May need local disk management (per-tile DSM/roof caches are sizeable;
  gitignored).
- **Runtime** — calibrated 12-day `r.sun` × N tiles could be many hours sequentially. Accepted as a
  batch job; **parallelism is the deferred escape hatch** if it bites (per-worker GRASS-session
  isolation is the cost).
- **Tile-size tuning** — too small = many GRASS invocations (overhead); too big = memory pressure. Pick
  from the step-1 experiment, not a guess.
- **DSM vintage seam city-wide.** Glover Park alone already mosaics 2018 and 2014/15 3DEP tiles
  (roadmap Phase-1 finding); across the District the seam grows. Decide whether it's documented
  per-tile or handled specially — a data-quality risk to surface, not silently average over.
- **DSM source — decided (was a risk).** Reuse Stage-1's 2 m PC DSM unchanged; the finer 3DEP/PDAL DSM
  is deferred to Part 2-5 (recorded there). No longer an open risk for this part.
- **Buffer sizing — resolved (was a risk).** `TILE_BUFFER_M = 300 m` is generous given DC's Height-Act
  building heights, and the buffer *is* the shadow reach (no separate wired search distance). No
  downtown special-casing needed.
- **Touching tested Stage-1 code.** The core-filter extract (`run_stage1`) and `build_dsm(out_path=…)`
  modify modules Stage-1 tests cover — do them red-green and keep the Stage-1 suite green (they must be
  behaviour-preserving for the single-bbox path).
- **Runtime forces scope.** The full-District compute budget is unknown until measured; it may force a
  scope cut (fewer tiles) back into this plan.
- **MS footprint rowhouse merging** (ADR-0005) persists at scale — per-*building* metrics stay
  unstable, but aggregates / specific yield / the **per-household** equity metric are robust to it.
- **Downstream checkpoint (living-plans):** when Part 2-2 is built, re-read the tentative Part 2-3…2-5
  plans against what it taught (real tile counts, runtime, storage format, city-scale data quirks)
  before Part 2-3 — 2-3 needs the real dataset scale/format, 2-4/2-5 the real per-tile re-run cost.
