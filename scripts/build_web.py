#!/usr/bin/env python
"""Part 2-3 CLI — build the web-map assets from the pipeline's ``dc_*`` deliverables.

Thin wrapper over ``web_build.build_web``: reads ``dc_tracts.gpkg`` (the aggregation tract
layer) + ``dc_roofs.parquet`` (the city per-roof layer) from an input dir and writes
``web/assets/{tracts.geojson, roofs.pmtiles, scatter.png}`` — the inline tract layer, the
100k-roof vector tiles, and the about-panel chart (plan §4). Point ``--input`` at
``outputs/1day/`` for a dev build or ``outputs/`` for the calibrated 12-day run.

    python scripts/build_web.py                       # outputs/  -> web/assets/
    python scripts/build_web.py --input outputs/1day   # dev build off the 1-day pass
    python scripts/build_web.py --output /tmp/assets    # write elsewhere

Requires ``tippecanoe`` on PATH (a system dep, ADR-0010).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rooftop_solar import web_build


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--input", default=None,
        help="dir holding dc_tracts.gpkg + dc_roofs.parquet (default: outputs/)",
    )
    parser.add_argument(
        "--output", default=None, help="web assets output dir (default: web/assets/)"
    )
    args = parser.parse_args()

    outputs = web_build.build_web(
        input_dir=Path(args.input) if args.input else None,
        output_dir=Path(args.output) if args.output else None,
    )

    print("=== Part 2-3 build_web — wrote web assets ===")
    for role, path in outputs.items():
        print(f"  {role:8s}: {path}  ({path.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
