# Stage 2 · Part 2-4 — validation: is multi-plane better than RANSAC?

- **Date:** 2026-10-18 · **Phase:** 2 (Part 2-4, steps 6–7) · **Plan:** [`stage-2-part4-plan.md`](../plans/stage-2-part4-plan.md) §6 · **ADR:** [0011](../adr/0011-part2-4-classical-multiplane-ml-demo.md) · [0012](../adr/0012-per-plane-poa.md) · [0013](../adr/0013-obstruction-aware-usable-area.md) · [0014](../adr/0014-tiered-per-tile-cache-component-versions.md) · **Issue:** #24
- **Dataset:** the tuned run, `SCORING_VERSION` `2-4.2` (#23 round 1: `MIN_PLANE_PX` 8, τ 0.5 m,
  setback 0.85, `FLAT_ROOF_GCR` 0.50). It is a re-score of the `12day_gcr` DSM + radiation tiers
  (#19), so the DSMs are byte-identical to `12day_gcr`.
- **Reproduced by:** `python scripts/self_consistency.py --tiles-dir <tuned tile cache> --out-dir <out>
  --workers 4` (self-consistency) and `python scripts/spot_check_compare.py` (spot-check, see
  [`spot_check/tuning.md`](spot_check/tuning.md) "Reproduce").

> **There is no DC ground truth (risks §14 item 4).** No public per-roof plane or obstruction dataset
> exists for Washington, DC (RoofN3D is NYC-specific), and none was collected. Nothing below is an
> accuracy measurement. The DSM self-consistency check measures how well each fit explains the
> model's **own input**, and the spot-check is 17 roofs labelled by eye. Together they are the
> plan's honest stand-in for "accuracy vs the RANSAC baseline" (§6). They are not a
> statistical accuracy claim.

## Verdict

**Multi-plane fits DC roofs' surface geometry much better than one plane. It is not proven to
model their structure better.**

- **Self-consistency (all 100,064 roofs):** multiplane halves the median reconstruction residual,
  from 1.79 m to 0.85 m RMSE. It reconstructs 73% of a roof's pixels within 0.5 m, against 45% for
  one plane. The gain is largest on hip and complex roofs. But this check **cannot lose by
  construction**, and it rewards spurious planes (HVAC fit as "facets") as much as real ones.
- **Spot-check (17 roofs):** ±1 plane-count agreement is **12/17 (71%) vs 11/17 (65%)**, which is a
  statistical tie. Exact agreement is **9/17 vs 4/17**. Multiplane reaches the cap on the six roofs
  labelled above it, where one plane can't, and over-splits flat roofs with HVAC (ADR-0013, follow-up #30).
  *Since then:* #30's superstructure rule (`SCORING_VERSION` `2-4.3`) reaches **14/17 ±1 and 10/17
  exact** on the same labels. The numbers in this write-up are for `2-4.2` and aren't re-run; see
  [`spot_check/tuning.md`](spot_check/tuning.md) "#30".
- **City total vs NREL:** **2.9× → 1.77× → 1.72×** capacity (pre-GCR → GCR → tuned). The tuned
  run is **2,237.6 MW / 2,658 GWh/yr, 1.72× / 1.66×** NREL's ~1.3 GW / ~1.6 TWh. This is reported,
  not targeted.

## 1. DSM self-consistency — multiplane vs single-plane ransac (step 7a)

**Method.** For every roof in the city, `validation.tile_self_consistency` clips the roof's DSM
pixels and fits them twice through the same fitter: multiplane (`fit_planes_multi`, up to
`MAX_PLANES` = 4 facets) and the single-plane baseline (`max_planes=1`). Each pixel is rebuilt from
its nearest fitted plane (`reconstruct_residuals`). Per roof it reports RMSE and the % of pixels
within 0.5 m (`SELF_CONSISTENCY_TOL_M`, the RANSAC inlier band). The roof type is the
stratification proxy from the model's own output (`validation.roof_type`: flat / gable = 1–2 planes,
hip / complex = 3–4 planes on a pitched / flat dominant facet). The re-fit's plane count matched the
production `n_planes` on 100% of fittable roofs.

**Per-roof residuals by roof type** (medians and p90 over fittable roofs; metres; 2 m DSM):

| roof type | roofs | unfittable | RMSE multiplane | RMSE ransac | RMSE improvement (median / p90) | % px ≤ 0.5 m multiplane | % px ≤ 0.5 m ransac | capacity share |
|---|---|---|---|---|---|---|---|---|
| flat | 16,787 | 0 | 0.71 | 1.29 | 0.50 / 1.14 | 78 | 55 | 180 MW (8%) |
| gable | 45,881 | 0 | 1.13 | 1.80 | 0.37 / 1.78 | 63 | 44 | 321 MW (14%) |
| hip | 14,016 | 0 | 0.68 | 2.57 | 1.82 / 3.69 | 80 | 35 | 480 MW (21%) |
| complex | 14,987 | 0 | 0.54 | 1.86 | 1.24 / 2.66 | 87 | 50 | 1,257 MW (56%) |
| unfittable¹ | 8,393 | 8,393 | — | — | — | — | — | 0 MW (0%) |
| **all** | **100,064** | **8,393** | **0.85** | **1.79** | **0.81 / 2.32** | **73** | **45** | **2,238 MW** |

¹ An unfittable roof (too few DSM pixels, or no facet that clears `MIN_PLANE_PX`) has no planes, so the
self-consistency summary gives it its own bin (#31; the spot-check's `roof_type` proxy is unchanged).
These roofs are small (median footprint 63 m²) and all `low_confidence`. None is usable, so they
carry **0 MW**. They are excluded from the medians.

Share of fittable roofs where multiplane cuts RMSE by more than 0.25 m: flat 64%, gable 52%, hip
100%, complex 99.9%, all 70%. A roof fit as a single plane improves by exactly 0. That is 33% of
fittable flat roofs and 46% of fittable gables.

**How to read it.**
- **Multiplane can never reconstruct worse.** Its first plane is the single-plane fit (same
  fitter, same seed), and every pixel uses its nearest plane. So the multiplane residual is never
  larger, and the measured minimum improvement over 91,671 fittable roofs is exactly 0. The question
  this check answers is *how much better*, not *whether*.
- **It rewards over-segmentation.** An HVAC box fit as its own "plane" lowers the residual just as
  a real facet does. The **complex** bin (flat-dominant roofs with 3–4 planes) is where this
  matters most. It shows a large gain (median 1.24 m) and carries **56% of city capacity**, but it is
  also exactly where the spot-check finds HVAC fit as planes. The self-consistency number for complex
  roofs is the least trustworthy part of this table.
- **Hip roofs are the clean win.** A single plane can't follow four pitched facets (median 2.57 m
  RMSE, 35% of pixels within 0.5 m). Multiplane brings that to 0.68 m and 80%. This is the case the
  method was built for, and the spot-check agrees on the multi-facet roofs.
- **Gables gain least.** 46% of fittable gables are fit as one plane at the 2 m DSM, where a
  small gable's second facet often doesn't reach `MIN_PLANE_PX` (8 px ≈ 32 m²).
- **The residual floor is the DSM, not the fit.** A 0.5–1 m median RMSE at 2 m resolution
  includes parapets, eaves mixing with ground and trees, and DSM noise, which no plane model removes.

**Compute.** 100,064 roofs, 2 fits each: **3,420 s (57 min) on the dev Mac at `--workers 4`**
(niced, host load ~8–10 during the run). That is ~80 ms per roof per core, measured on 200 roofs
single-threaded. No `r.sun` is involved: it reads cached DSMs only.

## 2. Spot-check — indicative, 17 roofs (step 7b; #21, #22, #23)

Hand labels: [`spot_check/labels_12day_gcr.csv`](spot_check/labels_12day_gcr.csv), 19 roofs from
Glover Park + Farragut/K St NW, stratified by the roof-type proxy. 2 rows are `EXCLUDE`d, leaving
**17 scored**. Labels are clamped to `MAX_PLANES` (4) for the headline rule. Scoring rules and
provenance are in the [`spot_check/README.md`](spot_check/README.md). Metrics are on the tuned code
(`2-4.2`), from `scripts/spot_check_compare.py`.

| method | ±1 agree (clamped) | 95% CI² | ±1 agree (raw) | exact agree (clamped) | 95% CI² | obstr. hit / miss / FP / TN | large-planar flags | escaped usable m² |
|---|---|---|---|---|---|---|---|---|
| **multiplane** | **12/17 (71%)** | 47–87% | 8/17 (47%) | **9/17 (53%)** | 31–74% | 14 / 1 / 2 / 0 | 6 | ~740³ |
| ransac (single plane) | 11/17 (65%) | 41–83% | 11/17 (65%) | 4/17 (24%) | 10–47% | 13 / 2 / 2 / 0 | — | — |

² Wilson score intervals, n = 17.
³ A heuristic estimate (it assumes the surplus planes are the smallest ones). Read it as an
order of magnitude.

**What the paired roofs show.** The two methods disagree on ±1 for 11 roofs.
- **Multiplane wins 6.** All six are roofs labelled above the cap (`6_3_74`, `6_4_28`, `6_4_42`,
  `6_4_55`, `6_4_56`, `7_2_225`): multi-facet roofs or merged block footprints where one plane is
  off by 3+.
- **Ransac wins 5.** These are the flat Glover Park rowhouses with HVAC (`7_2_103`, `7_2_66`,
  `7_2_882`) plus `7_2_364` and `6_4_246`, which multiplane splits into 3–4 planes. This is the
  ADR-0013 facet-vs-obstruction gap.

On exact agreement, multiplane wins 8 roofs and ransac 3. A two-sided exact sign test on the discordant
pairs (`scipy.stats.binomtest(3, 11)` and `binomtest(5, 11)`) gives p ≈ 0.23 for exact and p = 1.0
for ±1. **Neither difference is significant at n = 17.** One roof is ~6 points of
agreement.

**Uncertainty, stated plainly.**
- **n = 17**, so the intervals above are ±20 points wide. The ~80% stop rule (#13) is inside
  multiplane's interval and was **not met** at 71%.
- **The labels aren't blind.** The drafter saw the model panels, and the human review changed 3
  rows. A blind relabel of a subset is a #30 prerequisite.
- **±1 flatters single-plane fits on 1–2-plane roofs.** A gable fit as 1 plane still "agrees", which
  is why exact agreement is reported next to it.
- **Obstruction false positives can't be judged.** Only 2 scored roofs are labelled
  `has_obstruction=n`, and both methods flag both. "Low false positives" is untested, not met.
- **The sample is two neighbourhoods**, stratified by the model's own roof-type proxy. It says
  nothing about the rest of the District.

## 3. City total vs NREL — before and after (risks §15, a smell test, not a gate)

NREL's DC rooftop technical potential is order **~1.3 GW / ~1.6 TWh/yr**. All rows are the full
District (66 tiles, 100,064 roofs, 12-day calibrated radiation).

| run | geometry | `SCORING_VERSION` | capacity | ÷ NREL | energy | ÷ NREL | specific yield |
|---|---|---|---|---|---|---|---|
| `12day` (Part 2-2) | single-plane ransac | — | 3,619 MW | 2.8× | 4,067 GWh/yr | 2.5× | ~1,124 kWh/kWp |
| `12day` multiplane, pre-GCR | multiplane | — (`PIPELINE_VERSION` 2-4.0) | 3,831.5 MW | **2.9×** | 4,531 GWh/yr | 2.8× | ~1,183 |
| `12day_gcr` (#19) | multiplane + flat GCR 0.50 | 2-4.1 | 2,302.7 MW | **1.77×** | 2,734 GWh/yr | 1.71× | ~1,187 |
| **tuned (#23 round 1)** | multiplane + GCR, τ 0.5 m | **2-4.2** | **2,237.6 MW** | **1.72×** | **2,658 GWh/yr** | **1.66×** | ~1,188 |

- **The GCR calibration did nearly all of the work** (2.9× → 1.77×): flat-roof arrays now get
  tilted-row spacing (plan step 6). Tuning τ took off another 2.8% of usable area (−65 MW).
- **Multiplane alone did not shrink the total.** It went *up* vs the single-plane run (3,619 →
  3,832 MW). A likely reason, not measured: pitched facets that one plane averaged away now pass the
  tilt/aspect cutoffs on their own.
- **The remaining ~1.7× is not closed and was not targeted.** Known contributors: the HVAC planes
  that escape subtraction (~740 m² on 17 roofs alone; #30), MS footprint rowhouse merging
  (ADR-0005), and differences from NREL's own method (its setback and shading assumptions are not
  reproduced here, so their share is unknown). Specific yield is
  stable at ~1,188 kWh/kWp (Stage-1 Esri-validated ~1,162), so the gap is in **usable area, not
  radiation**.

## 4. Compute cost (#16, #19, #23, #27; the Part 2-2 §8 run-log)

| step | machine | split (`workers × nprocs`) | wall-clock | source |
|---|---|---|---|---|
| `r.sun`, one tile (`6_7`, 1300² px, 12-day), 1 / 4 / 8 threads | dev Mac (4P+4E) | 1 × {1, 4, 8} | 324.4 / 116.1 / 90.7 s (1.00 / 2.79 / 3.58×), bit-identical | #16 |
| `r.sun`, 8 full tiles | VM (8 vCPU) | 1 × 8 vs 8 × 1 | 680.0 vs 747.4 s (workers 0.91×), bit-identical | #27 |
| `r.sun`, 4 full tiles | dev Mac | 1 × 8 vs 2 × 4 | 739.6 vs 603.2 s (workers 1.23×, contended) | #27 |
| Full city cold run (DSM + `r.sun` + score), `12day_gcr` | VM, shared, `nice 10` | 1 × 8 | **14,186 s (~3.9 h)**, 214.9 s/tile | #19 |
| Full city re-score from cached DSM + radiation, `2-4.2` | VM, shared | 1 × 8 then 4 × 2 (resumed) | **~85 min** (run log: 3,741 s for the resumed `4 × 2` leg) | #23 |
| Spot-check re-score, 17 roofs, one setting | dev Mac | — | seconds | #23 |
| DSM self-consistency, all 100,064 roofs | dev Mac | 4 workers | **3,420 s (57 min)** | this note |

For comparison, the pre-#16 single-core cold run (Part 2-2, `12day`) took 17,454 s (~4.85 h) on the
dev Mac. The tiered cache (ADR-0014) is what makes tuning cheap. A downstream-only change is now a
~1 h re-score, not a ~4 h `r.sun` re-run. The re-score is dominated by footprint fetch and Python
scoring, not by radiation.

## Caveats

- **No DC ground truth** (risks §14 item 4). Self-consistency is internal, and the spot-check is
  indicative. Neither is a city-wide accuracy figure.
- **The roof-type bins come from the model's own output**, so a roof mis-fit as 3 planes is binned
  "hip"/"complex" by that same mis-fit. The bins spread the comparison across archetypes. They are
  not true roof types.
- **The 2 m DSM limits both methods.** Small gable facets fall under `MIN_PLANE_PX`, and residuals
  include DSM noise and edge mixing. Finer DSM is Part 2-5.
- **Open structural gap:** the facet-vs-obstruction rule (#30). Until it lands, the complex-roof
  numbers above, and the 56% of capacity they carry, are the most likely to be over-stated.
