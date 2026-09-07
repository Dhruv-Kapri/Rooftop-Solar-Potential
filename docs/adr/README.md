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
