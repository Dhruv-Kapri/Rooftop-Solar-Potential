"""Stage 7 — aggregate per-roof results to census tracts (architecture.md §3, Phase 2).

Roll per-building suitability/energy up to US Census TIGER/Line tracts (data-sources.md)
for neighbourhood choropleths + the equity overlay. Building-level resolution, city-wide
extent, then aggregate up — the Project Sunroof / NREL-NLR pattern.

Three decomposed pure transforms (stage-2-part1-plan.md §4), each GeoDataFrame/DataFrame in,
GeoDataFrame out, no hidden globals — same idiom as usable_area.py / yield_pv.py:

  - `aggregate_to_tracts` — centroid point-in-polygon roll-up + extensive/intensive summaries
    (roof→tract assignment is centroid-based, decided in the Part 2-1 plan session).
  - `attach_equity` — GEOID-string join of ACS + DOE LEAD, per-household/per-capita
    normalization (ADR-0006, ADR-0007).
  - `classify_equity` — the 2x2 potential x burden quadrant + priority flag (ADR-0007).
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

# --- extensive per-roof columns summed (plain sums) into each tract ---
_EXTENSIVE_COLUMNS = ("usable_area_m2", "capacity_kw", "annual_energy_kwh", "annual_co2_kg")


def aggregate_to_tracts(buildings: gpd.GeoDataFrame, tracts: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Spatial-join per-building results to census tracts and summarise per tract.

    Assigns each building to a tract by its **centroid** (point-in-polygon; both inputs
    already in `config.WORKING_CRS`) — the Part 2-1 plan's locked roof->tract rule, not a
    naive polygon-intersect join on the *footprint*, which would double-count a
    boundary-straddling building into every tract its footprint touches. The centroid join
    uses an ``intersects`` predicate (not ``within``) so a centroid sitting exactly on a
    shared tract edge is **kept** — ``within`` excludes the boundary and would silently drop
    it (breaking conservation). A boundary centroid then matches both adjacent tracts, so a
    dedup on the building index (`~index.duplicated`) keeps exactly one — preserving the
    "assigned once" invariant with no roof lost.

    Per tract, computes **extensive** sums (`usable_area_m2`, `capacity_kw`,
    `annual_energy_kwh`, `annual_co2_kg`, `n_buildings`, `n_usable`) and **intensive**
    summaries (`median_suitability` over all buildings, `pct_usable` as a 0-100 percentage).
    Unusable roofs arrive with `usable_area_m2 = 0` / `annual_energy_kwh = 0` (usable_area.py,
    yield_pv.py) and are kept, not dropped, so they still count toward `n_buildings` and the
    suitability median.

    Returns one row per tract that contains >=1 building (empty tracts are dropped), with the
    tract `GEOID` (kept as a string — never coerced to int) and tract polygon `geometry`, in
    `WORKING_CRS`.
    """
    centroids = gpd.GeoDataFrame(
        buildings.drop(columns="geometry"),
        geometry=buildings.geometry.centroid,
        crs=buildings.crs,
    )
    joined = gpd.sjoin(
        centroids, tracts[["GEOID", "geometry"]], how="inner", predicate="intersects"
    )
    joined = joined[~joined.index.duplicated(keep="first")]

    per_tract = joined.groupby("GEOID", as_index=False).agg(
        **{col: (col, "sum") for col in _EXTENSIVE_COLUMNS},
        n_buildings=("usable", "size"),
        n_usable=("usable", "sum"),
        median_suitability=("suitability", "median"),
    )
    per_tract["pct_usable"] = 100.0 * per_tract["n_usable"] / per_tract["n_buildings"]

    out = tracts.merge(per_tract, on="GEOID", how="inner")
    return gpd.GeoDataFrame(out, geometry="geometry", crs=tracts.crs)


def attach_equity(
    tracts_gdf: gpd.GeoDataFrame, acs: pd.DataFrame, energy_burden: pd.DataFrame
) -> gpd.GeoDataFrame:
    """Left-join ACS demographics + DOE LEAD energy burden onto tracts (ADR-0006).

    Joins both `acs` (`GEOID`, `population`, `households`, `median_income`) and
    `energy_burden` (`GEOID`, `energy_burden`) onto `tracts_gdf` on the **string** `GEOID` —
    never coerced to int, which would silently drop DC's leading "11" (§6, §11).

    Adds the per-household/per-capita normalization (ADR-0007): `potential_per_household` =
    `annual_energy_kwh / households`, `potential_per_capita` = `annual_energy_kwh /
    population`, each **NaN** (never inf/0) when its denominator is missing or zero.

    A tract with no ACS/LEAD match, or with zero households, is flagged via
    `equity_data_missing` (bool) rather than defaulted to 0 — a spurious 0 would corrupt the
    medians in `classify_equity` (ADR-0007).
    """
    out = tracts_gdf.merge(acs, on="GEOID", how="left").merge(
        energy_burden, on="GEOID", how="left"
    )

    households = out["households"].replace(0, np.nan)
    population = out["population"].replace(0, np.nan)
    out["potential_per_household"] = out["annual_energy_kwh"] / households
    out["potential_per_capita"] = out["annual_energy_kwh"] / population

    out["equity_data_missing"] = households.isna() | out["energy_burden"].isna()

    return gpd.GeoDataFrame(out, geometry="geometry", crs=tracts_gdf.crs)


def classify_equity(tracts_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """2x2 median-split quadrant classification + priority flag (ADR-0007).

    Splits the **classifiable** tracts (`equity_data_missing` False) at the medians of
    `potential_per_household` and `energy_burden` — the median is taken only over those rows,
    so a flagged-missing tract's NaN can never leak into (or, worse, be defaulted to 0 and
    corrupt) the split. Each classifiable tract gets `potential_level` / `burden_level` in
    {"high", "low"} (tie at the median -> "high", matching the Stage-1 boundary-inclusive
    style), a combined `equity_class` (e.g. "high_potential_high_burden"), and `is_priority`
    (high potential AND high burden).

    Flagged-missing tracts get `equity_class = "unknown"`, `potential_level` /
    `burden_level = "unknown"`, `is_priority = False`. If no tract is classifiable, every row
    is "unknown" and nothing is a priority — this must not raise.
    """
    out = tracts_gdf.copy()
    classifiable = ~out["equity_data_missing"]

    median_potential = out.loc[classifiable, "potential_per_household"].median()
    median_burden = out.loc[classifiable, "energy_burden"].median()

    high_potential = out["potential_per_household"] >= median_potential
    high_burden = out["energy_burden"] >= median_burden

    out["potential_level"] = np.where(high_potential, "high", "low")
    out["burden_level"] = np.where(high_burden, "high", "low")
    out["equity_class"] = (
        out["potential_level"] + "_potential_" + out["burden_level"] + "_burden"
    )
    out["is_priority"] = high_potential & high_burden

    out.loc[~classifiable, ["potential_level", "burden_level", "equity_class"]] = "unknown"
    out.loc[~classifiable, "is_priority"] = False

    return out
