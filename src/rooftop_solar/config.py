"""Central config: paths, secrets, and study-area constants.

Kept deliberately small for Phase 0/1. Study-area values default to the Washington, DC
Phase 1 target (Glover Park neighbourhood) — see docs/roadmap.md.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv absent under bare system Python
    # Keep this module importable with zero third-party deps (e.g. the Phase 0
    # data-access smoke test runs under /usr/bin/python3, no conda env yet).
    def load_dotenv() -> None:
        return None


load_dotenv()

# --- paths (data/ and outputs/ are gitignored) ---
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
OUTPUTS_DIR = REPO_ROOT / "outputs"

# --- secrets (see .env.example) ---
NLR_API_KEY = os.getenv("NLR_API_KEY")
NLR_API_EMAIL = os.getenv("NLR_API_EMAIL")
NLR_API_BASE = "https://developer.nlr.gov"  # renamed from developer.nrel.gov (2025-12-01)
CENSUS_API_KEY = os.getenv("CENSUS_API_KEY")  # free key: https://api.census.gov/data/key_signup.html

# --- study area (Phase 1) ---
STUDY_AREA = "Washington, DC"
PHASE1_NEIGHBOURHOOD = "Glover Park"
# Working CRS: a metric projected CRS is required for area/plane fitting.
# NAD83(2011) / UTM 18N covers DC. Revisit per study area.
WORKING_CRS = "EPSG:6347"

# Phase 1 AOI — a hand-picked bounding box around the Glover Park core, in WGS84
# lon/lat (EPSG:4326) as (west, south, east, north). Chosen decision, not an official
# boundary: reproducible, easy to buffer, and easy to line up against the Esri
# "Estimate solar power potential" Glover Park tutorial extent (docs/roadmap.md §12).
# ~1.3 km (E-W) x ~1.7 km (N-S). Refine here if the benchmark needs a different frame.
AOI_BBOX_WGS84 = (-77.0825, 38.9130, -77.0680, 38.9280)

# Buffer (metres, applied in WORKING_CRS) grown around the core AOI before fetching
# footprints/DSM, so taller buildings just OUTSIDE the frame still cast shadows into it
# (the inter-building-shading trap — risks §8, trap 2). Keep >= the r.sun shadow search
# distance used in radiation.py so no relevant caster is clipped away.
AOI_BUFFER_M = 300.0

# --- census / equity aggregation (Phase 2, Part 2-1 — ADR-0006/0007) ---
# GEOID is a ZERO-PADDED STRING everywhere (DC tracts start "11001…"); integer coercion
# silently drops the leading "11" (§6, §11). FIPS "11" = District of Columbia.
DC_STATE_FIPS = "11"
# All three equity sources are aligned to 2020 tracts (ADR-0006) — no 2010/2020 vintage mix.
TIGER_TRACT_YEAR = 2020  # TIGER/Line tract geometry vintage
ACS_YEAR = 2023  # ACS 5-year demographics vintage
LEAD_YEAR = 2022  # DOE LEAD energy-burden vintage
# Data endpoints (fetched once, then cached under data/ — gitignored).
CENSUS_API_BASE = "https://api.census.gov/data"  # ACS 5-year Data API
TIGER_TRACT_BASE = "https://www2.census.gov/geo/tiger/TIGER2020/TRACT"  # tl_2020_<ff>_tract.zip
# DOE LEAD is the least Census-native source; the exact DC download is confirmed at fetch
# time (open risk §11) — the loader owns the URL, this only pins the vintage above.
# Per-source caches under data/ (created on first fetch).
TRACTS_CACHE_DIR = DATA_DIR / "tracts"
