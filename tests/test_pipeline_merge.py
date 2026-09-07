"""Unit tests for the city-scale merge + seam-conservation invariant (Part 2-2, ADR-0009).

`pipeline.merge_tile_roofs` stacks per-tile roof frames into one city frame. Exactly-once
already holds by the `floor` partition upstream (`tiling.tile_membership` +
`pipeline.select_by_membership`), so merging is *just* a concatenation — these tests pin
that contract (CRS-preserving, id-preserving, mixed-CRS rejected, all-empty schema-preserving)
plus the end-to-end conservation invariant tying `tile_membership` + `select_by_membership`
+ `merge_tile_roofs` together (stage-2-part2-plan.md §5, §7).

Pure/offline: synthetic footprints and roof frames, no network/GRASS.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import box

from rooftop_solar import config, pipeline, tiling

_ORIGIN = (0.0, 0.0)
_SIZE = 10.0


def _roofs(fids: list[int], crs: str = config.WORKING_CRS) -> gpd.GeoDataFrame:
    """A synthetic per-tile roof frame: an `fid` column + a distinct box geometry per row."""
    return gpd.GeoDataFrame(
        {"fid": fids},
        geometry=[box(float(i), float(i), float(i) + 1.0, float(i) + 1.0) for i in fids],
        crs=crs,
    )


def test_merge_concatenates_nonempty_frames_preserving_ids_and_crs():
    two = _roofs([1, 2])
    zero = _roofs([])
    one = _roofs([3])

    merged = pipeline.merge_tile_roofs([two, zero, one])

    assert len(merged) == 3
    assert sorted(merged["fid"].tolist()) == [1, 2, 3]
    assert merged.crs == config.WORKING_CRS
    assert merged.geometry.name == "geometry"


def test_merge_mixed_crs_raises_value_error():
    working = _roofs([1], crs=config.WORKING_CRS)
    wgs84 = _roofs([2], crs="EPSG:4326")

    with pytest.raises(ValueError):
        pipeline.merge_tile_roofs([working, wgs84])


def test_merge_all_empty_frames_returns_schema_preserving_empty_frame():
    zero_a = _roofs([])
    zero_b = _roofs([])

    merged = pipeline.merge_tile_roofs([zero_a, zero_b])

    assert len(merged) == 0
    assert list(merged.columns) == list(zero_a.columns)


def test_merge_no_frames_returns_empty_geodataframe():
    merged = pipeline.merge_tile_roofs([])

    assert isinstance(merged, gpd.GeoDataFrame)
    assert len(merged) == 0


# --------------------------------------------------------------------------- #
# Conservation — Part 2-2's seam invariant (stage-2-part2-plan.md §5, §7):    #
# tile_membership + select_by_membership + merge_tile_roofs never drop or    #
# double-count a building.                                                    #
# --------------------------------------------------------------------------- #


def test_seam_conservation_across_a_2x2_tile_block():
    # 5 synthetic footprints scattered across a 2x2 block of a 10-unit grid, each with a
    # representative point STRICTLY interior to its cell (no seam ambiguity here — the
    # on-seam case is already covered at the mask level in test_tiling.py).
    fp = gpd.GeoDataFrame(
        {"fid": [1, 2, 3, 4, 5]},
        geometry=[
            box(1.0, 1.0, 3.0, 3.0),  # rep-point (2, 2) -> tile (0, 0)
            box(6.0, 2.0, 8.0, 4.0),  # rep-point (7, 3) -> tile (0, 0), a 2nd building
            box(12.0, 2.0, 14.0, 4.0),  # rep-point (13, 3) -> tile (0, 1)
            box(2.0, 12.0, 4.0, 14.0),  # rep-point (3, 13) -> tile (1, 0)
            box(15.0, 15.0, 17.0, 17.0),  # rep-point (16, 16) -> tile (1, 1)
        ],
        crs=config.WORKING_CRS,
    )
    aoi = box(0.0, 0.0, 20.0, 20.0)

    tiles = tiling.make_grid(aoi, _SIZE, 3.0, _ORIGIN)
    partitions = [
        pipeline.select_by_membership(fp, tiling.tile_membership(t, _SIZE, _ORIGIN))
        for t in tiles
    ]
    city = pipeline.merge_tile_roofs(partitions)

    assert len(city) == len(fp)
    assert sum(len(part) for part in partitions) == len(city)
    assert set(city["fid"].tolist()) == set(fp["fid"].tolist())
