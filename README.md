<h1 align="center">Rooftop Solar Potential</h1>

<p align="center">
  <em>A city-scale pipeline that scores <strong>every rooftop</strong> for solar suitability —<br>
  the engineering evolution of a Grasshopper + Ladybug prototype into a reproducible pipeline over an entire city.</em>
</p>

<p align="center">
  <a href="https://dhruv-kapri.github.io/Rooftop-Solar-Potential/"><img alt="live web map" src="https://img.shields.io/badge/%E2%96%B6%20live-web%20map-2C6C8A"></a>
  <img alt="status" src="https://img.shields.io/badge/status-Phase%202%20%C2%B7%20Part%202--3%20%C2%B7%20web%20map%20live-2F7D5B">
  <img alt="focus" src="https://img.shields.io/badge/focus-geospatial%20ML-2C6C8A">
  <img alt="data" src="https://img.shields.io/badge/data-public%20%2F%20open--source-2F7D5B">
  <img alt="delivery" src="https://img.shields.io/badge/delivery-interactive%20web%20map-6B7686">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-informational">
</p>

---

Per-roof geometry, orientation, tilt, shading, and irradiance → estimated **PV capacity, annual
energy, and CO₂ offset** — then aggregated to neighbourhood choropleths with an equity lens. Built
from **public data and APIs**, modelled in **open-source Python**, and delivered as an **interactive
web map**. The Esri stack is *one supported deployment target, not a dependency*.

> **Status: Phase 1 MVP complete; Phase 2 in progress — Parts 2-1 (census-tract aggregation + equity
> overlay), 2-2 (city-scale tiling), and 2-3 (deployed web map —
> [live](https://dhruv-kapri.github.io/Rooftop-Solar-Potential/)) built + tested.** The full per-roof pipeline — footprints → DSM →
> shaded radiation → roof planes → usable area → PV yield — runs end-to-end in
> [`src/rooftop_solar/`](src/rooftop_solar/) for Glover Park and passes the Esri benchmark
> ([see the result below](#stage-1-result--glover-park-benchmarked-against-esri)).
> Part 2-1 rolls those per-roof results up to **US Census tracts with an energy-burden equity overlay**
> — `python scripts/run_aggregation.py` (ADR-0006/0007/0008). Part 2-2 scales the same spine over the
> **whole District** by tiling it — `python scripts/run_city.py` (ADR-0009); the code is green at both
> test tiers, and the expensive full-DC batch that produces the *populated, city-wide* equity map is
> deferred (a multi-hour job, run when ready). The [walkthrough notebooks](notebooks/) remain the
> plain-language tour. **Part 2-3 — the deployed web map — is now live:
> [dhruv-kapri.github.io/Rooftop-Solar-Potential](https://dhruv-kapri.github.io/Rooftop-Solar-Potential/)**
> (static MapLibre GL JS + PMTiles on GitHub Pages; numbers preliminary pending the calibrated run).
> Next: ML roof segmentation and fidelity passes (see the [roadmap](docs/roadmap.md)).

## Lineage — from a hackathon prototype to a city pipeline

This is **not** a from-scratch portfolio piece. In 2024 I built a working **Grasshopper / Rhino +
Ladybug** prototype (Smart India Hackathon, PS SIH1739 — Team Operation_VIJAY) that pulled
OpenStreetMap footprints, extruded them into a **LOD-1 city model** of central New Delhi, and ran
**full-year solar-radiation and inter-building shadow simulation** to compute BIPV potential on
rooftops *and* facades.

<p align="center">
  <img alt="Prototype pipeline: site selection → footprint tracing → LOD-1 model → shadow & solar-radiation simulation" src="docs/assets/prototype-pipeline.png" width="100%">
  <br>
  <sub><em>The 2024 SIH prototype pipeline (New Delhi): site selection → building-footprint tracing → LOD-1 city model → full-year shadow simulation &amp; solar-radiation analysis (kWh/m²).</em></sub>
</p>

The prototype already proved the hard part — **mutual shading between neighbouring buildings**, and
solar potential on *vertical facades*, not just roofs:

<p align="center">
  <img alt="3D visualization of per-building solar energy potential with inter-building shading" src="docs/assets/prototype-3d-render.png" width="72%">
  <br>
  <sub><em>Per-building solar energy potential on the LOD-1 model — hot roofs/facades in red, shaded surfaces in blue.</em></sub>
</p>

**This project scales that instinct** — from one neighbourhood in a design tool to an *entire city in
a reproducible pipeline*, trading hand-built LOD-1 blocks for LiDAR-derived per-roof geometry and
deep-learning roof segmentation. This version earns the resolution and the scale.

Prototype deck: [`docs/reference/SIH2024_BIPV_Solar_Prototype_Deck.pdf`](docs/reference/SIH2024_BIPV_Solar_Prototype_Deck.pdf).

## The pipeline

```mermaid
flowchart LR
    F[Building<br/>footprints] --> D[DSM<br/>from LiDAR]
    D --> R[Solar radiation<br/>on surface<br/><i>with shading</i>]
    R --> P[Roof-plane /<br/>tilt / aspect]
    P --> U[Filter<br/>usable area]
    U --> Y[PV capacity &<br/>annual energy<br/>PVWatts]
    Y --> A[Aggregate to<br/>census tracts]
    A --> W[Interactive<br/>web map]
```

**Two modelling modes behind one interface:**

| Mode | Geometry front-end | When |
|---|---|---|
| **LiDAR per-roof** *(v1 default)* | Clip LiDAR per footprint, fit individual roof planes | Dense-data cities (Washington DC) |
| **Block-model / LOD-1** *(heritage + fallback)* | Extrude footprints to one height per building | Sparse-data cities (India) — the mode the SIH prototype ran on |

The geometry front-end is swappable; radiation → yield → aggregation is identical downstream.

## Live web map — Washington, DC

**▶ [dhruv-kapri.github.io/Rooftop-Solar-Potential](https://dhruv-kapri.github.io/Rooftop-Solar-Potential/)**
— every DC roof scored for suitability, rolled up to a census-tract **equity** choropleth. Served
fully static: **MapLibre GL JS + PMTiles vector tiles on GitHub Pages**, no backend
([ADR-0010](docs/adr/0010-web-delivery-static-maplibre-pmtiles.md)).

<p align="center">
  <a href="https://dhruv-kapri.github.io/Rooftop-Solar-Potential/"><img alt="Interactive web map: DC census tracts coloured by a solar-potential × energy-burden equity quadrant" src="web/screenshot.png" width="80%"></a>
  <br>
  <sub><em>The default view: each tract median-split into a 2×2 <strong>equity quadrant</strong>
  (per-household solar potential × energy burden); <strong>priority</strong> tracts — high potential
  <em>and</em> high burden — outlined red. A switcher recolours to per-household potential / burden;
  zoom past level 13 for the <strong>100,064 individual rooftops</strong> coloured by suitability.</em></sub>
</p>

> **Numbers are preliminary.** Roof counts, geometry, and *relative* suitability are exact, but the
> absolute energy/capacity figures come from an uncalibrated single-day radiation pass (≈2–4× high) and
> will be swapped for the calibrated full-DC run. Rebuild the served tiles with
> `python scripts/build_web.py` (preview locally with `python scripts/serve_web.py` — a Range-capable
> static server, since PMTiles needs byte-range requests).

## Stage 1 result — Glover Park, benchmarked against Esri

Phase 1 runs the whole spine in [`src/rooftop_solar/`](src/rooftop_solar/) and scores every roof in
Washington DC's Glover Park — one command: `python scripts/run_stage1.py`.

<p align="center">
  <img alt="Per-roof solar suitability choropleth for Glover Park" src="docs/assets/stage1-glover-park-suitability.png" width="70%">
  <br>
  <sub><em>Per-roof suitability — the within-AOI percentile rank of each roof's annual energy density — exported alongside a GeoPackage of the full per-roof estimates (capacity, energy, CO₂, tilt/aspect, fit uncertainty).</em></sub>
</p>

It **passes** the sanity-check against Esri's "Estimate solar power potential" tutorial on the
size-independent metric — specific yield 1162 vs Esri's 1150 (<1%) — and the OSM run reproduces the
notebook-05 anchor (14.35 MWh/building) exactly. The full comparison, and the finding that Microsoft's
footprints *merge* Glover Park's rowhouses (which is why per-building energy needs a footprint-aware
reading), are written up in
[`docs/benchmarks/stage-1-glover-park-esri.md`](docs/benchmarks/stage-1-glover-park-esri.md).

## Walkthrough notebooks

The [`notebooks/`](notebooks/) directory is the **visual, plain-language way in** — the whole
Phase 1 pipeline explored end-to-end on a small scale (real data, real maps, worked maths)
before it's formalised into `src/rooftop_solar/`. Rendered outputs are committed, so they read
on GitHub without running anything:

- [`00_pipeline_overview`](notebooks/00_pipeline_overview.ipynb) — the end-to-end map, and the whole chain worked by hand on one toy roof
- [`01_footprints_and_dsm`](notebooks/01_footprints_and_dsm.ipynb) — footprints + DSM, and the **DSM-vs-DTM shading trap** proved by subtracting one surface from the other
- [`02_radiation_rsun`](notebooks/02_radiation_rsun.ipynb) — solar radiation with GRASS `r.sun`, **inter-building shading** shown shaded-vs-unshaded
- [`03_roof_planes`](notebooks/03_roof_planes.ipynb) — per-roof tilt/aspect via RANSAC, with fit-quality **uncertainty** reported honestly
- [`04_usable_area_and_yield`](notebooks/04_usable_area_and_yield.ipynb) — usable area → PV capacity/energy/CO₂ → a per-roof **suitability score**
- [`05_benchmark_esri`](notebooks/05_benchmark_esri.ipynb) — the full run at neighbourhood scale, **benchmarked against Esri's** Glover Park tutorial

See [`notebooks/README.md`](notebooks/README.md) for the full index.

## Roadmap at a glance

| Phase | Scope |
|---|---|
| **0 · Scoping & de-risking** | Pick study area (Washington, DC), confirm LiDAR coverage, get the NLR API key, decide the radiation engine |
| **1 · MVP** | One DC neighbourhood, end-to-end → per-building suitability score + static map |
| **2 · Full** | City-scale + ML segmentation + census-tract aggregation + deployed web app |
| **3 · India** | Re-version via modular adapters + the block-model fallback path |

Full detail in [`docs/roadmap.md`](docs/roadmap.md).

## Docs

| File | What |
|---|---|
| [`docs/reference/Rooftop_Solar_Project_Plan.pdf`](docs/reference/Rooftop_Solar_Project_Plan.pdf) | **The full engineering plan** — source of truth |
| [`docs/architecture.md`](docs/architecture.md) | Two modelling modes, provider interface, Esri deployment track |
| [`docs/data-sources.md`](docs/data-sources.md) | Every data layer, source, and access notes (US v1) |
| [`docs/risks.md`](docs/risks.md) | Honest risks, the inter-building shading traps, reference benchmarks |
| [`docs/roadmap.md`](docs/roadmap.md) | Phased roadmap |
| [`docs/reference/SIH2024_BIPV_Solar_Prototype_Deck.pdf`](docs/reference/SIH2024_BIPV_Solar_Prototype_Deck.pdf) | The 2024 prototype this project grows from |
| [`docs/reference/Foundations_Geospatial_ML.pdf`](docs/reference/Foundations_Geospatial_ML.pdf) | Reading list — geospatial data science & ML foundations (background) |
| [`docs/reference/Resources_GeoAI_Solar.pdf`](docs/reference/Resources_GeoAI_Solar.pdf) | Reading list — curated GeoAI-for-solar resources (background) |

## License & credits

Released under the [MIT License](LICENSE) — free to use, modify, and build on for **personal,
community, academic, and commercial** purposes. The one condition: **keep the copyright and license
notice**, i.e. give credit.

If this work helps yours, a citation or link back is appreciated:

> Dhruv Kapri, *Rooftop Solar Potential* (2026). https://github.com/Dhruv-Kapri/Rooftop-Solar-Potential

**Credits:** builds on the 2024 Smart India Hackathon BIPV prototype (PS SIH1739) by
**Team Operation_VIJAY**. Prototype figures in this README are from that team's project deck.
