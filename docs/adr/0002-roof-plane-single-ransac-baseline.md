# ADR-0002: Roof planes — single-plane RANSAC baseline with uncertainty flags

- **Status:** Accepted
- **Date:** 2026-09-07
- **Phase:** 1 (MVP — Glover Park)
- **Context tags:** roof-planes, RANSAC, uncertainty, weakest-link

## Context
Real roofs are often gable or hip — multiple facets at different tilt/aspect, not one plane.
Notebook 03's `fit_roof_plane` fits a single plane per footprint via RANSAC (z = a·x + b·y + c),
which averages any multi-facet roof into one tilt/aspect value. Fit quality varies hard across
Glover Park: about 55% of roofs come back low-confidence (inlier ratio < 0.5, or fewer than 20
inlier pixels).

Multi-plane / iterative RANSAC and ML-based roof segmentation are explicitly Phase 2 scope (plan
§9). Roof-plane extraction is separately named the pipeline's "weakest link" (risks §14.4) — its
errors propagate directly into capacity, kWh, and CO₂ downstream, so silently discarding fit quality
would hide exactly the number a reader most needs to see.

## Decision
Stage 1 ships the single-plane RANSAC baseline as-is. It **must** carry per-roof uncertainty
(`inlier_ratio`, `n_px`, `low_confidence` flag) through to the output GeoDataFrame end to end — not
dropped at any stage between roof-plane fitting and the final map/table.

## Consequences
- Honest MVP: the spine (footprint → plane → yield → score) works end-to-end without waiting on
  Phase-2 ML segmentation.
- Downstream numbers are order-of-magnitude, not bankable, for the ~55% of roofs flagged
  low-confidence — and that fact is visible per roof, not buried in an aggregate.
- Deferred to Phase 2: iterative RANSAC (fit → remove inliers → refit on the remainder, to recover
  multi-facet roofs) and ML-based roof/obstruction segmentation (RoofN3D-trained, per roadmap
  Phase 2).

## Alternatives considered
- **Multi-plane fitting now** — rejected: Phase 2 scope: `docs/roadmap.md`, fights "simplest
  solution that fits" for a single-neighbourhood MVP.
- **Block-model / LOD-1** — rejected for DC: that's the sparse-data fallback path reserved for the
  India phase (plan §5, §13), not appropriate where LiDAR is available.
