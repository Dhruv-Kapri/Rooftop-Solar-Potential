#!/usr/bin/env python
"""Time one tile's `r.sun` radiation pass at several thread counts and check the outputs agree.

Issue #16 / Part 2-2 compute-cost item: `radiation.surface_irradiance(nprocs=N)` is run over one
cached tile DSM for each N, timed wall-clock, and every output raster is compared against the
first (lowest-N) run — the thread count must not change results (it is not part of any cache key).

    python scripts/bench_rsun_threads.py data/tiles/tile_9_2_dsm.tif
    python scripts/bench_rsun_threads.py <dsm.tif> --threads 1,4,8 --day-range 172   # fast check

Default `--day-range` is the calibrated 12-day pass (ADR-0001), i.e. what one city tile costs.
Note the machine load (printed) — a busy host skews the speedup.
"""

from __future__ import annotations

import argparse
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import rasterio

from rooftop_solar import radiation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dsm", type=Path, help="a tile DSM GeoTIFF (e.g. a cached tile_*_dsm.tif)")
    parser.add_argument("--threads", default="1,4,8", help="comma list of r.sun thread counts")
    parser.add_argument(
        "--day-range", default=None, help="comma day-of-year list; default = calibrated 12-day"
    )
    args = parser.parse_args()

    threads = [int(n) for n in args.threads.split(",")]
    day_range = None if args.day_range is None else [int(d) for d in args.day_range.split(",")]

    print(f"dsm: {args.dsm}  cpus: {os.cpu_count()}  load avg: {os.getloadavg()}")
    with tempfile.TemporaryDirectory(prefix="bench_rsun_") as tmp:
        timings: dict[int, float] = {}
        arrays: dict[int, np.ndarray] = {}
        for n in threads:
            out = Path(tmp) / f"insol_{n}.tif"
            started = time.perf_counter()
            radiation.surface_irradiance(args.dsm, day_range=day_range, out_path=out, nprocs=n)
            timings[n] = time.perf_counter() - started
            with rasterio.open(out) as src:
                arrays[n] = src.read(1)
            print(f"  nprocs={n:>2}: {timings[n]:8.1f} s   (load avg {os.getloadavg()[0]:.2f})")

    base = threads[0]
    print(f"\n{'nprocs':>6} {'wall s':>9} {'speedup':>8} {'identical':>10} {'max |diff|':>11}")
    for n in threads:
        diff = float(np.max(np.abs(arrays[n].astype(np.float64) - arrays[base])))
        same = bool(np.array_equal(arrays[n], arrays[base]))
        print(
            f"{n:>6} {timings[n]:>9.1f} {timings[base] / timings[n]:>7.2f}x {same!s:>10} "
            f"{diff:>11.3g}"
        )


if __name__ == "__main__":
    main()
