# ADR-0013: Obstruction-aware usable area from DSM residuals (revises ADR-0003)

- **Status:** Accepted
- **Date:** 2026-09-08
- **Phase:** 2 (Part 2-4 — roof-geometry refinement)
- **Context tags:** usable-area, obstructions, DSM-residual, revises-ADR-0003

## Context
ADR-0003 derates a qualifying roof's packable area by a **flat `× 0.70` utilization fraction** — one
constant lumping edge setbacks, panel spacing, and rooftop obstructions (chimneys, vents, HVAC,
bulkheads). Obstructions are, per architecture §9, "where accuracy pays off," and are the single crudest
factor in the usable-area estimate. The multi-plane DSM fit (ADR-0011) gives us the fitted roof surface,
so obstructions are recoverable **classically** as DSM residuals — with no training data (RoofN3D has no
obstruction labels anyway).

## Decision
Detect obstructions as DSM pixels sitting **more than a threshold τ (≈ 0.5–1.0 m, tunable) above the
fitted plane** — the RANSAC outliers-above. Replace the flat `× 0.70` with **measured** obstruction
subtraction plus a smaller, principled setback factor:

    usable_plane_area = (plane_area − obstruction_area) × setback_factor        # setback_factor ≈ 0.85–0.90

where `setback_factor` covers only edge setback/access. The `usable_area(..., obstructions=…)` parameter
— accepted but unused since Stage 1 (`usable_area.py:63`) — is filled with per-roof obstruction
**polygons** (geometrically test-first, and reusable if obstructions are ever drawn on the map). The
plan's hard qualification cutoffs (slope ≤ 45°, irradiance ≥ 800 kWh/m²/yr, not north-facing) are
unchanged and now applied **per plane**.

## Consequences
- Replaces the single most crude constant in the usable-area estimate with a measured, per-roof quantity —
  the highest-value accuracy lever (ADR-0011).
- τ and `setback_factor` are explicit, inspectable, tunable constants (in the ADR-0003 spirit), not hidden
  — and reported.
- Deterministic → test-first: a fixture roof with a known bump yields a hand-computable obstruction area
  and usable area.
- Revises ADR-0003's `× 0.70`: the utilization fraction is no longer a single flat number but
  (measured obstructions) × (setback factor). Stage-1 outputs computed under the flat 0.70 remain valid as
  the ADR-0003 baseline.

## Alternatives considered
- **Keep the flat `× 0.70`** — rejected: it is the biggest fudge factor and the clearest accuracy win to
  remove; the DSM already contains the obstruction signal.
- **ML obstruction segmentation** — rejected (ADR-0011): no training labels (RoofN3D has none), no
  checkpoint, unvalidatable; the DSM-residual approach needs none of that.
- **A finer flat fraction** (e.g. `× 0.80`) — rejected: still a blind constant; it moves the number
  without measuring the actual obstructed area that varies hugely roof to roof.
