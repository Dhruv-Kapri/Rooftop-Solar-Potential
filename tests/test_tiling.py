"""Unit tests for the city-scale tiling grid (Phase 2, Part 2-2; ADR-0009).

Pure geometry, no network/GRASS: synthetic grid with `origin=(0.0, 0.0)`,
`tile_size_m=10.0` throughout, so every expected `(row, col)` and every expected
polygon coordinate is a hand-worked literal — never recomputed with the same
`floor`/`box` formula the implementation uses (that would test nothing).

Two seams under test (stage-2-part2-plan.md §7, ADR-0009):

  - `tile_index` — floor-arithmetic membership, including the on-seam/on-corner
    case `.within` would drop (the exactly-once guarantee).
  - `make_grid` — fixed-origin cells covering an AOI: core/buffer shape, non-overlap,
    off-AOI cells dropped, fixed-origin invariance across different AOI framings.
"""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import Point, box

from rooftop_solar import tiling

_ORIGIN = (0.0, 0.0)
_SIZE = 10.0


def test_tile_index_point_strictly_inside_origin_cell():
    # (5, 5) sits strictly inside the cell covering x in [0, 10), y in [0, 10) — cell (0, 0).
    assert tiling.tile_index(Point(5.0, 5.0), _SIZE, _ORIGIN) == (0, 0)


def test_tile_index_point_strictly_inside_a_distant_cell():
    # (35, 25): col = floor(35/10) = 3, row = floor(25/10) = 2 — cell (row=2, col=3).
    assert tiling.tile_index(Point(35.0, 25.0), _SIZE, _ORIGIN) == (2, 3)


def test_tile_index_point_on_shared_edge_resolves_to_higher_index_cell():
    # x=10.0 sits exactly on the seam between col 0 and col 1; y=5.0 is strictly inside
    # row 0. floor(10.0/10) = 1, not 0 — the higher-index cell, not `.within`'s drop.
    assert tiling.tile_index(Point(10.0, 5.0), _SIZE, _ORIGIN) == (0, 1)


def test_tile_index_point_on_shared_corner_resolves_to_higher_index_cell_both_axes():
    # (10.0, 10.0) is the corner shared by cells (0,0), (0,1), (1,0), (1,1). floor gives
    # the higher index on BOTH axes, deterministically picking exactly one: (1, 1).
    assert tiling.tile_index(Point(10.0, 10.0), _SIZE, _ORIGIN) == (1, 1)


def test_make_grid_single_cell_aoi_yields_one_tile():
    # aoi strictly inside cell (0, 0) — no seam-touching ambiguity.
    aoi = box(2.0, 2.0, 8.0, 8.0)
    buffer_m = 3.0

    tiles = tiling.make_grid(aoi, _SIZE, buffer_m, _ORIGIN)

    assert len(tiles) == 1
    tile = tiles[0]
    assert (tile.row, tile.col) == (0, 0)
    assert tile.core.equals(box(0.0, 0.0, 10.0, 10.0))
    assert tile.buffered.equals(box(0.0, 0.0, 10.0, 10.0).buffer(buffer_m))


def test_make_grid_2x2_block_aoi_yields_four_nonoverlapping_covering_tiles():
    # aoi strictly inside the union of cells (0,0),(0,1),(1,0),(1,1) — kept off the outer
    # grid lines (x=20, y=20) so it can't accidentally touch a 5th cell.
    aoi = box(1.0, 1.0, 19.0, 19.0)

    tiles = tiling.make_grid(aoi, _SIZE, 3.0, _ORIGIN)

    assert {(t.row, t.col) for t in tiles} == {(0, 0), (0, 1), (1, 0), (1, 1)}

    # Cores are pairwise non-overlapping (share at most an edge — zero-area intersection).
    cores = [t.core for t in tiles]
    for i, a in enumerate(cores):
        for b in cores[i + 1 :]:
            assert a.intersection(b).area == 0.0

    # The union of cores covers the aoi.
    union = cores[0]
    for c in cores[1:]:
        union = union.union(c)
    assert union.covers(aoi)


def test_make_grid_drops_offaoi_cells_in_the_bbox():
    # Two small squares, each strictly inside a different cell of a 3x3 bbox: (0,0) and
    # (2,2). The bbox spans 9 cells, but only these two actually intersect the aoi — the
    # other 7 (including the "diagonal" (1,1)) must be absent.
    aoi = box(1.0, 1.0, 4.0, 4.0).union(box(21.0, 21.0, 24.0, 24.0))

    tiles = tiling.make_grid(aoi, _SIZE, 3.0, _ORIGIN)

    assert {(t.row, t.col) for t in tiles} == {(0, 0), (2, 2)}


def test_make_grid_fixed_origin_gives_identical_core_regardless_of_aoi_framing():
    # Cell (row=1, col=1) is box(10, 10, 20, 20) by construction from _ORIGIN. Two very
    # differently-framed AOIs both overlap it; the resulting core must be the byte-for-byte
    # same polygon either way, because both anchor to the same fixed origin, not to the aoi.
    expected_core = box(10.0, 10.0, 20.0, 20.0)

    small_aoi = box(11.0, 11.0, 15.0, 15.0)  # inside cell (1,1) only
    large_aoi = box(5.0, 5.0, 25.0, 25.0)  # spans a 2x2 block including cell (1,1)

    small_tiles = {(t.row, t.col): t.core for t in tiling.make_grid(small_aoi, _SIZE, 3.0, _ORIGIN)}
    large_tiles = {(t.row, t.col): t.core for t in tiling.make_grid(large_aoi, _SIZE, 3.0, _ORIGIN)}

    assert small_tiles[(1, 1)].equals_exact(expected_core, tolerance=0.0)
    assert large_tiles[(1, 1)].equals_exact(expected_core, tolerance=0.0)


def test_tile_index_and_make_grid_agree_across_a_seam():
    # Two points just either side of the col-0/col-1 seam at x=10, both in row 0 —
    # strictly interior to their own cells (no boundary-contains ambiguity).
    left = Point(9.99, 5.0)
    right = Point(10.01, 5.0)
    assert tiling.tile_index(left, _SIZE, _ORIGIN) == (0, 0)
    assert tiling.tile_index(right, _SIZE, _ORIGIN) == (0, 1)

    aoi = box(5.0, 3.0, 15.0, 7.0)  # spans both cells (0,0) and (0,1)
    tiles = {(t.row, t.col): t for t in tiling.make_grid(aoi, _SIZE, 3.0, _ORIGIN)}

    for point in (left, right):
        row_col = tiling.tile_index(point, _SIZE, _ORIGIN)
        assert row_col in tiles
        assert tiles[row_col].core.contains(point)


# --------------------------------------------------------------------------- #
# tile_membership — the vectorized predicate that feeds                      #
# pipeline.select_by_membership (stage-2-part2-plan.md §4, §7)               #
# --------------------------------------------------------------------------- #

_TILE_0_0 = tiling.Tile(
    row=0, col=0, core=box(0.0, 0.0, 10.0, 10.0), buffered=box(0.0, 0.0, 10.0, 10.0).buffer(3.0)
)
_TILE_0_1 = tiling.Tile(
    row=0, col=1, core=box(10.0, 0.0, 20.0, 10.0), buffered=box(10.0, 0.0, 20.0, 10.0).buffer(3.0)
)


def test_tile_membership_true_only_for_own_tile_interior_points():
    # (5, 5) is strictly interior to (row=0, col=0); (15, 5) is strictly interior to (row=0, col=1).
    pts = gpd.GeoSeries([Point(5.0, 5.0), Point(15.0, 5.0)])

    mask_0_0 = tiling.tile_membership(_TILE_0_0, _SIZE, _ORIGIN)(pts)
    mask_0_1 = tiling.tile_membership(_TILE_0_1, _SIZE, _ORIGIN)(pts)

    assert mask_0_0.tolist() == [True, False]
    assert mask_0_1.tolist() == [False, True]


def test_tile_membership_on_seam_point_true_for_higher_index_tile_only():
    # x=10.0 sits exactly on the seam between col 0 and col 1 (y=5.0 strictly inside row 0):
    # floor gives col=1 (tile_index's exactly-once guarantee) — True for (0,1), False for (0,0).
    pts = gpd.GeoSeries([Point(10.0, 5.0)])

    mask_0_0 = tiling.tile_membership(_TILE_0_0, _SIZE, _ORIGIN)(pts)
    mask_0_1 = tiling.tile_membership(_TILE_0_1, _SIZE, _ORIGIN)(pts)

    assert mask_0_0.tolist() == [False]
    assert mask_0_1.tolist() == [True]
