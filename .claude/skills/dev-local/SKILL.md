---
name: dev-local
description: >-
  Bring the local dev environment up with one command via scripts/dev-local.sh.
  Use when the user says "start the app", "run dev-local", "launch Jupyter / the
  notebooks", "spin up the local stack", "how do I run this locally", or needs
  the conda env / GRASS preflight checked before working.
---

# dev-local

One launcher for local work: `scripts/dev-local.sh`. This repo is a **conda
geospatial pipeline**, not a web stack — the only long-lived server is
**JupyterLab** (the notebooks are the interactive surface). No DB/cache/queue
infra. GRASS is a system CLI (a preflight check), not a service.

## Services

| Window | Command | Port | Depends on |
|---|---|---|---|
| `jupyter` | `conda run -n rooftop-solar jupyter lab` | 8888 | conda env `rooftop-solar` |

CLIs (`scripts/run_stage1.py`, `scripts/check_data_access.py`, `scripts/fetch_aoi_data.py`)
are one-shot, not servers — run them directly in the env, not via `up`.

## Prerequisites

- **tmux** (`brew install tmux`).
- **conda** (Miniconda/Miniforge) with the **`rooftop-solar`** env — create it with
  `scripts/dev-local.sh setup` (runs `conda env create -f environment.yml` +
  `pip install -e .`).
- **GRASS GIS** on `PATH` — only needed for the radiation stage (`r.sun`); Jupyter
  and notebooks 01/03/04 launch without it. `up` warns, doesn't block (risks §14.2).
- `.env` with `NLR_API_KEY` (copy `.env.example`); Stage 2 adds `CENSUS_API_KEY`.

## Commands

| Command | Does |
|---|---|
| `scripts/dev-local.sh up` | Preflight, then start JupyterLab in tmux (idempotent — re-running leaves a live window alone). |
| `… down` | Stop the tmux session. `down --all` is a no-op here (no infra). |
| `… status` | Window list + port check. Read-only. |
| `… logs jupyter` | Tail Jupyter's pane — **this is where the `?token=…` login URL is**. |
| `… restart jupyter` | Kill + relaunch the Jupyter window. |
| `… attach` | Attach to the tmux session (Ctrl-b d to detach). |
| `… setup` | First run: create/update the conda env + editable install. |
| `… test` | Unit tier (`pytest`; the `integration` GRASS/network tier is deselected by default). |
| `… smoke` | Data-access reachability check (`scripts/check_data_access.py`). |

## Troubleshooting

- **Port 8888 in use** — another Jupyter is running; `status` shows the port ●, or
  set a different port: `JUPYTER_PORT=8889 scripts/dev-local.sh up`.
- **"env 'rooftop-solar' not found"** — run `scripts/dev-local.sh setup`.
- **Jupyter window exited immediately** — `logs jupyter` to see why (usually a
  broken env or missing `jupyter`); rebuild with `setup`.
- **No token URL in the browser** — `logs jupyter` and copy the
  `http://127.0.0.1:8888/lab?token=…` line.
- **GRASS warning on `up`** — expected if GRASS isn't installed; only the radiation
  stage needs it.

## Extending (future)

When Part 2-3 (the deployed web app, `docs/plans/stage-2-part3-plan.md`) lands, add
its dev server as a second entry in the `SERVERS=(…)` array and its port to
`WATCH_PORTS=(…)` in `scripts/dev-local.sh`.
