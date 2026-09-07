# Stage 2 · Part 2-5 plan (tentative) — Fidelity

> **STATUS: tentative** — a hypothesis written 2026-09-07, before Part 2-4 exists. Per the
> living-plans protocol ([`stage-2-overview.md`](stage-2-overview.md) §4), **re-read and revise this
> before starting it**, against what the earlier parts actually taught. Guesses are flagged `ASSUMPTION:`.

- **Phase:** 2, Part 5 of 5 · **Branch:** `stage-2` · **Overview:** [stage-2-overview.md](stage-2-overview.md)
- **Prerequisite:** Part 2-4 built.

## 1. What this part is

The last part: a radiation-engine fidelity pass, folding in two rocks from the roadmap (§12, Phase 2)
— a **365-day radiation sum** (replacing the 12-day mid-month sample, ADR-0001) and an **explicit
shadow search distance** in `r.horizon`/`r.sun` (trap 3, risks §8 — currently region-bounded rather
than an explicit `maxdistance`). Both change per-roof numbers, so they're done together as one
deliberate accuracy pass rather than trickled in.

**Also folded in here — a third fidelity lever, deferred from Part 2-2 (Q5 grilling, 2026-09-07):**
whether to replace the 2 m Planetary-Computer DSM with a **finer self-derived DSM from raw 3DEP EPT
via PDAL** (Phase-0 finding, risks §1). This is *geometry-input* fidelity (upstream of radiation),
distinct from the radiation-engine fidelity above — but it belongs to the same deliberate accuracy
pass. Part 2-2 deliberately kept Stage-1's 2 m DSM unchanged to stay a pure orchestration part, so the
2 m-vs-finer-DSM compute/accuracy tradeoff lands here.

## 2. Where it continues from

- `radiation.surface_irradiance(dsm_path, day_range=None, shadow_search_distance_m=500.0)` — today
  `day_range=None` defaults to the 12 `MID_MONTH_DAYS`, weighted by `DAYS_IN_MONTH` (ADR-0001, "the
  *only* calibrated annual estimate"); `shadow_search_distance_m` already exists as a parameter but
  trap 3 (risks §8) notes the effective search is currently region-bounded, not an explicit distance
  passed through to `r.horizon`/`r.sun`.
- Part 2-4's ML-segmented, district-wide dataset (this part re-runs on top of whatever 2-4 produced).
- ADR-0001 itself, which this part is expected to revise (not rewrite here — just flag the revision is
  owed, per the task).

## 3. Likely approach (tentative)

- ASSUMPTION: extend `surface_irradiance` to run a full 365-day sum (or a denser-than-12 sample if the
  grilled cost/benefit below says so) rather than the 12 mid-month days.
- ASSUMPTION: set `r.horizon`/`r.sun`'s `maxdistance` explicitly, to a value ≥ the configured shadow
  search distance (`AOI_BUFFER_M` today, possibly a city-scale-adjusted value from Part 2-2's
  findings) — closing trap 3 rather than relying on the region's extent to bound it implicitly.
- ASSUMPTION: validate the new sum against the existing 12-day result on a known AOI (Glover Park)
  before trusting a city-scale re-run — a delta/sanity check, not a new benchmark gate.
- Then re-run city-scale (Part 2-2's tiling/caching, re-invoked) to propagate the fidelity upgrade
  through to the deployed map.

## 4. Key questions to grill when we reach this part

- 365 days vs a denser-but-sub-365 sample (e.g. weekly) — what's the actual cost/benefit, and does the
  12-day result already get "close enough" that 365 isn't worth 30× the compute?
- Compute cost at city scale — with Part 2-2's real per-tile numbers in hand, what does a 30×
  radiation re-run actually cost, and is that affordable?
- Does an `r.horizon` precompute (separate from the per-day `r.sun` call) help amortize the shadow
  calculation across the 365 (or N) days, rather than recomputing horizons every run?
- What exactly needs to change in ADR-0001 — revise it here, or hand that off as a follow-up? (The
  task for this part is to note the revision is owed, not to rewrite the ADR itself.)
- **Finer DSM (self-derived 3DEP/PDAL) vs the 2 m PC DSM** (deferred from Part 2-2): is the resolution
  gain worth the compute + new PDAL code, and does it change roof-plane fits / shading enough to matter?
  Sequence it against the 365-day work — both are re-runs of the same expensive city pass.

## 5. Tentative deliverables

- `radiation.surface_irradiance` supporting the upgraded day-sampling (365 or the grilled alternative)
  and an explicit `maxdistance`.
- A validation note comparing the new sum against the 12-day baseline on Glover Park.
- A city-scale re-run (via Part 2-2's machinery) with the fidelity upgrade applied.
- A flagged, not-yet-written revision to ADR-0001 (status noted as owed; the actual rewrite is a
  follow-up, not blocking this part's completion).

## 6. Risks / dependencies

- **The most expensive re-run in Stage 2** — a 30× (or similar) multiplier on the already-expensive
  Part 2-2 district-wide compute (overview §3's re-run tradeoff, paid a second time after Part 2-4).
- Depends on Part 2-2's caching/tiling actually being cheap to re-invoke, and on Part 2-4's dataset
  being stable enough to re-run against without re-doing the ML pass too.
- If the cost turns out prohibitive, the "denser-but-sub-365" fallback (§4) needs to be a real,
  pre-considered fallback, not a late scramble.

## 7. Downstream review checkpoint

This is the last part of Stage 2 — there is no downstream plan to review. The checkpoint here is to
**update the roadmap (Phase 2 status) and any ADRs this part revised (ADR-0001 at minimum)**, and to
close out the overview's part table (§2) as complete.
