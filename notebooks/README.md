# Notebooks — learning & experimentation

The **experimental, visual companion** to the pipeline. Each notebook explores a stage on a
small scale — with maps and worked maths — *before* it is formalised into
`src/rooftop_solar/`. Read them top-to-bottom: the rendered outputs are committed, so they
read on GitHub without running anything.

| Notebook | Covers | Pipeline stages |
|---|---|---|
| [`00_pipeline_overview.ipynb`](00_pipeline_overview.ipynb) | The end-to-end map + the whole chain worked by hand on one toy roof | all (overview) |
| [`01_footprints_and_dsm.ipynb`](01_footprints_and_dsm.ipynb) | Footprints + DSM on a tiny AOI; the **DSM-vs-DTM shading trap**, proved by subtraction | 1–2 |
| [`02_radiation_rsun.ipynb`](02_radiation_rsun.ipynb) | Solar radiation with GRASS `r.sun`; **inter-building shading** shown shaded-vs-unshaded, integrated to annual insolation | 3 |

More land as stages are built (roof planes → usable area → yield).

**Self-contained by design.** A notebook re-does the work *inline*, on a deliberately small
AOI, rather than importing `src/` — so it reads as genuine exploration, and the clean,
reusable version lives in the package. This is the intended workflow: **experiment in a
notebook, then formalise into code.**

**Convention:** every function defined in a notebook is **fully type-hinted** (args and
return), same as the `src/` package — the notebooks are teaching material, so the signatures
should read as clearly as the prose.

Run any of them with the `rooftop-solar` conda env active
(`jupyter lab`, or `python -m nbconvert --execute ...`).
