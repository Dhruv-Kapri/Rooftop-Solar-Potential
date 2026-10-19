# ADR-0007: Equity metric — per-household normalization + 2×2 quadrant classification

- **Status:** Accepted
- **Date:** 2026-09-07
- **Phase:** 2 (Part 2-1 — census aggregation + equity overlay)
- **Context tags:** equity, aggregation, methodology, classification, normalization

## Context
With the equity dimension and data fixed (ADR-0006), the overlay still needs a **method**: how to turn
a tract's rooftop-solar potential and its energy burden into something mappable and actionable. Two
sub-choices sit here — how to *normalize* potential for a fair cross-tract comparison, and how to
*combine* potential with burden.

Rooftop-solar benefit accrues to **dwellings**, not individuals, and energy burden is already a
per-household metric — so the natural normalizer is per-household, not per-capita. And ADR-0005 showed
that per-*building* metrics are unstable here (Microsoft footprints merge rowhouses), which rules out
building-count normalization.

## Decision
1. **Normalization:** normalize a tract's **extensive** potential (usable area / capacity / energy) by
   its **occupied housing units** → **per-household potential**. Report **per-capita** as a legible
   secondary. Extensive totals remain the headline "technical potential."
2. **Combination:** a **2×2 quadrant classification** — split the tract set at the **medians** of
   (per-household potential) × (energy burden) → four classes:
   - **High potential + High burden = priority tract** (biggest equity win from rooftop solar),
   - High potential + Low burden,
   - **Low potential + High burden** (needs non-rooftop solutions),
   - Low potential + Low burden.
   Support with a potential-vs-burden **scatter**. **No continuous composite index.**

## Consequences
- **Transparent + actionable:** a four-class choropleth names priority tracts without inventing
  weights; a reader can see exactly why a tract is classified as it is.
- **Shared unit:** per-household potential and per-household energy burden use the same denominator,
  so the two axes are directly comparable.
- **Relative, like ADR-0004:** the median split is defined *over the tract set in the run*. At Glover
  Park's ~2 tracts it is a **smoke test of the machinery, not an analytical result** — the quadrant is
  only meaningful across many tracts, i.e. city-wide (Part 2-2). Stated plainly in outputs.
- Tracts missing an ACS/LEAD match must be **flagged, not defaulted to 0** — a spurious 0 would
  corrupt the medians and misclassify neighbours (a Part-2-1 correctness invariant).

## Alternatives considered
- **Continuous composite equity index** (weighted blend of potential + burden) — rejected: the weights
  are arbitrary and hide their assumptions; consistent with ADR-0006's preference for legible
  variables.
- **Per-capita as the primary normalizer** — rejected: individuals don't own roofs, and it over-counts
  large-household tracts; kept as a secondary for legibility.
- **Per-building normalization** — rejected: Microsoft footprint merging makes per-building metrics
  unstable in dense residential areas (ADR-0005).
