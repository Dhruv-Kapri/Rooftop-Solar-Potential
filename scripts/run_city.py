#!/usr/bin/env python
"""Part 2-2 CLI — run the per-roof spine over the whole District, then aggregate to tracts.

Thin wrapper over `pipeline.run_city` (tile the DC AOI → score each tile's roofs → cache →
merge → recompute city-wide suitability) followed by the reused `pipeline.run_aggregation`
(→ the real, populated tract equity map). Sequential + resumable: a crashed or accuracy-swapped
run resumes only the tiles it must (ADR-0009). Prints the compute-cost run-log Parts 2-4/2-5
budget re-runs against (stage-2-part2-plan.md §6.5/§8).

    python scripts/run_city.py                        # full DC, calibrated 12-day (batch job!)
    python scripts/run_city.py --day-range 172        # fast single-day smoke over the whole grid
    python scripts/run_city.py --no-aggregate         # roof side only (skip the equity map)
    python scripts/run_city.py --no-resume            # force a full recompute

This is a BATCH JOB, not CI: the calibrated full-DC run is many tiles × a 12-day r.sun pass.
It writes the city per-roof GeoParquet (outputs/dc_roofs.parquet) and — unless --no-aggregate —
the tract equity GeoPackage + choropleths (the actual Phase-2 payoff).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import geopandas as gpd

from rooftop_solar import config, pipeline


def _parse_day_range(value: str | None) -> list[int] | None:
    """`None`/"calibrated" → the ADR-0001 12-day default; else a comma list of day-of-year ints."""
    if value is None or value == "calibrated":
        return None
    return [int(d) for d in value.split(",")]


def _report_runlog(tiles_dir, city: gpd.GeoDataFrame, wall_clock_s: float) -> None:
    """Print the §8 compute-cost run-log from the tile manifest + wall-clock."""
    manifest = json.loads((tiles_dir / pipeline.MANIFEST_NAME).read_text())
    done = {k: v for k, v in manifest.items() if v.get("status") == "done"}
    failed = {k: v for k, v in manifest.items() if v.get("status") == "failed"}
    n_with_roofs = sum(1 for v in done.values() if v.get("n_roofs", 0) > 0)

    print("\n=== Part 2-2 city run — compute-cost run-log (§8) ===")
    print(f"  tiles done           : {len(done)}  ({n_with_roofs} with roofs, "
          f"{len(done) - n_with_roofs} empty)")
    print(f"  tiles failed         : {len(failed)}")
    if failed:
        for key, entry in list(failed.items())[:5]:
            print(f"      tile {key}: {entry.get('error', '')[:100]}")
    print(f"  city roofs (merged)  : {len(city)}")
    print(f"  wall-clock           : {wall_clock_s:8.1f} s")
    if len(done):
        print(f"  per-tile (mean)      : {wall_clock_s / len(done):8.1f} s")

    if len(city):
        total_energy_mwh = float(city["annual_energy_kwh"].sum()) / 1000.0
        total_capacity_mw = float(city["capacity_kw"].sum()) / 1000.0
        print("\n=== city per-roof totals (sanity vs NREL/NLR DC potential, §6.4) ===")
        print(f"  installed capacity   : {total_capacity_mw:10.1f} MW")
        print(f"  annual energy        : {total_energy_mwh:10.1f} MWh/yr")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tile-size-m", type=float, default=config.TILE_SIZE_M)
    parser.add_argument("--buffer-m", type=float, default=config.TILE_BUFFER_M)
    parser.add_argument(
        "--day-range", default=None,
        help="comma day-of-year list (e.g. '172' for a fast smoke); default = calibrated 12-day",
    )
    parser.add_argument("--tiles-dir", default=None, help="per-tile cache + manifest dir")
    parser.add_argument("--output-dir", default=None, help="where deliverables are written")
    parser.add_argument("--no-resume", action="store_true", help="recompute every tile")
    parser.add_argument("--no-aggregate", action="store_true", help="skip the tract equity map")
    args = parser.parse_args()

    tiles_dir = Path(args.tiles_dir) if args.tiles_dir is not None else config.TILES_CACHE_DIR

    started = time.perf_counter()
    city = pipeline.run_city(
        tile_size_m=args.tile_size_m,
        buffer_m=args.buffer_m,
        day_range=_parse_day_range(args.day_range),
        tiles_dir=tiles_dir,
        output_dir=args.output_dir,
        resume=not args.no_resume,
    )
    wall_clock_s = time.perf_counter() - started
    _report_runlog(tiles_dir, city, wall_clock_s)

    if not args.no_aggregate:
        print("\n=== aggregating city roofs to census tracts (reused Part 2-1) ===")
        pipeline.run_aggregation(
            buildings=city,
            output_dir=args.output_dir,
            area_name="Washington, DC",
            file_prefix="dc",
        )
        print("  wrote tract equity GeoPackage + choropleths (dc_*).")


if __name__ == "__main__":
    main()
