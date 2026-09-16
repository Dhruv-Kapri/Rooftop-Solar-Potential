"""Cache-validity sidecars for the tiered per-tile cache (ADR-0014).

A cached artifact (a per-tile DSM raster, an r.sun insolation raster) carries a small
plain-text `.key` sidecar next to it holding its validity key. The producer writes the
sidecar after writing the artifact; a later run reuses the artifact only when the sidecar
matches the current key AND the artifact still exists.

Lives in its own low-level module with no imports from the `rooftop_solar` package (stdlib +
pathlib only), so `dsm.py`/`radiation.py` can consume it without creating a
dsm/radiation -> pipeline -> dsm/radiation import cycle (`pipeline.py` already imports
`dsm`/`radiation`). The key-derivation functions and component-version constants that
*produce* the key strings this module stores live in `pipeline.py`; this module only knows
how to read, write, and match them — it has no idea what a key means.
"""

from __future__ import annotations

from pathlib import Path

KEY_SIDECAR_SUFFIX = ".key"


def sidecar_path(artifact_path: str | Path) -> Path:
    """The sidecar path for an artifact: `<artifact_path>.key`."""
    return Path(f"{artifact_path}{KEY_SIDECAR_SUFFIX}")


def write_key_sidecar(artifact_path: str | Path, key: str) -> Path:
    """Write `key` to the artifact's sidecar (mkdir-ing parents); return the sidecar path."""
    p = sidecar_path(artifact_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(key)
    return p


def read_key_sidecar(artifact_path: str | Path) -> str | None:
    """Return the sidecar's contents, or None if the sidecar does not exist."""
    p = sidecar_path(artifact_path)
    return p.read_text() if p.exists() else None


def sidecar_matches(artifact_path: str | Path, key: str) -> bool:
    """True only if the artifact exists AND its sidecar holds exactly `key`.

    A sidecar without its artifact (or vice versa) is never a cache hit — both must be present
    and agree, so a half-written or externally-deleted artifact forces a recompute.
    """
    return Path(artifact_path).exists() and read_key_sidecar(artifact_path) == key
