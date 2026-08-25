# Phased roadmap (§12)

## Phase 0 · Scoping & de-risking — a few evenings  ← YOU ARE HERE

Pick the study area (recommended: **Washington, DC** — dense open LiDAR, and a well-documented Esri
reference workflow for a DC neighbourhood to benchmark against). Confirm 3DEP coverage/vintage, get
the NLR API key, stand up the repo and a data-access smoke test per source. Decide GRASS-vs-ArcGIS
for the radiation step now — **decided: GRASS `r.sun`.**

**Exit criteria:**
- [ ] Study area + neighbourhood confirmed, 3DEP LiDAR coverage/vintage verified
- [ ] NLR API key obtained
- [ ] GRASS GIS installed and callable
- [ ] A data-access check confirmed for each source (footprints, DSM, imagery, irradiance, boundaries)
- [ ] Add the styled plan (`Rooftop_Solar_Project_Plan.html`) and publish it via GitHub Pages so
      reviewers get the rendered version, not just the PDF download

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
