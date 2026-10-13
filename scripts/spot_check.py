#!/usr/bin/env python
"""Part 2-4 step 7b CLI — build the hand spot-check kit (front half).

Draws the stratified spot-check sample from the city roofs (plan §6), re-scores it with
both the multiplane and the single-plane "ransac" methods, and renders one comparison
figure per building (DC 2023 orthophoto | DSM hillshade | multiplane | ransac) so the
roofs can be hand-labelled in ``labels.csv``. Comparing the labels against the pipeline
is a later ticket; this script stops at the figures + labels template.

    python scripts/spot_check.py                         # all areas -> <city dir>/spot_check/
    python scripts/spot_check.py --areas glover_park --per-cell 2
    python scripts/spot_check.py --no-images             # data only, skip the figures

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

from rooftop_solar import config, validation  # noqa: E402

PAD_M = 8.0  # padding around the footprint bounds in every panel
ORTHO_RES_M = 0.1
ORTHO_MAX_PX = 800
ORTHO_TIMEOUT_S = 30


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
    """Standard Horn-style hillshade (0-1) of a DSM window; NaN-safe."""
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
    with rasterio.open(tiles_dir / f"tile_{row}_{col}_dsm.tif") as src:
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
    ax.set_xlim(extent[0], extent[2])
    ax.set_ylim(extent[1], extent[3])
    ax.set_xticks([])
    ax.set_yticks([])


def draw_method(ax, shade, plot_extent, extent, footprint, mem, obs, planes, method) -> None:
    """Hillshade + plane-coloured DSM pixels + red obstruction outlines for one method."""
    _panel_base(ax, shade, plot_extent, extent, footprint)
    m = mem[mem["method"] == method]
    fitted = m[m["plane_id"] >= 0]
    ax.scatter(
        fitted["x"], fitted["y"], c=fitted["plane_id"], cmap="tab10", vmin=0, vmax=9,
        s=6, marker="s", alpha=0.5, linewidths=0,
    )  # fmt: skip
    unfit = m[m["plane_id"] < 0]
    ax.scatter(unfit["x"], unfit["y"], c="white", s=3, marker="x", alpha=0.5, linewidths=0.4)
    o = obs[obs["method"] == method]
    for geom in o.geometry:
        _outline(ax, geom, color="red", lw=1.0)
    p = planes[planes["method"] == method]
    ax.set_title(
        f"{method}: {int(p['n_planes'].max()) if len(p) else 0} planes, "
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
        axes[0].text(0.5, 0.5, "orthophoto unavailable", ha="center", va="center")
        axes[0].set_xlim(extent[0], extent[2])
        axes[0].set_ylim(extent[1], extent[3])
        _outline(axes[0], fp, color="cyan", lw=1.2)
        axes[0].set_xticks([])
        axes[0].set_yticks([])
    else:
        axes[0].imshow(ortho, extent=(extent[0], extent[2], extent[1], extent[3]))
        _outline(axes[0], fp, color="cyan", lw=1.2)
        axes[0].set_xlim(extent[0], extent[2])
        axes[0].set_ylim(extent[1], extent[3])
        axes[0].set_xticks([])
        axes[0].set_yticks([])
    axes[0].set_title("DC 2023 orthophoto", fontsize=8)

    _panel_base(axes[1], shade, plot_extent, extent, fp)
    axes[1].set_title("DSM hillshade", fontsize=8)

    mem = score.membership[score.membership["building_id"] == bid]
    obs = score.obstructions[score.obstructions["building_id"] == bid]
    planes = score.planes[score.planes["building_id"] == bid]
    for ax, method in zip(axes[2:], ("multiplane", "ransac"), strict=True):
        draw_method(ax, shade, plot_extent, extent, fp, mem, obs, planes, method)

    fig.suptitle(f"{bid}  |  {row['area']}  |  {row['roof_type']}", fontsize=10)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--city", default=None, help="city roofs.parquet (default: calibrated run's roofs.parquet)"
    )
    parser.add_argument("--tiles-dir", default=None, help="tile cache dir (default: data/tiles)")
    parser.add_argument("--out-dir", default=None, help="default: <city parquet dir>/spot_check")
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

    city_path = (
        Path(args.city) if args.city else config.CALIBRATED_CITY_OUTPUTS_DIR / "roofs.parquet"
    )
    tiles_dir = Path(args.tiles_dir) if args.tiles_dir else config.TILES_CACHE_DIR
    out_dir = Path(args.out_dir) if args.out_dir else city_path.parent / "spot_check"
    ortho_cache = Path(args.ortho_cache) if args.ortho_cache else config.DATA_DIR / "ortho_2023"
    out_dir.mkdir(parents=True, exist_ok=True)

    city = gpd.read_parquet(city_path)
    names = args.areas or list(config.SPOT_CHECK_AREAS_WGS84)
    areas = {
        n: gpd.GeoSeries([box(*config.SPOT_CHECK_AREAS_WGS84[n])], crs="EPSG:4326")
        .to_crs(city.crs)
        .iloc[0]
        for n in names
    }
    sample = validation.select_spot_check_sample(areas=areas, city=city, per_cell=args.per_cell,
                                                 seed=args.seed)  # fmt: skip
    sample.drop(columns="geometry").to_csv(out_dir / "sample.csv", index=False)
    print(sample.groupby(["area", "roof_type"]).size().rename("n").to_string())

    try:
        labels = validation.write_labels_template(sample, out_dir / "labels.csv")
        print(f"wrote labels template: {labels}")
    except FileExistsError:
        print(f"labels.csv already exists in {out_dir}; kept untouched")

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

    print(f"\nNext: fill {out_dir / 'labels.csv'} (n_major_planes, has_obstruction y/n, notes).")


if __name__ == "__main__":
    main()
