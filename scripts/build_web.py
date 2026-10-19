#!/usr/bin/env python
"""Part 2-3 CLI — build the web-map assets from the pipeline's deliverables.

Thin wrapper over ``web_build.build_web``: reads ``tracts.gpkg`` (the aggregation tract
layer) + ``roofs.parquet`` (the city per-roof layer) from an input dir and writes
``web/assets/{tracts.geojson, roofs.pmtiles, scatter.png}`` — the inline tract layer, the
100k-roof vector tiles, and the about-panel chart (plan §4). Defaults ``--input`` to the published
run (``outputs/dc/12day_2-4.3/``, ``config.PUBLISHED_CITY_OUTPUTS_DIR``); point it at
``outputs/dc/1day_<SCORING_VERSION>/`` for a dev build.

    python scripts/build_web.py                          # outputs/dc/12day_2-4.3/ -> web/assets/
    python scripts/build_web.py --input outputs/dc/1day_2-4.3   # dev build off a 1-day pass
    python scripts/build_web.py --output /tmp/assets       # write elsewhere

Requires ``tippecanoe`` on PATH (a system dep, ADR-0010).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rooftop_solar import web_build


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--input",
        default=None,
        help="dir with tracts.gpkg + roofs.parquet (default: published outputs/dc/12day_2-4.3/)",
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
