# ADR-0001: Radiation fidelity — 12-day representative-day sampling

- **Status:** Accepted
- **Date:** 2026-09-07
- **Phase:** 1 (MVP — Glover Park)
- **Context tags:** radiation, r.sun, compute-cost, shading

## Context
GRASS `r.sun` computes per-day surface radiation; a true annual insolation sum means running it 365
times (once per day) over the AOI. The plan (§7/§8) describes annual integration only qualitatively
— it does not prescribe a day count or a required fidelity.

Notebooks 02, 04, and 05 already approximate the annual surface with 12 mid-month representative
days, one per month, and sum with a days-per-month weight. A full 365-day run is roughly 30× the
compute of the 12-day sample, for a single-neighbourhood MVP where the plan's own bar is "ship it
even if rough" (roadmap Phase 1).

The risk to guard against: cutting fidelity for compute must not become an excuse to also cut
shading. The two are independent — a 12-day sample can still be a fully shaded surface if each of
the 12 runs uses `r.sun` mode 2 with shadowing on.

## Decision
Stage 1 computes annual insolation from **12 monthly representative-day** `r.sun` runs (one
mid-month day per month, weighted by days-in-month). Every run keeps shading **ON** — `r.sun` mode 2
with shadowing, over the buffered DSM (risks §8 traps 1–2).

## Consequences
- Keeps a full-Glover-Park run tractable on a laptop within the Phase-1 timebox.
- Notebook 05's benchmark shows the resulting per-building estimates land within ~6–8% of Esri's
  published values — the approximation is not free, but it's small enough to pass Stage 1's
  acceptance bar (ADR-0005).
- Documented as a known approximation, not hidden: any consumer of the insolation raster should read
  it as a 12-sample estimate, not a true annual integral.
- Full 365-day summation is deferred to Phase 2 as a straightforward accuracy upgrade — the code
  path doesn't change, only the day list and compute budget.

## Alternatives considered
- **Full 365-day sum** — rejected for Stage 1: ~30× the compute for no accuracy mandate in the plan.
- **pvlib point model** — rejected: it has no raster shadowing, and the plan (§7) requires
  inter-building shading to be modelled, which a point model cannot do.
