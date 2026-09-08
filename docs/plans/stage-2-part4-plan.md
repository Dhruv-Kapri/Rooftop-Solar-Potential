# Stage 2 · Part 2-4 plan (tentative) — ML segmentation

> **STATUS: tentative** — a hypothesis written 2026-09-07, before Part 2-3 exists. Per the
> living-plans protocol ([`stage-2-overview.md`](stage-2-overview.md) §4), **re-read and revise this
> before starting it**, against what the earlier parts actually taught. Guesses are flagged `ASSUMPTION:`.

- **Phase:** 2, Part 4 of 5 · **Branch:** `stage-2` · **Overview:** [stage-2-overview.md](stage-2-overview.md)
- **Prerequisite:** Part 2-3 built.

## 1. What this part is

A RoofN3D-trained multi-plane + obstruction segmentation model, swapped in **behind the already-wired
seams** — `roof_planes.fit_roof_planes(method="ml")` and `usable_area.usable_area(obstructions=…)` —
replacing the RANSAC single-plane baseline (ADR-0002) with per-roof multi-plane geometry and real
obstruction polygons. This is the accuracy upgrade the overview (§3) deliberately placed *after* the
end-goal product (2-3) ships, accepting that it must be **re-propagated by re-running Part 2-2**
(the re-run tradeoff, overview §3).

Today `fit_roof_planes` explicitly rejects any `method` other than `"ransac"`
(`src/rooftop_solar/roof_planes.py:112-117`, raising "not implemented — only the Phase 1 baseline"),
and `usable_area`'s `obstructions` parameter is accepted but unused (`usable_area.py:74-75`). This
part fills both without changing their call signatures.

## 2. Where it continues from

- Part 2-2's district-wide run (the dataset this part's swap will eventually be re-run against).
- Part 2-3's deployed map — the consumer of the per-roof output. **Propagation (from Part 2-3's §10
  checkpoint, 2026-09-08):** an accuracy re-run reaches the live map by re-running
  `scripts/build_web.py` against the refreshed `outputs/` and committing the new `web/assets/`
  (`roofs.pmtiles` + `tracts.geojson`) — the `web/` app code is **not** rebuilt (ADR-0010). But this
  "no app rebuild" guarantee holds **only if 2-4's output stays a per-building roof shape** carrying the
  exact fields `web_build.roofs_to_web` + the map consume (`usable` + `suitability` / `capacity_kw` /
  `annual_energy_kwh`; Part 2-3 §5). A true **per-plane** output (the schema change flagged in §3) would
  additionally require updating `web_build.roofs_to_web` and `web/main.js` (roof layer + popups) — fold
  that into the multi-plane schema decision, don't discover it at tile-build time.
- The existing seam contracts: `fit_roof_planes(footprints, dsm_path, method="ransac")` and
  `usable_area(roof_planes, obstructions=None)` — **no new architecture is proposed here**; this part
  is scoped to filling those two seams only.

## 3. Likely approach (tentative)

- ASSUMPTION: per architecture §9, choose between a point-cloud model (RoofN3D / RoofSeg / SPPSFormer,
  operating on the same DSM/LiDAR clip Stage 1 already produces) and an imagery model (U-Net / Mask
  R-CNN, requiring an imagery source not yet wired into the pipeline). Point-cloud keeps the input
  data the same as today; imagery adds a new data dependency.
- ASSUMPTION: a `torch` (or equivalent) dependency is added, scoped to this part / an optional extra,
  not the base `environment.yml`.
- ASSUMPTION: output is **multiple planes per roof** (not one) plus **obstruction polygons** per roof,
  the latter feeding `usable_area`'s currently-unused `obstructions` argument to subtract obstructed
  area from usable area.
- **Flagged loudly, not an ASSUMPTION to skim past:** moving from one plane per roof to multiple planes
  per roof is a **schema change** to the per-roof contract downstream stages consume (`yield_pv.py`,
  `aggregate.py`, the map layers) — likely one row per *plane* rather than per *building*, or an
  aggregated-back-to-building shape. This needs a concrete decision before implementation starts, not
  during it.

## 4. Key questions to grill when we reach this part

- Model + training-data choice — point-cloud vs imagery, and which specific architecture (RoofSeg,
  SPPSFormer, Mask R-CNN, …) — resolve against what Part 2-2/2-3 actually made available.
- GPU/compute availability and cost for training and for city-scale inference.
- **The RoofN3D-is-NYC-specific ground-truth gap** (risks §14.4: "No large public ground-truth for
  arbitrary US cities... RoofN3D is NYC-specific") — how do we validate a model trained on NYC data
  against DC roofs with no DC ground truth? Transfer-learning risk, or find/build a small DC
  validation set?
- The multi-plane schema change flagged in §3 above — exactly how does the per-roof contract change,
  and what breaks downstream (yield, aggregation, the map) until they're updated?
- Accuracy target vs the RANSAC baseline — what's the bar for "this is actually better," and how is it
  measured without ground truth (proxy metrics? visual QA? a held-out benchmark neighbourhood)?

## 5. Tentative deliverables

- A trained (or fine-tuned) segmentation model, with its training/validation approach documented.
- `roof_planes.fit_roof_planes(method="ml")` implemented; `usable_area(obstructions=…)` implemented.
- A documented schema change for multi-plane output, propagated through `yield_pv.py` / `aggregate.py`,
  and — **only if the per-building roof shape changes** — through `web_build.roofs_to_web` +
  `web/main.js`. The live map is then refreshed by re-running `scripts/build_web.py` + committing
  `web/assets/` (no `web/` rebuild otherwise; Part 2-3 §10).
- A Part 2-2 re-run (full or incremental, per its caching) with the ML path, and an accuracy
  comparison against the RANSAC baseline.

## 6. Risks / dependencies

- **Highest research risk in Stage 2** — model choice, training data, and validation are all open.
- Ground-truth validation is the crux (risks §14.4) — this part may not converge on a clean accuracy
  number, and that should be reported honestly rather than papered over.
- The schema change is a cross-cutting risk: every downstream consumer (yield, aggregation, map) needs
  to be checked, not just the two seam functions.
- Re-running Part 2-2 at full district scale costs whatever Part 2-2 measured (§6 of that plan) — a
  second time.

## 7. Downstream review checkpoint

When this part is built, re-read the remaining downstream plan (2-5) and revise it before starting the
next — in particular, whether the multi-plane schema change affects how 2-5's radiation-fidelity
re-run is structured or costed.
