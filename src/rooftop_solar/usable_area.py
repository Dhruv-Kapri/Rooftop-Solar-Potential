"""Stage 5 — filter usable roof area.

Remove area that can't host panels: too-steep or wrong-aspect planes, setbacks/edges, and
obstructions (chimneys, vents, HVAC — segmenting these is where the Phase 2 ML pays off,
architecture.md §9). Output is the panel-able area per roof plane, feeding yield_pv.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np

from rooftop_solar.radiation import POA_COLUMN  # per-roof shaded insolation column contract

# --- classification labels (reporting only — NOT drop thresholds; ADR-0003) ---
FLAT_THRESHOLD_DEG = 10.0
SUN_FACING_MIN_DEG = 90.0
SUN_FACING_MAX_DEG = 270.0

# --- the plan's three hard drop cutoffs (§7 step 5; ADR-0003) ---
MAX_SLOPE_DEG = 45.0  # drop roofs steeper than this
MIN_INSOLATION_KWH_M2 = 800.0  # drop roofs below this annual shaded insolation
# (north-facing is the third cutoff — expressed via classify_roof)

# --- packing realism (ADR-0003): fraction of a qualifying roof that can hold panels ---
UTILIZATION_FRACTION = 0.70


def classify_roof(tilt_deg: float, aspect_deg: float) -> str:
    """Label one roof 'flat', 'pitched_sun_facing', or 'pitched_north_facing'.

    A *reporting label only* — it drives the map legend, not whether a roof is dropped
    (ADR-0003). Roofs too small to fit a plane arrive with NaN tilt/aspect; the NaN
    comparisons fall through to 'pitched_north_facing', which the usable-area filter then
    drops — the conservative choice for a roof we couldn't orient.
    """
    if tilt_deg < FLAT_THRESHOLD_DEG:
        return "flat"
    if SUN_FACING_MIN_DEG <= aspect_deg <= SUN_FACING_MAX_DEG:
        return "pitched_sun_facing"
    return "pitched_north_facing"


def is_usable(tilt_deg: float, aspect_deg: float, insolation_kwh_m2: float) -> bool:
    """Apply the plan's three hard drop cutoffs (ADR-0003): a roof qualifies unless it is
    steeper than ``MAX_SLOPE_DEG``, below ``MIN_INSOLATION_KWH_M2`` annual shaded insolation,
    or pitched north-facing.

    ``insolation_kwh_m2`` is the per-roof mean *shaded, clear-sky* annual insolation from the
    radiation stage (the real-sky derate is applied later, in yield_pv). NaN tilt/aspect (an
    unfittable roof) classifies as north-facing and is dropped — the conservative choice.
    """
    if tilt_deg > MAX_SLOPE_DEG:
        return False
    if insolation_kwh_m2 < MIN_INSOLATION_KWH_M2:
        return False
    if classify_roof(tilt_deg, aspect_deg) == "pitched_north_facing":
        return False
    return True


def usable_area(
    roof_planes: gpd.GeoDataFrame, obstructions=None
) -> gpd.GeoDataFrame:
    """Add per-roof usable panel area to `roof_planes` (ADR-0003).

    Two composed steps: qualify each roof by the three hard cutoffs (:func:`is_usable`),
    then derate qualifying roofs by :data:`UTILIZATION_FRACTION`. Reads ``tilt_deg``,
    ``aspect_deg`` and the shaded-insolation column (:data:`INSOLATION_COL`) plus geometry
    (in a metric CRS, so ``geometry.area`` is m²); adds ``footprint_area_m2``,
    ``roof_class``, ``usable`` and ``usable_area_m2``. No rows are dropped — unusable roofs
    are kept with ``usable_area_m2 = 0`` so they still appear on the map.

    `obstructions` is accepted for the Phase-1 stage contract but unused until ML
    obstruction segmentation lands (Phase 2).
    """
    roofs = roof_planes.copy()
    roofs["footprint_area_m2"] = roofs.geometry.area
    roofs["roof_class"] = [
        classify_roof(t, a)
        for t, a in zip(roofs["tilt_deg"], roofs["aspect_deg"], strict=True)
    ]
    roofs["usable"] = [
        is_usable(t, a, poa)
        for t, a, poa in zip(
            roofs["tilt_deg"], roofs["aspect_deg"], roofs[POA_COLUMN], strict=True
        )
    ]
    roofs["usable_area_m2"] = np.where(
        roofs["usable"], roofs["footprint_area_m2"] * UTILIZATION_FRACTION, 0.0
    )
    return roofs
