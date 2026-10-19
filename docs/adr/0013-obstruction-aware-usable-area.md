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
- **Interaction with multi-plane RANSAC — found during the Part 2-4 build (2026-09-08); deferred to
  step-7 tuning (plan §8/§10).** Obstruction detection only sees pixels that stay *outliers* of a fitted
  plane. A superstructure large and planar enough to win **≥ `MIN_PLANE_PX` inlier pixels** (≈32 m² at the
  2 m DSM) is instead fit as its **own plane** by the sequential multi-plane RANSAC (ADR-0011) and, if
  usably oriented, is counted as usable roof rather than subtracted — the opposite of the intent for
  exactly the *largest* obstructions. Small/irregular clutter is still correctly subtracted (the unit
  tests cover that path). `MIN_PLANE_PX` is therefore the tunable knob sitting at the facet-vs-obstruction
  seam: it is explicit/inspectable (`roof_planes.py`), reported, and calibrated on the ~20-roof spot-check
  (whose hand-labelled *obstruction presence* is positioned to reveal escapes). A principled follow-up, if
  the spot-check shows it matters: reclassify a plane lying entirely > τ above and roughly parallel to a
  larger plane beneath it as that plane's obstruction (deferred — new logic + its own test, not built).

## Alternatives considered
- **Keep the flat `× 0.70`** — rejected: it is the biggest fudge factor and the clearest accuracy win to
  remove; the DSM already contains the obstruction signal.
- **ML obstruction segmentation** — rejected (ADR-0011): no training labels (RoofN3D has none), no
  checkpoint, unvalidatable; the DSM-residual approach needs none of that.
- **A finer flat fraction** (e.g. `× 0.80`) — rejected: still a blind constant; it moves the number
  without measuring the actual obstructed area that varies hugely roof to roof.

## Addendum (2026-10-18) — structural facet-vs-obstruction rule (#30)

The Consequences gap above still mattered after the #23 tuning: flat rowhouse roofs with HVAC fit
as 3–4 planes, and `MIN_PLANE_PX` can't close it because the same threshold gates a roof's *first*
plane (raising it makes real gables unfittable; `spot_check/tuning.md`).

**Rule (`roof_planes.fit_planes_multi`).** After the sequential fit, a **later** plane (not the
dominant, largest one) is a raised superstructure, and is dropped, when both hold:

- its inliers' **median** residual against the **dominant** plane is more than
  `RESIDUAL_THRESHOLD_M` (0.5 m, the RANSAC inlier band, equal to τ = `OBSTRUCTION_TAU_M`), i.e. it
  sits above the dominant plane's band over its own footprint; and
- it has fewer than `SUPERSTRUCTURE_MAX_PX` = **25** inliers (≈100 m² at the 2 m DSM).

Any tilt counts. The spot-check's HVAC and overhanging-tree "planes" are fit steep (22–45°), not
flat. There is no refit: the dropped plane's pixels fall to the nearest kept plane in
`obstruction_pixels`, so anything more than τ above it is subtracted as an obstruction. The
dominant plane is never dropped, so the rule can't make a roof unfittable. A convex roof's other
facets (gable, hip) sit *below* the dominant facet's extension, so they never trigger it. A
non-convex roof's small facet that rises away from the dominant one (a valley, butterfly or
sawtooth roof) *does* sit above that extension, and is dropped like a superstructure. Raised
planes of 25 px or more are real raised roof levels (the whole-block Farragut footprints) and are
kept. `SCORING_VERSION` `2-4.2` → `2-4.3`.

**Evidence.** Chosen on the 17 scored spot-check roofs, with ±1 *and* exact agreement reported
(`validation.compare_labels` gains `plane_exact`). Candidates were swept over the cached DSMs in
seconds per setting:

| rule (later planes) | ±1 agree (clamped) | exact | obstr. hit / miss / FP / TN | large-planar flags | escaped m² | unfittable |
|---|---|---|---|---|---|---|
| none (2-4.2) | 12/17 | 9/17 | 14 / 1 / 2 / 0 | 6 | 739 | 0 |
| **raised > 0.5 m, < 25 px (chosen)** | **14/17 (82%)** | **10/17** | 14 / 1 / 2 / 0 | 4 | 590 | 0 |
| raised > 0.5 m, < 40 px | 14/17 | 10/17 | 14 / 1 / 2 / 0 | 3 | 252 | 0 |
| raised > 0.5 m, any size | 13/17 | 7/17 | 14 / 1 / 2 / 0 | 3 | 252 | 0 |
| raised > 1.0 m, < 25 px | 14/17 | 9/17 | 14 / 1 / 2 / 0 | 5 | 641 | 0 |
| raised, near-flat (< 10°) only | 13/17 | 9/17 | 14 / 1 / 2 / 0 | 6 | 401 | 0 |
| relative size < 0.3 × dominant | 15/17 | 11/17 | 13 / 2 / 2 / 0 | 3 | 478 | 0 |
| raised < 25 px + relative < 0.15 | 15/17 | 10/17 | 14 / 1 / 2 / 0 | 4 | 597 | 0 |

- The chosen rule is the smallest one that improves ±1 and exact together without losing an
  obstruction hit. It reuses the inlier band, so its only new constant is the 25 px size cap.
- 40 px scores the same, but it starts collapsing large raised levels: `6_4_246` and `6_4_28`
  each go 4 → 2. 25 px is the conservative cap.
- The ticket's "near-flat" variant misses the steep HVAC and tree planes.
- A relative-size gate scores higher, but it collapses labelled 2-plane roofs and loses an
  obstruction hit (`7_2_1212`'s pitched strip at 0.3). Added to the raised rule at 0.15, its whole
  gain is one roof (`7_2_66`). It is recorded here, not adopted.
- **Known cost:** the front mansard strip on `7_2_1177` (a real facet, 10 px, raised 1.6 m) is
  dropped, 2 → 1 plane. That loses an exact agreement but stays within ±1.
- **Known cost, not measured:** a small rising facet on a non-convex roof (valley, butterfly,
  sawtooth) is dropped the same way (above). No spot-check roof has one. A synthetic valley roof
  (a 200 px facet plus a 20 px facet rising away from it) fits 2 planes without the rule and 1 with it.
- **Side effects, by construction:**
  - `low_confidence` changes meaning at `2-4.3`. A dropped plane no longer counts toward a
    building's inlier fraction, so the flag (inliers < 50%) now also fires on a well-fit dominant
    plane carrying raised clutter. It isn't comparable across `2-4.2` and `2-4.3` runs.
  - Only the dropped plane's pixels that sit more than τ above their nearest kept plane become
    obstruction. The rule tests the *median*, so up to half of them stay usable on the kept plane.
  - A dropped plane still used one of the `MAX_PLANES` fit slots.
  - The drop band is `RESIDUAL_THRESHOLD_M` and the obstruction band is `OBSTRUCTION_TAU_M`. The
    rule relies on them being equal, and a unit test pins that.

**City re-score** (cached DSM + radiation tiers, no `r.sun` reruns): **2,196.1 MW / 2,607 GWh/yr,
1.69× / 1.63×** NREL (was 1.72× / 1.66× at `2-4.2`). Reported, not targeted. No new unfittable roofs.
1,265 roofs lose all usable area (likely: their only usable "facet" was a raised plane), and 5,890 more are
`low_confidence`. Detail in `spot_check/tuning.md` "#30".

**Not settled.** The labels aren't blind (the drafter saw the model panels), only 2 scored roofs
are `has_obstruction=n`, and with 17 roofs one roof is ~6 points. A blind relabel of a subset and
more obstruction-free roofs should confirm the rule before it is tuned further (#23 caveats).
