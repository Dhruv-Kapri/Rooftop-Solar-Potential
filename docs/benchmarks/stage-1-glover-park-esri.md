# Stage-1 benchmark — Glover Park vs Esri

- **Date:** 2026-09-07 · **Phase:** 1 (MVP) · **Gate:** [ADR-0005](../adr/0005-benchmark-acceptance-intensive.md) (revised)
- **Reference:** Esri "Estimate solar power potential" (Glover Park tutorial)
- **Reproduced by:** `python scripts/run_stage1.py` (canonical) / `--source osm` (regression anchor)

## Verdict

**PASS** (revised ADR-0005 gate). The size-independent intensive quantity — **specific yield** —
matches Esri to <1% on the canonical MS footprints. The model is validated. The one large
divergence (median per-building energy on MS) is a **footprint-source artifact**, fully
explained below, and is no longer part of the gate.

Cross-checked on **OSM** footprints (Esri-granularity segmentation): **both** intensive quantities
pass — specific yield 1186 and median per-building 14.35 MWh — reproducing notebook 05 exactly.
That confirms the MS per-building divergence is footprint segmentation, not the model.

<p align="center">
  <img alt="Per-roof solar suitability choropleth for Glover Park (canonical MS run)" src="../assets/stage1-glover-park-suitability.png" width="70%">
  <br>
  <sub><em>Stage-1 output — per-roof suitability (within-AOI percentile rank of annual energy
  density) for Glover Park, canonical MS ML Buildings run. The chunky polygons are MS's merged
  rowhouses (771 footprints); an OSM run resolves the same blocks into ~2,885 individual roofs.</em></sub>
</p>

## Results

| Quantity | MS ML Buildings (canonical) | OSM (regression anchor) | Esri | Notebook 05 (OSM) |
|---|---|---|---|---|
| **Specific yield** (kWh/kWp/yr) — *gated, ±15%* | **1162** ✅ | **1186** ✅ | 1150 | 1220 |
| Median per-building energy (MWh) — *context* | 44.2 | 14.35 ✅ | 13.35 | 14.35 |
| Roofs scored (core AOI) | 771 | 2862 | — | 4391 (buffered) |
| Usable roofs | 592 | 2047 | — | — |
| Low-confidence fits | 61% | 67% | — | ~55% |
| Total installed capacity (kW) | 41 315 | 43 527 | — | — |
| Total annual energy (MWh/yr) | 47 995 | 51 615 | — | — |
| Total annual CO₂ offset (t/yr) | 16 798 | 18 065 | — | — |
| Median footprint (m²) | 215 | 82 | ~82 (implied) | — |

Totals are **not** comparable across pipelines/sources (method- and segmentation-dependent) —
reported for context only.

**Faithful promotion confirmed (plan §2).** The ported `src/` pipeline on OSM footprints
reproduces notebook 05's median per-building energy — **14.35 MWh, to the decimal** — and lands
within ~3% on specific yield (1186 vs 1220; the small gap is core-vs-buffered scoping + MS-derived
buffer/DSM). The regression anchor holds: the promotion preserved the notebook's numbers.

## The footprint-source finding (why median-per-building diverged on MS)

`specific yield = energy / capacity = 0.8 × poa_real` is size-independent and passes. But
**median per-building energy scales with footprint area**, so it measures the footprint source's
segmentation as much as the model:

- **MS ML Buildings** returns **771** footprints for the Glover Park core at a **215 m² median** —
  it *merges* adjacent rowhouses into larger blocks.
- **OSM** returns **2 885** footprints at an **82 m² median** for the same core (3.7× more, ~2.6×
  smaller) — individual rowhouses, matching Esri's curated layer.
- OSM's 82 m² median reproduces Esri's per-building figure almost exactly:
  `82 m² × 0.70 utilization × 1430 kWh/m²/yr (real-sky) × 0.16 (eff × PR) ≈ 13 MWh` ≈ Esri 13.35.

So median-per-building energy on MS is ~3.3× high **purely because MS buildings are ~3.3× larger**,
not because the per-unit model is wrong. This **inverts** the plan's original expectation (that MS
would match Esri better than OSM, since OSM includes sheds/garages): for dense rowhouse
neighbourhoods, OSM matches Esri's granularity and **MS is the coarse outlier**.

**Documented limitation:** MS ML Buildings under-segments dense residential areas; its *per-building*
metrics are coarse (aggregate and per-m² metrics are unaffected). This is the canonical source per
ADR-0005; use OSM where per-building granularity matters for dense residential.

## Other divergences (expected, per ADR-0005)

- **Single-plane RANSAC vs Esri per-pixel.** Stage 1 fits one plane per roof (ADR-0002); Esri models
  per-pixel suitability. This averages gable/hip facets (61% of roofs flagged low-confidence) and is
  a known source of scatter in per-roof numbers — surfaced per roof, not hidden.
- **12-day radiation sample** (ADR-0001), not a true 365-day sum — a stated approximation.
- **Region-bounded shadowing** (trap 3): shadow search is bounded by the buffered DSM extent, not an
  explicit `r.horizon` maxdistance — a documented Phase-2 hardening item.

## Reproduce

```bash
python scripts/run_stage1.py                          # canonical: MS footprints, 12-day
python scripts/run_stage1.py --source osm --output-dir outputs/osm   # regression anchor
```

Outputs: `outputs/glover_park_roofs.gpkg` (per-roof geometry + tilt/aspect/uncertainty/usable
area/capacity/energy/CO₂/suitability) and `outputs/glover_park_suitability.png` (choropleth).
