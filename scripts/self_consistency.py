#!/usr/bin/env python
"""Part 2-4 step 7a CLI — DSM self-consistency over the real city tile cache.

For every cached tile, reconstructs each roof's own DSM pixels from the multiplane facets and
from the single-plane ransac baseline, then summarises the per-roof residuals by roof type
(plan §6, risks §14.4, issue #24). Writes ``self_consistency_roofs.parquet`` (per roof) and
``self_consistency_summary.csv`` (per roof type + ``all``) to ``--out-dir`` and prints the summary.

    python scripts/self_consistency.py --tiles-dir data/tiles_12day_gcr --out-dir /tmp/sc
    python scripts/self_consistency.py --tiles-dir ... --out-dir ... --tiles 3_4 3_5 --workers 4

Needs each tile's roofs parquet + DSM (with its sidecar) under ``--tiles-dir`` (ADR-0009/0014).
"""

from __future__ import annotations

import argparse
import multiprocessing
import re
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import geopandas as gpd
import pandas as pd

from rooftop_solar import validation

_TILE_ROOFS = re.compile(r"tile_(\d+)_(\d+)_roofs\.parquet")


def discover_tiles(tiles_dir: Path) -> list[tuple[int, int]]:
    """Every (row, col) with a roofs parquet in `tiles_dir`, sorted."""
    found = (_TILE_ROOFS.fullmatch(p.name) for p in tiles_dir.iterdir())
    return sorted((int(m[1]), int(m[2])) for m in found if m)


def parse_tile(text: str) -> tuple[int, int]:
    """``ROW_COL`` -> (row, col)."""
    row, col = text.split("_")
    return int(row), int(col)


def _run_tile(args: tuple[Path, int, int]) -> gpd.GeoDataFrame:
    return validation.tile_self_consistency(*args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tiles-dir", required=True, help="the pipeline.run_city tile cache")
    parser.add_argument("--out-dir", required=True, help="where the parquet + csv are written")
    parser.add_argument(
        "--tiles", nargs="+", default=None, metavar="ROW_COL", help="default: every cached tile"
    )
    parser.add_argument("--workers", type=int, default=1, help="tile-level processes (default 1)")
    args = parser.parse_args()

    tiles_dir, out_dir = Path(args.tiles_dir), Path(args.out_dir)
    tiles = sorted(parse_tile(t) for t in args.tiles) if args.tiles else discover_tiles(tiles_dir)
    if not tiles:
        raise SystemExit(f"no tile_*_roofs.parquet under {tiles_dir}")

    start = time.perf_counter()
    jobs = [(tiles_dir, row, col) for row, col in tiles]
    if args.workers > 1:
        # spawn, not fork: fork is unsafe with GDAL threads on macOS (as in pipeline.run_city).
        with ProcessPoolExecutor(
            max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            frames = list(pool.map(_run_tile, jobs))  # map keeps tile order
    else:
        frames = [_run_tile(job) for job in jobs]
    per_roof = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)
    summary = validation.summarize_self_consistency(per_roof)

    out_dir.mkdir(parents=True, exist_ok=True)
    per_roof.to_parquet(out_dir / "self_consistency_roofs.parquet")
    summary.to_csv(out_dir / "self_consistency_summary.csv")
    print(f"{len(tiles)} tiles, {len(per_roof)} roofs in {time.perf_counter() - start:.0f} s")
    print(f"wrote {out_dir / 'self_consistency_roofs.parquet'} and self_consistency_summary.csv\n")
    print(summary.round(3).to_string())


if __name__ == "__main__":
    main()
