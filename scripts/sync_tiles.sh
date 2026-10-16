#!/usr/bin/env bash
#
# sync_tiles.sh — hand the city tile cache + manifest between this machine and the VM.
#
# A full-DC `run_city` batch started on one machine (the office VM `graham-build` or the Mac)
# resumes on the other: stop the run, sync the tile cache, rerun the same command there. The
# validity sidecars make a partial or stale copy safe — every cached artifact is reused only if
# its `.key` sidecar matches the current cache key AND the artifact's sha256 (ADR-0014, issue
# #18); anything truncated, swapped or from another GRASS version is recomputed, never reused.
#
# Usage:
#   scripts/sync_tiles.sh push [-n]   # this machine -> VM (e.g. leaving home, VM takes over)
#   scripts/sync_tiles.sh pull [-n]   # VM -> this machine (e.g. leaving the office)
#                                     # -n: dry run, list what would transfer
#
# Env overrides:
#   SYNC_HOST     ssh alias of the other machine      (default: graham-build; graham-ts = Tailscale)
#   LOCAL_TILES   local tile cache dir                (default: <repo>/data/tiles)
#   REMOTE_TILES  remote tile cache dir, ~-relative   (default: rooftop-solar-potential/data/tiles)
#
# Rules:
#   - Stop the run on the sending machine first; one machine owns the run at a time. The sent
#     manifest replaces the receiver's, which is right when the sender holds the newer state.
#   - No --delete: files only the receiver has stay on disk. Their manifest entries are replaced,
#     so those tiles re-score, but their DSM/insolation rasters are still reused by sidecar.
#   - No --inplace/--partial: rsync writes each file to a temp name and renames it, so an
#     interrupted sync leaves the old file or none, not a half file under the real name.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # repo root (script lives in scripts/)
SYNC_HOST="${SYNC_HOST:-graham-build}"
LOCAL_TILES="${LOCAL_TILES:-$ROOT/data/tiles}"
REMOTE_TILES="${REMOTE_TILES:-rooftop-solar-potential/data/tiles}"

usage() { sed -n '11,14p' "$0" >&2; exit 2; }

direction="${1:-}"
[[ "$direction" == push || "$direction" == pull ]] || usage
dry_run=()
case "${2:-}" in
  "") ;;
  -n) dry_run=(-n) ;;
  *) usage ;;
esac

if ! ssh -o ConnectTimeout=4 "$SYNC_HOST" true 2>/dev/null; then
  echo "sync_tiles: $SYNC_HOST unreachable (try SYNC_HOST=graham-ts for Tailscale) — nothing synced." >&2
  exit 1
fi

case "$direction" in
  push)
    [[ -d "$LOCAL_TILES" ]] || { echo "sync_tiles: no local cache at $LOCAL_TILES" >&2; exit 1; }
    (( ${#dry_run[@]} )) || ssh "$SYNC_HOST" mkdir -p "$REMOTE_TILES"
    src="$LOCAL_TILES/"; dst="$SYNC_HOST:$REMOTE_TILES/"
    ;;
  pull)
    (( ${#dry_run[@]} )) || mkdir -p "$LOCAL_TILES"
    src="$SYNC_HOST:$REMOTE_TILES/"; dst="$LOCAL_TILES/"
    ;;
esac

echo "sync_tiles: $src -> $dst"
# -a keeps mtimes, so a later sync only resends what changed; *.tmp is a manifest mid-save.
rsync -av --exclude '*.tmp' ${dry_run[@]+"${dry_run[@]}"} "$src" "$dst"
