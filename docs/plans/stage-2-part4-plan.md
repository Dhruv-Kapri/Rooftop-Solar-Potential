# Stage 2 · Part 2-4 plan — roof-geometry refinement (classical multi-plane + obstructions; ML demo)

> **STATUS: settled — `/tdd`-ready (2026-09-08)** — grilled to a full decision set (grill 2026-09-08,
> grounded on two fact-finds: the per-roof contract map + the ML-model landscape) and recorded in
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

1. Lift the `roof_planes.py:112` guard; add `method="multiplane"` — sequential RANSAC + `n_planes` +
   pixel membership (unit).
2. Per-plane zonal POA — move/ split the `radiation` zonal aggregation to consume membership (unit).
3. `detect_obstructions` + the obstruction-aware `usable_area` formula (unit).
4. Per-plane yield → per-building collapse + `suitability` recompute; downstream regression (unit).
5. Wire `pipeline` (+ `run_city`) to the `"multiplane"` path end-to-end (integration smoke).
6. City re-run (Part 2-2's caching re-invoked) → `build_web` + commit `web/assets/` (no app rebuild).
7. Validation: DSM self-consistency + the spot-check harness + your ~20 labels.
8. The ML demo: `method="ml"` adapter (integration) + the comparison notebook (best-effort).

**Delegation note:** steps 1–4 (deterministic, clear contracts) are strong Sonnet-subagent TDD tasks,
reviewed against §4; steps 5–8 (pipeline wiring, the city re-run, validation judgement, the ML demo) stay
closer to hand.

## 9. Deliverables

- `fit_roof_planes(method="multiplane")` + pixel membership; per-plane zonal POA; `detect_obstructions` +
  obstruction-aware `usable_area(obstructions=…)`; per-building collapse — all unit-tested (§4/§7).
- `method="ml"` inference adapter + a ML-vs-classical comparison notebook (best-effort).
- A refreshed city dataset + `web/assets/` (re-run `build_web`, commit — no app rebuild).
- The validation write-up: DSM self-consistency (multi-plane vs single) + the ~20-roof spot-check, with
  uncertainty stated — the honest "is it better than RANSAC" story.
- ADR-0001-style follow-ups already recorded: ADR-0011/0012/0013; glossary updated.

## 10. Risks / dependencies

- **Multi-plane RANSAC tuning** (max planes, min area/inliers, τ, setback_factor) — real constants; keep
  them explicit/inspectable/reported (ADR-0013 spirit), tune on the spot-check set.
- **The `roof_planes → radiation` coupling** (ADR-0012) is the one new structural seam — contained, but
  the pipeline reordering (§3) must land before per-plane POA works.
- **Compute** — largely dissolves for the classical core: the re-run is Part 2-2's cost ≈ unchanged (the
  `r.sun` pass dominates, not plane-fitting). Only the **ML demo** wants a GPU, and it's inference-only on
  the M1. (The 12-day-timing figure just confirms the re-run cost; it's not a blocker.)
- **ML demo may not run cleanly** (unofficial, unmaintained code) — hence best-effort with fallbacks (§5).
- **Validation is honest, not conclusive** — no DC ground truth (risks §14.4); the spot-check is
  indicative. Said plainly, not papered over (Q2 / ADR-0011).

## 11. Downstream review checkpoint

When this part is built, re-read Part 2-5 (Fidelity) and revise it before starting — in particular whether
the multi-plane geometry + per-plane POA change how 2-5's 365-day / finer-DSM re-run is structured or
costed (it re-runs on top of this part's dataset). Part 2-5 already notes its map propagation is a clean
tile refresh (numbers-only); confirm that still holds once the `n_planes`/collapse changes land.
