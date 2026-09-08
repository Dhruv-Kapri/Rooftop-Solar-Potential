# Architecture Decision Records

An ADR here records one decision the plan (`docs/Rooftop_Solar_Project_Plan.pdf`) left open or
underspecified — what we chose, why, and what it costs. Filenames follow `NNNN-title.md`, numbered
in the order the decision was made; do not renumber or reuse a number. ADRs 0001–0005 are Stage 1;
0006+ are Stage 2 (see `docs/plans/stage-2-overview.md`).

| ADR | Decision |
|---|---|
| [0001](0001-radiation-fidelity-12-day-sample.md) | Annual insolation from 12 monthly representative days, shading always on |
| [0002](0002-roof-plane-single-ransac-baseline.md) | Single-plane RANSAC per roof, with per-roof uncertainty carried through |
| [0003](0003-usable-area-filter-and-pv-constants.md) | Plan's hard usable-area cutoffs + notebook's utilization fraction and PV constants, combined |
| [0004](0004-suitability-score-percentile-rank.md) | Suitability score = within-AOI percentile rank of energy density |
| [0005](0005-benchmark-acceptance-intensive.md) | Esri benchmark passes on intensive quantities (±15%); totals reported, not gating |
| [0006](0006-equity-data-source-and-dimension.md) | Equity = energy burden (DOE LEAD) + income (ACS) on 2020 tracts; de-hosted composites optional only |
| [0007](0007-equity-metric-per-household-quadrant.md) | Equity metric = per-household potential × energy burden, as a 2×2 quadrant (priority tracts) |
| [0008](0008-lead-energy-burden-tract-aggregation.md) | DOE LEAD energy burden aggregated to 2020 tracts; missing/zero-household tracts flagged, never zeroed |
| [0009](0009-city-scale-tiling-and-idempotency.md) | City-scale via a fixed-origin tile grid + per-tile GeoParquet cache + resumable params-stamped manifest (idempotent) |
| [0010](0010-web-delivery-static-maplibre-pmtiles.md) | Web delivery = static MapLibre GL JS + PMTiles on GitHub Pages, no backend |
| [0011](0011-part2-4-classical-multiplane-ml-demo.md) | Part 2-4 = classical multi-plane RANSAC + DSM-residual obstructions; ML demoted to a bounded, inference-only demo |
| [0012](0012-per-plane-poa.md) | Per-plane POA — insolation selected per usable plane via pixel–plane membership |
| [0013](0013-obstruction-aware-usable-area.md) | Obstruction-aware usable area from DSM residuals — replaces the flat × 0.70 utilization fraction |
