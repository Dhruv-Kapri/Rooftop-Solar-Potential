# ADR-0003: Usable-area filter + PV yield constants

- **Status:** Accepted
- **Date:** 2026-09-07
- **Phase:** 1 (MVP — Glover Park)
- **Context tags:** usable-area, PV-constants, PVWatts, reconciliation

## Context
Plan §7 step 5 gives hard usable-area cutoffs: drop slope > 45°, drop irradiance < 800 kWh/m²/yr,
drop north-facing surfaces (northern hemisphere). Notebook 04 instead built a classifier — flat
(< 10° tilt), pitched sun-facing (90–270° aspect), pitched north-facing — and applied a flat 0.70
utilization fraction to derate the packable area of qualifying roofs. It did not apply the plan's
45° or 800 cutoffs.

The plan leaves PV constants (efficiency, derates, performance ratio) inside PVWatts as an external
model. The notebook instead hardcoded explicit constants: real-sky derate 0.75, module efficiency
0.20, performance ratio 0.80, 0.20 kW/m² module power density, and a CO₂ factor of 0.35 kg/kWh.

These two things — the plan's cutoffs and the notebook's classifier/utilization fraction — answer
different questions (which roofs qualify, vs. how much of a qualifying roof is packable) and are not
actually in conflict.

## Decision
Use both, composed in this order:
1. **Qualification** — apply the plan's three hard cutoffs to decide which roofs qualify at all:
   drop slope > 45°, drop irradiance < 800 kWh/m²/yr, drop north-facing.
2. **Packing** — apply the notebook's 0.70 utilization fraction to derate the usable area of roofs
   that qualify.

The notebook's "flat < 10°" is a **classification label** for reporting/visualization only — it is
**not** a drop threshold. The only drop thresholds are the plan's: slope > 45°, irradiance < 800
kWh/m²/yr, north-facing. The 800 kWh/m²/yr cutoff (absent from the notebook) is added for Stage 1.
The notebook's explicit PV constants (0.75 / 0.20 / 0.80 / 0.20 kW/m² / 0.35 kg CO₂/kWh) are adopted
as documented Stage-1 baselines.

## Consequences
- Reconciles the plan's cutoffs with the notebook's more realistic area-packing model instead of
  picking one and discarding the other.
- Constants are explicit and inspectable in code, not hidden inside an external model call.
- Results remain Stage-1 baselines — order-of-magnitude, consistent with ADR-0002's uncertainty
  posture upstream.
- A direct PVWatts-v8 API call (letting NREL/NLR own efficiency and performance ratio internally) is
  left as a Phase-2 option, once cross-checking against an external model's constants is worth the
  added API dependency.

## Alternatives considered
- **Notebook classifier alone, no cutoffs** — rejected: silently ignores the plan's explicit,
  numbered drop thresholds.
- **Plan cutoffs alone, no utilization fraction** — rejected: overstates usable area by assuming
  100% of a qualifying roof is packable, losing the notebook's more realistic area packing.

## Addendum (2026-11-14) — 30 m² minimum usable area + a recorded "why not suitable" reason (#40)

**Fourth cutoff.** A building now qualifies only if its total `usable_area_m2` (after obstruction
subtraction, setback and the flat-roof GCR; ADR-0013) is at least `MIN_USABLE_AREA_M2` = **30 m²**.
This matches the Esri tutorial's `Suitable_Buildings` filter (suitable roof area ≥ 30 m²), the
ADR-0005 benchmark. Strictly below 30 fails; exactly 30 passes. It is a **building-level** gate, so it
runs after the per-plane cutoffs: inside `usable_area()` on the single-plane `ransac` path (one row
per footprint) and inside `yield_pv.collapse_to_buildings` on the `multiplane` path, before
`suitability` is ranked. A gated building is `usable = False`, with usable area, capacity, energy and
CO₂ set to 0, so it drops out of the suitability percentile population (ADR-0004).

Measured on `12day_2-4.3` before the change: it drops 5,204 of 65,779 usable roofs (7.9%), worth
1.1% of city capacity; the dropped roofs have a median capacity of 4.8 kW. The re-score's headline and
per-tract equity deltas are in `docs/benchmarks/stage-2-part4-validation.md` (published map run).

**Reason column.** Each plane (each footprint on `ransac`) records `unsuitable_reason`, the
first cutoff it fails, in this order: `no_plane_fit` (NaN tilt: no plane could be fitted),
`too_steep` (> 45°), `north_facing`, `low_insolation` (< 800 kWh/m²/yr shaded clear-sky). It is
`None` for a qualifying plane. The building takes its **dominant plane's** reason. That is the same
row `collapse_to_buildings` already takes `tilt_deg` / `aspect_deg` / `roof_class` from, so the map
popup's reason always agrees with the orientation it shows. The area gate then overrides it with
`too_small`. The reason is computed in the pipeline because the inputs it needs are not in the
per-building output. The clear-sky POA behind the 800 cutoff is not persisted (only
`poa_real_kwh_m2` is). The per-plane failures collapse away. Unfittable roofs carry
`roof_class = "pitched_north_facing"` by design, so the web ETL would have mislabelled them as
north-facing.

`SCORING_VERSION` `2-4.4` → `2-4.5`: a downstream-only re-score on the tiered cache (ADR-0014).
