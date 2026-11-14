"""`pipeline.score_stages` is the multiplane spine split out so the walkthrough (#41) can read
its intermediates; `_score_roofs(method="multiplane")` must stay a thin wrapper over it.

No GRASS and no network: `dsm.build_dsm` and `radiation.surface_irradiance` are stubbed with
small synthetic rasters on the working-CRS grid, everything downstream runs for real.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from geopandas.testing import assert_geodataframe_equal
from rasterio.transform import from_origin
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from rooftop_solar import config, dsm, pipeline, radiation

_NODATA = -9999.0
_X0, _Y0, _RES = 320000.0, 4306000.0, 2.0
_SIZE = 40  # px per side


def _write(path: Path, array: np.ndarray) -> Path:
    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": config.WORKING_CRS,
        "transform": from_origin(_X0, _Y0, _RES, _RES),
        "nodata": _NODATA,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype("float32"), 1)
    return path


@pytest.fixture
def stubbed_rasters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A south-facing 30-degree roof with a 2 m chimney, plus a one-pixel (unfittable) hut."""
    ys = _Y0 - (np.arange(_SIZE) + 0.5) * _RES
    z = np.tile((10.0 + np.tan(np.radians(30.0)) * (ys - ys.min()))[:, None], (1, _SIZE))
    z[5:7, 5:7] += 2.0  # chimney
    insol = np.full((_SIZE, _SIZE), 1200.0)

    dsm_path = _write(tmp_path / "dsm.tif", z)
    insol_path = _write(tmp_path / "insol.tif", insol)
    monkeypatch.setattr(dsm, "build_dsm", lambda aoi, **kw: dsm_path)
    monkeypatch.setattr(radiation, "surface_irradiance", lambda path, **kw: insol_path)


def _footprints() -> gpd.GeoDataFrame:
    big = box(_X0 + 2, _Y0 - 60, _X0 + 60, _Y0 - 2)
    hut = box(_X0 + 70, _Y0 - 8, _X0 + 72, _Y0 - 6)  # one pixel centre at most
    return gpd.GeoDataFrame(geometry=[big, hut], crs=config.WORKING_CRS)


def test_score_roofs_multiplane_is_score_stages_buildings(stubbed_rasters: None) -> None:
    fp = _footprints()
    aoi: BaseGeometry = box(-77.08, 38.91, -77.07, 38.92)

    old = pipeline._score_roofs(fp, aoi, day_range=[172], method="multiplane")
    stages = pipeline.score_stages(fp, aoi, day_range=[172])

    assert_geodataframe_equal(old, stages.buildings)
    # The per-plane frames carry what the walkthrough reads (and what collapse consumes).
    assert {radiation.POA_COLUMN, "inlier_xy"} <= set(stages.planes.columns)
    assert {"usable", "usable_area_m2", "annual_energy_kwh"} <= set(stages.usable_planes.columns)
    assert len(stages.obstructions) == len(stages.planes)
    assert stages.obstructions.geometry.notna().any()  # the chimney was found
    assert stages.dsm_path.exists() and stages.insol_path.exists()
