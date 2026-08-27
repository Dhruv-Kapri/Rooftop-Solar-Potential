"""Stage 5 — filter usable roof area.

Remove area that can't host panels: too-steep or wrong-aspect planes, setbacks/edges, and
obstructions (chimneys, vents, HVAC — segmenting these is where the Phase 2 ML pays off,
architecture.md §9). Output is the panel-able area per roof plane, feeding yield_pv.
"""

from __future__ import annotations


def usable_area(roof_planes, obstructions=None):
    """Return usable (panel-able) area per roof plane after filtering."""
    raise NotImplementedError("Phase 1")
