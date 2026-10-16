#!/usr/bin/env python
"""Part 2-4 step 7b CLI — compare the hand labels to the re-score (back half).

Reads what ``scripts/spot_check.py`` wrote into the spot-check dir (the four parquets and
the hand-filled ``labels.csv``), scores plane-count (+-1) agreement and obstruction
hits / false positives per method, and flags large planar obstructions fit as their own
plane (plan §6, ADR-0013). Writes ``comparison.csv`` (per roof x method) and ``summary.csv``
(per method) next to the labels and prints the summary as a markdown table.

    python scripts/spot_check_compare.py --spot-dir outputs/dc/12day_gcr/spot_check
    python scripts/spot_check_compare.py --spot-dir ... --labels path/to/labels.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd

from rooftop_solar import validation


def load_score(spot_dir: Path) -> validation.DetailedScore:
    """Rebuild the `DetailedScore` from the parquets ``spot_check.py`` wrote."""
    return validation.DetailedScore(
        planes=gpd.read_parquet(spot_dir / "planes.parquet"),
        obstructions=gpd.read_parquet(spot_dir / "obstructions.parquet"),
        membership=pd.read_parquet(spot_dir / "membership.parquet"),
        buildings=gpd.read_parquet(spot_dir / "buildings.parquet"),
    )


def markdown_table(df: pd.DataFrame) -> str:
    """GitHub-markdown table of `df` (index included); floats to 1 decimal."""
    table = df.reset_index()
    cells = [[f"{v:.1f}" if isinstance(v, float) else str(v) for v in row] for row in table.values]
    header = [str(c) for c in table.columns]
    lines = [header, ["---"] * len(header), *cells]
    return "\n".join("| " + " | ".join(line) + " |" for line in lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--spot-dir", required=True, help="the dir scripts/spot_check.py wrote (parquets + labels)"
    )
    parser.add_argument("--labels", default=None, help="default: <spot-dir>/labels.csv")
    args = parser.parse_args()

    spot_dir = Path(args.spot_dir)
    labels_path = Path(args.labels) if args.labels else spot_dir / "labels.csv"
    labels = pd.read_csv(labels_path, dtype={"building_id": str})

    comparison = validation.compare_labels(labels, load_score(spot_dir))
    excluded = sorted(set(labels["building_id"]) - set(comparison["building_id"]))
    if excluded:
        print(f"excluded by notes ({validation.EXCLUDE_NOTE}...): {excluded}")
    summary = validation.summarize_spot_check(comparison)
    comparison.to_csv(spot_dir / "comparison.csv", index=False)
    summary.to_csv(spot_dir / "summary.csv")
    print(f"wrote {spot_dir / 'comparison.csv'} and {spot_dir / 'summary.csv'}\n")
    print(markdown_table(summary))


if __name__ == "__main__":
    main()
