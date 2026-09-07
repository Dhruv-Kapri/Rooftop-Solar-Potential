"""City-scale tiling grid (Phase 2, Part 2-2 — stage-2-part2-plan.md §4; ADR-0009).

Pure geometry: no I/O, no CRS handling. Callers (`pipeline.run_city`) are responsible
for supplying `aoi`/`point` already in `config.WORKING_CRS` (metres) — this module
just does the arithmetic and box-building.

`tile_index` is the membership rule: `(row, col) = floor((point - origin) / tile_size_m)`.
`floor` makes membership a **total partition** — every point maps to exactly one
`(row, col)`, with a point exactly on a shared cell edge or corner resolving to the
higher-index cell (ADR-0009). This is deliberately *not* a shapely `.within`/`.intersects`
polygon test: `.within` excludes the boundary and would silently drop a rep-point sitting
exactly on a seam (the Part 2-1 bug this ADR avoids one scale up — risks §8, ADR-0007).

`make_grid` builds the `Tile`s covering an AOI from the *same* fixed `origin`, so a cell's
`(row, col)` — and its `core` polygon's coordinates — never depend on how the AOI happens
to be framed. That fixed-origin property is what makes tile caching idempotent
(stage-2-part2-plan.md §3): the same physical cell always gets the same cache key.

`tile_membership` is `tile_index`'s vectorized companion: given one `Tile`, it returns a
predicate over a whole representative-point `GeoSeries` (rather than one point at a time),
shaped to plug straight into `pipeline.select_by_membership` for the city runner's per-tile
core-footprint selection.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
from shapely.geometry import Point, Polygon, box
from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class Tile:
    """One grid cell: its `(row, col)` index plus its `core` and `buffered` polygons.

    `core` is the cell itself — the extent a building must fall in (by `tile_index`
    membership) to be scored by this tile, so cores never overlap and partition the
    grid. `buffered` is `core` grown by `TILE_BUFFER_M` — the extent fetched/rendered
    (DSM, footprints, `r.sun`) so a caster just outside the core still shades a roof near
    its edge (ADR-0009's inter-tile shading fix, ADR-0005/risks §8 trap 2 one scale up).
    Frozen because a `Tile` is an immutable grid fact, not mutable run state — the
    per-tile cache/manifest keys off `(row, col)`.

    Attributes:
        row: Northing (y) grid index.
        col: Easting (x) grid index.
        core: The cell polygon, `box(ox + col*size, oy + row*size, ox + (col+1)*size,
            oy + (row+1)*size)`.
        buffered: `core.buffer(buffer_m)`.
    """

    row: int
    col: int
    core: Polygon
    buffered: Polygon


def tile_index(
    point: Point, tile_size_m: float, origin: tuple[float, float]
) -> tuple[int, int]:
    """Map a point to its `(row, col)` grid cell by floor-arithmetic membership.

    `row` comes from northing (y), `col` from easting (x): `col = floor((x - ox) /
    tile_size_m)`, `row = floor((y - oy) / tile_size_m)`. `math.floor` (not `int()`,
    which truncates toward zero and would misbehave for negative offsets) makes this a
    **total partition** of the plane: every point lands in exactly one cell, with a point
    exactly on a shared edge or corner resolving to the higher-index cell on that axis
    (e.g. origin `(0, 0)`, `tile_size_m=10`, point `(10.0, y)` -> `col=1`, not `0`). This
    is the exactly-once guarantee ADR-0009 relies on for seam conservation — a shapely
    `.within` test would instead drop that boundary point entirely.

    Args:
        point: The point to place, in the same metric CRS as `origin`/`tile_size_m`
            (`config.WORKING_CRS` for this project) — typically a building's
            `representative_point()`.
        tile_size_m: Grid cell edge length, in metres.
        origin: The grid's fixed `(x, y)` anchor, in metres. Must be the same `origin`
            passed to `make_grid` for the two to agree on cell boundaries.

    Returns:
        The `(row, col)` of the cell containing `point`.
    """
    ox, oy = origin
    col = math.floor((point.x - ox) / tile_size_m)
    row = math.floor((point.y - oy) / tile_size_m)
    return row, col


def make_grid(
    aoi: BaseGeometry, tile_size_m: float, buffer_m: float, origin: tuple[float, float]
) -> list[Tile]:
    """Build the fixed-origin `Tile`s whose core cell intersects `aoi`.

    Candidate cells are found by running `tile_index` over `aoi`'s bounding-box corners
    (the same `floor` arithmetic, so the candidate range always covers every cell the bbox
    could touch), then each candidate's `core` is built directly from `origin`/`col`/`row`
    — never from `aoi` itself — and kept only if `core.intersects(aoi)`. Building `core`
    from the fixed origin (not from `aoi`) is what gives **fixed-origin invariance**: the
    same physical cell comes out with the identical polygon regardless of how `aoi` happens
    to be framed, which is what makes the per-tile cache key (`row`, `col`) stable across
    runs (ADR-0009).

    A cell whose core only *touches* `aoi` (e.g. shares an edge) still counts as
    intersecting under shapely's `intersects` — harmless here since such a cell contributes
    no interior footprints anyway (`tile_index` on any real building's representative point
    inside it would place it in a genuinely-overlapping neighbour, if any), and dropping it
    would risk the opposite failure (a core that touches `aoi` at more than a single point).

    Args:
        aoi: The area of interest to cover, in the same metric CRS as `origin`.
        tile_size_m: Grid cell edge length, in metres.
        buffer_m: Metres to grow each `core` by for the `buffered` field (the inter-tile
            shading reach — `config.TILE_BUFFER_M`).
        origin: The grid's fixed `(x, y)` anchor, in metres. Must be the same `origin`
            passed to `tile_index` for the two to agree on cell boundaries.

    Returns:
        One `Tile` per grid cell whose `core` intersects `aoi`, in no particular order.
    """
    ox, oy = origin
    minx, miny, maxx, maxy = aoi.bounds
    row_min, col_min = tile_index(Point(minx, miny), tile_size_m, origin)
    row_max, col_max = tile_index(Point(maxx, maxy), tile_size_m, origin)

    tiles: list[Tile] = []
    for row in range(row_min, row_max + 1):
        for col in range(col_min, col_max + 1):
            core = box(
                ox + col * tile_size_m,
                oy + row * tile_size_m,
                ox + (col + 1) * tile_size_m,
                oy + (row + 1) * tile_size_m,
            )
            if core.intersects(aoi):
                tiles.append(Tile(row=row, col=col, core=core, buffered=core.buffer(buffer_m)))
    return tiles


def tile_membership(
    tile: Tile, tile_size_m: float, origin: tuple[float, float]
) -> Callable[[gpd.GeoSeries], np.ndarray]:
    """Return the vectorized `tile_index` membership predicate for `tile`.

    The companion to `tile_index`, shaped to feed `pipeline.select_by_membership`
    (ADR-0009, stage-2-part2-plan.md §4): rather than mapping one point to its
    `(row, col)`, this returns a predicate that tests a whole representative-point
    `GeoSeries` against one fixed `(tile.row, tile.col)` at once, using the identical
    `floor` arithmetic. Because `tile_index`'s `floor` partition is total, exactly one
    tile's predicate is True for any given point — including a point sitting exactly on
    a shared seam, which resolves to the higher-index tile (the same exactly-once
    guarantee `tile_index` documents; a shapely `.within` test would instead drop it).

    Args:
        tile: the tile whose `(row, col)` membership to test against.
        tile_size_m: grid cell edge length, in metres — must match the `tile_size_m`
            the tile's grid was built with.
        origin: the grid's fixed `(x, y)` anchor, in metres — must match the `origin`
            the tile's grid was built with.

    Returns:
        A predicate over a representative-point `GeoSeries` (in the same metric CRS
        as `origin`) returning a boolean `numpy` array, True where that point's cell
        equals `(tile.row, tile.col)`.
    """
    ox, oy = origin

    def predicate(pts: gpd.GeoSeries) -> np.ndarray:
        cols = np.floor((pts.x.to_numpy() - ox) / tile_size_m).astype(int)
        rows = np.floor((pts.y.to_numpy() - oy) / tile_size_m).astype(int)
        return (rows == tile.row) & (cols == tile.col)

    return predicate
