# ADR-0012: Per-plane POA — insolation selected per usable plane

- **Status:** Accepted
- **Date:** 2026-09-08
- **Phase:** 2 (Part 2-4 — roof-geometry refinement)
- **Context tags:** radiation, POA, per-plane, roof-planes, coupling

## Context
Today `poa_clear_sky_kwh_m2` is a zonal mean of the `r.sun` raster over the **whole footprint**
(`radiation.zonal_insolation`). A roof with a sun-facing plane and a north-facing plane (or obstructions)
has its usable plane's insolation **diluted** by the low-insolation pixels of the rest — understating the
energy the usable area would actually produce. With multi-plane sequential RANSAC (ADR-0011) we now know
**which pixels belong to which plane** (pixel–plane membership).

## Decision
Compute POA **per usable plane** — the `r.sun` zonal mean over that plane's inlier pixels — and take
per-roof energy as the sum over usable planes of (plane usable-area × plane POA × constants). This
requires `roof_planes` (multiplane) to **expose per-plane pixel membership**, consumed by the zonal step —
a contained new coupling between `roof_planes` and `radiation`. The footprint-mean POA remains for the
single-plane `"ransac"` path.

## Consequences
- The single biggest honest energy-accuracy gain in Part 2-4, and nearly free — pixel–plane membership is
  a byproduct of the multi-plane fit, so per-plane POA is just re-masking the same raster.
- Introduces a `roof_planes → radiation` coupling that did not exist (they were independent, both keyed
  off footprint geometry). Contained but real: the multi-plane fit must run before / feed the zonal step.
- Deterministic → test-first: a fixture roof with two known planes over a synthetic raster yields
  hand-computable per-plane means.
- Reinforces ADR-0011's finding: energy is driven by **insolation on the usable surface**, not by
  tilt/aspect.

## Alternatives considered
- **Keep footprint-mean POA** — rejected: keeps the dilution, forgoing the main accuracy lever the
  multi-plane fit unlocks, for a small plumbing saving.
- **Weight by tilt/aspect analytically** — rejected: the `r.sun` raster already encodes the true tilted,
  shaded insolation per pixel; re-deriving it from plane tilt would discard the shading the DSM pass
  computed.
