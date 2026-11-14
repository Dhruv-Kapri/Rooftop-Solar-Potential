"""Glover Park walkthrough ETL (#41, ADR-0015): raster math + colouring, the raster-PMTiles
writer, the vector layers and the manifest the page reads.

No GRASS, no network: every input is a small synthetic raster/frame. GDAL's MBTiles driver and the
`pmtiles` package come with the conda env.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import pandas as pd
import pytest
import rasterio
from affine import Affine
from pmtiles.reader import MmapSource, Reader
from rasterio.transform import from_origin
from shapely.geometry import box

from rooftop_solar import config, radiation, roof_planes, usable_area, walkthrough

RES = 2.0
X0, Y0 = 322000.0, 4306000.0  # inside DC, EPSG:6347


def _plane(dz_dx: float, dz_dy: float, n: int = 12) -> npt.NDArray[np.float64]:
    """A DSM plane z = a*x + b*y over an n x n grid (rows run north to south)."""
    xs = (np.arange(n) + 0.5) * RES
    ys = (n - np.arange(n) - 0.5) * RES
    return 10.0 + dz_dx * xs[None, :] + dz_dy * ys[:, None]


# --- raster math ---------------------------------------------------------------------------


def test_slope_aspect_matches_the_ransac_fit_and_flat_has_no_slope() -> None:
    """Pixel slope/aspect follow roof_planes' convention (aspect = compass azimuth of the
    downslope side), checked on a south-east-facing plane against the RANSAC fit."""
    dz_dx, dz_dy = -0.3, 0.4  # faces roughly south-east
    z = _plane(dz_dx, dz_dy)
    rows, cols = np.indices(z.shape)
    X = np.column_stack([(cols.ravel() + 0.5) * RES, (z.shape[0] - rows.ravel() - 0.5) * RES])
    fit = roof_planes.fit_roof_plane(X, z.ravel())

    slope, asp = walkthrough.slope_aspect(z, RES)
    assert slope[5, 5] == pytest.approx(fit["tilt_deg"])
    assert asp[5, 5] == pytest.approx(fit["aspect_deg"])

    flat_slope, _ = walkthrough.slope_aspect(np.full((8, 8), 12.0), RES)
    assert np.all(flat_slope == 0.0)


def test_hillshade_lights_the_slope_facing_the_sun() -> None:
    """Sun in the north-west at 45 degrees: a north-facing slope is brighter than flat ground,
    a south-facing one darker; flat ground is 255 * cos(45 degrees)."""
    north = walkthrough.hillshade(_plane(0.0, -0.5), RES)[5, 5]
    flat = walkthrough.hillshade(_plane(0.0, 0.0), RES)[5, 5]
    south = walkthrough.hillshade(_plane(0.0, 0.5), RES)[5, 5]

    assert flat == pytest.approx(255 * np.cos(np.radians(45.0)))
    assert north > flat > south


def test_hillshade_keeps_nodata_as_nan() -> None:
    z = _plane(0.0, 0.5)
    z[6, 6] = np.nan

    assert np.isnan(walkthrough.hillshade(z, RES)[6, 6])


def test_mask_to_footprints_blanks_everything_outside() -> None:
    transform = from_origin(X0, Y0, RES, RES)
    footprints = gpd.GeoDataFrame(
        geometry=[box(X0 + 4, Y0 - 10, X0 + 10, Y0 - 4)], crs=config.WORKING_CRS
    )

    out = walkthrough.mask_to_footprints(np.ones((10, 10)), transform, footprints)

    assert out[3, 3] == 1.0
    assert np.isnan(out[0, 0]) and np.isnan(out[9, 9])
    assert 0 < np.isfinite(out).sum() < out.size


# --- colouring + raster PMTiles ------------------------------------------------------------


def test_colorize_makes_nodata_transparent_and_clamps_to_the_range() -> None:
    rgba = walkthrough.colorize(np.array([[np.nan, -5.0, 0.0, 10.0, 99.0]]), 0.0, 10.0, "viridis")

    assert rgba.dtype == np.uint8 and rgba.shape == (1, 5, 4)
    assert rgba[0, 0, 3] == 0
    assert np.all(rgba[0, 1:, 3] == 255)
    assert np.array_equal(rgba[0, 1], rgba[0, 2])  # below the range = the low end
    assert np.array_equal(rgba[0, 3], rgba[0, 4])  # above the range = the high end
    assert not np.array_equal(rgba[0, 2], rgba[0, 3])


def test_write_raster_pmtiles_is_a_readable_archive_over_the_raster(
    tmp_path: Path,
) -> None:
    n = 600
    rgba = walkthrough.colorize(np.linspace(0, 1, n * n).reshape(n, n), 0, 1, "inferno")
    transform = Affine(RES, 0, X0, 0, -RES, Y0)

    out = walkthrough.write_raster_pmtiles(
        rgba, transform, config.WORKING_CRS, tmp_path / "r.pmtiles", minzoom=14, maxzoom=17
    )

    with open(out, "rb") as f:
        header = Reader(MmapSource(f)).header()
    assert (header["min_zoom"], header["max_zoom"]) == (14, 16)  # a 2 m raster tiles at z16
    # DC is near lon -77.0, lat 38.9; the raster is 1.2 km wide.
    west, south, east, north = (
        header[k] / 1e7 for k in ("min_lon_e7", "min_lat_e7", "max_lon_e7", "max_lat_e7")
    )
    assert -77.2 < west < east < -76.9 and 38.8 < south < north < 39.0
    assert 0.012 < east - west < 0.016


# --- vector layers -------------------------------------------------------------------------

_X, _Y = 322000.0, 4306000.0


def _usable_planes() -> gpd.GeoDataFrame:
    """b0: a 4 px south facet + a 2 px north-facing facet; b1: unfittable placeholder."""
    xy0 = np.array([[_X, _Y], [_X + 2, _Y], [_X, _Y + 2], [_X + 2, _Y + 2]])
    xy1 = np.array([[_X + 10, _Y], [_X + 12, _Y]])
    fp0, fp1 = box(_X - 1, _Y - 1, _X + 13, _Y + 3), box(_X + 30, _Y, _X + 33, _Y + 3)
    return gpd.GeoDataFrame(
        {
            "building_id": [0, 0, 1],
            "plane_id": [0, 1, 0],
            "tilt_deg": [30.0, 25.0, np.nan],
            "aspect_deg": [180.0, 0.0, np.nan],
            "inlier_xy": [xy0, xy1, np.empty((0, 2))],
            radiation.POA_COLUMN: [1500.0, 1400.0, np.nan],
            walkthrough.REASON_COLUMN: pd.Series(
                [None, "north_facing", "no_plane_fit"], dtype=object
            ),
        },
        geometry=[fp0, fp0, fp1],
        crs=config.WORKING_CRS,
    )


def _buildings() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "building_id": [0, 1],
            "usable": [True, False],
            walkthrough.REASON_COLUMN: pd.Series([None, "no_plane_fit"], dtype=object),
            "annual_energy_kwh": [20000.0, 0.0],
            "capacity_kw": [16.0, 0.0],
            "usable_area_m2": [80.0, 0.0],
            "tilt_deg": [30.0, np.nan],
            "aspect_deg": [180.0, np.nan],
            "n_planes": [2, 1],
        },
        geometry=[box(_X - 1, _Y - 1, _X + 13, _Y + 3), box(_X + 30, _Y, _X + 33, _Y + 3)],
        crs=config.WORKING_CRS,
    )


def test_facet_polygons_one_per_fitted_plane_with_the_reasons() -> None:
    facets = walkthrough.facet_polygons(_usable_planes(), RES, buildings=_buildings())

    assert facets.crs == "EPSG:4326"
    assert facets[["building_id", "plane_id"]].values.tolist() == [
        [0, 0],
        [0, 1],
    ]  # no facet for b1
    south, north = facets.iloc[0], facets.iloc[1]
    assert south["area_m2"] == pytest.approx(4 * RES * RES)  # px count x pixel area
    assert north["area_m2"] == pytest.approx(2 * RES * RES)
    assert pd.isna(south["reason"]) and north["reason"] == "north_facing"
    assert pd.isna(south["building_reason"]) and pd.isna(north["building_reason"])
    assert south["poa_kwh_m2"] == 1500.0 and south["aspect_deg"] == 180.0


def test_obstruction_and_building_layers_carry_the_page_schema() -> None:
    planes = _usable_planes()
    obstructions = gpd.GeoDataFrame(
        {"plane_area_m2": [16.0, 8.0, 0.0], "obstruction_area_m2": [4.0, 0.0, 0.0]},
        geometry=[box(_X, _Y, _X + 2, _Y + 2), None, None],
        crs=config.WORKING_CRS,
        index=planes.index,
    )

    obs = walkthrough.obstruction_polygons(obstructions, planes)
    assert len(obs) == 1 and obs.crs == "EPSG:4326"
    assert obs.iloc[0][["building_id", "plane_id"]].tolist() == [0, 0]
    assert obs.iloc[0]["area_m2"] == pytest.approx(4.0)

    web = walkthrough.buildings_to_web(_buildings())
    assert web.crs == "EPSG:4326"
    assert web.loc[0, "annual_energy_mwh"] == pytest.approx(20.0)
    assert set(web.columns) == {
        "building_id", "usable", "unsuitable_reason", "annual_energy_mwh", "capacity_kw",
        "usable_area_m2", "tilt_deg", "aspect_deg", "n_planes", "geometry",
    }  # fmt: skip


def test_walkthrough_reasons_are_the_pipelines_reasons() -> None:
    assert set(walkthrough.REASONS) == set(usable_area.UNSUITABLE_REASONS)


def test_manifest_has_the_schema_the_page_codes_against() -> None:
    buildings = _buildings()
    buildings.loc[1, "annual_energy_kwh"] = 0.0
    layer: dict[str, Any] = {"url": "x.pmtiles", "minzoom": 13, "maxzoom": 17, "legend": {}}
    rasters = {k: layer for k in ("hillshade", "insolation", "slope", "aspect")}

    m = walkthrough.walkthrough_manifest(
        bounds=(-77.08, 38.91, -77.07, 38.92),
        rasters=rasters,
        buildings=buildings,
        planes=_usable_planes(),
    )

    assert m["bounds"] == [-77.08, 38.91, -77.07, 38.92]
    assert m["center"] == pytest.approx([-77.075, 38.915])
    assert set(m["rasters"]) == {"hillshade", "insolation", "slope", "aspect"}
    assert m["aspect_flat"] == {"max_tilt_deg": 10.0, "color": "#bebebe"}
    assert m["vectors"] == {
        "facets": "facets.geojson",
        "obstructions": "obstructions.geojson",
        "buildings": "buildings.geojson",
    }
    assert m["reasons"] == [
        "too_steep",
        "north_facing",
        "low_insolation",
        "too_small",
        "no_plane_fit",
    ]
    stats = m["stats"]
    assert (stats["n_buildings"], stats["n_planes"], stats["n_suitable"]) == (2, 2, 1)
    assert stats["reason_counts"] == {  # buildings, by their dominant-plane reason
        "too_steep": 0, "low_insolation": 0, "north_facing": 0, "too_small": 0, "no_plane_fit": 1,
    }  # fmt: skip
    assert stats["plane_reason_counts"] == {  # fitted planes, by their own reason
        "too_steep": 0,
        "north_facing": 1,
        "low_insolation": 0,
    }
    assert stats["total_energy_mwh"] == pytest.approx(20.0)
    assert stats["median_energy_mwh"] == pytest.approx(20.0)  # suitable buildings only
    assert stats["specific_yield_kwh_kwp"] == pytest.approx(20000 / 16)
    assert stats["esri_specific_yield_kwh_kwp"] == config.ESRI_SPECIFIC_YIELD_KWH_KWP == 1150.0
    assert stats["esri_median_energy_mwh"] == config.ESRI_MEDIAN_ENERGY_MWH == 13.35


def test_build_rasters_writes_four_layers_clipped_to_the_footprints(tmp_path: Path) -> None:
    n = 120
    transform = from_origin(X0, Y0, RES, RES)
    dsm = _plane(0.0, 0.5, n)  # a 27-degree slope facing south
    insol = np.tile(1000.0 + 5.0 * np.arange(n), (n, 1))  # 1000..1595, rising eastwards
    insol[:, :10] = -9999.0  # nodata
    paths: dict[str, Path] = {}
    for name, array in (("dsm", dsm), ("insol", insol)):
        paths[name] = tmp_path / f"{name}.tif"
        with rasterio.open(
            paths[name], "w", driver="GTiff", width=n, height=n, count=1, dtype="float32",
            crs=config.WORKING_CRS, transform=transform, nodata=-9999.0,
        ) as dst:  # fmt: skip
            dst.write(array.astype("float32"), 1)
    footprints = gpd.GeoDataFrame(
        geometry=[box(X0 + 60, Y0 - 160, X0 + 120, Y0 - 100)], crs=config.WORKING_CRS
    )

    layers = walkthrough.build_rasters(paths["dsm"], paths["insol"], footprints, tmp_path / "out")

    assert set(layers) == {"hillshade", "insolation", "slope", "aspect"}
    for name, layer in layers.items():
        assert (tmp_path / "out" / layer["url"]).exists() and layer["url"] == f"{name}.pmtiles"
        assert layer["minzoom"] <= layer["maxzoom"]
        assert layer["legend"]["min"] < layer["legend"]["max"]
    # The footprint covers columns 30-59 (1150..1295), so the legend range stays inside that.
    assert 1150.0 <= layers["insolation"]["legend"]["min"] < layers["insolation"]["legend"]["max"]
    assert layers["insolation"]["legend"]["max"] <= 1295.0
    assert layers["slope"]["legend"]["max"] == 60.0 and layers["aspect"]["legend"]["max"] == 360.0
