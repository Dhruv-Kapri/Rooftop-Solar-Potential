"""Stage 6 — PV capacity, annual energy, CO2 offset (data-sources.md, risks §8 trap 4).

Stage 1 uses explicit, inspectable constants (ADR-0003) rather than an external PVWatts
call: usable area + shaded per-roof insolation -> real-sky POA -> capacity, annual energy,
CO2 offset, plus a within-AOI suitability score (ADR-0004). A direct PVWatts-v8 API call
(letting NLR own efficiency/PR) is a documented Phase-2 option.

CRITICAL (risks §8, trap 4): a PV model assumes an UNSHADED horizon. Feed it the
shading-adjusted per-roof insolation from radiation.py (the zonal mean of the shaded r.sun
raster) — never the raw NSRDB location value, or you silently discard inter-building shading.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import numpy.typing as npt
from scipy.stats import rankdata

from rooftop_solar.radiation import POA_COLUMN  # per-roof shaded insolation column contract

# --- Stage-1 PV constants (documented baselines; ADR-0003) ---
REAL_SKY_FACTOR = 0.75  # clear-sky -> real-sky derate (clouds/aerosols)
MODULE_EFF = 0.20  # module efficiency
PERFORMANCE_RATIO = 0.80  # balance-of-system performance ratio
POWER_DENSITY_KW_PER_M2 = 0.20  # installed capacity per usable m2
GRID_KG_CO2_PER_KWH = 0.35  # grid emissions factor for CO2 offset


def pv_yield(
    usable_area_m2: float, poa_clear_sky_kwh_m2: float
) -> tuple[float, float, float, float]:
    """Convert usable area + shaded clear-sky insolation to yield (ADR-0003).

    `poa_clear_sky_kwh_m2` is the per-roof zonal mean of the *shaded* r.sun raster (trap 4),
    still clear-sky; this derates it to real-sky before computing energy. Returns
    ``(poa_real_kwh_m2, capacity_kw, annual_energy_kwh, annual_co2_kg)``.
    """
    poa_real = poa_clear_sky_kwh_m2 * REAL_SKY_FACTOR
    capacity_kw = usable_area_m2 * POWER_DENSITY_KW_PER_M2
    annual_energy_kwh = usable_area_m2 * poa_real * MODULE_EFF * PERFORMANCE_RATIO
    annual_co2_kg = annual_energy_kwh * GRID_KG_CO2_PER_KWH
    return poa_real, capacity_kw, annual_energy_kwh, annual_co2_kg


def suitability_score(
    energy_density: npt.NDArray[np.float64],
    usable_area_m2: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """0–100 within-AOI percentile-rank score of annual energy density (ADR-0004).

    Each usable roof scores by where its energy density falls among all usable roofs, so the
    range is populated by construction and robust to outliers; the highest density scores 100.
    Ties share the averaged rank. Unusable roofs (``usable_area_m2 <= 0``) are pinned to
    exactly 0 — distinct from the worst-but-usable roof (just above 0). Relative *within this
    AOI only*: not comparable across runs or cities (that's Phase 2).
    """
    scores = np.zeros(len(energy_density), dtype=float)
    usable = usable_area_m2 > 0
    d = energy_density[usable]
    if d.size > 1:
        ranks = rankdata(d, method="average")  # 1..n_usable, ties averaged
        scores[usable] = 100.0 * ranks / d.size  # (0, 100]; highest density -> 100
    elif d.size == 1:
        scores[usable] = 100.0
    return scores


def plane_yields(planes_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add per-PLANE PV yield to the obstruction-aware usable-area output (Part 2-4).

    Same :func:`pv_yield` reused per row as :func:`estimate_yield` does for the ransac path,
    but the input here is one row per (building, plane) — `usable_area.usable_area`'s
    multiplane output — not one row per footprint. Adds ``poa_real_kwh_m2``, ``capacity_kw``,
    ``annual_energy_kwh``, ``annual_co2_kg``.

    **Guard:** an unusable plane (``usable_area_m2 == 0``) can still carry a NaN
    ``poa_clear_sky_kwh_m2`` (no valid raster pixel fell under it — e.g. the unfittable-
    footprint placeholder row). `pv_yield`'s energy/CO2 formulas multiply usable area by POA,
    so ``0 * NaN`` is itself NaN under IEEE-754, not the clean 0 the caller expects for a
    plane that contributes nothing. Wherever ``usable_area_m2 == 0``, this forces
    ``capacity_kw`` / ``annual_energy_kwh`` / ``annual_co2_kg`` to exactly ``0.0`` after the
    fact, overriding any NaN that formula would otherwise produce.
    """
    planes = planes_gdf.copy()
    yields = [
        pv_yield(ua, poa)
        for ua, poa in zip(planes["usable_area_m2"], planes[POA_COLUMN], strict=True)
    ]
    planes["poa_real_kwh_m2"] = [y[0] for y in yields]
    planes["capacity_kw"] = [y[1] for y in yields]
    planes["annual_energy_kwh"] = [y[2] for y in yields]
    planes["annual_co2_kg"] = [y[3] for y in yields]

    zero_area = planes["usable_area_m2"] == 0
    planes.loc[zero_area, ["capacity_kw", "annual_energy_kwh", "annual_co2_kg"]] = 0.0
    return planes


def collapse_to_buildings(plane_yields_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Collapse per-plane yield rows to one row per building (Part 2-4, ADR-0011).

    `plane_yields_gdf` — :func:`plane_yields`'s output, one row per (building, plane) — is
    grouped by `building_id` and reduced to exactly the downstream contract today's
    :func:`estimate_yield` emits, PLUS `n_planes`/`low_confidence` (the multiplane
    provenance):

      - ``geometry`` — the building's footprint (identical across its plane rows; take the
        first); ``footprint_area_m2 = geometry.area``.
      - ``usable`` — True if ANY of the building's planes is usable.
      - ``usable_area_m2``, ``capacity_kw``, ``annual_energy_kwh``, ``annual_co2_kg`` — summed
        over the building's planes (an unusable plane already contributes exactly 0 via
        :func:`plane_yields`'s guard, so the sum needs no extra masking).
      - ``tilt_deg``, ``aspect_deg``, ``roof_class``, ``poa_real_kwh_m2`` — taken from the
        building's DOMINANT usable plane (largest ``usable_area_m2``; a tie keeps the
        smallest ``plane_id``, i.e. the largest-inlier-count facet). A building with no
        usable plane at all reports its `plane_id == 0` row instead — there is no dominant
        usable facet to prefer, but the building still needs *some* orientation to display.
      - ``n_planes``, ``low_confidence`` — per-building values, repeated across a building's
        plane rows upstream; take the first.
      - ``energy_density_kwh_m2 = annual_energy_kwh / footprint_area_m2``.

    Then recomputes ``suitability`` (:func:`suitability_score`) over the resulting
    PER-BUILDING frame — the same score formula, but now the population it ranks against is
    buildings, not planes (an accidental within-a-single-building comparison would be
    meaningless).

    Per-plane-only columns (`plane_id`, `inlier_ratio`, `n_px`, `coef`, `inlier_xy`,
    `plane_area_m2`, `obstruction_area_m2`, `poa_clear_sky_kwh_m2`) are dropped — none of them
    are meaningful once several planes have been folded into one building row. CRS is
    preserved from the input.
    """
    rows = []
    for building_id, group in plane_yields_gdf.groupby("building_id", sort=False):
        geometry = group.iloc[0]["geometry"]
        footprint_area_m2 = geometry.area
        usable = bool(group["usable"].any())
        usable_area_m2 = float(group["usable_area_m2"].sum())
        capacity_kw = float(group["capacity_kw"].sum())
        annual_energy_kwh = float(group["annual_energy_kwh"].sum())
        annual_co2_kg = float(group["annual_co2_kg"].sum())

        usable_planes = group[group["usable"]]
        if len(usable_planes) > 0:
            dominant = usable_planes.sort_values(
                ["usable_area_m2", "plane_id"], ascending=[False, True]
            ).iloc[0]
        else:
            dominant = group[group["plane_id"] == 0].iloc[0]

        rows.append(
            {
                "geometry": geometry,
                "building_id": building_id,
                "n_planes": group.iloc[0]["n_planes"],
                "low_confidence": group.iloc[0]["low_confidence"],
                "tilt_deg": dominant["tilt_deg"],
                "aspect_deg": dominant["aspect_deg"],
                "roof_class": dominant["roof_class"],
                "footprint_area_m2": footprint_area_m2,
                "usable": usable,
                "usable_area_m2": usable_area_m2,
                "poa_real_kwh_m2": dominant["poa_real_kwh_m2"],
                "capacity_kw": capacity_kw,
                "annual_energy_kwh": annual_energy_kwh,
                "annual_co2_kg": annual_co2_kg,
                "energy_density_kwh_m2": annual_energy_kwh / footprint_area_m2,
            }
        )

    out = gpd.GeoDataFrame(rows, crs=plane_yields_gdf.crs)
    out["suitability"] = suitability_score(
        out["energy_density_kwh_m2"].to_numpy(), out["usable_area_m2"].to_numpy()
    )
    return out


def estimate_yield(usable: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add per-roof PV yield + suitability to the usable-area GeoDataFrame (plan §4).

    Consumes the columns the usable-area stage produced — ``usable_area_m2``,
    ``footprint_area_m2`` and the shaded-insolation column ``poa_clear_sky_kwh_m2`` (the
    shading-adjusted value, never a raw NSRDB location value; trap 4). Adds
    ``poa_real_kwh_m2``, ``capacity_kw``, ``annual_energy_kwh``, ``annual_co2_kg``,
    ``energy_density_kwh_m2`` and the within-AOI ``suitability`` score. No rows dropped.
    """
    roofs = usable.copy()
    yields = [
        pv_yield(ua, poa)
        for ua, poa in zip(
            roofs["usable_area_m2"], roofs[POA_COLUMN], strict=True
        )
    ]
    roofs["poa_real_kwh_m2"] = [y[0] for y in yields]
    roofs["capacity_kw"] = [y[1] for y in yields]
    roofs["annual_energy_kwh"] = [y[2] for y in yields]
    roofs["annual_co2_kg"] = [y[3] for y in yields]
    roofs["energy_density_kwh_m2"] = (
        roofs["annual_energy_kwh"] / roofs["footprint_area_m2"]
    )
    roofs["suitability"] = suitability_score(
        roofs["energy_density_kwh_m2"].to_numpy(), roofs["usable_area_m2"].to_numpy()
    )
    return roofs
