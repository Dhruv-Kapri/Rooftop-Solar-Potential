#!/usr/bin/env bash
#
# dev-local.sh — bring the local dev environment up in one command.
#
# This is a conda geospatial pipeline, not a web stack: the only long-lived
# server is JupyterLab (the notebooks are the interactive surface). There is no
# DB/cache/queue infra. GRASS GIS is a system CLI invoked per-run (radiation.py),
# not a service — so it's a preflight check, not a window.
#
# Usage:
#   scripts/dev-local.sh up            # preflight + start JupyterLab (idempotent)
#   scripts/dev-local.sh down          # stop the tmux session (Jupyter)
#   scripts/dev-local.sh status        # window list + port check (read-only)
#   scripts/dev-local.sh logs jupyter  # tail Jupyter's pane (has the token URL)
#   scripts/dev-local.sh restart jupyter
#   scripts/dev-local.sh attach        # attach to the tmux session (Ctrl-b d to detach)
#   scripts/dev-local.sh setup         # first-run: create conda env + pip install -e .
#   scripts/dev-local.sh test          # run the unit tier (pytest; integration deselected)
#   scripts/dev-local.sh smoke         # data-access reachability check
#
set -euo pipefail

# --- config -----------------------------------------------------------------
SESSION="rooftop-solar-dev"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # repo root (script lives in scripts/)
CONDA_ENV="rooftop-solar"                                  # environment.yml
JUPYTER_PORT="${JUPYTER_PORT:-8888}"

# Long-lived servers: "window_name|start command". One per service.
# conda run --no-capture-output streams output to the pane so `logs` shows the token URL.
SERVERS=(
  "jupyter|conda run --no-capture-output -n ${CONDA_ENV} jupyter lab --no-browser --ip 127.0.0.1 --port ${JUPYTER_PORT} --notebook-dir '${ROOT}'"
)

# Ports to verify in `status`/`up` (name:port).
WATCH_PORTS=(
  "jupyter:${JUPYTER_PORT}"
)

# --- pretty print -----------------------------------------------------------
c_reset=$'\033[0m'; c_dim=$'\033[2m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_red=$'\033[31m'; c_cyn=$'\033[36m'
say()  { printf "%s\n" "$*"; }
info() { printf "${c_cyn}▸ %s${c_reset}\n" "$*"; }
ok()   { printf "${c_grn}✓ %s${c_reset}\n" "$*"; }
warn() { printf "${c_ylw}! %s${c_reset}\n" "$*"; }
die()  { printf "${c_red}✗ %s${c_reset}\n" "$*" >&2; exit 1; }
port_up() { lsof -ti :"$1" -sTCP:LISTEN >/dev/null 2>&1; }
in_env()  { conda run -n "$CONDA_ENV" "$@" >/dev/null 2>&1; }

# --- preflight --------------------------------------------------------------
# Hard requirements to launch Jupyter die; degraded-but-launchable states warn.
preflight() {
  command -v tmux  >/dev/null 2>&1 || die "tmux not found. Install: brew install tmux"
  command -v conda >/dev/null 2>&1 || die "conda not found. Install Miniconda/Miniforge, then: scripts/dev-local.sh setup"
  conda run -n "$CONDA_ENV" true >/dev/null 2>&1 \
    || die "conda env '$CONDA_ENV' not found. Create it: scripts/dev-local.sh setup"
  in_env jupyter --version \
    || die "jupyter missing in '$CONDA_ENV'. Rebuild the env: scripts/dev-local.sh setup"
  # Editable install + GRASS are not needed just to launch Jupyter — warn, don't block.
  in_env python -c "import rooftop_solar" \
    || warn "package 'rooftop_solar' not importable in '$CONDA_ENV' — run: conda run -n $CONDA_ENV pip install -e ."
  command -v grass >/dev/null 2>&1 \
    || warn "GRASS not on PATH — radiation.py (r.sun) will fail; notebooks 01/03/04 still run (risks §14.2)."
}

# --- tmux helpers -----------------------------------------------------------
start_window() {  # idempotent: skip if the window already exists
  local name="$1" cmd="$2"
  if tmux list-windows -t "$SESSION" -F '#{window_name}' 2>/dev/null | grep -qx "$name"; then
    warn "window '$name' already exists — leaving it alone"; return
  fi
  tmux new-window -t "$SESSION" -n "$name" -c "$ROOT"
  tmux send-keys -t "$SESSION:$name" "$cmd" C-m
}

port_check() {
  [ ${#WATCH_PORTS[@]} -eq 0 ] && return
  say "  Port status (${c_dim}· = still starting${c_reset}):"
  for e in "${WATCH_PORTS[@]}"; do
    local nm="${e%%:*}" pt="${e##*:}"
    if port_up "$pt"; then printf "    ${c_grn}●${c_reset} %-14s :%s\n" "$nm" "$pt"
    else                   printf "    ${c_dim}·${c_reset} %-14s :%s\n" "$nm" "$pt"; fi
  done
}

# --- commands ---------------------------------------------------------------
cmd_up() {
  preflight
  tmux has-session -t "$SESSION" 2>/dev/null || tmux new-session -d -s "$SESSION" -n _bootstrap -c "$ROOT"
  for s in "${SERVERS[@]}"; do start_window "${s%%|*}" "${s#*|}"; done
  tmux kill-window -t "$SESSION:_bootstrap" 2>/dev/null || true
  echo; ok "JupyterLab starting in tmux session '$SESSION' → http://127.0.0.1:${JUPYTER_PORT}"; echo
  port_check
  echo
  say "${c_dim}  Token URL: scripts/dev-local.sh logs jupyter   (copy the http://127.0.0.1:${JUPYTER_PORT}/lab?token=… line)${c_reset}"
  say "${c_dim}  Attach:    scripts/dev-local.sh attach          (Ctrl-b d to detach)${c_reset}"
  say "${c_dim}  Stop:      scripts/dev-local.sh down${c_reset}"
}

cmd_status() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    info "tmux '$SESSION' windows:"
    tmux list-windows -t "$SESSION" -F '    #{window_index}: #{window_name}'
  else warn "session '$SESSION' not running"; fi
  echo; port_check
}

cmd_logs()    { tmux has-session -t "$SESSION" 2>/dev/null || die "session not running"; tmux capture-pane -p -S -400 -t "$SESSION:${1:?usage: logs <name>}"; }
cmd_restart() { tmux has-session -t "$SESSION" 2>/dev/null || die "session not running"
  local n="${1:?usage: restart <name>}"; tmux kill-window -t "$SESSION:$n" 2>/dev/null || true
  for s in "${SERVERS[@]}"; do [ "${s%%|*}" = "$n" ] && start_window "$n" "${s#*|}" && { ok "restarted $n"; return; }; done
  die "unknown window '$n'"; }
cmd_attach()  { tmux has-session -t "$SESSION" 2>/dev/null || die "not running — start with: dev-local.sh up"; tmux attach -t "$SESSION"; }
cmd_down() {
  tmux kill-session -t "$SESSION" 2>/dev/null && ok "JupyterLab stopped" || warn "no session '$SESSION'"
  [ "${1:-}" = "--all" ] && warn "no infra for this repo (no DB/cache/queue) — nothing else to stop."
}

# --- project one-shots (NOT part of `up`) -----------------------------------
cmd_setup() {
  command -v conda >/dev/null 2>&1 || die "conda not found. Install Miniconda/Miniforge first."
  if conda run -n "$CONDA_ENV" true >/dev/null 2>&1; then
    info "env '$CONDA_ENV' exists — updating from environment.yml"
    conda env update -n "$CONDA_ENV" -f "$ROOT/environment.yml" --prune
  else
    info "creating env '$CONDA_ENV' from environment.yml"
    conda env create -f "$ROOT/environment.yml"
  fi
  conda run -n "$CONDA_ENV" pip install -e "$ROOT"
  ok "env ready. Next: scripts/dev-local.sh up"
  command -v grass >/dev/null 2>&1 || warn "GRASS still not on PATH — install separately for the radiation stage (risks §14.2)."
}
cmd_test()  { preflight_env; ( cd "$ROOT" && conda run --no-capture-output -n "$CONDA_ENV" pytest "$@" ); }
cmd_smoke() { preflight_env; ( cd "$ROOT" && conda run --no-capture-output -n "$CONDA_ENV" python scripts/check_data_access.py ); }
preflight_env() {
  command -v conda >/dev/null 2>&1 || die "conda not found. Run: scripts/dev-local.sh setup"
  conda run -n "$CONDA_ENV" true >/dev/null 2>&1 || die "env '$CONDA_ENV' missing. Run: scripts/dev-local.sh setup"
}

case "${1:-up}" in
  up)      cmd_up ;;
  down)    cmd_down "${2:-}" ;;
  status)  cmd_status ;;
  logs)    cmd_logs "${2:-}" ;;
  restart) cmd_restart "${2:-}" ;;
  attach)  cmd_attach ;;
  setup)   cmd_setup ;;
  test)    shift; cmd_test "$@" ;;
  smoke)   cmd_smoke ;;
  -h|--help|help) awk 'NR==1{next} /^#/{sub(/^# ?/,"");print;next}{exit}' "${BASH_SOURCE[0]}" ;;
  *) die "unknown command '$1' (try: up|down|status|logs|restart|attach|setup|test|smoke)" ;;
esac
