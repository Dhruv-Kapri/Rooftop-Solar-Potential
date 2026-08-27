"""Stage 3 — solar radiation on the surface, WITH inter-building shading (risks §8).

Engine: GRASS GIS `r.sun` (decided — risks §14.2). Runs a full-year radiation model over
the DSM; `r.sun`/`r.horizon` do per-cell horizon/shadow-casting so neighbouring buildings
shade each other.

Traps to respect (risks §8):
  - input must be the DSM, not the DTM
  - AOI must be buffered
  - shadow search distance must be large enough for tall far towers
GRASS is a system dependency; call it via subprocess (grass session) or the pygrass API.
"""

from __future__ import annotations


def surface_irradiance(dsm_path, day_range=None, shadow_search_distance_m: float = 500.0):
    """Return per-cell annual (or period) insolation raster over the DSM, shading included.

    Args:
        dsm_path: path to the DSM raster (NOT a DTM).
        day_range: days to integrate over (full year by default).
        shadow_search_distance_m: horizon search distance for r.sun/r.horizon.
    """
    raise NotImplementedError("Phase 1")
