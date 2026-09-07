"""Stage 2, Part 2-1 — census/equity data access (stage-2-part1-plan.md §4-5, ADR-0006/0007).

The equity spine is three sources, all keyed on **2020 tracts** (ADR-0006):

  - **TIGER/Line** tract geometry (`load_tracts` / `parse_tracts`)
  - **ACS 5-year** demographics — population, households, median income
    (`load_acs` / `parse_acs`)
  - **DOE LEAD** energy burden (`load_energy_burden` / `aggregate_lead_burden`)

Split, like `footprints.py`/`dsm.py` vs. the pure transforms, into an impure **fetch** half
(network, cached under `config.TRACTS_CACHE_DIR`, exercised only by the `integration`-marked
tests) and a pure **parse** half (no network/IO, unit-tested offline in `tests/test_tracts.py`
against hand-built fixtures). Network imports (`requests`) are kept lazy inside the fetch
functions so importing this module, or calling any `parse_*`, never touches the network.

The single most important invariant (§6, §11 of the plan): **GEOID is a zero-padded string
everywhere** — DC's tracts start with the state FIPS "11", and pandas/geopandas will happily
read that back as an int and silently drop the leading zero pattern the moment any component
FIPS code itself starts with "0" (e.g. Alabama "01…"). Every parser here restores/enforces an
11-char zero-padded string GEOID; never coerce it to int.
"""

from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import pandas as pd

from rooftop_solar import config

# ACS variable code -> friendly column name (ADR-0006 §5: income headline + pop/households for
# the per-household/per-capita normalization in aggregate.py). Reused by `load_acs` to build the
# `get=` query string, so the API request and the parser can never drift apart.
ACS_VARIABLE_MAP: dict[str, str] = {
    "B01003_001E": "population",
    "B25003_001E": "households",  # occupied housing units, per ADR-0007
    "B19013_001E": "median_income",
}

# Every GEOID in this module is zero-padded to this width (state[2] + county[3] + tract[6]).
_GEOID_WIDTH = 11


def parse_tracts(raw: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Normalize a raw TIGER/Line tract read to `GEOID(str) + geometry` in `config.WORKING_CRS`.

    Drops every other TIGER attribute column (STATEFP, COUNTYFP, TRACTCE, NAMELSAD, ALAND, ...)
    — this module only needs the join key and the polygon. `GEOID` is coerced through
    `.astype(str).str.zfill(11)` so it comes back a zero-padded 11-char string even if the
    upstream read handed it back as an int (the classic AL "01…" hazard, §6/§11).

    Args:
        raw: a TIGER/Line tract GeoDataFrame (has `GEOID` + geometry, typically EPSG:4269).

    Returns:
        GeoDataFrame with columns `GEOID`, `geometry`, reprojected to `config.WORKING_CRS`.
    """
    out = raw[["GEOID", "geometry"]].copy()
    out["GEOID"] = out["GEOID"].astype(str).str.zfill(_GEOID_WIDTH)
    return out.to_crs(config.WORKING_CRS)


def parse_acs(rows: list[list[str]]) -> pd.DataFrame:
    """Normalize the Census Data API's array-of-arrays response to tidy demographics.

    `rows` is the raw JSON body of an ACS 5-year Data API call: row 0 is the header, the rest
    are data rows. The trailing header columns are the geography components `state`, `county`,
    `tract` — `GEOID` is built by **string concatenation** of those (they arrive already
    zero-padded), never by casting to int first (§6/§11).

    ACS variable codes (see `ACS_VARIABLE_MAP`) are renamed to friendly columns and coerced to
    numeric. ACS uses large negative sentinels for a suppressed/inapplicable estimate (e.g.
    `-666666666`); since no population/household/income value is legitimately negative, any
    negative value in these columns is mapped to `NaN` rather than matching an exact sentinel
    list (ADR-0006).

    Args:
        rows: the Data API's `[header, *data_rows]` array of arrays (all strings).

    Returns:
        DataFrame with columns `GEOID` (str), `population`, `households`, `median_income`
        (float, NaN where suppressed/negative).
    """
    header, *data = rows
    df = pd.DataFrame(data, columns=header)

    out = pd.DataFrame({"GEOID": df["state"] + df["county"] + df["tract"]})

    value_cols = list(ACS_VARIABLE_MAP.values())
    for code, name in ACS_VARIABLE_MAP.items():
        out[name] = pd.to_numeric(df[code], errors="coerce")
    out[value_cols] = out[value_cols].where(out[value_cols] >= 0, np.nan)

    return out


# DOE LEAD tract-file columns, confirmed against the real DC AMI 2022 CSV (ADR-0008). The
# `<VAR>*UNITS` columns are already household-count-weighted totals, so the overall tract burden
# is a plain per-tract column sum — no re-weighting by the `UNITS` count.
_LEAD_GEOID_COL = "FIP"  # 11-digit tract FIPS (read back as int by default — coercion hazard)
_LEAD_INCOME_COL = "HINCP*UNITS"  # household income x households
_LEAD_ENERGY_COST_COLS = ("ELEP*UNITS", "GASP*UNITS", "FULP*UNITS")  # elec/gas/other-fuel x hh


def aggregate_lead_burden(
    raw: pd.DataFrame,
    *,
    geoid_col: str = _LEAD_GEOID_COL,
    income_col: str = _LEAD_INCOME_COL,
    cost_cols: tuple[str, ...] = _LEAD_ENERGY_COST_COLS,
) -> pd.DataFrame:
    """Aggregate LEAD stratum-level microdata to one overall energy burden per tract (ADR-0008).

    The LEAD tract file is row-per-stratum (income-band x tenure x vintage x heating-fuel). Since
    the `*UNITS` columns are already household-weighted totals, the **overall** tract energy
    burden — income-weighted across all income bands (the ADR-0008 decision) — is simply
    ``Σ(energy cost x units) / Σ(income x units)`` over a tract's strata, i.e. a groupby-sum per
    `geoid_col`. The result is a **fraction** of income; a tract whose total income is <= 0 gets
    **NaN** (never 0/inf), so it flags as missing downstream rather than poisoning the median
    (ADR-0007). Units follow the source; the median split is relative, so it is unit-invariant.

    Args:
        raw: the row-per-stratum LEAD frame (a `geoid_col` + the weighted `*UNITS` columns).
        geoid_col: the tract-key column (default `FIP`).
        income_col: the weighted-income-total column (default `HINCP*UNITS`).
        cost_cols: the weighted energy-cost-total columns summed into total energy cost.

    Returns:
        DataFrame with columns `GEOID` (zero-padded str) + `energy_burden` (float fraction).
    """
    grouped = raw.groupby(geoid_col, as_index=False)[[income_col, *cost_cols]].sum()
    total_income = grouped[income_col].where(grouped[income_col] > 0, np.nan)
    total_cost = grouped[list(cost_cols)].sum(axis=1)
    return pd.DataFrame(
        {
            "GEOID": grouped[geoid_col].astype(str).str.zfill(_GEOID_WIDTH),
            "energy_burden": total_cost / total_income,
        }
    )


# --------------------------------------------------------------------------- #
# Network fetch (integration-only — no unit test calls these; see plan §8)   #
# --------------------------------------------------------------------------- #


def load_tracts(
    state_fips: str = config.DC_STATE_FIPS, year: int = config.TIGER_TRACT_YEAR
) -> gpd.GeoDataFrame:
    """Download (or reuse the cached) TIGER/Line tract shapefile and return it parsed.

    Fetches `tl_{year}_{state_fips}_tract.zip` from `config.TIGER_TRACT_BASE`, caches it under
    `config.TRACTS_CACHE_DIR` (fetch once, reuse on subsequent calls), reads it with geopandas,
    and passes it through `parse_tracts`.

    Args:
        state_fips: two-digit state FIPS code (default DC, `config.DC_STATE_FIPS`).
        year: TIGER/Line vintage (default `config.TIGER_TRACT_YEAR` — 2020 tracts, ADR-0006).

    Returns:
        GeoDataFrame with columns `GEOID`, `geometry`, in `config.WORKING_CRS`.
    """
    import requests

    config.TRACTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"tl_{year}_{state_fips}_tract.zip"
    cache_path = config.TRACTS_CACHE_DIR / filename

    if not cache_path.exists():
        response = requests.get(f"{config.TIGER_TRACT_BASE}/{filename}", timeout=60)
        response.raise_for_status()
        cache_path.write_bytes(response.content)

    raw = gpd.read_file(f"zip://{cache_path}")
    return parse_tracts(raw)


def load_acs(
    state_fips: str = config.DC_STATE_FIPS, year: int = config.ACS_YEAR
) -> pd.DataFrame:
    """Download (or reuse the cached) ACS 5-year Data API response and return it parsed.

    GETs `{config.CENSUS_API_BASE}/{year}/acs/acs5` for the variables in `ACS_VARIABLE_MAP`
    (plus `NAME`) over every tract in `state_fips`, caches the raw JSON body under
    `config.TRACTS_CACHE_DIR`, and passes it through `parse_acs`.

    Args:
        state_fips: two-digit state FIPS code (default DC, `config.DC_STATE_FIPS`).
        year: ACS 5-year vintage (default `config.ACS_YEAR` — 2023, ADR-0006).

    Returns:
        DataFrame with columns `GEOID` (str), `population`, `households`, `median_income`.

    Raises:
        RuntimeError: if `config.CENSUS_API_KEY` is not set.
    """
    import requests

    if not config.CENSUS_API_KEY:
        raise RuntimeError(
            "CENSUS_API_KEY is not set. Get a free key at "
            "https://api.census.gov/data/key_signup.html and add it to .env as "
            "CENSUS_API_KEY (see .env.example)."
        )

    config.TRACTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = config.TRACTS_CACHE_DIR / f"acs_{year}_{state_fips}.json"

    if not cache_path.exists():
        variables = ",".join(ACS_VARIABLE_MAP.keys())
        url = (
            f"{config.CENSUS_API_BASE}/{year}/acs/acs5"
            f"?get=NAME,{variables}&for=tract:*&in=state:{state_fips}"
            f"&key={config.CENSUS_API_KEY}"
        )
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        cache_path.write_text(response.text)

    rows = json.loads(cache_path.read_text())
    return parse_acs(rows)


# DOE LEAD DC 2022 bundle (ADR-0008) — the "DOE LEAD Tool - 2022 Update" OpenEI submission
# (https://data.openei.org/submissions/6219; zip verified reachable 2026-09-07). The zip holds
# four income-basis tract CSVs (AMI/SMI/FPL/LLSI); ADR-0008 uses AMI (basis-invariant once summed
# over all income bands). Each is row-per-stratum microdata keyed on `FIP`.
_LEAD_DC_URL = "https://data.openei.org/files/6219/DC-2022-LEAD-data.zip"
_LEAD_DC_AMI_MEMBER = "DC AMI Census Tracts 2022.csv"  # the AMI-basis tract file inside the zip


def load_energy_burden(state_fips: str = config.DC_STATE_FIPS) -> pd.DataFrame:
    """Download (or reuse the cached) DOE LEAD DC bundle and return one burden per tract (ADR-0008).

    Fetches `_LEAD_DC_URL`, caches the zip under `config.TRACTS_CACHE_DIR`, reads the AMI
    tract CSV (`_LEAD_DC_AMI_MEMBER`) with `FIP` forced to string (avoiding the int-coercion
    hazard, §11), and aggregates its stratum microdata to an overall per-tract energy burden via
    `aggregate_lead_burden`.

    Args:
        state_fips: two-digit state FIPS code (default DC). Only DC is wired up — `_LEAD_DC_URL`
            is the DC-specific bundle; other states are out of scope for Part 2-1.

    Returns:
        DataFrame with columns `GEOID` (str), `energy_burden` (float fraction of income).

    Raises:
        NotImplementedError: for any `state_fips` other than DC (out of scope for Part 2-1).
    """
    if state_fips != config.DC_STATE_FIPS:
        raise NotImplementedError(
            f"load_energy_burden is wired for DC only (_LEAD_DC_URL is the DC bundle); "
            f"state_fips={state_fips!r} is out of scope for Part 2-1."
        )

    import zipfile

    import requests

    config.TRACTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = config.TRACTS_CACHE_DIR / f"lead_{config.LEAD_YEAR}_{state_fips}.zip"

    if not cache_path.exists():
        response = requests.get(_LEAD_DC_URL, timeout=180)
        response.raise_for_status()
        cache_path.write_bytes(response.content)

    with zipfile.ZipFile(cache_path) as zf, zf.open(_LEAD_DC_AMI_MEMBER) as member:
        raw = pd.read_csv(member, dtype={_LEAD_GEOID_COL: str})
    return aggregate_lead_burden(raw)
