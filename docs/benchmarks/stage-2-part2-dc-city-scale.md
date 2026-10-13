# Stage 2 · Part 2-2 — city-scale results (whole District of Columbia)

- **Date:** 2026-09-08 · **Phase:** 2 (Part 2-2) · **Plan:** [`stage-2-part2-plan.md`](../plans/stage-2-part2-plan.md) · **ADR:** [0009](../adr/0009-city-scale-tiling-and-idempotency.md)
- **Reproduced by:** `python scripts/run_city.py` → `outputs/12day/` (calibrated 12-day) · `--day-range 172` → `outputs/1day/` (fast preview)
- **Acceptance:** no external city-wide benchmark exists — accepted on **invariants + sanity + the payoff** (plan §6), like Part 2-1.

> **STATUS: COMPLETE — the calibrated 12-day full-DC run landed 2026-09-08** (66 tiles, 100,064 roofs,
> ~4.85 h wall-clock; deliverables in `outputs/12day/`). Both columns below are real full-DC data:
> **1-day** is an uncalibrated single-day preview (geometry/counts exact, absolute energy not usable),
> **12-day** is the ADR-0001 calibrated run. This doc is the §8 deliverable.

## Verdict

**Accepted.** **Seam conservation** holds (Σ per-tile roof counts = 100,064 merged — every DC building
scored once) and **idempotency** holds — both verified by the `integration` smoke
(`tests/test_pipeline_city_integration.py`). The tract **equity map is populated** (206 tracts, 58
priority, 2 data-missing — Part 2-1's partial-coverage artifact resolved). City totals sit **above**
NREL's DC technical potential (see Sanity) — a documented over-estimate from the usable-area/footprint
model, **not** a radiation error; the **specific yield recovers to ~1,124 kWh/kWp** (near Stage-1's
Esri-validated ~1,162), which is the calibration signal that matters.

<p align="center">
  <img alt="Washington DC census tracts coloured by a per-household-potential x energy-burden equity quadrant, priority tracts outlined" src="../assets/stage2-dc-equity-quadrant.png" width="70%">
  <br>
  <sub><em>The Phase-2 payoff (calibrated 12-day): each DC tract placed in a 2×2 equity quadrant
  (per-household solar potential × energy burden); the <strong>58 priority</strong> tracts — high
  potential <em>and</em> high burden — are where rooftop solar does the most equity good.</em></sub>
</p>

## The dataset (day-count-independent — holds for both runs)

| Property | Value |
|---|---|
| Roofs scored, whole DC | **100,064** (usable **57,648**) |
| City per-roof file | **18.1 MB** GeoParquet (`dc_roofs.parquet`) |
| Geometry weight | ~676k vertices (mean **6.9/roof** — simple MS footprints) |
| Grid | **66 tiles** @ 2 km (fixed origin `(315000, 4295000)`, EPSG:6347); **0 empty** (dense urban) |
| Census tracts covered | **206** (priority **57**, data-missing **2**) |

## Compute-cost run-log (§8)

| | 1-day (preview) | 12-day (calibrated) |
|---|---|---|
| Tiles | 66 (65 + 1 retried after a transient auth failure) | 66 (all with roofs, 0 empty) |
| Failed tiles | 0 (after one resume pass) | 0 |
| Wall-clock (cold) | not captured¹ | 17,454 s (~4.85 h) |
| Per-tile mean | — | 264.5 s |

¹ The original 1-day run's timing was lost to a since-fixed CLI crash; the resume was cache-warm
(2.9 s), so it is **not** a real cold-run figure. The **12-day cold run above is the authoritative
timing** — the figure Parts 2-4/2-5 budget their re-runs against.

### Multi-core `r.sun` — measured per-tile speedup (#16)

`run_city(nprocs=N)` / `run_city.py --nprocs N` forwards a thread count to every tile's `r.sun`
(GRASS 8.5 OpenMP). It is **not** part of any cache key — the output is **bit-identical** at every
thread count (max |diff| = 0, checked below). Measured with `scripts/bench_rsun_threads.py` on the
largest tile (`6_7`, 1300×1300 px @ 2 m), radiation step only, on the dev Mac (8 cores = 4P + 4E):

| `nprocs` | 12-day wall-clock | speedup | identical to 1 thread |
|---|---|---|---|
| 1 | 324.4 s | 1.00× | — |
| 4 | 116.1 s | **2.79×** | ✓ |
| 8 | 90.7 s | **3.58×** | ✓ |

One quiet-window run of all three counts back-to-back, so they are directly comparable: dev Mac
(8 cores = 4P + 4E), host 1-min load ~5 at start and 3.0 / 5.7 / 10.6 reported at the end of the
1 / 4 / 8-thread runs (the rise tracks the benchmark's own threads; no external load). It replaces an
earlier run whose 8-thread row was contaminated by other sessions' jobs (load ~52). On this 4P+4E
part the 8 threads include efficiency cores, so scaling is sub-linear. Every reading is **below
~4×**, which is why tile-level worker processes were tried next (#27, below).

Budget implication: `--nprocs 8` cuts the radiation step ~3.6× per tile (`--nprocs 4` ~2.8×); with
the default `--workers 1` tiles run in sequence, and DSM/footprint/plane stages are unaffected.

### Tile-level workers × `r.sun` threads — measured throughput (#27)

`run_city(workers=K)` / `run_city.py --workers K` scores K tiles at once in spawned worker processes
(ADR-0009 addendum), each running `r.sun` on `--nprocs` threads. Like `nprocs`, `workers` is in no
cache key; output is **bit-identical** at every split. Measured with `scripts/bench_rsun_workers.py`
on 8 full-size tiles (`5_4 5_5 5_6 5_7 6_5 6_6 6_8 7_5`, each 1300×1300 px @ 2 m), 12-day, radiation
step only, at a fixed 8-core budget, on the VM `graham-build` (8 vCPU Xeon Gold 6530, GRASS 8.5.0,
`nice -n 10`):

| split (`workers × nprocs`) | 8 tiles wall-clock | tiles/h | vs 1 × 8 | identical |
|---|---|---|---|---|
| 1 × 8 | 680.0 s | 42.4 | 1.00× | ✓ |
| 8 × 1 | 747.4 s | 38.5 | **0.91×** | ✓ |

One quiet-window pair, run back-to-back (VM 1-min load ~1.3 at start; 7.8 / 1.6 at the end of each
split is the benchmark's own load). An earlier full sweep ran while the VM's other tenants were busy
(load 12–14 during the first two splits) and is **not** comparable across splits: 1 × 8 1265.9 s and
2 × 4 1851.9 s (both contaminated), 4 × 2 1221.5 s (load fell to ~3 during the split), 8 × 1 736.2 s
(quiet). Only its 8 × 1 reading is clean, and it agrees with the pair above within 2%.

**Reading:** for the radiation stage, worker processes do **not** beat `r.sun`'s own threads on the
VM: 8 threads on one tile already use the cores slightly better than 8 single-threaded tiles. This
measures `r.sun` only. Workers could still help the fetch/DSM/plane stages, which `nprocs` doesn't
speed up and which are partly network-bound, but that end-to-end gain is **unmeasured**.

**Defaults chosen:**
- **VM:** `--workers 1 --nprocs 8` (the code default for `workers` is 1). Worth an end-to-end A/B
  against `--workers 2 --nprocs 4` on the first full-city run (#19) before switching.
- **Mac:** `--workers 1 --nprocs 8`, **provisional**. The Mac sweep was stopped when the host
  ran low on memory (8 GB, other projects' jobs at load ~24–44). `workers > 1` multiplies per-process
  memory (each worker imports the geo stack and holds its tile's rasters), so the Mac needs a quiet
  window before it is measured.

## 1-day vs 12-day — what fidelity changes (and what it doesn't)

The comparison is the point: single-day vs 12-day calibrated is a **radiation-fidelity** difference.
Absolute magnitudes scale; the **equity ranking is expected to be robust** (single-day inflates every
roof ~proportionally, so relative per-household potential — and the quadrant classification — barely
moves). **Confirm once the 12-day run lands.**

| Quantity | 1-day (uncalibrated) | 12-day (calibrated) | Note |
|---|---|---|---|
| Installed capacity | 3,619 MW (~3.6 GW) | **3,619 MW** | day-independent (usable area) — confirms capacity doesn't scale with fidelity |
| Annual energy | 5,998 GWh/yr (~6.0 TWh) | **4,067 GWh/yr (~4.1 TWh)** | calibration cuts it ~32% |
| Annual CO₂ offset | ~2,099 kt/yr (~2.1 Mt) | **~1,424 kt/yr (~1.4 Mt)** | scales with energy |
| **Specific yield** (kWh/kWp/yr) | **~1,657** | **~1,124** | **prediction held:** within ~3% of Stage-1's Esri-validated ~**1,162** ✓ |
| Priority tracts (high × high) | 57 / 206 | **58 / 206** | **~unchanged** as predicted (ranking robust) |

## Sanity vs NREL (a smell test, not a gate — risks §15)

NREL's DC rooftop-PV **technical potential** is order **~1.3 GW / ~1.6 TWh/yr** (confirm exact figure
vs risks §15). The **1-day** totals (~3.6 GW / ~6.0 TWh) were inflated by the single-day extrapolation.
The **12-day calibrated** totals (**~3.6 GW / ~4.1 TWh**) fix the *energy* — specific yield recovers to
~1,124 kWh/kWp, near the Esri ~1,162 — but still sit **~2.8× / ~2.5× above NREL**, because **capacity**
is day-independent and our **usable-area estimate overstates** it (the flat ×0.70 utilization + MS
footprint rowhouse merging, ADR-0003/0005). This is a *geometry / usable-area* over-estimate, not a
radiation one — and it is exactly the lever **Part 2-4** attacks (obstruction-aware usable area,
ADR-0013). Reported honestly, not smoothed over.

## Caveats

- **1-day absolute numbers are not real** — single-day extrapolation. Use them only for scale/ranking.
- **MS footprint rowhouse merging** (ADR-0005) persists at scale: per-*building* metrics are unstable,
  but aggregates, specific yield, and the **per-household** equity metric are robust to it.
- **DSM vintage seam** across the District (2018 vs 2014/15 3DEP mosaics; roadmap Phase-1 finding) grows
  city-wide — a data-quality note, not silently averaged over.
- **Transient Planetary Computer auth/SAS failures** on a long run cost ~1 tile; **skip-and-continue**
  records it and a **resume** pass retries it (verified: tile `11_3` failed then succeeded on resume).
- **Radiation fidelity is the 12-day sample** (ADR-0001); the DSM is 2 m PC (ADR-0009). Finer DSM /
  365-day sum / explicit `r.horizon` are Part 2-5, and would re-run this whole city pass.
