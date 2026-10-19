#!/usr/bin/env python
"""Part 2-4 step 7b CLI — build the hand spot-check kit (front half).

Draws the stratified spot-check sample from the city roofs (plan §6), re-scores it with
multiplane and the single-plane baseline (`validation.SPOT_CHECK_METHODS`), and renders one
comparison figure per building (DC 2023 orthophoto | DSM hillshade | multiplane | single-plane
baseline) so the
roofs can be hand-labelled in ``labels.csv``. Comparing the labels against the pipeline is
``scripts/spot_check_compare.py``; this script stops at the figures + labels template.

    python scripts/spot_check.py --city outputs/dc/12day_gcr/roofs.parquet  # -> <city>/spot_check/
    python scripts/spot_check.py --city ... --areas glover_park --per-cell 2
    python scripts/spot_check.py --city ... --no-images  # data only, skip the figures
    python scripts/spot_check.py --sample outputs/dc/12day_gcr/spot_check --out-dir ...  # #23

``--sample DIR`` re-scores the fixed sample of an earlier spot-check dir instead of drawing one,
so tuning rounds (#23) are all scored against the same hand-labelled roofs.

Needs the per-tile DSM + insolation caches under ``--tiles-dir`` (ADR-0009/0014).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
import requests  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402
from shapely.geometry import box  # noqa: E402

from rooftop_solar import config, pipeline, validation  # noqa: E402

PAD_M = 8.0  # padding around the footprint bounds in every panel
PANEL_IN = 3.6  # approx. drawn width of one panel (16 in figure / 4 panels, minus margins)
ORTHO_RES_M = 0.1
ORTHO_MAX_PX = 800
ORTHO_TIMEOUT_S = 30


# Panel titles: "ransac" here is the single-plane baseline through the multiplane chain
# (validation.SPOT_CHECK_METHODS), not the Phase-1 production ransac path -- say so on the image.
METHOD_TITLES = {"multiplane": "multiplane", "ransac": "single-plane baseline"}


def fetch_ortho(bounds, building_id: str, cache_dir: Path) -> np.ndarray | None:
    """DC 2023 orthophoto over `bounds` (EPSG:6347), cached per building; None on failure."""
    path = cache_dir / f"{building_id}.png"
    if not path.exists():
        minx, miny, maxx, maxy = bounds
        w = max(1, min(ORTHO_MAX_PX, round((maxx - minx) / ORTHO_RES_M)))
        h = max(1, min(ORTHO_MAX_PX, round((maxy - miny) / ORTHO_RES_M)))
        params = {
            "bbox": f"{minx},{miny},{maxx},{maxy}",
            "bboxSR": 6347,
            "imageSR": 6347,
            "size": f"{w},{h}",
            "format": "png",
            "f": "image",
        }
        try:
            resp = requests.get(
                f"{config.DC_ORTHO_2023_URL}/exportImage", params=params, timeout=ORTHO_TIMEOUT_S
            )
            resp.raise_for_status()
            if not resp.headers.get("content-type", "").startswith("image"):
                return None
        except requests.RequestException as exc:
            print(f"  orthophoto unavailable for {building_id}: {exc}")
            return None
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
    return plt.imread(path)


def hillshade(dsm: np.ndarray, res: float, azimuth: float = 315.0, altitude: float = 45.0):
    """Hillshade (0-1) of a DSM window from central-difference slope/aspect; NaN-safe."""
    dsm = np.where(np.isfinite(dsm), dsm, np.nanmean(dsm) if np.isfinite(dsm).any() else 0.0)
    dzdy, dzdx = np.gradient(dsm, res)  # rows run north->south, so dzdy is d/d(south)
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(dzdy, -dzdx)
    az = np.radians(360.0 - azimuth + 90.0)
    zen = np.radians(90.0 - altitude)
    shade = np.cos(zen) * np.cos(slope) + np.sin(zen) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(shade, 0.0, 1.0)


def read_hillshade(tiles_dir: Path, row: int, col: int, extent):
    """Hillshade of the tile DSM over `extent`; returns (image, (l, r, b, t) plot extent)."""
    with rasterio.open(pipeline.tile_cache_path(tiles_dir, row, col, "dsm")) as src:
        win = from_bounds(*extent, transform=src.transform).round_offsets().round_lengths()
        data = src.read(1, window=win, masked=True, boundless=True).astype(float)
        data = data.filled(np.nan)
        left, top = src.window_transform(win) * (0, 0)
        res = abs(src.res[0])
    h, w = data.shape
    return hillshade(data, res), (left, left + w * res, top - h * res, top)


def _outline(ax, geom, **kw) -> None:
    polys = getattr(geom, "geoms", [geom])
    for p in polys:
        ax.plot(*p.exterior.xy, **kw)


def _panel_base(ax, shade, plot_extent, extent, footprint) -> None:
    ax.imshow(shade, cmap="gray", extent=plot_extent, vmin=0, vmax=1)
    _outline(ax, footprint, color="cyan", lw=1.2)
    _frame(ax, extent)


def _frame(ax, extent) -> None:
    """Clip a panel to the shared (minx, miny, maxx, maxy) extent, no ticks."""
    ax.set_xlim(extent[0], extent[2])
    ax.set_ylim(extent[1], extent[3])
    ax.set_xticks([])
    ax.set_yticks([])


def draw_method(ax, shade, plot_extent, extent, footprint, mem, obs, planes, method) -> None:
    """Hillshade + plane-coloured DSM pixels + red obstruction outlines for one method."""
    _panel_base(ax, shade, plot_extent, extent, footprint)
    m = mem[mem["method"] == method]
    # Marker side = one DSM cell in points (the panel is ~PANEL_IN inches across `extent`).
    cell_m = (plot_extent[1] - plot_extent[0]) / shade.shape[1]
    cell_pt = 72.0 * PANEL_IN * cell_m / max(extent[2] - extent[0], extent[3] - extent[1])
    fitted = m[m["plane_id"] >= 0]
    ax.scatter(
        fitted["x"], fitted["y"], c=fitted["plane_id"], cmap="tab10", vmin=0, vmax=9,
        s=(0.8 * cell_pt) ** 2, marker="s", alpha=0.5, linewidths=0,
    )  # fmt: skip
    unfit = m[m["plane_id"] < 0]
    ax.scatter(unfit["x"], unfit["y"], c="white", s=3, marker="x", alpha=0.5, linewidths=0.4)
    o = obs[obs["method"] == method]
    for geom in o.geometry:
        _outline(ax, geom, color="red", lw=1.0)
    p = planes[planes["method"] == method]
    ax.set_title(
        f"{METHOD_TITLES[method]}: {int(p['n_planes'].max()) if len(p) else 0} planes, "
        f"obstr {p['obstruction_area_m2'].sum():.0f} m2, usable {p['usable_area_m2'].sum():.0f} m2",
        fontsize=8,
    )


def render_building(row, score, tiles_dir: Path, ortho_cache: Path, out_path: Path) -> None:
    bid = row["building_id"]
    fp = row.geometry
    minx, miny, maxx, maxy = fp.bounds
    extent = (minx - PAD_M, miny - PAD_M, maxx + PAD_M, maxy + PAD_M)
    shade, plot_extent = read_hillshade(tiles_dir, row["tile_row"], row["tile_col"], extent)

    fig, axes = plt.subplots(1, 4, figsize=(16, 4.4))
    ortho = fetch_ortho(extent, bid, ortho_cache)
    if ortho is None:
        axes[0].text(
            0.5, 0.5, "orthophoto unavailable", ha="center", va="center",
            transform=axes[0].transAxes,
        )  # fmt: skip
    else:
        axes[0].imshow(ortho, extent=(extent[0], extent[2], extent[1], extent[3]))
    _outline(axes[0], fp, color="cyan", lw=1.2)
    _frame(axes[0], extent)
    axes[0].set_title("DC 2023 orthophoto", fontsize=8)

    _panel_base(axes[1], shade, plot_extent, extent, fp)
    axes[1].set_title("DSM hillshade", fontsize=8)

    mem = score.membership[score.membership["building_id"] == bid]
    obs = score.obstructions[score.obstructions["building_id"] == bid]
    planes = score.planes[score.planes["building_id"] == bid]
    for ax, method in zip(axes[2:], validation.SPOT_CHECK_METHODS, strict=True):
        draw_method(ax, shade, plot_extent, extent, fp, mem, obs, planes, method)

    fig.suptitle(f"{bid}  |  {row['area']}  |  {row['roof_type']}", fontsize=10)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--city", help="city roofs.parquet, e.g. outputs/dc/12day_gcr/roofs.parquet"
    )
    source.add_argument(
        "--sample", help="earlier spot-check dir whose fixed sample to re-score (needs --out-dir)"
    )
    parser.add_argument("--tiles-dir", default=None, help="tile cache dir (default: data/tiles)")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="default: <city parquet dir>/spot_check; required with --sample",
    )
    parser.add_argument("--per-cell", type=int, default=3, help="buildings per (area, roof_type)")
    parser.add_argument("--seed", type=int, default=20)
    parser.add_argument(
        "--areas", nargs="+", choices=sorted(config.SPOT_CHECK_AREAS_WGS84), default=None,
        help="subset of areas (default: all)",
    )  # fmt: skip
    parser.add_argument("--no-images", action="store_true", help="skip the per-building figures")
    parser.add_argument(
        "--ortho-cache", default=None, help="orthophoto PNG cache (default: data/ortho_2023)"
    )
    args = parser.parse_args()
    if args.sample and not args.out_dir:
        parser.error("--sample requires --out-dir (don't overwrite the earlier spot-check dir)")

    tiles_dir = Path(args.tiles_dir) if args.tiles_dir else config.TILES_CACHE_DIR
    out_dir = Path(args.out_dir) if args.out_dir else Path(args.city).parent / "spot_check"
    ortho_cache = Path(args.ortho_cache) if args.ortho_cache else config.DATA_DIR / "ortho_2023"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.sample:
        sample = validation.load_spot_check_sample(args.sample)
        print(f"reusing {len(sample)} sampled roofs from {args.sample}")
    else:
        city = gpd.read_parquet(args.city)
        names = args.areas or list(config.SPOT_CHECK_AREAS_WGS84)
        areas = {
            n: gpd.GeoSeries([box(*config.SPOT_CHECK_AREAS_WGS84[n])], crs="EPSG:4326")
            .to_crs(city.crs)
            .iloc[0]
            for n in names
        }
        sample = validation.select_spot_check_sample(areas=areas, city=city, per_cell=args.per_cell,
                                                     seed=args.seed)  # fmt: skip
        print(sample.groupby(["area", "roof_type"]).size().rename("n").to_string())

        try:
            labels = validation.write_labels_template(sample, out_dir / "labels.csv")
            print(f"wrote labels template: {labels}")
        except FileExistsError:
            print(f"labels.csv already exists in {out_dir}; kept untouched")

    sample.drop(columns="geometry").to_csv(out_dir / "sample.csv", index=False)

    score = validation.rescore_sample(sample, tiles_dir)
    frames = {
        "planes": score.planes,
        "obstructions": score.obstructions,
        "membership": score.membership,
        "buildings": score.buildings,
    }
    for name, frame in frames.items():
        path = out_dir / f"{name}.parquet"
        frame.to_parquet(path)  # GeoDataFrame -> GeoParquet, DataFrame -> plain parquet
        print(f"wrote {path}")

    if not args.no_images:
        for _, row in sample.iterrows():
            render_building(
                row, score, tiles_dir, ortho_cache, out_dir / "images" / f"{row['building_id']}.png"
            )
        print(f"wrote {len(sample)} figures to {out_dir / 'images'}")

    if not args.sample:
        print(
            f"\nNext: fill {out_dir / 'labels.csv'} (n_major_planes, has_obstruction y/n, notes)."
        )


if __name__ == "__main__":
    main()
