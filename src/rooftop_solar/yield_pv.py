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
