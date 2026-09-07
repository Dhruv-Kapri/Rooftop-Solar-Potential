#!/usr/bin/env python
"""Stage-1 CLI — run the whole Glover Park pipeline and report the Esri benchmark.

Thin wrapper over `pipeline.run_stage1`: runs footprints -> DSM -> r.sun -> roof planes ->
usable area -> yield for the config Glover Park AOI, writes the GeoPackage + choropleth, and
prints the ADR-0005 sanity check (intensive quantities within ~±15% of Esri's "Estimate solar
power potential" Glover Park tutorial; totals reported but not gated).

    python scripts/run_stage1.py                 # canonical run (MS footprints, 12-day)
    python scripts/run_stage1.py --source osm    # documented fallback footprint source
    python scripts/run_stage1.py --quick         # single-day radiation: fast, NOT calibrated

The full 12-day Glover Park run is the documented manual benchmark (stage-1-plan.md §9), not
a CI job — it fetches ~city-block-scale data and runs GRASS 12x.
"""

from __future__ import annotations

import argparse

import geopandas as gpd

from rooftop_solar import pipeline

# Esri "Estimate solar power potential" (Glover Park tutorial) reference values — the
# intensive quantities ADR-0005 gates on. Totals are method-dependent and not gated.
ESRI_MEDIAN_ENERGY_MWH = 13.35  # median per-building annual energy
ESRI_SPECIFIC_YIELD_KWH_KWP = 1150.0  # kWh per kWp per year
GATE_TOLERANCE = 0.15  # ~±15% on each intensive quantity (ADR-0005)


def _within_tolerance(value: float, reference: float, tol: float = GATE_TOLERANCE) -> bool:
    return abs(value - reference) / reference <= tol


def _report(result: gpd.GeoDataFrame) -> bool:
    """Print headline totals + the ADR-0005 (revised) gate. Returns pass/fail.

    The revised ADR-0005 gates on **specific yield** — the genuinely size-independent
    intensive quantity. Median per-building energy is reported for CONTEXT only: it scales
    with footprint size, so it measures the footprint source's segmentation (MS merges
    rowhouses) as much as the model, and is not gated.
    """
    usable = result[result["usable"]]

    total_capacity_kw = float(result["capacity_kw"].sum())
    total_energy_mwh = float(result["annual_energy_kwh"].sum()) / 1000.0
    total_co2_t = float(result["annual_co2_kg"].sum()) / 1000.0
    median_energy_mwh = (
        float(usable["annual_energy_kwh"].median()) / 1000.0 if len(usable) else float("nan")
    )
    median_energy_density = (
        float(usable["energy_density_kwh_m2"].median()) if len(usable) else float("nan")
    )
    specific_yield = (
        float(result["annual_energy_kwh"].sum()) / total_capacity_kw
        if total_capacity_kw > 0
        else float("nan")
    )

    print("\n=== Stage-1 Glover Park — headline results ===")
    print(f"  roofs scored             : {len(result)}")
    print(f"  usable roofs             : {len(usable)}")
    print(f"  low-confidence fits      : {int(result['low_confidence'].sum())} "
          f"({100 * result['low_confidence'].mean():.0f}%)")
    print(f"  total installed capacity : {total_capacity_kw:10.1f} kW")
    print(f"  total annual energy      : {total_energy_mwh:10.1f} MWh/yr")
    print(f"  total annual CO2 offset  : {total_co2_t:10.1f} t/yr")

    print("\n=== Esri benchmark — GATE: specific yield within ~±15% (ADR-0005, revised) ===")
    yield_ok = _within_tolerance(specific_yield, ESRI_SPECIFIC_YIELD_KWH_KWP)
    print(f"  specific yield             : {specific_yield:7.0f} kWh/kWp  "
          f"vs Esri {ESRI_SPECIFIC_YIELD_KWH_KWP:.0f}  -> {'PASS' if yield_ok else 'FAIL'}")

    print("\n  context (NOT gated — footprint-source-sensitive / method-dependent):")
    print(f"    median per-building energy : {median_energy_mwh:7.2f} MWh  "
          f"(Esri {ESRI_MEDIAN_ENERGY_MWH:.2f}; MS merges rowhouses -> coarser, larger)")
    print(f"    median energy density      : {median_energy_density:7.0f} kWh/m²/yr footprint")

    print(f"\n  ADR-0005 verdict: {'PASS' if yield_ok else 'FAIL'} "
          "(gate = specific yield; per-building energy + totals are context)\n")
    return yield_ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source", default="ms-buildings", choices=["ms-buildings", "osm"],
        help="footprint source (default: ms-buildings, the canonical Stage-1 source)",
    )
    parser.add_argument("--output-dir", default=None, help="where to write GeoPackage + PNG")
    parser.add_argument(
        "--quick", action="store_true",
        help="single-day radiation — fast smoke, NOT a calibrated annual estimate",
    )
    args = parser.parse_args()

    result = pipeline.run_stage1(
        footprints_source=args.source,
        day_range=[172] if args.quick else None,
        output_dir=args.output_dir,
    )
    _report(result)


if __name__ == "__main__":
    main()
