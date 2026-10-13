"""Cache-validity sidecars for the tiered per-tile cache (ADR-0014).

A cached artifact (a per-tile DSM raster, an r.sun insolation raster, a tile's roofs parquet)
carries a small `.key` sidecar next to it: a JSON object holding its validity key AND the
sha256 of the artifact's bytes. The producer writes the artifact, then the sidecar; a later run
reuses the artifact only when the sidecar's key matches the current key AND the artifact still
hashes to the recorded digest.

The digest exists for cross-machine handoff (issue #18): the cache is rsynced between the Mac
and the office VM, and rsync carries on past a per-file error. A truncated artifact with an
intact sidecar, or an OLD artifact left beside a NEW sidecar (same-tile rasters are usually the
same size, so a size check is not enough), must be recomputed, never reused. A sidecar that
does not parse in this format (e.g. a legacy plain-text key) is treated as absent.

Lives in its own low-level module with no imports from the `rooftop_solar` package (stdlib +
pathlib only), so `dsm.py`/`radiation.py` can consume it without creating a
dsm/radiation -> pipeline -> dsm/radiation import cycle (`pipeline.py` already imports
`dsm`/`radiation`). The key-derivation functions and component-version constants that
*produce* the key strings this module stores live in `pipeline.py`; this module only knows
how to read, write, and match them — it has no idea what a key means.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

KEY_SIDECAR_SUFFIX = ".key"
_HASH_CHUNK_BYTES = 1 << 20


def sidecar_path(artifact_path: str | Path) -> Path:
    """The sidecar path for an artifact: `<artifact_path>.key`."""
    return Path(f"{artifact_path}{KEY_SIDECAR_SUFFIX}")


def _sha256(path: Path) -> str:
    """Hex sha256 of the file's bytes, read in chunks (artifacts are ~5-10 MB)."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def write_key_sidecar(artifact_path: str | Path, key: str) -> Path:
    """Record `key` and the artifact's sha256 in its sidecar; return the sidecar path.

    The artifact must already exist (producer contract: write the artifact, then the sidecar).
    """
    artifact = Path(artifact_path)
    if not artifact.exists():
        raise FileNotFoundError(
            f"cannot write a cache sidecar for a missing artifact: {artifact} "
            "(write the artifact first, then its sidecar)"
        )
    p = sidecar_path(artifact)
    p.write_text(json.dumps({"key": key, "sha256": _sha256(artifact)}))
    return p


def _read_sidecar(artifact_path: str | Path) -> dict[str, str] | None:
    """The sidecar's parsed `{"key", "sha256"}`, or None if absent or not in that format."""
    p = sidecar_path(artifact_path)
    if not p.exists():
        return None
    try:
        record = json.loads(p.read_text())
    except ValueError:  # legacy plain-text sidecar, or a truncated one
        return None
    if (
        isinstance(record, dict)
        and isinstance(record.get("key"), str)
        and isinstance(record.get("sha256"), str)
    ):
        return record
    return None


def read_key_sidecar(artifact_path: str | Path) -> str | None:
    """Return the sidecar's key, or None if it is absent or not in the current format."""
    record = _read_sidecar(artifact_path)
    return record["key"] if record else None


def sidecar_matches(artifact_path: str | Path, key: str) -> bool:
    """True only if the artifact exists, its sidecar holds exactly `key`, AND the artifact's
    current sha256 equals the one recorded when the sidecar was written.

    Anything less is never a cache hit — a half-written, externally-deleted, truncated or
    swapped-for-an-older-copy artifact (issue #18, cross-machine handoff) forces a recompute.
    """
    artifact = Path(artifact_path)
    record = _read_sidecar(artifact)
    return (
        record is not None
        and artifact.exists()
        and record["key"] == key
        and record["sha256"] == _sha256(artifact)
    )
