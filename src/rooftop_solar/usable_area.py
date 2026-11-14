"""Stage 5 — filter usable roof area.

Remove area that can't host panels: too-steep or wrong-aspect planes, setbacks/edges, and
obstructions (chimneys, vents, HVAC — segmenting these is where the Phase 2 ML pays off,
architecture.md §9). Output is the panel-able area per roof plane, feeding yield_pv.

Four hard drop cutoffs in all: the plan's three (slope, insolation, north-facing; ADR-0003) per
plane/footprint, plus the 30 m2 minimum usable building area (ADR-0003 addendum, #40). Every
dropped roof/plane carries an ``unsuitable_reason`` saying which cutoff dropped it.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from shapely.geometry import box as _pixel_box
from shapely.ops import unary_union

from rooftop_solar.radiation import POA_COLUMN  # per-roof shaded insolation column contract
from rooftop_solar.roof_planes import roof_points  # re-clip the DSM per building

# --- classification labels (reporting only — NOT drop thresholds; ADR-0003) ---
FLAT_THRESHOLD_DEG = 10.0
SUN_FACING_MIN_DEG = 90.0
SUN_FACING_MAX_DEG = 270.0

# --- the plan's three hard drop cutoffs (§7 step 5; ADR-0003) + a 4th area gate (#40) ---
MAX_SLOPE_DEG = 45.0  # drop roofs steeper than this
MIN_INSOLATION_KWH_M2 = 800.0  # drop roofs below this annual shaded insolation
# (north-facing is the third cutoff — expressed via classify_roof)
# 4th cutoff (ADR-0003 addendum, #40), measured on a BUILDING's total usable area after
# obstruction/setback/GCR packing, not on a plane: matches the Esri tutorial's
# Suitable_Buildings >= 30 m2. Strictly below fails; exactly 30 passes.
MIN_USABLE_AREA_M2 = 30.0

# --- packing realism (ADR-0003): fraction of a qualifying roof that can hold panels ---
UTILIZATION_FRACTION = 0.70

# --- obstruction-aware usable area (Part 2-4, ADR-0013 — revises the flat UTILIZATION_FRACTION
# above for the multiplane path; the ransac path keeps UTILIZATION_FRACTION unchanged) ---
# A DSM pixel sitting more than this many metres above its supporting fitted plane is an
# obstruction (chimney, vent, HVAC, bulkhead) rather than roof surface. Tuned 0.75 -> 0.5 on the
# spot-check (#23, ADR-0013): it matches the RANSAC inlier band
# (roof_planes.RESIDUAL_THRESHOLD_M), so a pixel above every plane's band is an obstruction, not
# roof; it found 2 more labelled obstructions with no new false positives
# (docs/benchmarks/spot_check/tuning.md).
OBSTRUCTION_TAU_M = 0.5
# Replaces UTILIZATION_FRACTION's single flat number: once obstructions are measured and
# subtracted, only a smaller, principled setback factor (edge setback/panel access) remains.
SETBACK_FACTOR = 0.85

# Flat roofs are covered by tilted arrays with inter-row spacing, not flush-mount packing: the
# ground-coverage ratio (~0.4-0.55 at DC's latitude) replaces flush-mount packing. It stacks on
# top of the edge-setback (ADR-0013 spirit).
FLAT_ROOF_GCR = 0.50


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


def unsuitable_reason(tilt_deg: float, aspect_deg: float, insolation_kwh_m2: float) -> str | None:
    """Why a roof (or plane) fails the plan's three hard cutoffs, or None if it qualifies.

    The FIRST failing check wins, in this order (#40, ADR-0003 addendum): NaN tilt ->
    ``"no_plane_fit"`` (an unfittable roof, so no orientation to judge); tilt >
    ``MAX_SLOPE_DEG`` -> ``"too_steep"``; :func:`classify_roof` ``"pitched_north_facing"`` ->
    ``"north_facing"``; ``insolation_kwh_m2`` < ``MIN_INSOLATION_KWH_M2`` ->
    ``"low_insolation"``. So a steep north-facing plane reads ``too_steep``. The fourth,
    area cutoff (``"too_small"``, :data:`MIN_USABLE_AREA_M2`) is a per-building rule applied
    after packing, not here.

    ``insolation_kwh_m2`` is the per-roof mean *shaded, clear-sky* annual insolation from the
    radiation stage (the real-sky derate is applied later, in yield_pv). A NaN insolation
    compares False and so is not itself a failure.
    """
    if np.isnan(tilt_deg):
        return "no_plane_fit"
    if tilt_deg > MAX_SLOPE_DEG:
        return "too_steep"
    if classify_roof(tilt_deg, aspect_deg) == "pitched_north_facing":
        return "north_facing"
    if insolation_kwh_m2 < MIN_INSOLATION_KWH_M2:
        return "low_insolation"
    return None


def is_usable(tilt_deg: float, aspect_deg: float, insolation_kwh_m2: float) -> bool:
    """Apply the plan's three hard drop cutoffs (ADR-0003): a roof qualifies unless it is
    steeper than ``MAX_SLOPE_DEG``, below ``MIN_INSOLATION_KWH_M2`` annual shaded insolation,
    or pitched north-facing — i.e. :func:`unsuitable_reason` is None.

    NaN tilt/aspect (an unfittable roof) is dropped — the conservative choice. The 4th,
    building-area cutoff (:data:`MIN_USABLE_AREA_M2`, #40) is applied in :func:`usable_area`
    / ``yield_pv.collapse_to_buildings``, after packing, not here.
    """
    return unsuitable_reason(tilt_deg, aspect_deg, insolation_kwh_m2) is None


def obstruction_pixels(
    X_fp: np.ndarray, z_fp: np.ndarray, plane_coefs: list[tuple[float, float, float]], tau: float
) -> tuple[np.ndarray, np.ndarray]:
    """Attribute each footprint DSM pixel to its nearest fitted plane, then flag obstructions.

    Pure core of ADR-0013's obstruction detection. `X_fp` is (n, 2) easting/northing pixel
    centres, `z_fp` is the matching (n,) elevation, `plane_coefs` is the building's plane
    list (`(a, b, c)` of `z = a*x + b*y + c`, dominant facet first — order only matters in
    that the returned `support_idx` is a *position* into this list, not a semantic rank).

    For each pixel, computes its residual against every plane (`z_i - (a*x_i + b*y_i + c)`)
    and assigns it to whichever plane has the smallest |residual| (`support_idx`) — this is
    what lets a multi-facet roof's pixels partition across its facets, so a bump on one
    facet is attributed there rather than polluting a neighbouring facet's area. A pixel is
    an obstruction (`obstruction_mask`) when its residual against its *own* supporting plane
    is more than `tau` metres **above** that plane (a positive residual only — a pixel
    sitting below its plane is a gap/hole, not an obstruction).

    Returns `(support_idx, obstruction_mask)`, both length n: `support_idx` is `int`,
    `obstruction_mask` is `bool`.
    """
    n = len(z_fp)
    resid = np.empty((n, len(plane_coefs)), dtype=np.float64)
    for j, (a, b, c) in enumerate(plane_coefs):
        resid[:, j] = z_fp - (a * X_fp[:, 0] + b * X_fp[:, 1] + c)

    support_idx = np.argmin(np.abs(resid), axis=1)
    resid_to_support = resid[np.arange(n), support_idx]
    obstruction_mask = resid_to_support > tau
    return support_idx.astype(int), obstruction_mask


def detect_obstructions(
    planes_gdf: gpd.GeoDataFrame, dsm_path, tau: float = OBSTRUCTION_TAU_M
) -> gpd.GeoDataFrame:
    """Measure per-plane obstruction area from the DSM, row-aligned to `planes_gdf` (ADR-0013).

    For each building (grouped by `building_id`), re-clips the DSM to that building's
    footprint (:func:`roof_planes.roof_points`) and runs :func:`obstruction_pixels` against
    its plane coefficients (gathered in `plane_id` order, so `support_idx`'s position lines
    up with each plane row). Returns one row per input plane row — same index, same order as
    `planes_gdf` — with `plane_area_m2` (this plane's assigned-pixel footprint area),
    `obstruction_area_m2` (this plane's obstructed-pixel area), and `geometry` (the union of
    that plane's obstruction pixel squares, in `planes_gdf`'s CRS, or `None` if it has no
    obstruction — reusable if obstructions are ever drawn on the map, per the ADR).

    An **unfittable** building (a single row with `coef is None`) has no plane to test
    residuals against: `plane_area_m2` is simply that building's total DSM pixel count (all
    of it, since there's no facet to attribute a subset to), `obstruction_area_m2` is `0.0`,
    and `geometry` is `None` — it is dropped downstream anyway via its NaN tilt.
    """
    with rasterio.open(dsm_path) as src:
        transform = src.transform
    pixel_area = abs(transform.a * transform.e)
    half_w = abs(transform.a) / 2.0
    half_h = abs(transform.e) / 2.0

    results: dict[object, tuple[float, float, object]] = {}
    for _building_id, group in planes_gdf.groupby("building_id", sort=False):
        group = group.sort_values("plane_id")
        row_labels = list(group.index)
        footprint = group.iloc[0]["geometry"]
        X_fp, z_fp = roof_points(footprint, dsm_path)

        if len(group) == 1 and group.iloc[0]["coef"] is None:
            results[row_labels[0]] = (float(len(z_fp)) * pixel_area, 0.0, None)
            continue

        plane_coefs = list(group["coef"])
        support_idx, obstruction_mask = obstruction_pixels(X_fp, z_fp, plane_coefs, tau)
        for plane_pos, row_label in enumerate(row_labels):
            assigned = support_idx == plane_pos
            plane_area_m2 = float(assigned.sum()) * pixel_area
            obstructed = assigned & obstruction_mask
            obstruction_area_m2 = float(obstructed.sum()) * pixel_area
            geometry = None
            if obstructed.any():
                squares = [
                    _pixel_box(x - half_w, y - half_h, x + half_w, y + half_h)
                    for x, y in X_fp[obstructed]
                ]
                geometry = unary_union(squares)
            results[row_label] = (plane_area_m2, obstruction_area_m2, geometry)

    plane_area_m2 = [results[i][0] for i in planes_gdf.index]
    obstruction_area_m2 = [results[i][1] for i in planes_gdf.index]
    geometries = [results[i][2] for i in planes_gdf.index]

    return gpd.GeoDataFrame(
        {"plane_area_m2": plane_area_m2, "obstruction_area_m2": obstruction_area_m2},
        geometry=geometries,
        crs=planes_gdf.crs,
        index=planes_gdf.index,
    )


def usable_area(
    roof_planes: gpd.GeoDataFrame, obstructions: gpd.GeoDataFrame | None = None
) -> gpd.GeoDataFrame:
    """Add per-roof (or, given `obstructions`, per-plane) usable panel area.

    Two paths, selected by whether `obstructions` is supplied:

    **Legacy path** (`obstructions is None`, ADR-0003, unchanged): the Phase-1
    one-row-per-footprint contract. Qualify each roof by the three hard cutoffs
    (:func:`unsuitable_reason`), derate qualifying roofs by the flat
    :data:`UTILIZATION_FRACTION`, then drop any whose packed area is below
    :data:`MIN_USABLE_AREA_M2` as ``"too_small"`` (ADR-0003 addendum, #40; a footprint is a
    building here, so the gate lives on this path). Reads ``tilt_deg``, ``aspect_deg`` and the
    shaded-insolation column (:data:`POA_COLUMN`) plus geometry (in a metric CRS, so
    ``geometry.area`` is m²); adds ``footprint_area_m2``, ``roof_class``, ``usable``,
    ``unsuitable_reason`` (None when usable) and ``usable_area_m2``. No rows are dropped — unusable
    roofs are kept with ``usable_area_m2 = 0`` so they still appear on the map.

    **Multiplane path** (`obstructions` given — :func:`detect_obstructions`'s output,
    row-aligned to `roof_planes`; ADR-0013 revises ADR-0003 here): same three hard cutoffs,
    reused as-is, applied per PLANE rather than per roof — `roof_planes` is one row per
    (building, plane), and each plane's own tilt/aspect/POA decides its own qualification.
    Instead of the flat 0.70, a qualifying plane's usable area is its **measured**
    obstruction-free area (``plane_area_m2 - obstruction_area_m2``, clamped at 0 so a plane
    whose detected obstruction exceeds its own assigned area never goes negative) times the
    smaller, principled :data:`SETBACK_FACTOR`. A plane classified ``roof_class == "flat"``
    additionally gets :data:`FLAT_ROOF_GCR` applied on top — flat roofs use tilted racks with
    inter-row spacing, so their real panel-coverage is well below the edge-setback alone.
    ``plane_area_m2`` and ``obstruction_area_m2`` are carried onto the output alongside
    ``roof_class``, ``usable``, ``unsuitable_reason`` (the plane's own geometric reason, None
    when usable) and ``usable_area_m2``, still one row per plane. The 30 m2 area gate is NOT
    applied here: it is per building, so ``yield_pv.collapse_to_buildings`` applies it on the
    summed plane areas (#40).
    """
    if obstructions is None:
        roofs = roof_planes.copy()
        roofs["footprint_area_m2"] = roofs.geometry.area
        roofs["roof_class"] = [
            classify_roof(t, a) for t, a in zip(roofs["tilt_deg"], roofs["aspect_deg"], strict=True)
        ]
        reasons = [
            unsuitable_reason(t, a, poa)
            for t, a, poa in zip(
                roofs["tilt_deg"], roofs["aspect_deg"], roofs[POA_COLUMN], strict=True
            )
        ]
        packed_area = np.where(
            [r is None for r in reasons], roofs["footprint_area_m2"] * UTILIZATION_FRACTION, 0.0
        )
        # 4th cutoff (#40): per footprint == per building on this path; gate before yield.
        reasons = [
            "too_small" if r is None and area < MIN_USABLE_AREA_M2 else r
            for r, area in zip(reasons, packed_area, strict=True)
        ]
        roofs["unsuitable_reason"] = pd.Series(reasons, index=roofs.index, dtype=object)
        roofs["usable"] = [r is None for r in reasons]
        roofs["usable_area_m2"] = np.where(roofs["usable"], packed_area, 0.0)
        return roofs

    planes = roof_planes.copy()
    planes["plane_area_m2"] = obstructions["plane_area_m2"].to_numpy()
    planes["obstruction_area_m2"] = obstructions["obstruction_area_m2"].to_numpy()
    planes["roof_class"] = [
        classify_roof(t, a) for t, a in zip(planes["tilt_deg"], planes["aspect_deg"], strict=True)
    ]
    reasons = [
        unsuitable_reason(t, a, poa)
        for t, a, poa in zip(
            planes["tilt_deg"], planes["aspect_deg"], planes[POA_COLUMN], strict=True
        )
    ]
    planes["unsuitable_reason"] = pd.Series(reasons, index=planes.index, dtype=object)
    planes["usable"] = [r is None for r in reasons]
    net_area = np.maximum(0.0, planes["plane_area_m2"] - planes["obstruction_area_m2"])
    packing_factor = np.where(
        planes["roof_class"] == "flat", SETBACK_FACTOR * FLAT_ROOF_GCR, SETBACK_FACTOR
    )
    planes["usable_area_m2"] = np.where(planes["usable"], net_area * packing_factor, 0.0)
    return planes
