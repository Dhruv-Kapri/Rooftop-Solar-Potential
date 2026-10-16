#!/usr/bin/env python
"""One-off — upgrade the issue #19 city tile cache to the checksummed sidecar format (#18).

The #19 cache predates ADR-0014's checksum addendum: its DSM / insolation `.key` sidecars are
plain-text keys (now read as absent) and its roofs parquets have no sidecar at all. Re-using
it beats a ~3.9 h r.sun recompute: this rewrites each legacy raster sidecar in the JSON format
(same key, plus the file's sha256) and gives every manifest-`done` roofs parquet a sidecar
under its manifest stamp (the key `pipeline.cache_tile_result` would have written).

    python scripts/adopt_legacy_sidecars.py                     # data/tiles
    python scripts/adopt_legacy_sidecars.py --tiles-dir /path/to/tiles

Trust caveat: the new sidecars vouch for the artifacts' bytes as they are NOW. Run this only
on the machine that produced the cache, before the first `scripts/sync_tiles.sh` handoff —
never on a received copy. Idempotent: already-current sidecars are left alone.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import NamedTuple

from rooftop_solar import cache, config, pipeline


class Adopted(NamedTuple):
    """How many raster sidecars were upgraded and how many roofs sidecars newly written."""

    rasters: int
    roofs: int


def adopt_tile_cache(tiles_dir: str | Path) -> Adopted:
    """Adopt every legacy raster sidecar and back-fill the manifest-`done` roofs sidecars."""
    tiles_dir = Path(tiles_dir)
    rasters = sum(
        cache.adopt_legacy_sidecar(path)
        for pattern in ("tile_*_dsm.tif", "tile_*_insolation.tif")
        for path in sorted(tiles_dir.glob(pattern))
    )
    manifest = pipeline.TileManifest.load(tiles_dir / pipeline.MANIFEST_NAME)
    roofs = 0
    for key, entry in manifest.entries.items():
        if entry.get("status") != "done":
            continue
        row, col = (int(i) for i in key.split("_"))
        path = pipeline.tile_cache_path(tiles_dir, row, col, "roofs")
        if path.exists() and cache.read_key_sidecar(path) is None:
            cache.write_key_sidecar(path, entry["stamp"])
            roofs += 1
    return Adopted(rasters, roofs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--tiles-dir", default=config.TILES_CACHE_DIR, help="tile cache dir (default: data/tiles)"
    )
    args = parser.parse_args()

    adopted = adopt_tile_cache(args.tiles_dir)
    print(
        f"adopted {adopted.rasters} raster sidecars and {adopted.roofs} roofs sidecars "
        f"under {args.tiles_dir}"
    )


if __name__ == "__main__":
    main()
