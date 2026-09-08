"""Unit tests for the usable-area pure core (ADR-0003).

Two distinct steps, tested separately:
  - `classify_roof` — the reporting label (flat / pitched sun-facing / pitched north-facing).
    This is a *label only*, never a drop threshold (ADR-0003).
  - `is_usable` — the plan's three hard drop cutoffs: tilt > 45, insolation < 800, north-facing.
  - `usable_area` — composes qualification + the 0.70 packing derate over a GeoDataFrame.

Expected values come from the ADR-0003 spec thresholds, not from the implementation.
Fast: pure pandas/geopandas, no DSM, no network, no GRASS.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
import rasterio
import rasterio.transform
from shapely.geometry import box

from rooftop_solar import config, usable_area


def _roofs_gdf(rows):
    """Build a roofs GeoDataFrame in the metric working CRS. Each row is
    (tilt_deg, aspect_deg, poa_clear_sky_kwh_m2); geometry is a distinct 10x10 m square
    (area = 100 m2) so the 0.70 derate lands on a round number.
    """
    geoms, tilts, aspects, poas = [], [], [], []
    for i, (tilt, aspect, poa) in enumerate(rows):
        x0 = i * 50.0  # keep squares disjoint
        geoms.append(box(x0, 0.0, x0 + 10.0, 10.0))
        tilts.append(tilt)
        aspects.append(aspect)
        poas.append(poa)
    return gpd.GeoDataFrame(
        {"tilt_deg": tilts, "aspect_deg": aspects, "poa_clear_sky_kwh_m2": poas},
        geometry=geoms,
        crs=config.WORKING_CRS,
    )


@pytest.mark.parametrize(
    "tilt_deg, aspect_deg, expected",
    [
        (5.0, 180.0, "flat"),  # tilt < 10 -> flat, regardless of aspect
        (9.99, 0.0, "flat"),  # just under the flat cutoff
        (30.0, 180.0, "pitched_sun_facing"),  # due south
        (30.0, 90.0, "pitched_sun_facing"),  # east boundary (inclusive)
        (30.0, 270.0, "pitched_sun_facing"),  # west boundary (inclusive)
        (30.0, 0.0, "pitched_north_facing"),  # due north
        (30.0, 45.0, "pitched_north_facing"),  # NE
        (30.0, 315.0, "pitched_north_facing"),  # NW
    ],
)
def test_classify_roof_label(tilt_deg, aspect_deg, expected):
    assert usable_area.classify_roof(tilt_deg, aspect_deg) == expected


@pytest.mark.parametrize(
    "tilt_deg, aspect_deg, insol, expected, reason",
    [
        (30.0, 180.0, 1400.0, True, "south, moderate tilt, ample sun -> qualifies"),
        (5.0, 0.0, 1400.0, True, "flat roof qualifies (flat is not north-facing)"),
        (45.0, 180.0, 1400.0, True, "45 deg is the boundary — not > 45, still usable"),
        (45.01, 180.0, 1400.0, False, "just over the 45 deg slope cutoff -> drop"),
        (30.0, 180.0, 800.0, True, "800 is the boundary — not < 800, still usable"),
        (30.0, 180.0, 799.0, False, "just under the 800 insolation cutoff -> drop"),
        (30.0, 0.0, 1400.0, False, "pitched north-facing -> drop"),
        (np.nan, np.nan, 1400.0, False, "unfittable (NaN) roof -> conservatively dropped"),
    ],
)
def test_is_usable_hard_cutoffs(tilt_deg, aspect_deg, insol, expected, reason):
    assert usable_area.is_usable(tilt_deg, aspect_deg, insol) is expected, reason


def test_obstruction_pixels_flags_bump_above_plane():
    # One plane z=10 (a=0,b=0,c=10) under 30 flat pixels, all at z=10 except 9 chosen
    # pixels raised to 12 (a +2 m bump, above the tau=0.75 m threshold).
    n = 30
    X_fp = np.column_stack([np.arange(n, dtype=float), np.zeros(n)])
    z_fp = np.full(n, 10.0)
    bump_idx = np.array([2, 5, 6, 7, 12, 13, 14, 20, 21])
    z_fp[bump_idx] = 12.0

    support_idx, obstruction_mask = usable_area.obstruction_pixels(
        X_fp, z_fp, [(0.0, 0.0, 10.0)], tau=0.75
    )

    assert obstruction_mask.sum() == 9
    assert set(np.where(obstruction_mask)[0]) == set(bump_idx)
    assert (support_idx == 0).all()  # only one plane to support against


def test_obstruction_pixels_clean_plane_none():
    n = 30
    X_fp = np.column_stack([np.arange(n, dtype=float), np.zeros(n)])
    z_fp = np.full(n, 10.0)  # no bump anywhere

    _support_idx, obstruction_mask = usable_area.obstruction_pixels(
        X_fp, z_fp, [(0.0, 0.0, 10.0)], tau=0.75
    )

    assert obstruction_mask.sum() == 0


def test_obstruction_pixels_attributes_bump_to_nearest_facet():
    # A gable: plane A rises to the east (a=0.5), plane B rises to the west (a=-0.5),
    # meeting at a ridge (x=0). One pixel per plane at its own perfect-fit elevation,
    # plus a bump on plane A's side that is far closer to A's surface than B's.
    plane_a = (0.5, 0.0, 10.0)  # z = 0.5*x + 10
    plane_b = (-0.5, 0.0, 10.0)  # z = -0.5*x + 10

    X_fp = np.array(
        [
            [2.0, 0.0],  # on plane A exactly: z = 0.5*2+10 = 11
            [4.0, 0.0],  # plane-A bump: 2 m above plane A's surface (13 -> 11+2)
            [-2.0, 0.0],  # on plane B exactly: z = -0.5*-2+10 = 11
        ]
    )
    z_fp = np.array([11.0, 13.0, 11.0])

    support_idx, obstruction_mask = usable_area.obstruction_pixels(
        X_fp, z_fp, [plane_a, plane_b], tau=0.75
    )

    assert support_idx[0] == 0  # plane A
    assert support_idx[1] == 0  # the bump is nearer plane A than plane B
    assert support_idx[2] == 1  # plane B
    assert obstruction_mask.tolist() == [False, True, False]


def _bump_dsm(tmp_path, base_elev, bump_elev, grid_px=20, block_px=6, pixel_m=1.0):
    """A flat single-plane DSM at `base_elev` with a `block_px` x `block_px` square raised
    to `bump_elev`, centred in the grid -- the obstruction fixture (ADR-0013 §"Tests").
    """
    band = np.full((grid_px, grid_px), base_elev, dtype=np.float32)
    lo = (grid_px - block_px) // 2
    hi = lo + block_px
    band[lo:hi, lo:hi] = bump_elev

    transform = rasterio.transform.from_origin(0.0, float(grid_px) * pixel_m, pixel_m, pixel_m)
    path = tmp_path / "bump_dsm.tif"
    profile = {
        "driver": "GTiff",
        "height": grid_px,
        "width": grid_px,
        "count": 1,
        "dtype": band.dtype,
        "crs": config.WORKING_CRS,
        "transform": transform,
        "nodata": -9999.0,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(band, 1)
    return path, transform


def _single_plane_gdf(footprint, base_elev):
    """A hand-built one-row, single-plane `planes_gdf` (no dependency on the RANSAC fit)."""
    return gpd.GeoDataFrame(
        {
            "building_id": [0],
            "plane_id": [0],
            "n_planes": [1],
            "tilt_deg": [0.0],
            "aspect_deg": [0.0],
            "coef": [(0.0, 0.0, base_elev)],
        },
        geometry=[footprint],
        crs=config.WORKING_CRS,
    )


def test_detect_obstructions_measures_bump_area(tmp_path):
    grid_px, block_px, pixel_m = 20, 6, 1.0
    base_elev, bump_elev = 10.0, 12.0  # +2 m bump, well above tau=0.75
    dsm_path, transform = _bump_dsm(
        tmp_path, base_elev, bump_elev, grid_px=grid_px, block_px=block_px, pixel_m=pixel_m
    )
    footprint = box(0.0, 0.0, grid_px * pixel_m, grid_px * pixel_m)  # covers the whole grid
    planes_gdf = _single_plane_gdf(footprint, base_elev)

    out = usable_area.detect_obstructions(planes_gdf, dsm_path, tau=usable_area.OBSTRUCTION_TAU_M)

    pixel_area = pixel_m * pixel_m
    assert len(out) == 1
    np.testing.assert_allclose(out.iloc[0]["plane_area_m2"], grid_px * grid_px * pixel_area)
    np.testing.assert_allclose(out.iloc[0]["obstruction_area_m2"], block_px * block_px * pixel_area)
    assert out.iloc[0]["geometry"] is not None
    assert out.crs == config.WORKING_CRS


def test_detect_obstructions_no_bump_is_zero(tmp_path):
    grid_px, pixel_m = 20, 1.0
    base_elev = 10.0
    dsm_path, _ = _bump_dsm(
        tmp_path, base_elev, base_elev, grid_px=grid_px, block_px=0, pixel_m=pixel_m
    )
    footprint = box(0.0, 0.0, grid_px * pixel_m, grid_px * pixel_m)
    planes_gdf = _single_plane_gdf(footprint, base_elev)

    out = usable_area.detect_obstructions(planes_gdf, dsm_path, tau=usable_area.OBSTRUCTION_TAU_M)

    np.testing.assert_allclose(out.iloc[0]["obstruction_area_m2"], 0.0)
    assert out.iloc[0]["geometry"] is None


def test_detect_obstructions_unfittable_building_uses_raw_pixel_count(tmp_path):
    grid_px, pixel_m = 20, 1.0
    base_elev = 10.0
    dsm_path, _ = _bump_dsm(
        tmp_path, base_elev, base_elev, grid_px=grid_px, block_px=0, pixel_m=pixel_m
    )
    footprint = box(0.0, 0.0, grid_px * pixel_m, grid_px * pixel_m)
    planes_gdf = gpd.GeoDataFrame(
        {
            "building_id": [0],
            "plane_id": [0],
            "n_planes": [1],
            "tilt_deg": [np.nan],
            "aspect_deg": [np.nan],
            "coef": [None],
        },
        geometry=[footprint],
        crs=config.WORKING_CRS,
    )

    out = usable_area.detect_obstructions(planes_gdf, dsm_path, tau=usable_area.OBSTRUCTION_TAU_M)

    np.testing.assert_allclose(out.iloc[0]["plane_area_m2"], grid_px * grid_px * pixel_m * pixel_m)
    assert out.iloc[0]["obstruction_area_m2"] == 0.0
    assert out.iloc[0]["geometry"] is None


def _planes_gdf_for_multiplane_usable_area():
    """Two-plane building: plane0 qualifies (moderate south tilt, ample sun), plane1 is too
    steep. Distinct 10x10 m squares so geometry.area doesn't interfere (unused by this path).
    """
    return gpd.GeoDataFrame(
        {
            "building_id": [0, 0],
            "plane_id": [0, 1],
            "tilt_deg": [20.0, 60.0],
            "aspect_deg": [180.0, 180.0],
            "poa_clear_sky_kwh_m2": [1400.0, 1400.0],
        },
        geometry=[box(0.0, 0.0, 10.0, 10.0), box(50.0, 0.0, 60.0, 10.0)],
        crs=config.WORKING_CRS,
    )


def _obstructions_gdf(plane_area, obstruction_area):
    return gpd.GeoDataFrame(
        {"plane_area_m2": plane_area, "obstruction_area_m2": obstruction_area},
        geometry=[None] * len(plane_area),
        crs=config.WORKING_CRS,
    )


def test_usable_area_multiplane_path_obstruction_aware_area():
    planes = _planes_gdf_for_multiplane_usable_area()
    obstructions = _obstructions_gdf([100.0, 50.0], [20.0, 0.0])

    out = usable_area.usable_area(planes, obstructions=obstructions)

    # plane0: usable (tilt 20 <= 45, POA 1400 >= 800, south-facing) -> (100-20)*0.85 = 68.0.
    # plane1: unusable (tilt 60 > 45) -> 0.0 regardless of its obstruction-free area.
    np.testing.assert_allclose(out["usable_area_m2"].to_numpy(), [68.0, 0.0])
    assert out["usable"].tolist() == [True, False]
    assert out["plane_area_m2"].tolist() == [100.0, 50.0]
    assert out["obstruction_area_m2"].tolist() == [20.0, 0.0]


def test_usable_area_multiplane_path_clamps_negative_net_area():
    planes = gpd.GeoDataFrame(
        {
            "building_id": [0],
            "plane_id": [0],
            "tilt_deg": [20.0],
            "aspect_deg": [180.0],
            "poa_clear_sky_kwh_m2": [1400.0],
        },
        geometry=[box(0.0, 0.0, 10.0, 10.0)],
        crs=config.WORKING_CRS,
    )
    # obstruction_area_m2 exceeds plane_area_m2 -- must clamp to 0, never go negative.
    obstructions = _obstructions_gdf([50.0], [80.0])

    out = usable_area.usable_area(planes, obstructions=obstructions)

    assert out["usable_area_m2"].tolist() == [0.0]


def test_usable_area_composer_qualifies_and_derates():
    # One roof per branch: qualifying south, steep, low-insolation, north-facing, flat.
    roofs = _roofs_gdf(
        [
            (30.0, 180.0, 1400.0),  # 0 qualifies (sun-facing)
            (60.0, 180.0, 1400.0),  # 1 too steep -> drop
            (30.0, 180.0, 500.0),  # 2 too shaded -> drop
            (30.0, 0.0, 1400.0),  # 3 north-facing -> drop
            (3.0, 0.0, 1400.0),  # 4 flat -> qualifies
        ]
    )

    out = usable_area.usable_area(roofs)

    # Footprint area is recorded, and qualifying roofs are derated by 0.70 (100 -> 70).
    np.testing.assert_allclose(out["footprint_area_m2"], 100.0)
    np.testing.assert_allclose(
        out["usable_area_m2"].to_numpy(), [70.0, 0.0, 0.0, 0.0, 70.0]
    )
    assert out["usable"].tolist() == [True, False, False, False, True]
    assert out["roof_class"].tolist()[0] == "pitched_sun_facing"
    assert out["roof_class"].tolist()[4] == "flat"
    # The composer must not drop rows — every input roof appears in the output.
    assert len(out) == len(roofs)
