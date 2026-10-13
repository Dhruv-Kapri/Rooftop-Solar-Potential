#!/usr/bin/env python
"""Time the `r.sun` radiation pass over several tiles at a few `workers × nprocs` splits.

Issue #27 / Part 2-2 compute-cost item: `run_city(workers=K, nprocs=N)` scores K tiles at once in
worker processes, each running `r.sun` on N threads. This times the dominant stage at a fixed core
budget — every tile's `radiation.surface_irradiance(nprocs=N)` in a K-process spawn pool, the same
start method `run_city` uses — and reports throughput (tiles/hour) per split. Each tile's output is
compared against the first split's (worker count must not change results).

    python scripts/bench_rsun_workers.py data/tiles_12day/tile_{5_4,5_5,5_6,5_7}_dsm.tif
    python scripts/bench_rsun_workers.py <dsm.tif ...> --splits 1x8,2x4,4x2 --day-range 172

Default `--day-range` is the calibrated 12-day pass (ADR-0001). Note the machine load (printed) —
a busy host skews the comparison.
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import rasterio

from rooftop_solar import radiation


def _radiate(dsm: Path, out: Path, day_range: list[int] | None, nprocs: int) -> Path:
    try:
        radiation.surface_irradiance(dsm, day_range=day_range, out_path=out, nprocs=nprocs)
    except Exception as exc:  # as text: a GRASS exception can't be unpickled here (see run_city)
        raise RuntimeError(f"{dsm.name}: {exc!r} {getattr(exc, 'errors', '')}") from None
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "dsms", type=Path, nargs="+", help="tile DSM GeoTIFFs (cached tile_*_dsm.tif)"
    )
    parser.add_argument(
        "--splits", default="1x8,2x4,4x2,8x1", help="comma list of <workers>x<nprocs> splits"
    )
    parser.add_argument(
        "--day-range", default=None, help="comma day-of-year list; default = calibrated 12-day"
    )
    args = parser.parse_args()

    splits: list[tuple[int, int]] = []
    for split in args.splits.split(","):
        workers, nprocs = split.split("x")
        splits.append((int(workers), int(nprocs)))
    day_range = None if args.day_range is None else [int(d) for d in args.day_range.split(",")]
    spawn = multiprocessing.get_context("spawn")

    print(f"tiles: {len(args.dsms)}  cpus: {os.cpu_count()}  load avg: {os.getloadavg()}")
    timings: dict[tuple[int, int], float] = {}
    identical: dict[tuple[int, int], bool] = {}
    with tempfile.TemporaryDirectory(prefix="bench_rsun_workers_") as tmp:
        base: list[np.ndarray] | None = None
        for workers, nprocs in splits:
            outs = [Path(tmp) / f"{workers}x{nprocs}_{i}.tif" for i in range(len(args.dsms))]
            started = time.perf_counter()
            with ProcessPoolExecutor(max_workers=workers, mp_context=spawn) as pool:
                list(
                    pool.map(
                        _radiate, args.dsms, outs, [day_range] * len(outs), [nprocs] * len(outs)
                    )
                )
            timings[(workers, nprocs)] = time.perf_counter() - started
            arrays = []
            for out in outs:
                with rasterio.open(out) as src:
                    arrays.append(src.read(1))
            base = arrays if base is None else base
            pairs = zip(arrays, base, strict=True)
            identical[(workers, nprocs)] = all(np.array_equal(a, b) for a, b in pairs)
            print(
                f"  {workers}x{nprocs}: {timings[(workers, nprocs)]:8.1f} s   "
                f"(load avg {os.getloadavg()[0]:.2f})"
            )

    first = splits[0]
    vs_first = "vs " + "x".join(map(str, first))
    print(f"\n{'split':>6} {'wall s':>9} {'tiles/h':>8} {vs_first:>8} {'identical':>10}")
    for split in splits:
        t = timings[split]
        print(
            f"{'x'.join(map(str, split)):>6} {t:>9.1f} {len(args.dsms) * 3600 / t:>8.1f} "
            f"{timings[first] / t:>7.2f}x {identical[split]!s:>10}"
        )


if __name__ == "__main__":
    main()
