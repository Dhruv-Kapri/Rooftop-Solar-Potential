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
