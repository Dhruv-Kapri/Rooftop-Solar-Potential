"""Rooftop Solar Potential — city-scale rooftop solar suitability pipeline.

Pipeline stages (see README + docs/architecture.md):
    footprints -> dsm -> radiation -> roof_planes -> usable_area -> yield_pv -> aggregate

The geometry front-end (LiDAR per-roof vs block-model/LOD-1) is swappable; everything
from radiation onward is identical downstream.
"""

__version__ = "0.0.0"
