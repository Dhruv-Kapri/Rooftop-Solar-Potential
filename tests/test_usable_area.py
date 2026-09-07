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
