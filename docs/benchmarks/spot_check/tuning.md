# Spot-check tuning (#23, 2026-10-16)

Tunes `roof_planes.MIN_PLANE_PX`, `usable_area.OBSTRUCTION_TAU_M` and `usable_area.SETBACK_FACTOR`
against the 17 scored hand labels in `labels_12day_gcr.csv` (README here). Each round is a
`SCORING_VERSION` bump plus a city **re-score** from the cached `12day_gcr` DSM and radiation tiers
(ADR-0014). The spot-check decides; the city ÷ NREL ratio (~1.3 GW / ~1.6 TWh, risks §15) is
**reported, not targeted**. Power density and `FLAT_ROOF_GCR` are not knobs here.

**Result: one round.** `OBSTRUCTION_TAU_M` 0.75 → **0.5**; `MIN_PLANE_PX` stays **8**,
`SETBACK_FACTOR` stays **0.85**; `SCORING_VERSION` `2-4.1` → **`2-4.2`**. The plane-count stop rule
(±1 on ~80%) is **not met**: 12/17 (70.6%) with the clamp. The gap is structural, not a knob
setting. Follow-up: #30.

## Scoring rules (decided here)

- **Clamp accepted.** `validation.compare_labels` compares `min(label, roof_planes.MAX_PLANES)`
  with the model (`plane_agree`) and also reports the unclamped rule (`plane_agree_raw`). Six labels
  exceed the cap of 4. The fitter can never reach them, so unclamped they measure the cap and merged
  footprints, not the fitter. `MAX_PLANES` is not a knob in this ticket. This was the README's
  proposal. It was adopted while running unattended, then **confirmed by the user (2026-10-16)**.
  Both numbers are still reported.
- `EXCLUDE` rows (`7_2_594`, `6_4_34`) are left out, as in #21/#22.

## Rounds

Spot-check columns are multiplane (the production path). City numbers are from
`run_city.py --tiles-dir <tuning copy of the 12day_gcr cache> --no-aggregate` on the VM.

| Round | `MIN_PLANE_PX` | τ (m) | setback | `SCORING_VERSION` | ±1 agree (clamped / raw) | obstr. hit / miss / FP / TN | large-planar flags | escaped usable m² | city capacity | ÷ NREL | city energy | ÷ NREL |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 (baseline, #19) | 8 | 0.75 | 0.85 | 2-4.1 | 12 / 8 of 17 | 12 / 3 / 2 / 0 | 6 | 759.9 | 2,302.7 MW | 1.77× | 2,734 GWh/yr | 1.71× |
| **1** | 8 | **0.5** | 0.85 | **2-4.2** | 12 / 8 of 17 | **14 / 1 / 2 / 0** | 6 | 739.5 | **2,237.6 MW** | **1.72×** | **2,658 GWh/yr** | **1.66×** |

The single-plane baseline ("ransac", same chain with `max_planes=1`) at round 1: 11/17 ±1,
obstructions 13 / 2 / 2 / 0.

Round 1 kept all 100,064 roofs (67,044 with usable area, unchanged). Usable area fell 11.51 → 11.19
km² (−2.8%), flat-roof capacity 1,471 → 1,437 MW. Plane counts are unchanged because τ acts only on
obstruction subtraction, after the fit.

**Cache reuse verified (no `r.sun` reruns).** Before and after round 1, all 132 cached
`*_dsm.tif` / `*_insolation.tif` in the tuning cache were byte-identical (sha256) with unchanged
mtimes. All 66 manifest stamps moved to `2-4.2`. The log's only `r.sun` mention is its thread-count
line. The re-score took ~85 min on the shared VM (≈23 min at `1 × 8`, then ≈62 min at `4 × 2`,
resumed). The per-tile footprint fetch and Python scoring dominate, so it is "about an hour", not
"minutes". It is still far below the ~3.9 h cold DSM + `r.sun` run.

## How the settings were chosen

Before any city round, a scratch sweep re-scored only the 17 labelled roofs (seconds per setting,
`validation.rescore_sample` over the same cache) across `MIN_PLANE_PX` ∈ {8, 9, 10, 11, 12, 16, 20,
25, 30, 40} × τ ∈ {0.5, 0.75, 1.0}:

| `MIN_PLANE_PX` (τ 0.5) | ±1 clamped | raw | hit / miss / FP / TN | unfittable roofs | large-planar | escaped m² |
|---|---|---|---|---|---|---|
| 8 | 12 | 8 | 14 / 1 / 2 / 0 | 0 | 6 | 739 |
| 9 | 12 | 9 | 14 / 1 / 2 / 0 | 0 | 6 | 680 |
| 10 | 12 | 8 | 13 / 2 / 2 / 0 | 0 | 5 | 646 |
| 11–12 | 12 | 8 | 13 / 2 / 1 / 1 | 1 | 4–5 | 595 |
| 16 | 12 | 9 | 11 / 4 / 1 / 1 | 3 | 3 | 547 |
| ≥ 25 | 10–11 | 8 | 8 / 7 / 0 / 2 | ≥ 8 | 0 | 0 |

- **`MIN_PLANE_PX`: no headroom, kept at 8.** The same threshold also gates a roof's *first*
  plane. Raising it removes HVAC "planes" on flat rowhouses, but from 11 px real roofs become
  unfittable (the gable `7_2_587`; `7_2_225` and `7_2_1300` by 16 px). An unfittable roof drops out
  of the city. Clamped agreement never rises above 12/17. At 9 px `7_2_103` drops to 2 planes and agrees,
  but `7_2_225` (labelled 10) falls from 4 planes to 2 and stops agreeing: a swap, not a gain.
- **τ → 0.5.** At 8–12 px, 0.5 finds more labelled obstructions than 0.75 (14 vs 12 hits at 8 px;
  13 vs 12 at 12 px). At 16–20 px they tie, and 0.5 never finds fewer. False positives are the same
  at every `MIN_PLANE_PX`. It also equals the RANSAC inlier band
  (`roof_planes.RESIDUAL_THRESHOLD_M`), so every pixel is now either within a plane's band or,
  above it, an obstruction. At 0.75, pixels between 0.5 and 0.75 m above every plane counted as roof.
- **Setback: no evidence, kept at 0.85.** The labels say nothing about edge setback or usable
  area, so the spot-check can't tune it, and the NREL ratio is not a target.
- **Rounds 2–3 not used.** No remaining knob setting moved plane agreement, so another city
  re-score would only have changed the reported ratio.

## Caveats

- **Plane-count stop rule not met** (70.6% vs ~80%). The misses are the ADR-0013 facet-vs-obstruction
  case: flat rowhouses with HVAC (`7_2_103`, `7_2_364`, `7_2_66`, `7_2_882`) still fit as 3–4
  planes. A scratch "later planes need ≥ T px" rule reaches 14–15/17 ±1, but only by collapsing every
  Glover Park gable to 1 plane: exact agreement falls 9/17 → 7–8/17. That evidence and a structural
  rule are #30.
- **Obstruction false positives can't be judged from this sample.** Only 2 scored roofs are
  labelled `has_obstruction=n` (`6_4_42`, `7_2_587`), and at the chosen 8 px the model flags both at
  every τ ≤ 0.75 (from 11 px `7_2_587` turns true-negative only because it becomes unfittable).
  "Low false positives" needs more clean roofs.
- **`escaped_usable_m2` is a heuristic.** It assumes the surplus planes are the smallest ones (by
  plane area). Read it as an order of magnitude, not a measurement.
- **The labels aren't blind.** The drafter saw the model panels (README provenance), and the human
  review changed 3 rows. A blind relabel of a subset should come before these numbers drive more
  tuning. With 17 roofs, one roof is ~6 points of agreement.
- **±1 is loose for 1–2-plane roofs.** It can't tell a gable fit as 1 plane from a correct fit,
  which is why #30 asks for exact agreement next to it.

## Reproduce

```bash
# spot-check metrics for the current code, on the same 17 roofs (no city run needed)
python scripts/spot_check.py --sample outputs/dc/12day_gcr/spot_check \
    --tiles-dir data/tiles_12day_gcr --out-dir outputs/dc/tune23/round1/spot_check --no-images
python scripts/spot_check_compare.py --spot-dir outputs/dc/tune23/round1/spot_check \
    --labels docs/benchmarks/spot_check/labels_12day_gcr.csv
# city re-score (reuses the DSM + radiation tiers; on a copy of the cache)
python scripts/run_city.py --tiles-dir <copy of the 12day_gcr cache> --output-dir <out> \
    --no-aggregate --workers 4 --nprocs 2
```

## #30 — structural facet-vs-obstruction rule (2026-10-18)

**Rule:** drop a later plane with fewer than `SUPERSTRUCTURE_MAX_PX` (25) inliers whose median sits
more than 0.5 m above the dominant plane. Its pixels then fall to the kept planes and are subtracted
as obstruction. `SCORING_VERSION` `2-4.2` → **`2-4.3`**. The rule, the swept alternatives and its
known cost (the `7_2_1177` mansard) are in the ADR-0013 addendum (2026-10-18).
`validation.compare_labels` now reports **exact** agreement (`plane_exact`) next to ±1.

| `SCORING_VERSION` | ±1 agree (clamped / raw) | exact (clamped) | obstr. hit / miss / FP / TN | large-planar flags | escaped usable m² | unfittable (spot-check) | city capacity | ÷ NREL | city energy | ÷ NREL |
|---|---|---|---|---|---|---|---|---|---|---|
| 2-4.2 (#23) | 12 / 8 of 17 | 9/17 | 14 / 1 / 2 / 0 | 6 | 739.5 | 0 | 2,237.6 MW | 1.72× | 2,658 GWh/yr | 1.66× |
| **2-4.3 (#30)** | **14 / 10 of 17** | **10/17** | 14 / 1 / 2 / 0 | **4** | **589.9** | 0 | **2,196.1 MW** | **1.69×** | **2,607 GWh/yr** | **1.63×** |

The single-plane baseline is unchanged (11/17 ±1, 4/17 exact). The plane-count stop rule (±1 on
~80%) **is now met: 14/17 = 82%**, with the label caveats below and in #23.

**City re-score.** Run on the VM from a copy of the `2-4.2` tuning cache (`run_city.py
--tiles-dir <copy> --no-aggregate --workers 4 --nprocs 2`), 66/66 tiles in 38 min. All 132 cached
`*_dsm.tif` / `*_insolation.tif` were byte-identical (sha256) before and after. The log's only `r.sun`
mention is its thread-count line, and all 66 manifest stamps moved to `2-4.3`.

| city | 2-4.2 | 2-4.3 |
|---|---|---|
| roofs / with usable area / unfittable | 100,064 / 67,044 / 8,393 | 100,064 / **65,779** / 8,393 |
| usable area | 11.19 km² | 10.98 km² (−1.9%) |
| mean planes per roof | 2.09 | 1.95 |
| roofs with 1 / 2 / 3 / 4 planes | 34,904 / 36,157 / 13,733 / 15,270 | 41,472 / 33,742 / 12,997 / 11,853 |
| capacity: complex / hip / gable / flat | 1,257 / 480 / 321 / 180 MW | 1,218 / 422 / 342 / 215 MW |
| `low_confidence` roofs | 27,788 | 33,678 |

- **No new unfittable roofs.** The dominant plane is never dropped.
- **1,265 roofs lose all usable area.** Most likely their only usable plane was a dropped raised one (an
  HVAC or tree plane fit as a sun-facing "facet"), which now counts as obstruction. That is the
  intent, but these roofs weren't inspected one by one.
- **5,890 more roofs are `low_confidence`.** A dropped plane's pixels no longer count as inliers, so
  roofs that are mostly raised clutter now fall under the 50% inlier fraction. This is expected,
  not a regression.
- The type bins shift because `roof_type` reads the model's plane count. About 4,150 roofs leave the
  hip and complex bins (29,003 → 24,850).
- The ÷ NREL ratio moved 1.72× → 1.69× as a by-product. It was **not** a target.

**Still open.** These are the #23 caveats: the labels aren't blind, and only 2 scored roofs are
obstruction-free. A blind relabel of a subset and more `has_obstruction=n` roofs (human work)
should confirm the rule before any further tuning.

Reproduce: the #23 commands above, with `--out-dir`/`--output-dir` pointing at a `2-4.3` location.
