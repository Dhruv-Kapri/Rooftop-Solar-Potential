# Architecture

## Core design insight — building resolution × city extent (§3)

Keep **building-level resolution while operating at city-wide extent**, then aggregate up.
This is what Google Project Sunroof and NREL/NLR rooftop-potential studies do: score tens of
thousands of individual roofs across a metro, then roll up to neighbourhood / census-tract
choropleths for planning and equity analysis.

## Two modelling modes behind one interface (§5)

The geometry front-end is swappable; radiation → yield → aggregation is identical downstream.

- **LiDAR per-roof (v1 default, high-resolution).** Clip the LiDAR point cloud per footprint, fit
  individual roof planes (RANSAC baseline, or the ML route in §9), resolve genuine per-roof slope,
  aspect, and obstructions. This is the Washington-DC v1 default and the source of the project's
  resolution story.
- **Block-model / LOD-1 (heritage + fallback).** Extrude footprints to a single height per building
  (the LOD-1 model the SIH prototype ran on). No roof-plane detail, but still supports full
  inter-building shading and a flat-roof / fixed-tilt yield estimate. First-class citizen, not an
  afterthought: the honest fallback wherever LiDAR is unavailable (§13, India) and the direct
  continuation of the prototype.

## Provider interface (Phase 3 — not built yet)

The India re-version (§13) introduces four adapter seams so the core pipeline stays fixed and only
the data adapters change:

```
FootprintSource · ElevationSource · ImagerySource · IrradianceSource
```

**Deferred deliberately.** Building these abstractions now (at Phase 0/1, one US city) would be
premature. They land in Phase 3 when a second city (India) creates the actual need. The honest
boundary: footprints, irradiance, and aggregation are true adapter swaps; **DSM and imagery are
limited by *resolution*, not source** — so the India version drops to the block-model path (LOD-1 +
flat-roof/fixed-tilt), and inter-building shading degrades to a coarse sky-view-factor estimate.
State that accuracy hit plainly.

## Delivery — stack-neutral (§11)

- **Publish results:** turn the GeoDataFrame into hosted layers (vector tiles / hosted feature
  service) consumable by any web-map client.
- **Interactive deliverable:** a web map wrapped in a lightweight app. Framework-agnostic.
- **Esri route:** the full ArcGIS Online / Experience Builder / `arcgis.learn` path is a first-class
  *supported* deployment — see Appendix A of the plan and the GeoAI reading-list docs in `docs/`
  ([`Foundations_Geospatial_ML.pdf`](Foundations_Geospatial_ML.pdf),
  [`Resources_GeoAI_Solar.pdf`](Resources_GeoAI_Solar.pdf)). It is decoupled from the core so the
  pipeline is not coupled to one vendor.

## Where the ML lives (§9) — Phase 2

Two legitimate options:
1. **Deep-learning roof-plane & obstruction segmentation.** Train/fine-tune on RoofN3D (TU Berlin);
   reference RoofSeg / SPPSFormer (point-cloud) and U-Net / Mask R-CNN (imagery). Segmenting
   obstructions (chimneys, vents, HVAC) that eat usable area is where accuracy pays off.
2. **Learned suitability / ranking model.** Gradient boosting or a small NN over engineered features
   (slope, aspect, irradiance, usable area, self-shading), trained against PVWatts/Sunroof-style
   labels. Simpler to ship, pairs with SHAP-style attribution for interpretability.
