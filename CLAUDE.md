# CLAUDE.md — Rooftop Solar Potential

Orientation for Claude Code sessions in this repo. Read this first, then the plan docs.

## What this is

A city-scale pipeline that scores **every rooftop** for solar suitability: per-roof geometry →
orientation/tilt/shading → irradiance → **PV capacity, annual energy, CO₂ offset**, aggregated to
census tracts with an equity lens. Built from **public data + open-source Python**, delivered as an
**interactive web map**. It is the engineering evolution of a 2024 Grasshopper + Ladybug hackathon
prototype (SIH1739) into a reproducible, city-scale pipeline.

**Framing:** this is a general geospatial-analysis project. The Esri / ArcGIS stack is *one
supported deployment target, not a dependency*. Keep language stack-neutral — don't over-index on
Esri.

## Status

**Phase 1 MVP complete (2026-09-07); Phase 2 in progress — Parts 2-1 (aggregation + equity), 2-2
(city-scale tiling) and 2-3 (web map) built; Part 2-4 steps 1–7 done (2026-10-18).** The full spine —
footprints → DSM → shaded `r.sun` radiation → roof planes → usable area → PV yield + suitability — runs
end-to-end for Glover Park (`scripts/run_stage1.py`) and passes the ADR-0005 benchmark gate. Part 2-1
fills the last stub (`aggregate.py`), adds the census/equity data-access module (`tracts.py`), and rolls per-roof
results up to census tracts with an equity overlay (`scripts/run_aggregation.py`; ADR-0006/0007/0008).
Part 2-2 scales the spine over the **whole District** by tiling: `tiling.py` (fixed-origin grid + `floor`
membership) + `pipeline.run_city` (per-tile GeoParquet cache, resumable manifest/params-stamp, city-wide
merge + suitability recompute; `scripts/run_city.py`; ADR-0009). Both test tiers are green (unit +
network/GRASS integration smoke). The full-DC batch (66 tiles @ 2 km, `scripts/run_city.py`) has since run —
see Part 2-4 below. Deliverables are run-labelled under `outputs/dc/<label>/`. `run_city` writes to a
version-stamped label (`pipeline.city_run_label`, e.g. `12day_2-4.3`), so a `SCORING_VERSION` bump can't
overwrite an earlier run. The map serves `config.PUBLISHED_CITY_LABEL` (#32).
**Part 2-3 — the deployed web map — is built and live** at
<https://dhruv-kapri.github.io/Rooftop-Solar-Potential/>: a static MapLibre GL JS + PMTiles map on
GitHub Pages (`web/`, `scripts/build_web.py`; ADR-0010), ETL unit-tested with build + browser smokes
green, PMTiles range-requests on Pages confirmed. It serves the **Part 2-4 run `12day_2-4.3`** (below;
#32). Capacity/energy still overstate NREL ~1.6–1.7×, a usable-area over-estimate, reported not
targeted. The headline figures live in one place, the validation write-up's
[published map run](docs/benchmarks/stage-2-part4-validation.md#published-map-run); don't copy them here.
**Part 2-4 — roof-geometry refinement (classical multi-plane + obstructions) — steps 1–7 done
(2026-10-18).** Reframed from "ML segmentation" to a classical, training-free upgrade
(ADR-0011/0012/0013): multi-plane sequential RANSAC (`fit_roof_planes(method="multiplane")`), per-plane
POA (`radiation.plane_poa`), obstruction-aware usable area (`detect_obstructions` +
`usable_area(obstructions=…)`), per-plane yield → per-building collapse (`yield_pv.collapse_to_buildings`);
`method="multiplane"` is the production default, `"ransac"` the §6 baseline. Step 6 ran the city with
a flat-roof GCR (`12day_gcr`, #19) on the **tiered per-tile cache** (ADR-0014: DSM + `r.sun` tiers with
sha256 `.key` sidecars, keyed by component versions such as `SCORING_VERSION`), so downstream-only
changes are re-scores; #23 tuned it (`2-4.2`, run `12day_tuned`, on the map from #25 to #32). Step 7's
validation is [`docs/benchmarks/stage-2-part4-validation.md`](docs/benchmarks/stage-2-part4-validation.md)
(#24). **Remaining:** step 8 (the ML **demo** — `method="ml"`, bounded inference-only, no
longer the production path). #30 then added a structural facet-vs-obstruction rule: a small raised
later plane is dropped as a superstructure (`SCORING_VERSION` `2-4.3`; ADR-0013 addendum, risks §14
item 7). #32 moved the map assets to that re-score (`12day_2-4.3`); its labels are not blind yet.
#26 merged `stage-2` into `main`; Pages now deploys from `main` only, so the live map changes only
when `web/` lands on `main`.
**Phase 2 is planned in `docs/plans/stage-2-overview.md`** (5 parts; **Part 2-4 step 8 + Part 2-5
(fidelity) are next**). Part 2-5 starts from `main`, after
[its tentative plan](docs/plans/stage-2-part5-plan.md) is revised.
See `docs/roadmap.md` for phase boundaries/exit criteria and `docs/benchmarks/` for the benchmark
write-up.

## Source of truth

- `docs/reference/Rooftop_Solar_Project_Plan.pdf` — **the full engineering plan; section numbers (§N)
  are cited throughout the docs and code.** When in doubt, this wins.
- `docs/architecture.md` — two modelling modes, provider interface, delivery.
- `docs/data-sources.md` — every data layer, source, access notes (US v1).
- `docs/risks.md` — honest risks + the inter-building shading traps + reference benchmarks.
- `docs/roadmap.md` — phased roadmap.
- `docs/glossary.md` — project-specific domain terms (POA, specific yield, census tract, energy burden, …).
- `docs/plans/` — per-stage implementation plans; `stage-2-overview.md` is the current work map.

## Locked decisions (don't re-litigate without the user)

- **Radiation engine: GRASS GIS `r.sun`** (no pure-Python raster solar model exists; GRASS is a
  system dependency, installed/called separately — see risks §14.2).
- **Study area: Washington, DC**; **Phase 1 neighbourhood: Glover Park**, benchmarked against
  Esri's "Estimate solar power potential" tutorial.
- **Irradiance API: NREL → renamed NLR** (National Laboratory of the Rockies, 2025-12-01). Use
  `developer.nlr.gov`; key lives in `.env` as `NLR_API_KEY` (old NREL keys still valid).
- **Provider adapters are deferred to Phase 3.** Do **not** build the
  `FootprintSource / ElevationSource / ImagerySource / IrradianceSource` abstractions now — the plan
  says premature at one US city. They land when a second city (India) creates the real need.

## Two modelling modes (one interface)

**LiDAR per-roof** (v1 default, DC) vs **block-model / LOD-1** (heritage + India/Phase-3 fallback,
the mode the SIH prototype ran on). The geometry front-end is swappable; radiation → yield →
aggregation is identical downstream. Detail: `docs/architecture.md §5`.

## Pipeline → code map

`src/rooftop_solar/` mirrors the pipeline (README mermaid):

| Stage | Module |
|---|---|
| Building footprints | `footprints.py` |
| DSM from LiDAR | `dsm.py` |
| Solar radiation (with shading) | `radiation.py` (GRASS `r.sun`) |
| Roof-plane / tilt / aspect | `roof_planes.py` |
| Filter usable area | `usable_area.py` |
| PV capacity & annual energy | `yield_pv.py` (PVWatts) |
| Aggregate to census tracts | `aggregate.py` |
| Census / equity data access | `tracts.py` (TIGER · ACS · DOE LEAD) |
| City-scale tiling grid | `tiling.py` (fixed-origin grid · `floor` membership) |
| City runner (tile → cache → merge) | `pipeline.run_city` (+ `scripts/run_city.py`) |
| Web-map ETL (GeoJSON + PMTiles) | `web_build.py` (+ `scripts/build_web.py`; `scripts/serve_web.py` preview) |
| Validation (ground-truth-free) | `validation.py` (Part 2-4 step 7a: DSM self-consistency, multiplane vs ransac) |
| Paths / keys / study-area config | `config.py` |

All modules above are implemented and tested. Part 2-1 (Phase 2) filled `aggregate.py` and added
`tracts.py`; `pipeline.run_aggregation` rolls per-roof results up to census tracts with an equity
overlay (see `docs/plans/stage-2-part1-plan.md`, ADR-0006/0007/0008). Part 2-2 added `tiling.py` and
`pipeline.run_city` — the same spine tiled over the whole District, idempotent/resumable per-tile
(ADR-0009). Both tiers green; the full-DC batch has run (see Status).
Part 2-3 added `web_build.py` + `scripts/build_web.py` (the static web-map ETL: tracts → GeoJSON,
usable roofs → PMTiles via `tippecanoe`) and the buildless `web/` MapLibre map, deployed live on
GitHub Pages (ADR-0010; ETL unit-tested, build + browser smokes green). The map now serves
the tuned city run (#25). Part 2-4 (roof-geometry
refinement, ADR-0011/0012/0013) then upgraded `roof_planes.py` (multi-plane `fit_planes_multi` /
`fit_roof_planes(method="multiplane")`), `radiation.py` (`plane_poa`), `usable_area.py`
(`detect_obstructions` + obstruction-aware `usable_area`), `yield_pv.py` (`plane_yields` +
`collapse_to_buildings`), and wired `pipeline._score_roofs` to the multiplane chain — classical core
built + unit-tested (steps 1–5), then run city-wide (GCR, tiered cache, tuning) and validated
(steps 6–7; see Status). **Part 2-4 step 8 (ML demo) + Part 2-5 (fidelity) are next.**

## Correctness invariants — the shading traps (risks §8; easy to get wrong, hard to notice)

1. **Use the DSM, never the DTM** for the radiation pass — the DTM has no buildings, so it silently
   zeroes all inter-building shading.
2. **Buffer the AOI** — buildings on the south edge are shaded by taller ones just outside it.
3. **Set an adequate shadow search distance** in `r.sun`/`r.horizon` — downtown, far tall towers
   still matter.
4. **Don't let PVWatts undo the shading** — it assumes an unshaded horizon; feed it the
   shading-adjusted per-roof insolation (or a shading-loss factor), not the raw NSRDB value.

Roof-plane extraction is the weakest link (errors propagate into kWh/CO₂) — report uncertainty,
don't over-claim precision.

## Dev environment

- **Conda** (geospatial stack: GDAL/PDAL/GRASS-adjacent): `conda env create -f environment.yml`,
  then `pip install -e .` for the `rooftop_solar` package (src layout).
- **GRASS GIS** installed separately and callable on PATH (Phase 0 exit criterion).
- **tippecanoe** installed separately (e.g. `brew install tippecanoe`) and callable on PATH — the
  Part 2-3 web build (`scripts/build_web.py`) tiles roofs into PMTiles with it; the `pmtiles` pip
  reader (in `environment.yml`) validates the output in the build smoke. Only needed to (re)build
  the web map's `roofs.pmtiles`, not for the analytics pipeline.
- **Web map (Part 2-3):** `scripts/build_web.py` writes `web/assets/`; preview locally with
  `python scripts/serve_web.py` — a **Range-capable** static server, because stock
  `python -m http.server` does *not* serve the PMTiles' byte-range requests (the roof layer breaks
  under it; GitHub Pages' CDN is fine). The browser smoke (`tests/test_web_smoke.py`) needs
  Playwright + `playwright install chromium`.
- Lint/format: **ruff**. Tests: **pytest** — the unit tier runs by default; GRASS/network tests are
  marked `integration` and **deselected by default** (`pytest -m integration` to include).
- **Remote compute VM (two SSH aliases, same machine):** `graham-build` = office LAN
  (192.168.2.56); `graham-ts` = Tailscale (100.98.240.84), works from any network while the Mac's
  Tailscale app is connected. When reachable, prefer it for long batch runs (e.g. the full-DC
  `scripts/run_city.py` batch). It is **not always available** — the user moves jobs between it and
  the Mac day to day. So: probe `ssh -o ConnectTimeout=4 graham-build true`, then the same with
  `graham-ts`, and use whichever answers in the commands below (they say `graham-build`; swap the
  alias). If neither does, run locally — never block on it. Keep runs resumable (the per-tile manifest + cache
  tiers, ADR-0009/0014) so work started on one machine can continue on the other. **Specs (verified
  2026-10-13):** Ubuntu 24.04 x86_64, 8 vCPU (Xeon Gold 6530), 15 GiB RAM + 4 GiB swap, 98 GB disk.
  **Set up:** Miniforge at `~/miniforge3` (user-space; no passwordless sudo), repo at
  `~/rooftop-solar-potential` on `stage-2`, conda env `rooftop-solar` (editable install) with
  **GRASS 8.5.0 from conda-forge inside the env** — matches the Mac's 8.5.0. `.env` (NLR key) is
  in place. Unit tier + `tests/test_radiation_integration.py` pass there. Run as
  `ssh graham-build 'source ~/miniforge3/bin/activate rooftop-solar && cd ~/rooftop-solar-potential && nice -n 10 <cmd>'`.
  The VM is **shared** with another team's runs (load often above 8): check `uptime` first and run
  niced, but not at nice 19 — it starves under that load. Its checkout pulls from `origin`, so
  unpushed commits need a push or a `git bundle` before a run. **Keep every VM run's output on
  the VM:** detach it there (`nohup`/`tmux`) and `tee` it to a log file on the VM, then read that
  file, so a network switch (LAN ↔ Tailscale) that drops the SSH session loses neither the process
  nor its output (the user's call, 2026-10-14). Once you have used a run's output, delete its
  log (and any scratch output) on the VM; keep only results the repo's run layout persists on
  purpose (the manifest and cache tiers).
- **Moving a city run between the Mac and the VM (#18):** stop the run, then
  `scripts/sync_tiles.sh push` (Mac → VM) or `scripts/sync_tiles.sh pull` (VM → Mac), with `-n` for
  a dry run. Then rerun the same `run_city` command on the other machine. It rsyncs the tile cache +
  manifest (`data/tiles/`) only. Outputs are rebuilt by the merge, and there's no shared/cloud
  storage. Completed tiles are reused. Anything partial, truncated, stale or from another GRASS
  version is recomputed, because each cached artifact (DSM, insolation, roofs parquet) is reused
  only when its `.key` sidecar matches both the current key and the file's sha256 (ADR-0014
  addendum 2026-10-13). One machine owns the run at a time: the sent manifest replaces the
  receiver's. No `--delete`: tiles only the receiver had re-score, but reuse their DSM and
  insolation by sidecar. Both machines need GRASS even for a fully cached resume, because its
  version is part of the radiation key. Without it, `run_city` fails fast with a clear error.
- **Never commit data** — `data/`, `outputs/`, and all `*.tif/*.laz/*.las/*.copc.laz` are
  gitignored. LiDAR/DSM/imagery are huge.
- Secrets in `.env` (gitignored); copy `.env.example` to start.

## Working style

Follow the user's global CLAUDE.md: ask before assuming, simplest solution that fits, don't touch
unrelated code (surface smells separately), flag uncertainty. Cite plan §-numbers when a decision
traces to the plan.

**Per-ticket loop:** grill the plan → decide the seam → `tdd` (red → green → refactor), delegating
scoped implementation to subagents → `code-review` (Standards + Spec) → commit. Pick the subagent
model per task: Sonnet for scoped/mechanical work (a designed interface, tests, refactors, search),
Opus for genuinely hard reasoning (subtle bugs, ambiguous specs).

**Code standards** — every line shipped, yours or a subagent's:
- Match the surrounding code's idiom, naming and comment density; docstrings cite the §/ADR a
  choice traces to.
- Functions where they suffice; a class only where it models the domain cleanly.
- Extract shared logic once the repetition is real (the same concept twice), not on lookalike lines.
- Small focused units, clear names, no dead code, errors handled explicitly.
- `ruff check src tests` clean; `ruff format` the code you touch, and reformat untouched code only
  in its own format-only commit.

## Parallel sessions — worktrees + branches

Several sessions run at once. Each works in its own git worktree **inside the repo, under
`.claude/worktrees/<name>`** (git-ignored), on a child branch of the integration branch (currently
`stage-2`): `git worktree add .claude/worktrees/14 -b issue-14-<slug> stage-2`. Sibling copies
beside the repo root are the old layout — create new worktrees under `.claude/worktrees/`. One
session is the **integrator**: it alone merges peers' branches (`--no-ff`) into the integration
branch and runs the integration tier on the merged tip, so an implementor commits to its own branch
and hands the integrator its sha. `git worktree remove` once merged.

Worktree gotchas:
- **Run Python with `PYTHONPATH=src`** from the worktree root. The conda env's editable install
  points at the main checkout, so a bare `pytest` in a worktree silently tests the main checkout's
  code. Leave that install alone — `pip install -e .` from a worktree repoints the shared env under
  every other session.
- `config.REPO_ROOT` follows the code, so a worktree has its **own empty `data/` and `outputs/`**
  (tile cache included). Run data-producing batches (`scripts/run_city.py`, `scripts/build_web.py`)
  from the main checkout, where the caches live. `.env` is still found from an in-repo worktree:
  `config.py`'s bare `load_dotenv()` walks up from the *module's* location (not the cwd), and
  `.claude/worktrees/<name>/src/…` sits under the repo root. Sibling worktrees don't find it.
- The integration tier rewrites `web/screenshot*.png`; `git checkout` them before committing unless
  the ticket is about the map.

**Test runs, matched to the moment:**

| Moment | Run |
|---|---|
| Editing (red → green) | the test files you touched |
| Ticket close, in its worktree | the unit tier (`PYTHONPATH=src pytest`, seconds) + `ruff check src tests`; add the integration tier only when the ticket's criteria name it |
| Merge into the integration branch | the integration tier once, by the integrator, on the merged tip (`pytest -m integration`: network + GRASS, ~65 min on the Mac) |
| Docs-only commit | none |

Run anything long (the integration tier, `run_city`) in the background. The Mac takes **at most two
heavy runs at once** across all sessions and projects (it froze with 3–4 on 2026-09-26); past that,
send the run to the VM or wait for a slot.

## Agent skills

### Issue tracker

Issues and specs live as GitHub issues on `Dhruv-Kapri/Rooftop-Solar-Potential` (via the `gh` CLI).
See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`,
`wontfix`), used as-is. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
