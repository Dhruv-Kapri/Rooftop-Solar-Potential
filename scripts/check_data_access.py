"""Phase 0 data-access smoke test (roadmap.md exit criteria).

Confirms each source is reachable before any pipeline code is written:
footprints, DSM, imagery, irradiance (NLR), admin boundaries. Fill in per source as
Phase 0 progresses; each check should be cheap (a HEAD / small query), not a full pull.

Run:  python scripts/check_data_access.py
"""

from __future__ import annotations

from rooftop_solar import config


def check_footprints() -> bool:
    """Microsoft ML Building Footprints (STAC ms-buildings) / OSM fallback."""
    raise NotImplementedError("Phase 0")


def check_dsm() -> bool:
    """USGS 3DEP — Planetary Computer 3dep-lidar-dsm or usgs-lidar-public EPT."""
    raise NotImplementedError("Phase 0")


def check_imagery() -> bool:
    """NAIP on Planetary Computer."""
    raise NotImplementedError("Phase 0")


def check_irradiance() -> bool:
    """NLR NSRDB / PVWatts — needs config.NLR_API_KEY."""
    raise NotImplementedError("Phase 0")


def check_boundaries() -> bool:
    """US Census TIGER/Line tracts."""
    raise NotImplementedError("Phase 0")


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
            print(f"[{'ok' if ok else 'FAIL'}] {name}")
        except NotImplementedError:
            print(f"[todo] {name}")


if __name__ == "__main__":
    main()
