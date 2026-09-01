"""Phase 0 data-access smoke test (roadmap.md exit criteria).

Confirms each source is reachable before any pipeline code is written:
footprints, DSM, imagery, irradiance (NLR), admin boundaries. Fill in per source as
Phase 0 progresses; each check should be cheap (a HEAD / small query), not a full pull.

Stdlib only (urllib/json/ssl) — must run under the bare system Python, before the
conda env exists.

Run:  PYTHONPATH=src python3 scripts/check_data_access.py
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from rooftop_solar import config

TIMEOUT = 15
USER_AGENT = "rooftop-solar-potential/phase0-data-access-check"

PC_STAC_BASE = "https://planetarycomputer.microsoft.com/api/stac/v1"
CENSUS_DC_TRACTS_URL = "https://www2.census.gov/geo/tiger/TIGER2023/TRACT/tl_2023_11_tract.zip"

# Glover Park, DC — small bbox used for STAC coverage searches (lon_min, lat_min, lon_max, lat_max).
GLOVER_PARK_BBOX = [-77.075, 38.915, -77.060, 38.930]


def _get(url: str) -> tuple[int, bytes]:
    """GET url, return (status, body). Raises on network/DNS failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.status, resp.read()


def _head(url: str) -> int:
    """HEAD url, return status. Raises on network/DNS failure."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.status


def _post_json(url: str, payload: dict) -> tuple[int, bytes]:
    """POST a small JSON body, return (status, body). Raises on network/DNS failure."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.status, resp.read()


def _stac_collection_ok(collection_id: str) -> bool:
    """Confirm a Planetary Computer STAC collection exists (GET .../collections/<id> == 200)."""
    url = f"{PC_STAC_BASE}/collections/{collection_id}"
    try:
        status, _ = _get(url)
    except urllib.error.HTTPError as e:
        print(f"    {url} -> HTTP {e.code}")
        return False
    except (urllib.error.URLError, OSError) as e:
        print(f"    {url} -> network error: {e}")
        return False
    print(f"    {url} -> HTTP {status}")
    return status == 200


def check_footprints() -> bool:
    """Microsoft ML Building Footprints (STAC ms-buildings) / OSM fallback."""
    return _stac_collection_ok("ms-buildings")


def check_dsm() -> bool:
    """USGS 3DEP — Planetary Computer 3dep-lidar-dsm.

    Confirms the collection exists, then runs a tiny STAC search over the Glover Park
    bbox to double as a 3DEP coverage/vintage check (roadmap Phase 0 exit criterion).
    """
    if not _stac_collection_ok("3dep-lidar-dsm"):
        return False

    search_url = f"{PC_STAC_BASE}/search"
    payload = {"collections": ["3dep-lidar-dsm"], "bbox": GLOVER_PARK_BBOX, "limit": 1}
    try:
        status, body = _post_json(search_url, payload)
    except urllib.error.HTTPError as e:
        print(f"    {search_url} -> HTTP {e.code}")
        return False
    except (urllib.error.URLError, OSError) as e:
        print(f"    {search_url} -> network error: {e}")
        return False

    if status != 200:
        print(f"    {search_url} -> HTTP {status}")
        return False

    doc = json.loads(body)
    features = doc.get("features", [])
    print(f"    Glover Park bbox search: {len(features)} item(s) matched")
    if features:
        props = features[0].get("properties", {})
        # 3dep-lidar-dsm items often have a null top-level `datetime`; fall back to the
        # start/end range, which carries the LiDAR project vintage.
        item_datetime = (
            props.get("datetime") or props.get("start_datetime") or props.get("end_datetime")
        )
        print(f"    example item: {features[0].get('id')} (datetime: {item_datetime})")
    return True


def check_imagery() -> bool:
    """NAIP on Planetary Computer."""
    return _stac_collection_ok("naip")


def check_irradiance() -> bool | None:
    """NLR NSRDB / PVWatts v8 — needs config.NLR_API_KEY.

    Returns None (skip) when no key is configured, rather than failing the whole
    smoke test — Phase 0 can proceed without a key already issued.
    """
    if not config.NLR_API_KEY:
        print("    [no key] NLR_API_KEY not set in .env — skipping PVWatts call")
        return None

    params = {
        "api_key": config.NLR_API_KEY,
        "system_capacity": 4,
        "module_type": 0,
        "losses": 14,
        "array_type": 1,
        "tilt": 20,
        "azimuth": 180,
        "lat": 38.92,
        "lon": -77.07,
    }
    query = urllib.parse.urlencode(params)
    url = f"{config.NLR_API_BASE}/api/pvwatts/v8.json?{query}"
    try:
        status, body = _get(url)
    except urllib.error.HTTPError as e:
        print(f"    {config.NLR_API_BASE} -> HTTP {e.code}")
        return False
    except (urllib.error.URLError, OSError) as e:
        # Covers DNS failure too — developer.nlr.gov is a 2025-12-01 rename of
        # developer.nrel.gov; if that hasn't propagated this is where it shows up.
        print(f"    {config.NLR_API_BASE} -> network/DNS error: {e}")
        return False

    if status != 200:
        print(f"    {config.NLR_API_BASE} -> HTTP {status}")
        return False

    doc = json.loads(body)
    has_outputs = "outputs" in doc
    print(f"    HTTP {status}, 'outputs' key present: {has_outputs}")
    return has_outputs


def check_boundaries() -> bool:
    """US Census TIGER/Line tracts (DC, state FIPS 11)."""
    try:
        status = _head(CENSUS_DC_TRACTS_URL)
    except urllib.error.HTTPError as e:
        status = e.code
    except (urllib.error.URLError, OSError) as e:
        print(f"    {CENSUS_DC_TRACTS_URL} -> network error: {e}")
        return False
    print(f"    {CENSUS_DC_TRACTS_URL} -> HTTP {status}")
    return status in (200, 206)


CHECKS = {
    "footprints": check_footprints,
    "dsm": check_dsm,
    "imagery": check_imagery,
    "irradiance": check_irradiance,
    "boundaries": check_boundaries,
}


def main() -> None:
    print(f"Study area: {config.STUDY_AREA} / {config.PHASE1_NEIGHBOURHOOD}\n")
    for name, check in CHECKS.items():
        try:
            ok = check()
            if ok is None:
                print(f"[skip] {name}")
            else:
                print(f"[{'ok' if ok else 'FAIL'}] {name}")
        except NotImplementedError:
            print(f"[todo] {name}")


if __name__ == "__main__":
    main()
