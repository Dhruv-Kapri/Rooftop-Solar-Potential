# Phased roadmap (§12)

## Phase 0 · Scoping & de-risking — a few evenings  ← YOU ARE HERE

Pick the study area (recommended: **Washington, DC** — dense open LiDAR, and a well-documented Esri
reference workflow for a DC neighbourhood to benchmark against). Confirm 3DEP coverage/vintage, get
the NLR API key, stand up the repo and a data-access smoke test per source. Decide GRASS-vs-ArcGIS
for the radiation step now — **decided: GRASS `r.sun`.**

**Exit criteria:**
- [x] Study area + neighbourhood confirmed (DC / Glover Park); 3DEP LiDAR coverage/vintage verified
      (see findings below)
- [ ] NLR API key obtained — signup wizard ready: `bash scripts/setup_phase0.sh` (stage 5)
- [ ] GRASS GIS installed and callable — covered by the same wizard (stage 4)
- [x] A data-access check confirmed for each source — `scripts/check_data_access.py` (stdlib-only,
      runs before the conda env). Footprints/DSM/imagery/boundaries verified live; irradiance skips
      until the NLR key lands
- [ ] Add the styled plan (`Rooftop_Solar_Project_Plan.html`) and publish it via GitHub Pages so
      reviewers get the rendered version, not just the PDF download — **deferred to end of Phase 0**

**Phase 0 findings (data-access smoke test, verified):**
- The only `3dep-lidar-dsm` item covering the Glover Park bbox is
  `USGS_LPC_VA_Fairfax_County_2018-dsm-2m-...` — **vintage 2018**, and the id indicates a **2 m**
  derived DSM. Provenance is a project *named* for Fairfax County, VA whose tiles extend north into
  DC. Two things to decide before Phase 1: (a) 2 m is coarser than the ~1 m the plan assumes and
  bounds inter-building-shading fidelity (risks §8, trap 6) — if that ceiling is too low we derive a
  finer DSM ourselves from the raw 3DEP EPT point cloud via PDAL (risks §1); (b) 2018 is ~8 years
  old — confirm no major construction in the AOI since, or note it as a documented bias.

## Phase 1 · MVP — one neighbourhood, end-to-end · 2–3 weekends

One DC neighbourhood. Footprints + DSM + radiation (with shading) + RANSAC roof planes + PVWatts →
a per-building suitability score and a clean static map. **Explicit goal:** reproduce and
sanity-check against a published reference (Esri's Glover Park "Estimate solar power potential"
tutorial is the Phase-1 template). Ship it even if rough — it proves the whole spine works.

## Phase 2 · Full — city-scale + ML + deployed app · multi-week

Scale to the full District: tiling/batching for the whole point cloud, the ML segmentation model
(RoofN3D-trained) for planes + obstructions, census-tract aggregation, an equity overlay, and the
deployed web app. The portfolio centrepiece. Facade BIPV (§10) enters here as a stretch.

## Phase 3 · India — re-version via modular adapters · stretch

Swap data adapters behind the common interface for an Indian city (§13). Deliberately showcases
modular, provider-abstracted design — and the block-model path (§5) that traces straight back to the
SIH prototype. **Honest boundary:** DSM and imagery are resolution-limited, not source-limited, so
this drops to the block-model/LOD-1 path with a documented accuracy caveat on mutual shading.
