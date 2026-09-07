# Stage 2 overview — city-scale + ML + deployed app

- **Phase:** 2 (Full — roadmap.md) · **Branch:** `stage-2` · **Date:** 2026-09-07
- **Source of truth:** `docs/reference/Rooftop_Solar_Project_Plan.pdf` (§-numbers); roadmap in
  `docs/roadmap.md`; architecture in `docs/architecture.md`.
- **Detailed plan (now):** [`stage-2-part1-plan.md`](stage-2-part1-plan.md). Parts 2-2…2-5 are
  **tentative** (see "Living-plans protocol").

## 1. What Stage 2 is

Phase 2 is **not one deliverable** — the roadmap bundles ~8 independent big rocks. Trying to plan them
all at TDD depth up front would be speculative fiction. So Stage 2 is **sliced into consecutive
parts**, one plan doc each, ordered by **continuation → end goal**: each part reuses the previous
part's output, and the finished product lands as early as the ordering allows.

**End goal (the anchor):** *a deployed, interactive web map of the whole DC District — every roof
scored, rolled up to census tracts with an equity overlay, using ML-improved roof/obstruction
segmentation, at upgraded radiation fidelity.* Facade BIPV (§10) is a stretch.

## 2. The parts

| Part | Name | Rocks folded in | Continuation — why it sits here | Plan |
|---|---|---|---|---|
| **2-1** | **Aggregation + Equity** | census aggregation · equity overlay | Continues directly from Stage 1's per-roof GeoPackage. Pure geopandas + a census join; fills the one true stub (`aggregate.py`). The `/tdd` opener. | [detailed](stage-2-part1-plan.md) · **✓ built 2026-09-07** |
| **2-2** | **City scale** | full-DC tiling/batching | Reuses the *same* spine + 2-1's aggregation, now over the whole District instead of one bbox. Compute/orchestration, no new analytics. | tentative |
| **2-3** | **Deployed web app** | hosted interactive map | Renders 2-2's District-wide aggregated + equity data. **Reaches the end goal as a complete product** (RANSAC-baseline accuracy). | tentative |
| **2-4** | **ML segmentation** | RoofN3D multi-plane + obstructions | Accuracy upgrade, swapped in behind the *already-wired* `roof_planes.fit_roof_planes(method="ml")` / `usable_area(obstructions=…)` seams. Re-propagated by re-running 2-2. | tentative |
| **2-5** | **Fidelity** | 365-day radiation sum · explicit `r.horizon` (trap 3) | Radiation-engine refinements; both change per-roof numbers, done as one deliberate accuracy pass. | tentative |
| *stretch* | *Facade BIPV (§10)* | — | Optional; only if time remains. | — |

## 3. Ordering rationale + the one tradeoff to accept knowingly

The order is pure continuation: each part consumes the prior part's output. Crucially, **Part 2-3
delivers the finished portfolio product early** (at baseline accuracy) — then 2-4 and 2-5 improve
accuracy *behind stable interfaces* rather than blocking the end goal on the riskiest rock (ML).

**The tradeoff:** putting ML + fidelity *after* the big city-scale run (2-2) means **re-running the
expensive District-wide compute** when accuracy improves. Accepted deliberately (it de-risks and ships
a product fast), but it **requires Part 2-2 be built idempotent / tiled / cached** so a re-run is
incremental, not from scratch, and so the 2-4/2-5 swaps propagate cheaply. The rejected alternative
("perfect the per-roof engine — ML + fidelity — *before* the one expensive scale+deploy") avoids
re-runs but front-loads the riskiest work and delays the end goal, cutting against continuation.

## 4. Living-plans protocol (why the tentative plans are safe to write now)

Parts 2-2…2-5 are written now as **tentative** plans so the whole arc is legible — but they are
**hypotheses, not frozen specs**. Each tentative plan:

1. Opens with a `> **STATUS: tentative** — revise after Part 2-{N-1} is built` banner.
2. Carries its guesses as **explicitly flagged assumptions** (`ASSUMPTION:`), not silent decisions.
3. Ends with a **"Downstream review checkpoint"**: *before starting this part, re-read it against what
   the previous part actually taught us, and revise or rewrite it — including its ADRs — as needed.*

So the rule for whoever builds a part: **when a part is done, review every downstream plan and update
it before starting the next.** A tentative plan going stale is expected and cheap; that's the point.

## 5. Decisions locked for Stage 2 so far (Part 2-1)

| ADR | Decision |
|---|---|
| [0006](../adr/0006-equity-data-source-and-dimension.md) | Equity = **energy burden** (DOE LEAD) + median income (ACS), on **2020 tracts**; de-hosted composites (CEJST/EJScreen) optional only. |
| [0007](../adr/0007-equity-metric-per-household-quadrant.md) | Equity metric = **per-household potential × energy burden** as a **2×2 quadrant** (priority tracts); no composite index. |
| [0008](../adr/0008-lead-energy-burden-tract-aggregation.md) | DOE LEAD per-tract burden = **overall income-weighted ratio** Σ(energy cost×units)/Σ(income×units) over all income bands (AMI file); low-income-specific variant deferred to Part 2-2. |

Also settled this session: parts sliced + ordered by continuation (this doc); roof→tract assignment =
**centroid**; `aggregate.py` = **decomposed pure functions + a pipeline wiring entry**, scale-agnostic;
Part 2-1 runs on **Glover Park** (machinery + smoke, not the analytical payoff — that's 2-2).

## 6. Still Phase 3, do not build here

Provider adapters (`FootprintSource / ElevationSource / ImagerySource / IrradianceSource`) stay
deferred to Phase 3 (CLAUDE.md locked decisions; architecture.md). Stage 2 stays single-city (DC).

## 7. Part 2-1 built — learnings to fold into Part 2-2 (living-plans checkpoint)

Re-read the tentative Part 2-2…2-5 plans against what Part 2-1 taught before starting Part 2-2:

- **Real per-roof column names:** the Stage-1 GeoPackage uses `annual_energy_kwh` / `annual_co2_kg`
  (not `energy_kwh` / `co2_*`). The aggregation reads these; Part 2-2 must too.
- **Glover Park spans 7 DC tracts, not ~2** (the part-1 plan §7.4 guessed ~2). Still thin for a
  meaningful median split — the smoke-not-a-finding framing holds — but the count was wrong.
- **Partial-tract coverage makes per-household potential unreliable at Part 2-1.** The bbox AOI clips
  edge tracts, so a tract barely inside gets a few scored roofs ÷ its full household count → an
  understated per-household number (the real run spans 73–11,468 kWh/hh — the low end is this
  artifact, not a finding). Part 2-2 covers **whole** tracts, so per-household becomes meaningful there
  — a structural reason the equity payoff belongs to 2-2, beyond just "more tracts".
- **Scale-agnostic contract confirmed:** `aggregate_to_tracts` / `attach_equity` / `classify_equity`
  are pure and CRS/extent-agnostic; Part 2-2 reuses them untouched over all DC tracts. `tracts.py`
  loaders already fetch the whole state (DC) and cache under `data/` — Part 2-2's job is the *roof*
  side (tiling/batching the point cloud), not re-doing the tract side.
- **DOE LEAD resolved (ADR-0008):** the burden is an overall income-weighted ratio; if Part 2-2 wants
  the policy-headline *low-income* burden, restrict to the lower `AMI150` bands — a one-line change to
  `aggregate_lead_burden`.
- **`load_energy_burden` is DC-only** (the OpenEI URL is DC-specific). A multi-city Part 2-2/Phase-3
  needs the LEAD URL/state parameterized.

Done this session: the stale `CLAUDE.md` stub note is fixed (aggregate.py filled; tracts.py added).
