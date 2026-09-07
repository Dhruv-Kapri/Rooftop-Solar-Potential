# ADR-0005: Benchmark acceptance — intensive quantities within ~±15%, totals non-gating

- **Status:** Accepted
- **Date:** 2026-09-07
- **Phase:** 1 (MVP — Glover Park)
- **Context tags:** benchmark, Esri, acceptance-criteria, validation

## Context
The plan's Phase-1 bar is qualitative: "reproduce and sanity-check against a published reference;
ship it even if rough" (roadmap Phase 1). There is no numeric tolerance anywhere in the plan, and no
"within X% of Esri" clause — checked against the full plan PDF.

Notebook 05 benchmarks against Esri's Glover Park "Estimate solar power potential" tutorial and shows
a consistent shape: **intensive** quantities (per-unit, size-independent) match well — median annual
energy 14.35 vs. Esri's 13.35 MWh/building, specific yield 1220 vs. 1150 kWh/kWp/yr — while
**totals** (aggregate across all buildings) diverge 1.9–3.1×. The divergence in totals is explainable:
Stage 1 fits one RANSAC plane per roof against Esri's per-pixel suitability model, and the two use
different footprint sources (raw OSM sheds vs. Esri's curated building layer).

## Decision
Stage 1 "passes" its sanity-check if:
1. Median per-building annual energy **and** specific yield land within ~±15% of Esri's tutorial
   values, **and**
2. Every larger divergence — especially in totals — is explained in writing, not left unaddressed.

Totals are reported for context but are explicitly **not** a pass/fail gate.

## Consequences
- An honest, defensible MVP bar that matches the plan's own "ship it even if rough" posture, while
  still being a testable criterion rather than no bar at all.
- Forces divergences to be explained rather than hidden or averaged away.
- Note for the canonical Stage-1 run: it uses Microsoft ML Building Footprints, not the OSM
  footprints the notebooks used — the benchmark must be re-run against MS footprints, and the
  footprint-source component of the totals divergence is expected to shrink once it is.

## Alternatives considered
- **Gate on totals** — rejected: totals are method-dependent (plane-fitting approach, footprint
  source) and not directly comparable across two different pipelines.
- **No defined numeric bar** — rejected: "sanity-check" needs a testable meaning, or Phase 1's exit
  criterion is unfalsifiable.
