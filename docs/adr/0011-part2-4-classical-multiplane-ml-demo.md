# ADR-0011: Part 2-4 — classical multi-plane + obstruction geometry; ML demoted to a bounded demo

- **Status:** Accepted
- **Date:** 2026-09-08
- **Phase:** 2 (Part 2-4 — roof-geometry refinement)
- **Context tags:** roof-planes, multi-plane, RANSAC, ML, obstructions, reframe, supersedes-ADR-0002

## Context
The roadmap named Part 2-4 "ML segmentation" — a RoofN3D-trained multi-plane + obstruction model swapped
behind `fit_roof_planes(method="ml")`, superseding ADR-0002's single-plane baseline. Two grill fact-finds
(2026-09-08) undercut that framing on both value and feasibility.

**Value.** Per-roof energy does **not** come from the RANSAC tilt/aspect. `radiation.zonal_insolation`
takes a zonal mean of the `r.sun` raster (computed on the DSM, which already carries true roof slope), so
the real insolation is captured there; `tilt_deg`/`aspect_deg` feed only the binary usable/`roof_class`
gate, and nothing in the energy math reads them. So multi-plane's tilt gain barely moves energy. The real
accuracy levers are (a) obstruction subtraction replacing the flat `× 0.70` (ADR-0013), (b) per-plane
usable gating, and (c) per-plane POA selection (ADR-0012) — none of which needs ML.

**Feasibility.** RoofN3D is NYC-only, carries no obstruction labels, and ships no usable pretrained
checkpoint; RoofSeg/SPPSFormer/DeepRoofPlane are train-from-scratch (a real CUDA GPU — the dev machine is
an 8 GB M1). No DC ground truth exists, so any ML accuracy claim is unvalidatable — and a from-scratch,
unvalidatable model is not a clean `/tdd` target.

## Decision
Part 2-4 ships a **classical, training-free** geometry upgrade as the production path and the `/tdd` build:

- **Multi-plane sequential RANSAC** (`fit_roof_planes(method="multiplane")`): fit → remove inliers →
  refit on the remainder, ≤ ~4 planes/roof, dropping planes below a min area/inlier count. `"ransac"`
  (single-plane) is kept for the baseline comparison; the `method != "ransac"` guard
  (`roof_planes.py:112`) is lifted.
- **Per-building collapse**: fit multiple planes internally but emit **one row per building** — extensive
  quantities (`usable_area_m2`, `capacity_kw`, `annual_energy_kwh`, `annual_co2_kg`) summed over usable
  planes; `tilt_deg`/`aspect_deg`/`roof_class` from the dominant (largest usable) plane; a new `n_planes`
  column; `low_confidence` from the overall fit; `suitability` recomputed on per-building energy density.
  The downstream contract (aggregate, web_build, the MapLibre map) is unchanged — no schema change, no app
  rebuild (Part 2-3 §10).
- **ML is demoted to a bounded, inference-only demo** behind the pre-wired `fit_roof_planes(method="ml")`
  seam: an existing NYC-trained RoofN3D checkpoint run on a handful of DC roofs + a comparison notebook,
  labelled a NYC-trained *prior* (domain gap noted), non-default, experimental, unvalidated — never the
  shipped accuracy path, never blocking the classical build.

Per-plane POA is ADR-0012; obstruction detection + the usable-area formula are ADR-0013.

## Consequences
- The real accuracy levers are captured **classically** — deterministic, so directly test-first; no
  training, GPU, NYC→DC transfer, or unwinnable-validation problem.
- Blast radius is contained to `roof_planes.py`, `usable_area.py`, `yield_pv.py`, and a per-plane widening
  of the `radiation` zonal step (ADR-0012); nothing downstream moves (verified against the per-roof
  contract map, grill 2026-09-08).
- Validation is honest: DSM self-consistency (multi-plane vs single) + a ~20-roof hand-labelled spot-check
  — indicative, not an over-claimed accuracy number, since DC has no ground truth (risks §14.4).
- The portfolio's ML-segmentation story is kept **honestly** — as a demonstration behind the seam, not an
  over-claimed swap.
- The roadmap/overview "ML segmentation" label for Part 2-4 is reframed to "roof-geometry refinement
  (classical) + ML demo." Supersedes ADR-0002's expectation that Phase-2 ML replaces the single-plane
  baseline.

## Alternatives considered
- **ML multi-plane segmentation as planned** — rejected: high effort, low verifiable payoff (tilt barely
  moves energy; obstructions have no training labels), unvalidatable on DC, not a clean TDD target.
- **Obstructions-only, keep single-plane** — a legitimate minimal fallback (it alone kills the `× 0.70`),
  but leaves the per-plane usable-gating + per-plane-POA gains (ADR-0012) on the table; multi-plane is
  cheap given we already fit one plane.
- **Per-plane rows downstream** — rejected: detonates the schema across yield/aggregate/web_build/the map
  for a per-plane map-viz benefit not currently wanted; the per-building collapse banks Part 2-3 §10.
- **Pivot to a learned suitability/ranking model** (architecture §9 option 2) — rejected here: abandons
  the geometry-accuracy thesis, which is the pipeline's actual weakest link.
