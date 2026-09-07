# ADR-0004: Suitability score — within-AOI percentile rank

- **Status:** Accepted
- **Date:** 2026-09-07
- **Phase:** 1 (MVP — Glover Park)
- **Context tags:** suitability-score, percentile-rank, scoring

## Context
The plan names a "per-building suitability score" (§4, §12) as a deliverable but never defines a
formula for it. Notebook 04 scores each roof as the percentile rank of its annual energy density
(energy per usable m²) among all roofs in the AOI, with unusable roofs (those dropped by ADR-0003's
cutoffs) pinned at 0.

Percentile rank is robust to outliers — a handful of exceptionally large or small roofs can't skew
the scale — but it is defined **relative to the AOI it's computed over**. A score of 0.8 in Glover
Park says nothing about how that roof compares to one in a different neighbourhood or a different
run.

## Decision
Stage 1 scores each roof as its **within-AOI percentile rank of annual energy density**, with
unusable roofs pinned to 0.

## Consequences
- Robust and simple; produces a clean choropleth for a single-neighbourhood MVP map.
- Cost: not comparable across runs or across cities — rescoring a second neighbourhood does not let
  you compare its roofs' scores directly against Glover Park's.
- An absolute-threshold score (e.g. fixed kWh/m² bands, comparable across cities) is Phase 2 scope,
  deferred until there's a second city to actually compare against (plan §13, roadmap Phase 3).

## Alternatives considered
- **Absolute kWh/m² threshold score** — rejected for Stage 1: premature with only one AOI in scope;
  no second city exists yet to make cross-city comparability matter.
