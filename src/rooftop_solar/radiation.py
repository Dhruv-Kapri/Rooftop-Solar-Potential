"""Stage 3 — solar radiation on the surface, WITH inter-building shading (risks §8).

Engine: GRASS GIS `r.sun` (decided — risks §14.2). Runs a full-year radiation model over
the DSM; `r.sun`/`r.horizon` do per-cell horizon/shadow-casting so neighbouring buildings
shade each other.

Traps to respect (risks §8):
  - input must be the DSM, not the DTM
  - AOI must be buffered
  - shadow search distance must be large enough for tall far towers
GRASS is a system dependency; call it via subprocess (grass session) or the pygrass API.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import ModuleType

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import rasterio
import rasterio.transform
from rasterio.features import geometry_mask
from rasterio.windows import Window
from rasterio.windows import from_bounds as window_from_bounds
from rasterio.windows import transform as window_transform
from shapely.geometry import mapping

# Output nodata sentinel for the annual insolation GeoTIFF (surface_irradiance). A concrete
# float value, not NaN, so downstream readers (e.g. zonal_insolation's `raster == nodata`
# check) have an unambiguous sentinel to test against.
INSOLATION_NODATA = -9999.0

# Per-roof shaded, clear-sky annual insolation column (consumed by usable_area / yield_pv).
# Kept in sync with usable_area.INSOLATION_COL — the producer/consumer contract.
POA_COLUMN = "poa_clear_sky_kwh_m2"

# 12 monthly representative days + days-in-month weights (ADR-0001). Annual insolation is a
# 12-sample estimate, not a true 365-day integral — stated plainly in outputs; the 365-day
# sum is the straightforward Phase-2 accuracy upgrade (same code path, longer day list).
MID_MONTH_DAYS = [15, 45, 74, 105, 135, 162, 198, 228, 258, 288, 319, 349]
DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def annual_insolation(
    daily_wh: Sequence[npt.NDArray[np.float64]], days_in_month: Sequence[int]
) -> npt.NDArray[np.float64]:
    """Sum monthly representative-day rasters into an annual insolation raster (ADR-0001).

    Each `daily_wh[i]` is a `r.sun` daily global-radiation raster (Wh/m²/day) for one month's
    representative day, with nodata already set to NaN. Each is weighted by its month's day
    count and summed, then converted Wh -> kWh. A pixel that is nodata in *any* month can't
    have a trustworthy annual sum, so it propagates to NaN (mirrors the notebook's nodata
    mask). Returns kWh/m²/yr.
    """
    annual_wh = np.zeros_like(np.asarray(daily_wh[0], dtype=np.float64))
    nodata_mask = np.zeros(annual_wh.shape, dtype=bool)
    for day, n_days in zip(daily_wh, days_in_month, strict=True):
        day = np.asarray(day, dtype=np.float64)
        nodata_mask |= np.isnan(day)
        annual_wh += np.nan_to_num(day) * n_days
    annual_kwh = annual_wh / 1000.0
    annual_kwh[nodata_mask] = np.nan
    return annual_kwh


def zonal_insolation(footprints: gpd.GeoDataFrame, insol_path: str | Path) -> gpd.GeoDataFrame:
    """Attach each footprint's mean shaded insolation from the raster at `insol_path`.

    `footprints` must share the raster's CRS (both the metric working CRS in the pipeline).
    For each roof, averages the annual shaded insolation (kWh/m²/yr) over the pixels whose
    centre falls inside the footprint, excluding nodata; roofs covering no valid pixel get
    NaN. Adds the :data:`POA_COLUMN` column and returns a copy. This shaded per-roof value is
    what yield_pv must consume (trap 4), never a raw NSRDB location value.

    The mask is computed on each roof's small bbox window rather than over the whole raster —
    O(sum of roof-bbox areas) instead of O(roofs × full raster), which matters at
    neighbourhood scale (thousands of roofs). Semantics are identical: `geometry_mask` is
    still the pixel-centre rule, just applied on the windowed sub-array with its own transform
    (guaranteed behaviour-preserving by test_radiation.py's characterization tests).
    """
    with rasterio.open(insol_path) as src:
        raster = src.read(1).astype("float64")
        nodata = src.nodata
        transform = src.transform
    if nodata is not None:
        raster[raster == nodata] = np.nan
    n_rows, n_cols = raster.shape

    means: list[float] = []
    for geom in footprints.geometry:
        minx, miny, maxx, maxy = geom.bounds
        win = window_from_bounds(minx, miny, maxx, maxy, transform)
        # Round out to whole pixels and pad 1 px each side so no centre-inside pixel is lost
        # to float rounding at the bbox edge; clamp to the raster.
        r0 = max(0, int(np.floor(win.row_off)) - 1)
        c0 = max(0, int(np.floor(win.col_off)) - 1)
        r1 = min(n_rows, int(np.ceil(win.row_off + win.height)) + 1)
        c1 = min(n_cols, int(np.ceil(win.col_off + win.width)) + 1)
        if r1 <= r0 or c1 <= c0:  # bbox falls entirely outside the raster
            means.append(np.nan)
            continue

        sub = raster[r0:r1, c0:c1]
        sub_transform = window_transform(Window(c0, r0, c1 - c0, r1 - r0), transform)
        inside = ~geometry_mask(
            [mapping(geom)], out_shape=sub.shape, transform=sub_transform, invert=False
        )
        vals = sub[inside]
        vals = vals[~np.isnan(vals)]
        means.append(float(np.mean(vals)) if vals.size else np.nan)

    out = footprints.copy()
    out[POA_COLUMN] = means
    return out


def plane_poa(planes_gdf: gpd.GeoDataFrame, insol_path: str | Path) -> gpd.GeoDataFrame:
    """Attach each per-plane row's mean insolation, sampled over that plane's own pixels.

    ADR-0012's per-plane POA, the companion to :func:`zonal_insolation` (which stays,
    unchanged, footprint-wide, for the `"ransac"` path). Consumes the per-plane schema
    `roof_planes.fit_roof_planes(method="multiplane")` produces: for each row, reads the
    insolation raster at `insol_path` at exactly that plane's `inlier_xy` pixel-centre
    coordinates and averages, excluding nodata and any point that falls outside the
    raster. Because `inlier_xy` holds *exact* DSM pixel centres and the insolation
    raster shares the DSM's own grid (`surface_irradiance` guarantees the same
    transform/shape), the (easting, northing) -> (row, col) lookup via the raster's
    inverse transform is exact — no resampling, no interpolation, no double-counting. A
    plane with empty membership (the unfittable-footprint placeholder row) gets NaN,
    matching `zonal_insolation`'s "no valid pixel" convention. Adds :data:`POA_COLUMN`
    and returns a copy; every other column is untouched.
    """
    with rasterio.open(insol_path) as src:
        raster = src.read(1).astype("float64")
        nodata = src.nodata
        transform = src.transform
    if nodata is not None:
        raster[raster == nodata] = np.nan
    n_rows, n_cols = raster.shape

    means: list[float] = []
    for xy in planes_gdf["inlier_xy"]:
        xy = np.asarray(xy, dtype=np.float64)
        if xy.shape[0] == 0:
            means.append(np.nan)
            continue
        rows, cols = rasterio.transform.rowcol(transform, xy[:, 0], xy[:, 1])
        rows = np.asarray(rows)
        cols = np.asarray(cols)
        in_bounds = (rows >= 0) & (rows < n_rows) & (cols >= 0) & (cols < n_cols)
        vals = raster[rows[in_bounds], cols[in_bounds]]
        vals = vals[~np.isnan(vals)]
        means.append(float(np.mean(vals)) if vals.size else np.nan)

    out = planes_gdf.copy()
    out[POA_COLUMN] = means
    return out


@contextlib.contextmanager
def grass_session(epsg: str, gisdb: str) -> Iterator[ModuleType]:
    """Create a throwaway GRASS location/mapset bound to `epsg` and yield `grass.script`.

    Ported from notebooks 02/04's `start_grass_session`, wrapped in a context manager (the
    plan requires "one GRASS session via a context manager" — stage-1-plan.md §4). GISBASE
    comes from `grass --config path`; `grass/etc/python` must land on `sys.path` *before*
    `import grass.script`. All GRASS state (location/mapset) lives under `gisdb`, which the
    caller creates with `tempfile.mkdtemp` and is responsible for removing — nothing here
    ever touches the repo tree.

    `epsg` must look like `"EPSG:<code>"` (rasterio's `CRS.from_epsg(n).to_string()` form).

    GRASS 8.5 API note vs. the notebook: `grass.script.setup.init` now returns a
    `SessionHandle` (a context manager with a `.finish()` method) instead of nothing. The
    notebook never closed it (a notebook kernel just exits), but this function calls
    `.finish()` in a `finally` block so repeated `with grass_session(...)` blocks in one
    process (e.g. multiple pipeline runs, or tests) each get a clean session.
    """
    gisbase = subprocess.run(
        ["grass", "--config", "path"], capture_output=True, text=True, check=True
    ).stdout.strip()
    os.environ["GISBASE"] = gisbase
    sys.path.insert(0, os.path.join(gisbase, "etc", "python"))

    import grass.script as gs
    from grass.script import setup as gsetup

    location = os.path.join(gisdb, "solar")
    subprocess.run(
        ["grass", "-e", "-c", epsg, location], check=True, capture_output=True, text=True
    )
    session = gsetup.init(os.path.join(location, "PERMANENT"))
    try:
        yield gs
    finally:
        session.finish()


def run_r_sun(
    gs: ModuleType, day: int, workdir: str
) -> tuple[npt.NDArray[np.float64], float | None]:
    """Run `r.sun` mode 2 for one day (shading ON, the default) and read `glob_rad` back.

    Ported from notebooks 02/04's `run_r_sun`. Requires `dsm`/`aspect`/`slope` rasters to
    already exist in the current GRASS mapset (see `surface_irradiance`). Exports via a
    direct `subprocess` call to `r.out.gdal` rather than `gs.run_command`: `r.sun` attaches a
    GRASS colour table to `glob_rad`, and `r.out.gdal`'s attempt to carry it into the Float32
    GeoTIFF logs a benign "SetColorTable() only supported for Byte or UInt16 bands" GDAL
    error to stderr — verified here on GRASS 8.5 that the raster values are written
    correctly regardless (`r.out.gdal` still exits 0). Returns `(array, nodata)`.
    """
    out_name = f"glob_rad_{day}"
    gs.run_command(
        "r.sun",
        elevation="dsm",
        aspect="aspect",
        slope="slope",
        day=day,
        glob_rad=out_name,
        overwrite=True,
        quiet=True,
    )
    tif_path = f"{workdir}/{out_name}.tif"
    subprocess.run(
        [
            "r.out.gdal",
            f"input={out_name}",
            f"output={tif_path}",
            "format=GTiff",
            "--overwrite",
            "--quiet",
        ],
        env=os.environ,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    with rasterio.open(tif_path) as src:
        return src.read(1), src.nodata


def _masked(arr: npt.NDArray, nodata: float | None) -> npt.NDArray[np.float64]:
    """Copy `arr` as float64 with nodata -> NaN (ported from notebooks 02/04's `masked`)."""
    out = arr.astype(np.float64).copy()
    if nodata is not None:
        out[arr == nodata] = np.nan
    return out


def surface_irradiance(
    dsm_path: str | Path,
    day_range: Sequence[int] | None = None,
    shadow_search_distance_m: float = 500.0,
) -> Path:
    """Run GRASS `r.sun` over `dsm_path` and write an annual shaded insolation GeoTIFF.

    Ports notebooks 02/04's per-day `r.sun` driver loop into production: for each day in
    `day_range` (mode 2, terrain/building shadowing ON — the default, trap 1), export
    `glob_rad` (Wh/m²/day) and weight-sum it via :func:`annual_insolation` into an annual
    kWh/m²/yr raster on the *same grid as the input DSM*, written to a GeoTIFF, whose `Path`
    is returned. The caller decides where that file ultimately lives (e.g. copy it under
    `config.OUTPUTS_DIR`) — this function only ever writes into `tempfile` scratch space.

    Args:
        dsm_path: path to the DSM raster (NOT a DTM — trap 1). Its CRS/transform/shape are
            read directly and reused for both the GRASS location and the output raster, so
            the output is guaranteed to sit on the same grid as the input.
        day_range: days of year (1-365) to run `r.sun` for. `None` (default) uses the 12
            mid-month representative days (`MID_MONTH_DAYS`), weighted by `DAYS_IN_MONTH` —
            this is the *only* calibrated annual estimate (ADR-0001). Passing an explicit
            `day_range` instead weights each day uniformly by `365 / len(day_range)`; this
            is a fast smoke-test path (e.g. a single summer day for an integration test), not
            a physically meaningful annual total for anything other than the 12-day default.
        shadow_search_distance_m: nominal horizon/shadow search distance in metres. Must be
            positive. **Honest status (trap 3, risks §8):** this parameter is accepted for
            forward compatibility but is *not yet wired* into an explicit `r.sun`/`r.horizon`
            `maxdistance`. `r.sun` is run here exactly as in the notebook: no
            `horizon_basename` is supplied, so it falls back to its own built-in
            per-cell shadow search, which reaches to the edge of the current GRASS *region*
            (set below via `g.region raster=dsm`, i.e. bounded by the DSM's own extent — the
            buffered AOI, trap 2, is what actually determines how far shadows can be seen).
            Precomputing horizon rasters with `r.horizon` (`output=`, `step=`,
            `maxdistance=shadow_search_distance_m`) and feeding them to `r.sun` via
            `horizon_basename`/`horizon_step` was investigated: `r.horizon`'s multidirectional
            output *does* let `r.sun` honour an explicit `maxdistance`, but it also swaps
            `r.sun`'s shadow algorithm from continuous per-ray testing (this function's
            behaviour, matching the notebook exactly) to an interpolated, discretised
            horizon-angle approximation — a real accuracy trade-off, not just an explicitness
            fix. Flipping the default to that path without its own accuracy validation would
            silently change results vs. the notebook, which "faithful promotion first, harden
            second" (stage-1-plan.md §3) rules out here. Left as a documented Phase 2
            hardening item; do not read this docstring as trap-3 compliance.

    Returns:
        Path to a single-band float32 GeoTIFF (kWh/m²/yr), nodata `INSOLATION_NODATA`, on the
        DSM's grid.
    """
    if shadow_search_distance_m <= 0:
        raise ValueError(
            f"shadow_search_distance_m must be positive, got {shadow_search_distance_m}"
        )

    dsm_path = Path(dsm_path)
    with rasterio.open(dsm_path) as src:
        crs = src.crs
        transform = src.transform
        width, height = src.width, src.height
        if crs is None:
            raise ValueError(f"DSM at {dsm_path} has no CRS; cannot bind a GRASS location to it")
        epsg = crs.to_epsg()
        if epsg is None:
            raise ValueError(f"DSM CRS at {dsm_path} has no EPSG code: {crs.to_wkt()}")

    if day_range is None:
        days: Sequence[int] = MID_MONTH_DAYS
        weights: Sequence[float] = DAYS_IN_MONTH
    else:
        days = list(day_range)
        if not days:
            raise ValueError("day_range must not be empty")
        weights = [365.0 / len(days)] * len(days)

    gisdb = tempfile.mkdtemp(prefix="rooftop_solar_grassdata_")
    workdir = tempfile.mkdtemp(prefix="rooftop_solar_rsun_")
    try:
        with grass_session(f"EPSG:{epsg}", gisdb) as gs:
            gs.run_command(
                "r.in.gdal", input=str(dsm_path), output="dsm", overwrite=True, quiet=True
            )
            # Region = the DSM's own (upstream-buffered, trap 2) extent -- this is what
            # actually bounds r.sun's shadow search, see the shadow_search_distance_m note
            # above (trap 3).
            gs.run_command("g.region", raster="dsm")
            gs.run_command(
                "r.slope.aspect",
                elevation="dsm",
                slope="slope",
                aspect="aspect",
                overwrite=True,
                quiet=True,
            )
            daily = [_masked(*run_r_sun(gs, day, workdir)) for day in days]
    finally:
        shutil.rmtree(gisdb, ignore_errors=True)
        shutil.rmtree(workdir, ignore_errors=True)

    annual_kwh_m2 = annual_insolation(daily, weights)

    out_dir = Path(tempfile.mkdtemp(prefix="rooftop_solar_insolation_"))
    out_path = out_dir / "annual_insolation_kwh_m2.tif"
    out_array = np.where(np.isnan(annual_kwh_m2), INSOLATION_NODATA, annual_kwh_m2).astype(
        np.float32
    )
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": transform,
        "nodata": INSOLATION_NODATA,
        "compress": "deflate",
    }
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(out_array, 1)

    return out_path
