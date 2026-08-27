"""Stage 6 — PV capacity, annual energy, CO2 offset (data-sources.md, risks §8 trap 4).

PVWatts v8 (NLR API, via pvlib or the web API) converts tilt/azimuth/usable-area -> annual
kWh, then capacity and CO2 offset follow.

CRITICAL (risks §8, trap 4): PVWatts assumes an UNSHADED horizon. Feed it the
shading-adjusted per-roof insolation from radiation.py (or apply a shading-loss factor) —
never the raw NSRDB location value, or you silently discard all the inter-building shading.
"""

from __future__ import annotations


def estimate_yield(usable, roof_planes, insolation):
    """Return per-roof PV capacity (kW), annual energy (kWh), and CO2 offset.

    Args:
        insolation: shading-adjusted per-roof insolation from radiation.py — NOT raw NSRDB.
    """
    raise NotImplementedError("Phase 1")
