# Stage 2 · Part 2-2 — city-scale results (whole District of Columbia)

- **Date:** 2026-09-08 · **Phase:** 2 (Part 2-2) · **Plan:** [`stage-2-part2-plan.md`](../plans/stage-2-part2-plan.md) · **ADR:** [0009](../adr/0009-city-scale-tiling-and-idempotency.md)
- **Reproduced by:** `python scripts/run_city.py` (calibrated 12-day) · `--day-range 172` (fast 1-day preview)
- **Acceptance:** no external city-wide benchmark exists — accepted on **invariants + sanity + the payoff** (plan §6), like Part 2-1.

> **STATUS: SCAFFOLDED — awaiting the calibrated 12-day run.** The **1-day** column below is the real
> full-DC dataset from a fast, **uncalibrated** single-day run (building counts and geometry are
> exact; absolute energy/capacity are single-day extrapolations and NOT usable). The **12-day**
> column and the headline map are filled once the calibrated batch (`python scripts/run_city.py`)
> completes. See the [living-plans note](../plans/stage-2-part2-plan.md) — this doc is the §8 deliverable.

## Verdict

_TBD after the 12-day run._ Accepted if: **seam conservation** holds (every DC building scored once),
**idempotency** holds (a re-run reproduces an identical result), the tract **equity map is populated**
(many tracts, whole-tract coverage — Part 2-1's partial-coverage artifact resolved), and city totals
sit in a **plausible range vs NREL** DC rooftop technical potential (a smell test, not a gate).

Both invariants are already verified by the `integration`-marked smoke
(`tests/test_pipeline_city_integration.py`): conservation + idempotency on a real multi-tile DC run.

<p align="center">
  <!-- TODO: commit the calibrated equity-quadrant PNG to docs/assets/ and reference it here. -->
  <em>[headline: calibrated per-household-potential × energy-burden equity quadrant map — TBD]</em>
</p>

## The dataset (day-count-independent — holds for both runs)

| Property | Value |
|---|---|
| Roofs scored, whole DC | **100,064** (usable **57,648**) |
| City per-roof file | **18.1 MB** GeoParquet (`dc_roofs.parquet`) |
| Geometry weight | ~676k vertices (mean **6.9/roof** — simple MS footprints) |
| Grid | **66 tiles** @ 2 km (fixed origin `(315000, 4295000)`, EPSG:6347); **0 empty** (dense urban) |
| Census tracts covered | **206** (priority **57**, data-missing **2**) |

## Compute-cost run-log (§8)

| | 1-day (preview) | 12-day (calibrated) |
|---|---|---|
| Tiles | 66 (65 + 1 retried after a transient auth failure) | _TBD_ |
| Failed tiles | 0 (after one resume pass) | _TBD_ |
| Wall-clock (cold) | not captured¹ | _TBD_ |
| Per-tile mean | — | _TBD_ |

¹ The original 1-day run's timing was lost to a since-fixed CLI crash; the resume was cache-warm
(2.9 s), so it is **not** a real cold-run figure. The 12-day cold run provides the authoritative timing.

## 1-day vs 12-day — what fidelity changes (and what it doesn't)

The comparison is the point: single-day vs 12-day calibrated is a **radiation-fidelity** difference.
Absolute magnitudes scale; the **equity ranking is expected to be robust** (single-day inflates every
roof ~proportionally, so relative per-household potential — and the quadrant classification — barely
moves). **Confirm once the 12-day run lands.**

| Quantity | 1-day (uncalibrated) | 12-day (calibrated) | Note |
|---|---|---|---|
| Installed capacity | 3,619 MW (~3.6 GW) | _TBD_ | scales with fidelity |
| Annual energy | 5,998 GWh/yr (~6.0 TWh) | _TBD_ | scales with fidelity |
| Annual CO₂ offset | ~2,099 kt/yr (~2.1 Mt) | _TBD_ | scales with fidelity |
| **Specific yield** (kWh/kWp/yr) | **~1,657** | _TBD_ | **testable prediction:** the 12-day value should recover Stage-1's Esri-validated ~**1,162** (docs/benchmarks/stage-1) |
| Priority tracts (high × high) | 57 / 206 | _TBD_ | **expected ~unchanged** (ranking robust) |

## Sanity vs NREL (a smell test, not a gate — risks §15)

NREL's DC rooftop-PV **technical potential** is order **~1.3 GW / ~1.6 TWh/yr** (confirm exact figure
vs risks §15). The **1-day** totals (~3.6 GW / ~6.0 TWh) are inflated ~2.8×/3.7× — **expected**, since a
single day extrapolated over the year overstates the annual sum. The **12-day** totals are the ones to
cross-check against NREL. _TBD._

## Caveats

- **1-day absolute numbers are not real** — single-day extrapolation. Use them only for scale/ranking.
- **MS footprint rowhouse merging** (ADR-0005) persists at scale: per-*building* metrics are unstable,
  but aggregates, specific yield, and the **per-household** equity metric are robust to it.
- **DSM vintage seam** across the District (2018 vs 2014/15 3DEP mosaics; roadmap Phase-1 finding) grows
  city-wide — a data-quality note, not silently averaged over.
- **Transient Planetary Computer auth/SAS failures** on a long run cost ~1 tile; **skip-and-continue**
  records it and a **resume** pass retries it (verified: tile `11_3` failed then succeeded on resume).
- **Radiation fidelity is the 12-day sample** (ADR-0001); the DSM is 2 m PC (ADR-0009). Finer DSM /
  365-day sum / explicit `r.horizon` are Part 2-5, and would re-run this whole city pass.
