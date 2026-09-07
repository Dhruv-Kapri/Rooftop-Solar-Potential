#!/usr/bin/env python
"""Part 2-1 CLI — roll the Stage-1 per-roof result up to census tracts + equity overlay.

Thin wrapper over `pipeline.run_aggregation`: loads the tract spine (TIGER geometry + ACS
demographics + DOE LEAD energy burden), composes roll-up -> equity join -> quadrant
classification, writes the tract GeoPackage + choropleths, and prints a headline summary with
the Part 2-1 sanity checks and caveats (stage-2-part1-plan.md §7).

    python scripts/run_aggregation.py                 # real run: fetch DC tracts+equity
    python scripts/run_aggregation.py --output-dir /tmp/out

NOTE (plan §11): the real run needs a per-tract DOE LEAD energy-burden number. If the LEAD
burden aggregation is still unresolved, `pipeline.run_aggregation` raises from
`tracts.load_energy_burden` with a message describing the open methodology choice.

This is Glover Park only (~2 tracts) — a SMOKE TEST OF THE MACHINERY, not an equity finding.
The populated priority-tract map is Part 2-2 (city-wide), where the median split is meaningful.
"""

from __future__ import annotations

import argparse

import geopandas as gpd

from rooftop_solar import pipeline


def _report(result: gpd.GeoDataFrame) -> None:
    """Print headline per-tract totals, the equity-class breakdown, and the §7 caveats."""
    n_tracts = len(result)
    total_energy_mwh = float(result["annual_energy_kwh"].sum()) / 1000.0
    total_capacity_kw = float(result["capacity_kw"].sum())
    n_priority = int(result["is_priority"].sum())
    n_missing = int((result["equity_class"] == "unknown").sum())

    classifiable = result[result["equity_class"] != "unknown"]

    print("\n=== Part 2-1 Glover Park — census-tract aggregation ===")
    print(f"  tracts with >=1 building : {n_tracts}")
    print(f"  total installed capacity : {total_capacity_kw:10.1f} kW")
    print(f"  total annual energy      : {total_energy_mwh:10.1f} MWh/yr")
    print(f"  tracts missing equity data: {n_missing}  (flagged 'unknown', excluded from medians)")

    print("\n=== equity quadrant (ADR-0007) ===")
    for cls, n in result["equity_class"].value_counts().items():
        print(f"  {cls:32s}: {n}")
    print(f"  priority tracts (high potential x high burden): {n_priority}")

    if len(classifiable):
        pph = classifiable["potential_per_household"]
        print("\n=== sanity (§7.2 — a smell test, NOT a gate) ===")
        print(f"  per-household potential  : {pph.min():.0f} - {pph.max():.0f} kWh/hh/yr "
              "(cross-check vs NREL/NLR rooftop technical potential, risks §15)")

    print(f"\n  CAVEAT (§7.4): Glover Park spans {n_tracts} tracts, and the bbox AOI only "
          "partially covers\n  edge tracts (few scored roofs ÷ full household count), so "
          "per-household potential is\n  understated there and the quadrant medians are "
          "near-degenerate. This run is a SMOKE TEST\n  of the machinery, not an equity finding — "
          "that arrives at Part 2-2 (full-DC, whole tracts).\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--buildings", default=None,
        help="per-roof input GeoPackage (default: the Stage-1 outputs/glover_park_roofs.gpkg)",
    )
    parser.add_argument("--output-dir", default=None, help="where to write GeoPackage + PNGs")
    args = parser.parse_args()

    result = pipeline.run_aggregation(
        buildings=args.buildings,
        output_dir=args.output_dir,
    )
    _report(result)


if __name__ == "__main__":
    main()
