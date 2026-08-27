"""Stage 7 — aggregate per-roof results to census tracts (architecture.md §3, Phase 2).

Roll per-building suitability/energy up to US Census TIGER/Line tracts (data-sources.md)
for neighbourhood choropleths + the equity overlay. Building-level resolution, city-wide
extent, then aggregate up — the Project Sunroof / NREL-NLR pattern.
"""

from __future__ import annotations


def aggregate_to_tracts(buildings, tracts):
    """Spatial-join per-building results to census tracts and summarise per tract."""
    raise NotImplementedError("Phase 2")
