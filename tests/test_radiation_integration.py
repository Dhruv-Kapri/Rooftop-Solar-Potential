"""Integration smoke test for `radiation.surface_irradiance` (needs GRASS on PATH).

Builds a small SYNTHETIC DSM (a flat plane with a raised block in the middle, so there's a
real shadow-caster) and runs `r.sun` for a single day, end to end through a real GRASS 8.5
session. This is a smoke test, not a calibration: it asserts the plumbing (grid alignment,
finiteness, sane magnitude), not a specific insolation value. Deselected by default (see
`pyproject.toml`'s `addopts`); run explicitly with `pytest -m integration`.
"""

from __future__ import annotations

import shutil

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from rooftop_solar import radiation

WORKING_CRS = "EPSG:6347"  # NAD83(2011) / UTM 18N -- metric, matches the pipeline's WORKING_CRS
RES = 2.0
GRID_SIZE = 30
DSM_NODATA = -9999.0


def _write_synthetic_dsm(path) -> None:
    """A flat 10 m plane with a raised 30 m block in the middle -- a real shadow-caster."""
    dsm = np.full((GRID_SIZE, GRID_SIZE), 10.0, dtype="float32")
    lo, hi = 12, 18
    dsm[lo:hi, lo:hi] = 40.0

    # Plausible metric coordinates inside UTM zone 18N (covers DC), arbitrary otherwise.
    transform = from_origin(500_000.0, 4_310_000.0, RES, RES)
    profile = {
        "driver": "GTiff",
        "height": GRID_SIZE,
        "width": GRID_SIZE,
        "count": 1,
        "dtype": "float32",
        "crs": CRS.from_string(WORKING_CRS),
        "transform": transform,
        "nodata": DSM_NODATA,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(dsm, 1)


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("grass") is None, reason="GRASS not on PATH")
def test_surface_irradiance_smoke(tmp_path):
    dsm_path = tmp_path / "synthetic_dsm.tif"
    _write_synthetic_dsm(dsm_path)

    out_path = radiation.surface_irradiance(dsm_path, day_range=[172])

    assert out_path.exists()

    with rasterio.open(dsm_path) as dsm_src, rasterio.open(out_path) as out_src:
        assert out_src.width == dsm_src.width
        assert out_src.height == dsm_src.height
        assert out_src.transform == dsm_src.transform
        assert out_src.crs == dsm_src.crs

        arr = out_src.read(1).astype("float64")
        nodata = out_src.nodata
        assert nodata is not None
        arr[arr == nodata] = np.nan

    finite = arr[~np.isnan(arr)]
    assert finite.size > 0
    assert np.all(finite >= 0)
    # A single day carries a 365/1 annualisation weight (see surface_irradiance's day_range
    # docstring) -- not a physically meaningful annual total, just bounded and positive.
    # r.sun's single-day clear-sky glob_rad tops out at a few kWh/m2/day; even multiplied by
    # 365 that stays a couple orders of magnitude below this generous upper bound.
    assert finite.max() < 12_000.0
