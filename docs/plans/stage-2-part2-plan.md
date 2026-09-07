# Stage 2 · Part 2-2 plan (tentative) — City scale

> **STATUS: tentative** — a hypothesis written 2026-09-07, before Part 2-1 exists. Per the
> living-plans protocol ([`stage-2-overview.md`](stage-2-overview.md) §4), **re-read and revise this
> before starting it**, against what the earlier parts actually taught. Guesses are flagged `ASSUMPTION:`.

- **Phase:** 2, Part 2 of 5 · **Branch:** `stage-2` · **Overview:** [stage-2-overview.md](stage-2-overview.md)
- **Prerequisite:** Part 2-1 built.

## 1. What this part is

Run the whole Stage-1 spine (footprints → DSM → radiation → roof planes → usable area → yield) plus
Part 2-1's aggregation across the **entire DC District**, instead of the Glover Park bbox. No new
analytics — this is compute/orchestration scaling of machinery that already works at one bbox
(overview §2 table). It is also **the expensive re-run** that Part 2-4/2-5 pay for again later
(overview §3) — that tradeoff is the reason this part's build quality matters more than its logic.

## 2. Where it continues from

- The Stage-1 spine (`pipeline.run_stage1`), unmodified in its per-stage logic.
- Part 2-1's `aggregate.py` — asserted **scale-agnostic** already (Part 2-1 decision, overview §5), so
  it should need no changes here, only a District-wide `tracts` GeoDataFrame instead of ~2 tracts.
- `aoi.py`'s existing core/buffer pattern (`core_aoi_wgs84`, `buffered_aoi`, `AOI_BUFFER_M = 300 m`)
  and `pipeline.run_stage1`'s existing tile-edge dedup rule —
  `fp.geometry.representative_point().within(core_working)` — which already exists for the
  single-bbox case and is the natural rule to reuse per-tile.

## 3. Likely approach (tentative)

- ASSUMPTION: tile the District into a grid of AOIs, each sized well within GRASS memory/runtime
  limits, each carrying the *same* 300 m shadow buffer overlap `aoi.py` already implements — so tiling
  is "run the existing core/buffer machinery N times over a grid," not new geometry logic.
- ASSUMPTION: one GRASS session per tile (GRASS locations are not trivially shared across concurrent
  runs); tiles are independent units of work, so they parallelize (process-level) if compute allows.
- ASSUMPTION: cache per-tile outputs on disk and **skip tiles whose cache is already complete** —
  this part MUST be idempotent / tiled / cached / resumable (overview §3's re-run tradeoff depends on
  it: a partial run, a crash, or a Part 2-4/2-5 accuracy swap should resume or re-run incrementally,
  not from scratch).
- ASSUMPTION: dedupe buildings that straddle tile boundaries using the existing
  representative-point-in-core rule (`pipeline.run_stage1` already does this per-bbox) — each building
  is scored by exactly the tile whose core contains its representative point, even though it may be
  fetched (as a shadow-caster) by neighbouring tiles too.
- ASSUMPTION: stitch per-tile per-roof tables into one district-wide dataset in **GeoParquet** (not
  GeoPackage — better suited to a dataset this size and to partitioned/incremental writes). Revisit if
  Part 2-1 chose differently for its own outputs.
- Then aggregate to **all DC tracts** by calling Part 2-1's `aggregate_to_tracts` / `attach_equity` /
  `classify_equity` untouched on the stitched dataset.

## 4. Key questions to grill when we reach this part

- Tile size vs GRASS memory/runtime — what's the largest tile that stays fast and reliable?
- Parallel vs serial tile execution — worth the complexity, or is serial-but-resumable good enough?
- The mixed-DSM-vintage seam across the city (roadmap Phase-1 finding: Glover Park alone already
  mosaics 2018 and 2014/15 tiles) — does this get worse city-wide, and does it need to be documented
  per-tile or handled specially?
- Is the 2 m Planetary-Computer DSM good enough city-wide, or do we need to self-derive a finer DSM
  from raw 3DEP EPT via PDAL (roadmap Phase-0 finding; risks §1)? This is a compute/accuracy tradeoff
  that changes the whole part's cost.
- What's the actual compute budget (time, $, machine) for a full-District run, and does that change
  the tiling/parallelism answer?
- Final storage format — GeoParquet assumption above vs something else (PMTiles, a spatial DB) chosen
  with Part 2-3's needs in mind.
- Does the existing `AOI_BUFFER_M = 300` (sized for Glover Park) still make sense city-wide, or does
  downtown (taller buildings, risks §8 trap 3) need a larger buffer / search distance than the
  residential edges?

## 5. Tentative deliverables

- A tiling/orchestration module or script (name TBD) that partitions the District, runs the spine +
  Part 2-1 aggregation per tile, and is safely re-runnable.
- A per-tile cache layout under `data/`/`outputs/` (gitignored, per repo convention).
- A district-wide per-roof dataset (GeoParquet, ASSUMPTION) and a district-wide tract GeoPackage
  (reusing Part 2-1's writer).
- A short run-log / report on actual compute cost, for Part 2-4/2-5 to budget against.

## 6. Risks / dependencies

- This is the expensive compute the overview's re-run tradeoff (§3) is about: get idempotency wrong
  here and every later accuracy swap (2-4, 2-5) becomes a full re-run instead of an incremental one.
- Depends on Part 2-1's aggregation genuinely being scale-agnostic, as asserted — first real test of
  that claim.
- Depends on DSM coverage/vintage holding up city-wide (risks §1, roadmap Phase-0/Phase-1 findings).
- Compute budget is unknown until measured; may force a scope cut (fewer tiles, coarser DSM) back into
  this plan.

## 7. Downstream review checkpoint

When this part is built, re-read the remaining downstream plans (2-3, 2-4, 2-5) and revise them
before starting the next — in particular, 2-3 needs the real storage format and dataset scale this
part actually produced, and 2-4/2-5 need the real per-tile re-run cost this part measured.
