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

# --- study area (Phase 1) ---
STUDY_AREA = "Washington, DC"
PHASE1_NEIGHBOURHOOD = "Glover Park"
# Working CRS: a metric projected CRS is required for area/plane fitting.
# NAD83(2011) / UTM 18N covers DC. Revisit per study area.
WORKING_CRS = "EPSG:6347"
