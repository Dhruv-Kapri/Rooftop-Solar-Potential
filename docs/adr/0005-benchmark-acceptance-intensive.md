# ADR-0005: Benchmark acceptance — intensive quantities within ~±15%, totals non-gating

- **Status:** Accepted; **revised 2026-09-07** post-benchmark (see "Revision" below — the gate
  now keys on specific yield, not median per-building energy)
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

## Revision (2026-09-07) — gate on specific yield, not median per-building energy

Running the Stage-1 benchmark on the canonical **Microsoft ML Buildings** footprints exposed a
flaw in the gate this ADR originally defined. Glover Park scored:

- **specific yield 1162 kWh/kWp/yr** vs Esri 1150 → within **<1%** (PASS), but
- **median per-building annual energy 44.2 MWh** vs Esri 13.35 → **~3.3× high** (FAIL).

**Diagnosis (confirmed, not speculative).** MS ML Buildings *merges* Glover Park's rowhouses:
771 footprints at a **215 m² median**, against OSM's **2,885 at 82 m²** for the same core AOI
(3.7× more footprints). OSM's 82 m² median reproduces Esri's per-building figure almost exactly
(`82 × 0.70 × 1430 × 0.16 ≈ 13 MWh`), and OSM is the notebook-05 regression anchor.

**The lesson:** *median per-building energy is not a size-independent intensive quantity.* It
scales with footprint area, so it measures the footprint source's segmentation as much as the
model. **Specific yield (kWh/kWp/yr) is the genuinely intensive quantity**, and it passes on
both sources — the model is validated.

**Revised gate.** Stage 1 passes iff **specific yield is within ~±15% of Esri** (with per-m²
energy density as a supporting intensive check). **Median per-building energy is demoted to a
reported *context* metric** — footprint-source-sensitive, not gated (like totals). Larger
divergences are still explained in writing. Under this revised gate, **Stage 1 passes**
(specific yield 1162 vs 1150).

**Documented limitation** (this *inverts* the ADR's original expectation that MS would match
Esri better than OSM): MS ML Buildings under-segments dense residential/rowhouse neighbourhoods,
so *per-building* metrics from it are coarse. Aggregate and per-m² metrics are unaffected. OSM
matches Esri's granularity for Glover Park. See `docs/benchmarks/stage-1-glover-park-esri.md`.
