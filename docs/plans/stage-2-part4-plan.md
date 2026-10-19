# Stage 2 · Part 2-4 plan — roof-geometry refinement (classical multi-plane + obstructions; ML demo)

> **STATUS: steps 1–7 done; step 8 (the ML demo) remaining.**
> The classical, training-free geometry upgrade (ADR-0011/0012/0013) is built, unit-tested and wired in
> (`method="multiplane"` is the production default; `ransac` kept as the §6 baseline) — steps 1–5
> (2026-09-08). Step 6: GCR city run `12day_gcr` (#19, 2026-10-13), spot-check tuning to
> `SCORING_VERSION` `2-4.2` (#23, 2026-10-16), then the map refresh (#25, 2026-10-18) — `build_web` from
> `12day_tuned`. Step 7: validation write-up (#24, 2026-10-18,
> [`stage-2-part4-validation.md`](../benchmarks/stage-2-part4-validation.md)). **Remaining:** step 8 (the
> ML demo). The facet-vs-obstruction finding (a large planar obstruction is fit as its own plane and
> escapes subtraction; ADR-0013, risks §14 item 7) got a structural rule in #30 (`SCORING_VERSION`
> `2-4.3`, 2026-10-18); the map still serves `2-4.2` (#32).
>
> Grilled to a full decision set (grill 2026-09-08, grounded on two fact-finds: the per-roof contract map
> + the ML-model landscape) and recorded in
> **[ADR-0011](../adr/0011-part2-4-classical-multiplane-ml-demo.md)**,
> **[ADR-0012](../adr/0012-per-plane-poa.md)**, **[ADR-0013](../adr/0013-obstruction-aware-usable-area.md)**.
> The reframe from "RoofN3D-trained ML segmentation" to a **classical, training-free** geometry upgrade
> (with ML as a bounded demo) is locked; no open `ASSUMPTION:`s remain.

- **Phase:** 2, Part 4 of 5 · **Branch:** `stage-2` · **Overview:** [stage-2-overview.md](stage-2-overview.md)
- **Prerequisite:** Part 2-3 built (done — the map consumes this part's per-roof output).

## 1. What this part is (the reframe)

The roadmap called Part 2-4 "ML segmentation." The grill's two fact-finds killed that as the *production*
path, on both value and feasibility (ADR-0011):

- **Value:** per-roof **energy comes from the DSM + `r.sun` raster**, not from the RANSAC tilt/aspect —
  `tilt_deg`/`aspect_deg` feed only the binary usable/`roof_class` gate, nothing in the energy math reads
  them. So multi-plane's tilt gain barely moves energy. The real levers are **obstructions** (replacing
  the flat `× 0.70`), **per-plane usable gating**, and **per-plane POA** — none needs ML.
- **Feasibility:** RoofN3D is NYC-only, has no obstruction labels, ships no usable checkpoint; the
  point-cloud models are train-from-scratch (a real GPU — the dev box is an 8 GB M1); DC has no ground
  truth, so an ML accuracy claim is unvalidatable and not a clean `/tdd` target.

So Part 2-4 ships a **classical, deterministic, training-free** geometry upgrade as the production path and
the `/tdd` build, and keeps the ML-segmentation story **honestly** as a bounded, inference-only
**demonstration** behind the pre-wired `method="ml"` seam (§5). The shipped accuracy path is classical.

## 2. Where it continues from

- Part 2-2's district-wide run (the dataset re-run against this part's geometry).
- Part 2-3's deployed map — **unchanged**. This part's **per-building collapse** (§3) keeps the exact
  per-roof contract `web_build.roofs_to_web` + the map consume, so an accuracy re-run reaches the live map
  by re-running `scripts/build_web.py` + committing `web/assets/` — no app rebuild (Part 2-3 §10).
- The pre-wired seams: `fit_roof_planes(footprints, dsm_path, method=…)` and
  `usable_area(roof_planes, obstructions=None)`. The blocking constraint to lift first is the hard
  `method != "ransac"` guard at **`roof_planes.py:112`**; `obstructions=` is accepted-but-unused today
  (`usable_area.py:63`).
- **Blast radius (verified, grill fact-find):** a per-building-preserving multi-plane change is contained
  to `roof_planes.py`, `usable_area.py`, `yield_pv.py`, and a per-plane widening of the `radiation` zonal
  step (§4). Nothing downstream (`aggregate`, `web_build`, the map) moves.

## 3. The classical core — settled decisions

- **Multi-plane sequential RANSAC** (ADR-0011) — `fit_roof_planes(method="multiplane")`: fit best plane →
  remove inliers → refit on the remainder, repeat; **≤ ~4 planes/roof**, dropping planes below a min
  area/inlier count. Reuses the single-plane RANSAC per iteration. `method="ransac"` stays for the
  baseline comparison; `run_city` passes `"multiplane"`.
- **Per-plane POA** (ADR-0012) — insolation is the `r.sun` zonal mean over **each usable plane's inlier
  pixels**, not the footprint mean (which dilutes usable planes with north-face/obstruction pixels). The
  multi-plane fit exposes **pixel–plane membership**; the zonal step consumes it. The **biggest honest
  energy gain**, nearly free.
- **Obstruction-aware usable area** (ADR-0013) — obstructions = DSM pixels **> τ (≈0.5–1.0 m) above the
  fitted plane**. Replace the flat `× 0.70` with `usable = (plane_area − obstruction_area) × setback_factor`
  (`setback_factor ≈ 0.85–0.90`, edge/access only). The plan's hard cutoffs (slope ≤ 45°, irradiance ≥ 800,
  not north-facing) now apply **per plane**.
- **Per-building collapse** (ADR-0011) — fit planes internally, emit **one row per building**: `usable` =
  any plane usable; `usable_area_m2`/`capacity_kw`/`annual_energy_kwh`/`annual_co2_kg` = **Σ over usable
  planes**; `tilt_deg`/`aspect_deg`/`roof_class` = the **dominant (largest usable) plane**; new `n_planes`
  column; `low_confidence` from the overall fit; `suitability` recomputed on the per-building energy
  density. Every downstream-consumed column keeps its name/dtype.

**Pipeline ordering note (ADR-0012):** today the `radiation` zonal aggregation runs *before* `roof_planes`.
Per-plane POA needs plane membership, so the **`r.sun` raster generation stays first** (DSM-only) but the
**zonal aggregation moves to after `roof_planes`**, where it can mask per plane. This split is the one
structural change beyond the three touched modules.

## 4. Seam & property contract (what `/tdd` asserts)

| Seam | Contract (test-first, deterministic) |
|---|---|
| `fit_roof_planes(..., method="multiplane")` | GeoDataFrame, **one row per building**; recovers N planes on a synthetic multi-facet DSM (correct count, dominant tilt/aspect); exposes per-plane **pixel membership**; adds `n_planes`; keeps `tilt_deg`/`aspect_deg`/`inlier_ratio`/`n_px`/`low_confidence`. `method="ransac"` unchanged (regression). |
| per-plane zonal insolation | given plane membership + a synthetic `r.sun` raster, POA per usable plane = zonal mean over that plane's pixels (hand-computable). |
| `detect_obstructions(dsm, planes, tau)` | pixels > τ above a plane → obstruction **polygons/area**; a synthetic bump yields a known area; a clean plane yields none. |
| `usable_area(roof_planes, obstructions=…)` | `(plane_area − obstruction_area) × setback_factor`, per-plane qualification cutoffs; hand-computable usable area; `obstructions=` now consumed. |
| per-plane yield → per-building sum | two planes with known area/POA → summed `capacity_kw`/`annual_energy_kwh`/`annual_co2_kg`; `suitability` recomputed. |
| downstream contract | **regression**: `aggregate_to_tracts` + `roofs_to_web` still consume the collapsed row unchanged. |

## 5. The ML demo (bounded, inference-only — ADR-0011)

A demonstration, not a swap: run an **existing NYC-trained RoofN3D** point-cloud segmenter (unofficial
`sarthakTUM/roofn3d` checkpoints, or similar) on **a handful of DC roofs** behind
`fit_roof_planes(method="ml")` (an **integration-tested adapter** — loads the model, runs on a fixture
clip, returns valid planes; **non-default, labelled experimental/unvalidated**), plus a **walkthrough
notebook** comparing ML vs classical planes. **Inference-only** (the 8 GB M1 can't fine-tune); framed as a
NYC-trained *prior* applied to DC, domain gap stated. Don't commit the unlicensed weights (document the
fetch + cite). **Best-effort**: if the old code won't run, fall back to a minimal Colab train or a written
"what ML would add" analysis — it **never blocks the classical build**. `torch` is an optional extra, not
in the base `environment.yml`.

## 6. Validation (honest — no DC ground truth; risks §14.4)

- **DSM self-consistency** — reconstruct the DSM from the fitted planes; per-roof residual (RMSE / %
  within tolerance), **`multiplane` vs single `ransac`**. Multi-plane should reconstruct complex roofs with
  materially lower residual. Plus **visual QA** of detected obstructions.
- **Spot-check** — **~20 DC roofs**, stratified across archetypes (flat/gable/hip/complex) over **Glover
  Park + one dense downtown block**, hand-labelled for **major-plane count** + **obstruction presence**;
  compare plane-count agreement (±1, `multiplane` vs single) and obstruction hit/false-positive. Reported
  as **indicative**, not a statistical accuracy claim. The user provides the ~20 labels; the build ships
  the selection + comparison harness (a small notebook).

Together these are the plan's "accuracy comparison vs the RANSAC baseline" — done honestly given no GT.

## 7. Testing strategy (the TDD split)

- **Unit (TDD, red-green, deterministic):** sequential multi-plane RANSAC; per-plane pixel membership;
  per-plane zonal POA; obstruction detection; the obstruction-aware usable-area formula; per-plane yield →
  per-building collapse; the seam contract + downstream regression (§4). All on synthetic fixtures — no
  model, no network, no GRASS (the `r.sun` raster is a fixture array).
- **Integration (marked):** the `method="ml"` adapter (loads a checkpoint, runs on a fixture clip, returns
  valid planes); a real GRASS/DSM multi-plane smoke on a small AOI.
- **Validation (not a test):** §6 (DSM self-consistency + the spot-check).

## 8. Sequencing (TDD order)

1. **✓** Lift the `roof_planes.py:112` guard; add `method="multiplane"` — sequential RANSAC + `n_planes` +
   pixel membership (unit). *(`fit_planes_multi`; per-plane schema, ADR-0002 uncertainty carried.)*
2. **✓** Per-plane zonal POA (`radiation.plane_poa`) consuming membership; the `zonal_insolation` reorder
   was already in place, so no structural move was needed (unit).
3. **✓** `detect_obstructions` (`obstruction_pixels` + composer) + the obstruction-aware `usable_area`
   formula (per-plane cutoffs reuse `is_usable`/`classify_roof`) (unit).
4. **✓** Per-plane yield (`plane_yields`) → per-building collapse (`collapse_to_buildings`) + `suitability`
   recompute; downstream regression against `aggregate`/`web_build` (unit).
5. **✓** Wire `pipeline._score_roofs` (+ `run_stage1`/`run_city`) to the `"multiplane"` path; bump
   the pipeline version stamp (since split into component versions, ADR-0014); Stage-1 integration smoke updated to the collapsed schema (integration, GRASS-gated).
6. **✓** City re-run (Part 2-2's caching re-invoked) → `build_web` + commit `web/assets/` (no app rebuild).
   - **⊙ deferred (2026-09-15; superseded by the re-sequencing below — done in #19/#23/#25):** added a **flat-roof GCR** calibration (`FLAT_ROOF_GCR = 0.50` in
     `usable_area.py`, multiplane path only) — flat roofs are 84% of usable capacity and were modelled at
     0.85 packing with no tilted-array inter-row spacing. pipeline version bumped `2-4.0`→`2-4.1` (the stamp later became `SCORING_VERSION`, ADR-0014).
     Analytically (no re-run) this pulls the city over-estimate from **2.9× → ~1.7× NREL** (3.83→~2.23 GW).
     Power-density was *not* touched (200 W/m² is right for modern modules; cutting it would break the
     calibrated specific yield). **The full 10–15 h city re-run is deferred and bundled with step 7b's
     obstruction/setback-knob tuning** — run the expensive batch ONCE with both calibrations, not twice.
     Code + version bump are committed to the working tree; *at the time*, `outputs/12day/` + `web/assets/`
     still held the pre-GCR numbers (historical — the map has served the tuned run since #25). See risks note on r.sun caching below.
   - **⊙ re-sequenced (grill 2026-10-12):** the tiered cache (ADR-0014) removed the reason to bundle.
     Now: (1) multi-core `r.sun` (`nprocs`, measured on one tile; tile-level process pool only if <~4×
     on 8 cores) + GRASS version in `radiation_key` + log-noise cleanup; (2) **one** full-city run with
     GCR under a **new run label** (old `12day` outputs left untouched), on the office VM
     `graham-build` when reachable, else the Mac, with an rsync cache+manifest handoff between them;
     (3) step-7b tuning as **re-scores** (`SCORING_VERSION` bumps, ≤3 rounds) — DSM + radiation reused.
   - **✓ GCR city run (#19, 2026-10-13):** `12day_gcr` on the VM, 66/66 tiles, ~3.9 h at `1 × 8`;
     **2,302.7 MW / 2,734 GWh/yr, ~1.77× / ~1.71× NREL** (analytic ~1.7× held). DSM + radiation cache
     tiers filled for every tile. `build_web` waits on step-7b tuning. Details in
     `docs/benchmarks/stage-2-part2-dc-city-scale.md` ("Full-city GCR run").
7. **✓** Validation: DSM self-consistency + the spot-check harness + your ~20 labels.
   - **⊙ partial (2026-09-08):** the DSM self-consistency **functions** are built + unit-tested
     (`validation.py`: `reconstruct_residuals`, `self_consistency`, `compare_self_consistency`;
     `fit_planes_multi` gained a `max_planes` param for the single-plane baseline). The **actual
     validation run** on a real sample AOI + **step 7b** (the spot-check harness + your ~20 labels)
     remain — the run consumes the fresh step-6 dataset.
   - **Grill 2026-10-12 decisions for 7b:** the harness re-runs the scoring chain for just the sampled
     buildings from the tile cache (per-plane + obstruction output on demand; city `roofs.parquet` schema
     unchanged); sample = Glover Park + Farragut/K St NW; labels from DC 2023 orthophoto + rendered DSM
     hillshade into a CSV (`building_id, n_major_planes, has_obstruction, notes`). Stop rule: spot-check
     leads (plane count ±1 on ≥~80%, low obstruction false positives), ≤3 tuning rounds; the city ÷ NREL
     ratio is **reported, not targeted**. Facet-vs-obstruction: tune knobs only; a structural fix becomes a
     follow-up issue if the spot-check shows it matters. ML demo (step 8) split into its own spec.
   - **✓ spot-check tuning (#23, 2026-10-16):** one round. `OBSTRUCTION_TAU_M` 0.75→0.5,
     `SCORING_VERSION` `2-4.2`; city re-score from the cached tiers (rasters verified unchanged):
     **2,237.6 MW / 2,658 GWh/yr, ~1.72× / ~1.66× NREL**. Plane-count stop rule not met (12/17 ±1 with
     the `MAX_PLANES` clamp); `MIN_PLANE_PX` has no headroom, so the structural rule is follow-up #30.
     Write-up: `docs/benchmarks/spot_check/tuning.md`.
   - **✓ map refresh (#25, 2026-10-18):** `build_web` from `outputs/dc/12day_tuned/`, `web/assets/`
     committed — a data swap only, no app rebuild. `CALIBRATED_CITY_LABEL` now names `12day_tuned`.
     Assembled from the #23 re-score (run `--no-aggregate` into `outputs/dc/tune23/round1/`): its
     `roofs.parquet` + `run_log.txt` copied in, then `pipeline.run_aggregation(…, area_name="Washington,
     DC")` wrote `tracts.gpkg` + the choropleths beside them.
   - **✓ validation (#24, 2026-10-18):** write-up
     [`stage-2-part4-validation.md`](../benchmarks/stage-2-part4-validation.md). Multiplane halves the
     median DSM reconstruction RMSE (1.79→0.85 m); spot-check ±1 plane-count 12/17 vs 11/17 (a tie),
     exact 9/17 vs 4/17; city 1.72× / 1.66× NREL (reported, not targeted). Verdict: better surface
     geometry, not proven better structure; structural facet-vs-obstruction rule is follow-up #30.
   - **✓ structural facet-vs-obstruction rule (#30, 2026-10-18):** a later plane under
     `SUPERSTRUCTURE_MAX_PX` (25) whose median sits > 0.5 m above the dominant plane is dropped, so
     its pixels are subtracted as obstruction. `SCORING_VERSION` `2-4.3`; spot-check ±1 14/17 (stop
     rule met), exact 10/17; city re-score **2,196.1 MW / 2,607 GWh/yr, 1.69× / 1.63× NREL**. ADR-0013
     addendum; `spot_check/tuning.md` "#30". Labels still not blind. The map stays on `2-4.2` until #32.
8. The ML demo: `method="ml"` adapter (integration) + the comparison notebook (best-effort).

**Delegation note:** steps 1–4 (deterministic, clear contracts) are strong Sonnet-subagent TDD tasks,
reviewed against §4; steps 5–8 (pipeline wiring, the city re-run, validation judgement, the ML demo) stay
closer to hand.

## 9. Deliverables

- `fit_roof_planes(method="multiplane")` + pixel membership; per-plane zonal POA; `detect_obstructions` +
  obstruction-aware `usable_area(obstructions=…)`; per-building collapse — all unit-tested (§4/§7).
- `method="ml"` inference adapter + a ML-vs-classical comparison notebook (best-effort).
- **✓** A refreshed city dataset (`outputs/dc/12day_tuned/`) + `web/assets/` (`build_web`, committed — no
  app rebuild; #25).
- **✓** The validation write-up: DSM self-consistency (multi-plane vs single) + the 17-roof spot-check, with
  uncertainty stated — the honest "is it better than RANSAC" story
  ([`stage-2-part4-validation.md`](../benchmarks/stage-2-part4-validation.md), #24).
- ADR-0001-style follow-ups already recorded: ADR-0011/0012/0013; glossary updated.

## 10. Risks / dependencies

- **Multi-plane RANSAC tuning** (max planes, min area/inliers, τ, setback_factor) — real constants; keep
  them explicit/inspectable/reported (ADR-0013 spirit), tune on the spot-check set.
- **The `roof_planes → radiation` coupling** (ADR-0012) is the one new structural seam — contained, but
  the pipeline reordering (§3) must land before per-plane POA works.
- **Compute** — largely dissolves for the classical core: the re-run is Part 2-2's cost ≈ unchanged (the
  `r.sun` pass dominates, not plane-fitting). Only the **ML demo** wants a GPU, and it's inference-only on
  the M1. (The 12-day-timing figure just confirms the re-run cost; it's not a blocker.)
- **Resolved — r.sun is cached per tile (ADR-0014).** The design smell the earlier "r.sun is NOT cached"
  note raised (downstream-only changes paying the full r.sun cost) was fixed: DSM + insolation are cached
  with sha256-verified `.key` sidecars, so downstream changes are re-scores. Measured on the VM: cold full
  city ~3.9 h; re-score ~85 min (validation write-up §4).
- **ML demo may not run cleanly** (unofficial, unmaintained code) — hence best-effort with fallbacks (§5).
- **Validation is honest, not conclusive** — no DC ground truth (risks §14.4); the spot-check is
  indicative. Said plainly, not papered over (Q2 / ADR-0011).

## 11. Downstream review checkpoint

When this part is built, re-read Part 2-5 (Fidelity) and revise it before starting — in particular whether
the multi-plane geometry + per-plane POA change how 2-5's 365-day / finer-DSM re-run is structured or
costed (it re-runs on top of this part's dataset). Part 2-5 already notes its map propagation is a clean
tile refresh (numbers-only); confirm that still holds once the `n_planes`/collapse changes land.
