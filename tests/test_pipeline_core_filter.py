"""Unit tests for the shared core-filter step (`pipeline.select_by_membership`).

Extracted from `run_stage1`'s inline `.within` filter (Part 2-2, ADR-0009) so the same
whole-footprint selection serves both the Stage-1 single-bbox path (`.within` a core AOI)
and the city runner's per-tile path (`floor` membership). Both must select whole footprints
by where their representative point lands — never geometrically clip a boundary-straddling
building. Pure/offline: synthetic footprints, a simple predicate, no network/GRASS.
"""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import box

from rooftop_solar import config, pipeline

# Region for the `.within` predicate under test.
_REGION = box(0.0, 0.0, 50.0, 50.0)


def _footprints() -> gpd.GeoDataFrame:
    """Three footprints in the working CRS, carrying an extra `fid` column:

    - fid 1: fully inside the region (rep-point (5, 5)).
    - fid 2: fully outside (rep-point (105, 105)).
    - fid 3: STRADDLES the region's x=50 edge (extends to x=55) but its rep-point
      (47.5, 47.5) is inside — must be reported WHOLE, never clipped at x=50.
    """
    return gpd.GeoDataFrame(
        {"fid": [1, 2, 3]},
        geometry=[
            box(0.0, 0.0, 10.0, 10.0),
            box(100.0, 100.0, 110.0, 110.0),
            box(40.0, 40.0, 55.0, 55.0),
        ],
        crs=config.WORKING_CRS,
    )


def test_selects_whole_footprints_whose_rep_point_satisfies_the_rule():
    fp = _footprints()

    out = pipeline.select_by_membership(fp, lambda pts: pts.within(_REGION))

    # fid 1 (inside) and fid 3 (rep-point inside) are kept; fid 2 (outside) is dropped.
    assert out["fid"].tolist() == [1, 3]
    # The straddling footprint is reported WHOLE — geometry unchanged, not clipped at x=50.
    straddler = out.loc[out["fid"] == 3].geometry.iloc[0]
    assert straddler.equals(box(40.0, 40.0, 55.0, 55.0))
    # Index is reset (0..n-1), so downstream positional joins are safe.
    assert out.index.tolist() == [0, 1]


def test_empty_selection_returns_empty_frame_with_same_columns():
    fp = _footprints()

    out = pipeline.select_by_membership(fp, lambda pts: pts.within(box(-10.0, -10.0, -5.0, -5.0)))

    assert len(out) == 0
    assert list(out.columns) == list(fp.columns)
