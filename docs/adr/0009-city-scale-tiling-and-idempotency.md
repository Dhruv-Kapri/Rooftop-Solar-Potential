# ADR-0009: City-scale tiling — fixed-origin grid, `floor` membership, idempotent manifest

- **Status:** Accepted
- **Date:** 2026-09-07
- **Phase:** 2 (Part 2-2 — city-scale run over the whole District)
- **Context tags:** tiling, city-scale, conservation, idempotency, orchestration

## Context
Part 2-2 scales the Stage-1 per-roof spine (footprints → DSM → shaded `r.sun` → roof planes → usable
area → yield) from one Glover Park bounding box to the **entire District**. The aggregation side
(Part 2-1) is scale-agnostic and reused **untouched**; the open question is the **roof side** — how to
partition the District for compute, guarantee every building is scored **exactly once**, and make an
expensive multi-hour run **resumable** (the same run Parts 2-4/2-5 re-invoke after an accuracy swap).
Decided in a grilling session, 2026-09-07.

Facts that shaped it (verified against the code):
- `run_stage1` selects "core" footprints inline via `representative_point().within(core)`
  (`pipeline.py:83-86`). `within` **excludes the boundary**, so a rep-point exactly on a shared cell
  edge is dropped — the same class of bug fixed in Part 2-1's aggregation (ADR-0007-era).
- `radiation.surface_irradiance`'s `shadow_search_distance_m` is **not wired** to r.sun; the effective
  shadow reach is the buffered-tile GRASS region = the buffer.
- DC's **Height of Buildings Act** caps building heights (~40–50 m) — no skyscrapers.
- `dsm.build_dsm` writes a **hardcoded** `glover_park_dsm.tif` and never reuses it.

## Decision
1. **Fixed buffered grid.** A regular grid anchored to a **fixed origin** (so cell `(row, col)` is
   stable across runs — cache keys depend on it); each `Tile` carries a `core` cell and a `buffered`
   cell (`core` grown by `TILE_BUFFER_M`).
2. **`floor`-arithmetic membership.** Assign each building to a tile by
   `(row, col) = ⌊(rep_point − origin)/tile_size⌋` → **exactly one** tile *by construction*. **Not**
   `.within` (drops seam points) and **not** a polygon-intersect join (double-counts). This is the
   seam-conservation guarantee.
3. **`TILE_BUFFER_M = 300 m`** (= `AOI_BUFFER_M`). The buffer **is** the effective shadow reach;
   generous given the Height Act. One city-wide value, no downtown special-casing.
4. **Sequential + per-tile cache, resumable.** Each tile's roofs are cached as **GeoParquet**; a **tile
   manifest** records per-tile status + a **params-stamp** (radiation days, DSM source, buffer,
   tile_size, a pipeline version). `resume` skips only tiles whose stamp matches; a bumped stamp forces
   recompute. **Skip-and-continue** on tile failure; empty/no-data tiles record "done, 0 roofs".
   Parallelism deferred (per-worker GRASS-session isolation is the cost).
5. **City per-roof dataset = GeoParquet** (partitioned by tile); the final **tract** output stays
   **GeoPackage** (small, matches Part 2-1, good for the Part 2-3 web-map handoff).

## Consequences
- **Conservation is provable, not probabilistic.** `floor` is a total partition, so "every building in
  exactly one tile" holds by construction — the correctness invariant and its TDD test (including the
  on-seam case `.within` would drop) are deterministic, with no reconciliation pass.
- **The re-run tradeoff (overview §3) becomes real.** A 2-4/2-5 accuracy swap bumps the params-stamp →
  incremental recompute, not a from-scratch re-run and never a silently stale cache.
- **Two small refactors touch Stage-1-tested code:** extract `run_stage1`'s inline core-filter into a
  shared step taking a membership rule (Stage-1 keeps `.within` for its arbitrary bbox; tiling passes
  `floor`), and `build_dsm` gains an `out_path` param (default unchanged). Both must be
  behaviour-preserving for the single-bbox path — done red-green, Stage-1 suite stays green.
- **Fidelity is deliberately unchanged.** Radiation stays 12-day calibrated (ADR-0001) and the DSM
  stays 2 m PC; the finer 3DEP/PDAL DSM is deferred to Part 2-5. Part 2-2 is orchestration, not
  fidelity.

## Alternatives considered
- **Native 3DEP tiles / per-census-tract tiles** — rejected: variable extents; per-tract couples
  compute to census geometry and overlaps buffers heavily. A fixed grid is predictable and reuses the
  Stage-1 buffer/core pattern.
- **Keep `.within` + a reconciliation net** to re-add dropped seam buildings — rejected: strictly more
  machinery (a global footprint diff) to reach the guarantee `floor` gives for free; and the drop is
  measure-zero on real float coordinates anyway.
- **Bare file-exists cache** — rejected: serves stale results when radiation/DSM/code change, breaking
  the re-run promise. The params-stamp fixes it.
- **Parallel from the start** — deferred: per-worker GRASS-session isolation cost; sequential +
  resumable is enough first, with parallelism the documented escape hatch if runtime bites.
