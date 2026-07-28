"""Per-stage run manifests.

Build constraint from the spec: "Every stage writes a manifest recording input hashes,
tool versions and ontology version." This module is the single implementation of that, so
no stage has to remember what a manifest contains.

Usage:

    from crystal_ball.manifest import Manifest

    with Manifest("ingest.fetch_entries", params={"batch_size": 300}) as m:
        m.add_input(id_snapshot_path)
        ...
        m.add_output(entries_parquet)
        m.note(n_entries=205949, n_multi_row=142)

The manifest is written on the way out even when the stage raises, with `status` set to
`failed` and the traceback recorded. A failed run that left no trace is the one thing
worse than a failed run.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Optional

from . import config

# Hashing a 90 GB archive snapshot byte by byte is pointless: size plus mtime identifies it
# well enough, and the agreement report is the real provenance artefact. Files above this
# threshold record `sha256: null` and a `hash_skipped_bytes` reason instead.
HASH_SIZE_LIMIT = 4 * 1024**3  # 4 GiB

# Recorded in every manifest so a run can be reproduced with the same wheels.
_TRACKED_PACKAGES = (
    "requests", "tenacity", "orjson", "pyarrow", "polars", "duckdb",
    "pydantic", "jsonschema", "pyyaml", "gemmi", "regex", "mlx", "mlx-lm",
)


def _sha256(path: Path) -> Optional[str]:
    if path.stat().st_size > HASH_SIZE_LIMIT:
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _describe(path: Path) -> dict[str, Any]:
    """Describe one file or one directory of files for the manifest."""
    path = Path(path)
    if not path.exists():
        return {"path": str(path), "exists": False}
    if path.is_dir():
        # Directories are summarised, not enumerated: raw/graphql holds ~690 files and
        # listing every one would bury the manifest.
        files = sorted(p for p in path.rglob("*") if p.is_file())
        total = sum(p.stat().st_size for p in files)
        digest = hashlib.sha256()
        for p in files:
            digest.update(p.relative_to(path).as_posix().encode())
            digest.update(str(p.stat().st_size).encode())
        return {
            "path": str(path),
            "exists": True,
            "kind": "directory",
            "n_files": len(files),
            "total_bytes": total,
            "listing_sha256": digest.hexdigest(),
        }
    stat = path.stat()
    sha = _sha256(path)
    entry: dict[str, Any] = {
        "path": str(path),
        "exists": True,
        "kind": "file",
        "bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "sha256": sha,
    }
    if sha is None:
        entry["hash_skipped_reason"] = f"larger than {HASH_SIZE_LIMIT} bytes"
    return entry


def _git_state() -> dict[str, Any]:
    def run(*args: str) -> Optional[str]:
        try:
            out = subprocess.run(
                args, cwd=config.REPO_ROOT, capture_output=True, text=True, timeout=10,
            )
            return out.stdout.strip() if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    commit = run("git", "rev-parse", "HEAD")
    status = run("git", "status", "--porcelain")
    return {
        "commit": commit,
        # A dirty tree means the manifest's commit does not fully describe the code that
        # ran. Recorded rather than blocked, but it should be false for a release run.
        "dirty": bool(status) if status is not None else None,
        "branch": run("git", "rev-parse", "--abbrev-ref", "HEAD"),
    }


def _package_versions() -> dict[str, Optional[str]]:
    versions: dict[str, Optional[str]] = {}
    for pkg in _TRACKED_PACKAGES:
        try:
            versions[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            versions[pkg] = None
    return versions


class Manifest:
    """Context manager that writes `manifests/<stage>_<utc timestamp>.json`."""

    def __init__(self, stage: str, params: Optional[dict[str, Any]] = None):
        self.stage = stage
        self.params = dict(params or {})
        self.inputs: list[dict[str, Any]] = []
        self.outputs: list[dict[str, Any]] = []
        self.notes: dict[str, Any] = {}
        self.started_at = datetime.now(timezone.utc)
        self._t0 = time.monotonic()
        self.status = "running"
        self.error: Optional[dict[str, str]] = None
        self.path: Optional[Path] = None

    # -- recording ---------------------------------------------------------
    def add_input(self, path) -> "Manifest":
        self.inputs.append(_describe(path))
        return self

    def add_output(self, path) -> "Manifest":
        self.outputs.append(_describe(path))
        return self

    def note(self, **kwargs: Any) -> "Manifest":
        """Attach stage-specific results: counts, rates, agreement numbers."""
        self.notes.update(kwargs)
        return self

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "wall_seconds": round(time.monotonic() - self._t0, 3),
            "versions": {
                "schema": config.SCHEMA_VERSION,
                "dataset": config.DATASET_VERSION,
                "ontology": config.ONTOLOGY_VERSION,
            },
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "packages": _package_versions(),
            },
            "git": _git_state(),
            "params": self.params,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "notes": self.notes,
        }

    def write(self) -> Path:
        config.MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
        stamp = self.started_at.strftime("%Y%m%dT%H%M%SZ")
        self.path = config.MANIFEST_DIR / f"{self.stage}_{stamp}.json"
        self.path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n")
        return self.path

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> "Manifest":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.status = "ok"
        else:
            self.status = "failed"
            self.error = {
                "type": exc_type.__name__,
                "message": str(exc),
                "traceback": "".join(traceback.format_exception(exc_type, exc, tb))[-4000:],
            }
        path = self.write()
        print(f"[manifest] {self.status}: {path}", file=sys.stderr)
        return False  # never swallow the exception
