"""Stage 4 — roof-plane / tilt / aspect extraction (architecture.md §5, §9).

v1 default (LiDAR per-roof): clip the DSM/point cloud per footprint and fit individual roof
planes — RANSAC baseline in Phase 1; ML segmentation (RoofN3D-trained) in Phase 2.
Block-model/LOD-1 fallback: one height per building, flat-roof / fixed-tilt assumption.

WEAKEST LINK (risks §14.4): errors here propagate straight into capacity/kWh/CO₂. Report
uncertainty; don't over-claim precision.
"""

from __future__ import annotations


def fit_roof_planes(footprints, dsm_path, method: str = "ransac"):
    """Return per-roof planes with slope + aspect for each footprint.

    Args:
        method: "ransac" (Phase 1 baseline), "ml" (Phase 2), or "block" (LOD-1 fallback).
    """
    raise NotImplementedError("Phase 1")
