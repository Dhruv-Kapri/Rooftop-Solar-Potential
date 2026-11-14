#!/usr/bin/env python
"""Issue #41 CLI — build the Glover Park walkthrough assets.

Thin wrapper over ``walkthrough.build_walkthrough``: runs Glover Park through the production
spine (``pipeline.score_stages``) and writes the four raster PMTiles, the three GeoJSON layers and
``walkthrough.json`` that ``web/walkthrough.html`` reads (ADR-0015). The DSM and insolation
rasters are cached under ``data/walkthrough/`` (ADR-0014 keys), so only the first
run pays for ``r.sun``.

    python scripts/build_walkthrough.py            # 12-day (calibrated) -> web/assets/walkthrough/
    python scripts/build_walkthrough.py --quick    # single-day radiation: fast, NOT calibrated
    python scripts/build_walkthrough.py --output /tmp/wt   # write elsewhere

Needs GRASS on PATH and network access on a cold cache. The cutoff steps need issue #40's
``unsuitable_reason`` columns.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rooftop_solar import walkthrough


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output",
        default=None,
        help="assets output dir (default: web/assets/walkthrough/)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="single-day radiation — fast smoke, NOT a calibrated annual estimate",
    )
    args = parser.parse_args()

    outputs = walkthrough.build_walkthrough(
        out_dir=Path(args.output) if args.output else None,
        day_range=[172] if args.quick else None,
    )

    print("=== #41 build_walkthrough — wrote walkthrough assets ===")
    for role, path in outputs.items():
        print(f"  {role:12s}: {path}  ({path.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
