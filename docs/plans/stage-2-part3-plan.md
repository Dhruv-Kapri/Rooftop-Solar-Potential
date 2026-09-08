# Stage 2 · Part 2-3 plan (tentative) — Deployed web app

> **STATUS: BUILT + LIVE (2026-09-08)** — steps 1–5 implemented, tested, and deployed:
> `web_build.py` ETL (unit-tested), `scripts/build_web.py` + `scripts/serve_web.py`, the buildless
> `web/` MapLibre + PMTiles map, the Pages deploy workflow, and the Playwright browser smoke (green).
> Live at <https://dhruv-kapri.github.io/Rooftop-Solar-Potential/>; **PMTiles range-requests on GitHub
> Pages CONFIRMED** (HTTP 206 — the §9 build-time unknown is resolved, no Cloudflare fallback needed).
> Recorded in **[ADR-0010](../adr/0010-web-delivery-static-maplibre-pmtiles.md)**. **Step 6 done
> (2026-09-08):** the calibrated 12-day run landed and `web/assets/` was rebuilt from `outputs/12day/`
> and re-committed — the live map now serves the calibrated data (specific yield ~1,124 kWh/kWp,
> validated). **Part 2-3 is complete** (the only open follow-up is the merge-time CI cleanup).

- **Phase:** 2, Part 3 of 5 · **Branch:** `stage-2` · **Overview:** [stage-2-overview.md](stage-2-overview.md)
- **Prerequisite:** Part 2-2 built.

## 1. What this part is

An interactive, hosted web map of Part 2-2's district-wide per-roof data + tract choropleth + equity
overlay. Per the overview (§3), this **reaches the end goal as a complete product** — at RANSAC
baseline accuracy — before 2-4/2-5 improve accuracy behind stable interfaces. This is the piece with
the least in common with the rest of the pipeline: delivery/frontend, not analytics.

## 2. Where it continues from

- Part 2-2's district-wide per-roof dataset (GeoParquet — **CONFIRMED**, see §2a) and district-wide
  tract GeoPackage/choropleth (from Part 2-1's aggregation, reused unmodified).
- The equity classification (`equity_class`, `is_priority`) from Part 2-1 (ADR-0007) — this is the
  overlay layer, not a new computation.
- Architecture §11 (stack-neutral delivery): publish as hosted layers, wrap in a lightweight,
  framework-agnostic web map; the Esri/ArcGIS route stays a supported, decoupled option.

## 2a. Grounded against Part 2-2's real dataset (2026-09-08)

Part 2-2's roof side ran full-DC (a 1-day, uncalibrated pass — building counts and geometry are
day-count-independent, so these figures also hold for the pending 12-day calibrated run):

| Layer | Scale (measured) | Serving implication |
|---|---|---|
| **Per-roof** (`dc_roofs.parquet`) | **100,064 roofs** (57,648 usable), **18.1 MB** GeoParquet, ~676k vertices (mean **6.9/roof** — simple footprints) | Too heavy for raw client-side GeoJSON (~50–100 MB); **vector tiles are required, not optional** (§3 — a locked requirement). Simple geometries tile cheaply. |
| **Tract equity** (~206 DC tracts) | Tiny (hundreds of polygons) | The **primary** choropleth — trivially served as inline GeoJSON, **no tiling needed**. |

**What this settles for 2-3:**
- **Format CONFIRMED = GeoParquet** — Part 2-2's output does *not* need to change (§6's "absorbs an
  extra ETL step" risk is retired): the ETL is GeoParquet → (GeoJSON/FlatGeobuf) → `tippecanoe` →
  **PMTiles/MBTiles** for the roof layer only; the tract layer stays a small GeoJSON.
- **Two layers, two strategies** — build the small tract-equity map first (near-static; it *is* the
  finding), add the 100k-roof vector-tile layer as a zoom-in drill-down. Settles the interactivity-scope
  question (now locked — §6 / ADR-0010).

**City-scale quirks observed (feed §9):**
- **Transient Planetary Computer auth/SAS-token failures** on a long run: ~1 tile/run failed (e.g.
  `tile 11_3`) with `ClientAuthenticationError`; **skip-and-continue** caught it and **resume retries**
  it — so a "100% complete" dataset may need one resume pass. (Candidate 2-2 hardening: re-sign/retry
  on auth error.)
- **0 empty in-DC tiles** — all 66 grid tiles have buildings (dense urban), so the empty-tile
  short-circuit rarely fires for DC.
- **Absolute energy/capacity from the 1-day run are NOT usable** (single-day extrapolation → ~3.6 GW /
  6 TWh/yr, ~2–4× NREL's ~1.3 GW / ~1.6 TWh/yr); the map's numbers must come from the **12-day
  calibrated** run. Roof *counts* / geometry are exact regardless.

## 3. Locked architecture (grilled 2026-09-08 — ADR-0010)

A **static** MapLibre GL JS + PMTiles map on **GitHub Pages** — no backend. Locked in a
`/grill-with-docs` session and recorded in
**[ADR-0010](../adr/0010-web-delivery-static-maplibre-pmtiles.md)**:

| Concern | Decision |
|---|---|
| Product | Portfolio piece — a **public live URL** (`dhruv-kapri.github.io/Rooftop-Solar-Potential/`), ~few-days envelope. |
| Architecture | **Static / serverless** — PMTiles served off static hosting via HTTP range requests. |
| Framework | **MapLibre GL JS + PMTiles**, **buildless** (CDN, pinned versions). |
| Hosting | **GitHub Pages**, in-repo **project site**, public repo, **relative** asset paths. |
| Tiling | Install **`tippecanoe`** (system dep); `dc_roofs.parquet` → GeoJSON/FGB (`ogr2ogr`) → tippecanoe → PMTiles. |
| Basemap | **CARTO Positron** (light; attribution) — the only residual metered dep; a minimal background is the fallback. |
| Esri route | Documented, decoupled **optional** (§11) — not the target. |

The two-layer split (§2a) is the spine: the **tract equity layer** is tiny → inline **GeoJSON**, no
tiling; the **100k-roof layer** → **PMTiles** (usable roofs only, z≥13).

## 4. Target architecture — files & contracts

```
web/
  index.html            # MapLibre map page (CDN maplibre-gl + pmtiles, pinned)
  main.js               # map init · layer defs · layer switcher · popups · legend · about-panel
  style.css
  assets/
    tracts.geojson      # ~206 tracts, EPSG:4326 (committed, ~1 MB)
    roofs.pmtiles       # usable roofs as vector tiles, z>=13 (committed deployable, ~5-15 MB)
    scatter.png         # the pipeline's potential-vs-burden quadrant chart (about-panel)
scripts/build_web.py    # ETL: outputs/dc_*.{parquet,gpkg} -> web/assets/*  (--input for dev vs calibrated)
.github/workflows/deploy-pages.yml   # publishes web/ to Pages (no CI tiling)
```

- **`build_web.py`** — a repo script (like `run_stage1`/`run_city`): (1) tracts → keep map columns →
  reproject 6347→4326 → `tracts.geojson`; (2) roofs → **usable-only** → keep map columns → reproject →
  FlatGeobuf → `tippecanoe -o roofs.pmtiles` (low-zoom drop, minzoom 13); (3) copy the pipeline's
  scatter PNG. Takes `--input`/`--output` so dev runs against `outputs/1day/`, the real run against
  `outputs/`.
- **Deploy** — a GitHub Actions Pages workflow uploads the committed `web/` and deploys it. **No tiling
  in CI** (the source dataset is gitignored + needs the hours-long GRASS pipeline) — CI only publishes.
- **Local preview** — `python scripts/serve_web.py` — a **Range-capable** static server. (Correction:
  stock `python -m http.server` does *not* serve PMTiles' byte-range requests, so the roof layer breaks
  under it — GitHub Pages' Fastly CDN is fine. Verified 2026-09-08.) The Playwright smoke drives its own
  in-process Range server.

## 5. Property contract (the `build_web` ↔ map boundary — what §6's ETL tests assert)

**Tract GeoJSON** — one feature per tract, EPSG:4326:

| Property | Type | Used for |
|---|---|---|
| `GEOID` | string (zero-padded) | id / join key |
| `equity_class` | string | **default fill** (categorical quadrant) |
| `is_priority` | bool | priority highlight |
| `potential_per_household` | float | switcher layer (sequential) |
| `energy_burden` | float | switcher layer (sequential) |

**Roof PMTiles** — one feature per **usable** roof, EPSG:4326, minimal payload (properties × 100k bloat
tiles):

| Property | Type | Used for |
|---|---|---|
| `suitability` | float 0–100 | fill (sequential) |
| `capacity_kw` | float | popup |
| `annual_energy_kwh` | float | popup |

Default view: tract **equity quadrant** at low zoom; the switcher recolors to per-household potential /
burden; roofs (suitability-colored) fade in at z≥13. Popups: tract → the four fields + priority; roof →
the three fields.

## 6. Testing strategy (honest for a mostly-visual deliverable — ADR-0010)

- **Unit (TDD, offline):** `build_web`'s transforms are deterministic data → assert **feature counts**,
  the **exact property schema** above, **zero-padded `GEOID`** survival, **usable-only** filtering on the
  roof layer, and the **6347→4326 reprojection** (web maps need lon/lat — a real transform). No network.
- **Build smoke (`integration`):** `tippecanoe` runs and emits a **valid PMTiles** with the expected
  layer + minzoom; the GeoJSON validates.
- **Browser smoke (light e2e):** a **Playwright** check drives `python -m http.server web/` — the map
  canvas renders, **both layers appear**, no console errors, and it captures a **screenshot**. This is
  the honest ceiling; the map's *look* is manual/visual, said plainly — **not** the full
  `verifier-setup`/per-PR `e2e-setup` gate (over-scoped for a static map built once).

## 7. Sequencing (TDD order)

1. **`build_web.py` — tract GeoJSON** (red-green: schema · GEOID · reprojection) against a synthetic
   tract frame.
2. **`build_web.py` — roof PMTiles** (usable-only filter + schema unit-tested; the `tippecanoe` call is
   the `integration` build smoke).
3. **The map** (`web/`) — buildless MapLibre page: basemap → tract GeoJSON (default equity fill +
   switcher + popups) → roof PMTiles (z≥13 + popups) → legend + about-panel (+ scatter).
4. **Deploy** — the Actions Pages workflow; confirm the live project-site URL + that **PMTiles range
   requests work on Pages** (the one build-time unknown; Cloudflare Pages is the fallback).
5. **Browser smoke** — the Playwright check + committed screenshots.
6. **Data swap-in** — point `build_web` at the calibrated `outputs/` once the 12-day run lands; rebuild
   + commit `web/assets/`.

**Delegation note:** steps 1–2 (the pure ETL, clear contracts) are good Sonnet subagent tasks, reviewed
against §5; steps 3–5 (the map, deploy, browser smoke) stay closer to hand (visual judgement + the first
real frontend in the repo).

## 8. Deliverables

- `web/` (buildless MapLibre map) + `scripts/build_web.py` + `.github/workflows/deploy-pages.yml`;
  `tippecanoe` added to the dev-setup docs as a system dep.
- A **live public URL** (project site) with the tract equity map + the roof drill-down + the switcher.
- ETL unit tests + the build/browser smokes.
- A short note on the host + the PMTiles-on-Pages range-request confirmation →
  [`docs/benchmarks/stage-2-part3-web-delivery.md`](../benchmarks/stage-2-part3-web-delivery.md) (done).

## 9. Risks / dependencies

- **Committed PMTiles churn** — a ~5–15 MB artifact in history per pipeline re-run; Git LFS / a
  `gh-pages` orphan branch is the escalation if it bloats (ADR-0010).
- **PMTiles range requests on GitHub Pages** — **CONFIRMED (2026-09-08):** the deployed `.pmtiles`
  returns HTTP 206 with `accept-ranges: bytes` on Pages' Fastly CDN (checked by curl + a live Playwright
  render). The **Cloudflare Pages** fallback is no longer needed.
- **Basemap free-tier** — CARTO Positron is attribution-only with a generous free tier; a self-contained
  minimal background is the zero-dependency fallback.
- **Repo must be public** for free Pages (portfolio-appropriate) — confirm before wiring Pages.
- **This is the least test-first part of Stage 2** — the ETL is TDD'd; the map is smoke + visual. Said
  plainly, not retrofitted (ADR-0010 / §6).

## 10. Downstream review checkpoint

When Part 2-3 is built, re-read Parts 2-4/2-5 and revise them: the **committed `web/assets/` tiles are
how an accuracy re-run propagates to the live map** — 2-4/2-5 finish by re-running `build_web` +
committing refreshed tiles, no app rebuild. Confirm that stays true as their output evolves.
