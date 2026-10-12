# ADR-0014: Tiered per-tile cache — chained component-version keys + sidecars

- **Status:** Accepted
- **Date:** 2026-09-16
- **Phase:** 2 (Part 2-4/2-5 — cache tiering)
- **Context tags:** caching, city-scale, idempotency, incremental-recompute

**Supersedes:** ADR-0009 (the single params-stamp cache-invalidation rule; the fixed-origin grid +
floor membership + resumable manifest from 0009 all stand).

## Context
ADR-0009 gave `run_city` one `params_stamp`: a single JSON fingerprint of every param that could
change a tile's result, folded together with one `PIPELINE_VERSION`. That is exactly right for
"is this tile still correct end-to-end" but it is a single all-or-nothing key — bumping
`PIPELINE_VERSION` for a **downstream-only** change (e.g. a roof-plane or usable-area tweak, the
kind Part 2-4/2-5 keep making) invalidates *every* cached tile, including its `r.sun` radiation
raster. The full-DC batch is a ~10-15h job (`rerun-cost-rsun-not-cached` memory) and `r.sun` —
uncached today — dominates that cost. A single stamp forces re-running the most expensive stage
for a change that never touched it.

The fix is to split the cache into the three tiers that actually have independent lifetimes:

1. **DSM** — changes only when the DSM build (`dsm.build_dsm`) or the tile grid/geometry changes.
2. **Radiation** — changes only when the DSM changes (it consumes the DSM) or the `r.sun` model
   itself changes (day range, and eventually the Part 2-5 shadow/horizon parameters).
3. **Scoring** — changes when radiation changes, or anything purely downstream of it changes (roof
   planes, usable area, yield) — the tier Part 2-4/2-5 iterate on most.

Each tier's key **chains** the tier above it, so invalidation only ever cascades *downward*:
bumping `RADIATION_VERSION` invalidates radiation + scoring but leaves the DSM cache untouched;
bumping `SCORING_VERSION` (the common case during Part 2-4/2-5 tuning) leaves both DSM and
radiation caches untouched — the expensive `r.sun` pass is reused as-is.

This ticket (Seam A1) builds only the pure, deterministic key-derivation + sidecar surface. It
does **not** wire anything into `dsm.py`, `radiation.py`, or `run_city` — that's #10 (DSM
self-cache), #11 (radiation self-cache), and #12 (wiring `scoring_key` in as the `TileManifest`
stamp, replacing `params_stamp`). During the migration `params_stamp`/`PIPELINE_VERSION` and the
new key functions **coexist** unchanged in `pipeline.py`.

## Decision
1. **Three component versions**, one per tier, each bumped independently when that tier's
   behavior changes:
   - `DSM_VERSION` — the DSM build changes.
   - `RADIATION_VERSION` — the `r.sun` radiation model changes (e.g. Part 2-5 wiring an explicit
     `maxdistance`/horizon parameter, risks §8 trap 3).
   - `SCORING_VERSION` — anything downstream of `r.sun` (roof planes / usable area / yield)
     changes. Carries forward the retired single-stamp generation `"2-4.1"` (the flat-roof GCR
     calibration) as its starting value, so history isn't lost at the migration seam.
2. **A chained key per tier**, each a stable JSON encoding (`sort_keys=True`, list not tuple, as in
   `params_stamp`) so identical inputs give identical strings:
   - `dsm_key(origin, tile_size_m, buffer_m, row, col, resolution_m, dsm_source, version=DSM_VERSION)`
     — the root of the chain: grid geometry + tile identity + which DSM build + `DSM_VERSION`.
   - `radiation_key(dsm_key, day_range, version=RADIATION_VERSION, horizon_params=None)` — chains
     the parent `dsm_key` string, adds the (order-insensitive) radiation day range and
     `RADIATION_VERSION`. `horizon_params` is a **reserved slot**, defaulted to `None` today, for
     the Part 2-5 shadow/horizon parameters — adding a real value later is a key change, not a
     schema break.
   - `scoring_key(radiation_key, footprints_source, method, version=SCORING_VERSION)` — chains the
     parent `radiation_key` string, adds the footprints source and the geometry `method`
     (`"multiplane"`/`"ransac"`) and `SCORING_VERSION`. This is the string #12 makes the
     `TileManifest` stamp, in place of `params_stamp`.
3. **A `.key` sidecar per cached artifact**, read/written by a new low-level `cache.py` module
   (`sidecar_path` / `write_key_sidecar` / `read_key_sidecar` / `sidecar_matches`). A cached
   artifact (a tile's DSM raster, its radiation raster) is reused only when **both** the artifact
   file and its sidecar exist and the sidecar's contents match the current key — a half-written or
   externally-deleted artifact, or a stale sidecar, forces recompute, never a silent stale hit.
4. **`cache.py` has zero imports from the `rooftop_solar` package** (stdlib + `pathlib` only).
   `pipeline.py` already imports `dsm` and `radiation`; #10/#11 make `dsm.py`/`radiation.py`
   import the sidecar helpers to self-cache. If those helpers lived in `pipeline.py`, that would
   create `dsm -> pipeline -> dsm` / `radiation -> pipeline -> radiation` import cycles. Keeping
   `cache.py` dependency-free avoids the cycle entirely; `dsm.py`/`radiation.py` receive a
   ready-made key **string** from `pipeline.py`'s key functions and never call them directly.

## Consequences
- A downstream-only change (the common Part 2-4/2-5 case) bumps only `SCORING_VERSION`, so
  `run_city` reuses every tile's cached DSM and radiation raster and only re-scores — instead of
  re-running the ~10-15h uncached `r.sun` pass for a change that never touched radiation.
- The three keys are opaque JSON strings (like `params_stamp`), not human-readable — sensitivity
  and stability matter, not legibility.
- `params_stamp`/`PIPELINE_VERSION`/`TileManifest` stay wired into `run_city` unchanged until #12;
  two invalidation schemes coexist in `pipeline.py` during the migration, which is a known,
  temporary duplication, not an oversight.
- The reserved `horizon_params` slot means Part 2-5's shadow/horizon wiring won't need a
  `radiation_key` signature change — only a non-`None` value.
- `cache.py`'s independence is a real constraint on future edits to it: it must never gain a
  package import, or the cycle #10/#11 are designed to avoid comes back.

## Alternatives considered
- **Content-hash keys** (hash the actual DSM/radiation raster bytes) — rejected as
  over-engineering: it requires reading back every artifact to hash it (defeating the point of a
  cheap cache check) and gives no more invalidation precision than a component version, since the
  inputs that change a tier's output are already enumerable and small. Component versions chosen.
- **Keep the single `params_stamp`** — rejected: it invalidates the whole cache, `r.sun` included,
  on any downstream-only change, which is exactly the cost problem this ADR exists to fix.
- **Put the key functions in `cache.py`** — rejected: only `pipeline.py` and tests need to *derive*
  keys; `dsm.py`/`radiation.py` only need to *store/check* a key they're handed, and moving
  derivation into the dependency-free module buys nothing while making `cache.py` a bigger,
  less-obviously-safe surface to keep import-free.

## Addendum (2026-10-12) — engine version in the radiation key
The radiation tier's output depends on the GRASS build that ran `r.sun`, but `radiation_key` didn't
record it. Once the cache can be handed between machines (the office VM `graham-build` ↔ the Mac,
rsync handoff — Part 2-4 completion grill), two different GRASS versions could silently feed one
city dataset. **Decision:** `radiation_key` also carries the GRASS version string, so a cache made by
a different engine version is recomputed, not reused. Chosen over a documented "pin the same GRASS
on both machines" rule (unenforced) and over never handing off radiation (loses the cross-machine
resume). Invalidates no existing work — no radiation cache had been populated when this was decided.
