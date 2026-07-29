"""Stage `ingest.entry_ids`: snapshot the PDB entry id lists.

This snapshot defines "the archive version" for the entire release. Everything downstream
is relative to it, and the WP9 fidelity check compares against the same list, so it is
written once per run to a dated file and never edited in place.

Two sets are captured:

  xray          entries with exptl.method == X-RAY DIFFRACTION. The condition corpus.
  experimental  every experimental entry, X-ray or not. Sequences only, harvested so the
                Phase 3 construct-boundary work (which wants cryo-EM and NMR constructs,
                spec decision 12.6) does not require a second pass over the archive.

The Search API accepts `return_all_hits`, which returns all 205,949 identifiers in a single
response. That is used deliberately in preference to pagination: a paginated sweep of a
live, changing index can silently duplicate or drop rows between pages, and a missing entry
would be invisible until much later.

    ./run.sh ingest.entry_ids
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .. import config, http
from ..manifest import Manifest

STAGE = "ingest.entry_ids"

QUERIES: dict[str, dict[str, Any]] = {
    "xray": {
        "type": "terminal",
        "service": "text",
        "parameters": {
            "attribute": "exptl.method",
            "operator": "exact_match",
            "value": "X-RAY DIFFRACTION",
        },
    },
    "experimental": {
        "type": "terminal",
        "service": "text",
        "parameters": {
            "attribute": "rcsb_entry_info.structure_determination_methodology",
            "operator": "exact_match",
            "value": "experimental",
        },
    },
}

SNAPSHOT_DIR = config.RAW_DIR / "entry_ids"


def fetch_ids(query: dict[str, Any]) -> list[str]:
    """Return every entry id matching `query`, sorted by rcsb_id for reproducibility."""
    payload = {
        "query": query,
        "return_type": "entry",
        "request_options": {
            "return_all_hits": True,
            "sort": [{"sort_by": "rcsb_id", "direction": "asc"}],
        },
    }
    resp = http.post_json(config.RCSB_SEARCH_URL, payload)
    resp.raise_for_status()
    body = resp.json()
    ids = [row["identifier"] for row in body["result_set"]]
    total = body["total_count"]
    if len(ids) != total:
        # return_all_hits is doing the work here; if it ever silently caps, fail rather
        # than quietly build the release on a truncated archive.
        raise RuntimeError(f"expected {total} identifiers, received {len(ids)}")
    return ids


def latest_snapshot(snapshot_dir: Path = SNAPSHOT_DIR) -> Path:
    """Most recent snapshot file. Raises if the stage has never been run."""
    files = sorted(snapshot_dir.glob("entry_ids_*.json"))
    if not files:
        raise FileNotFoundError(
            f"no entry id snapshot in {snapshot_dir}. Run: ./run.sh ingest.entry_ids"
        )
    return files[-1]


def load_snapshot(path: Optional[Path] = None) -> dict[str, Any]:
    return json.loads((path or latest_snapshot()).read_text())


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out-dir", type=Path, default=SNAPSHOT_DIR,
        help="where to write the snapshot (default: data/raw/entry_ids)",
    )
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    out = args.out_dir / f"entry_ids_{now:%Y%m%d}.json"

    with Manifest(STAGE, params={"out": str(out)}) as m:
        sets: dict[str, list[str]] = {}
        for label, query in QUERIES.items():
            ids = fetch_ids(query)
            sets[label] = ids
            print(f"{label:14s} {len(ids):>7,} entries")

        # Every X-ray entry must appear in the experimental set. If it does not, the two
        # attributes have drifted apart and the sequence sidecar would be incomplete.
        missing = set(sets["xray"]) - set(sets["experimental"])
        if missing:
            raise RuntimeError(
                f"{len(missing)} X-ray ids absent from the experimental set, "
                f"e.g. {sorted(missing)[:5]}"
            )

        snapshot = {
            "snapshot_utc": now.isoformat(),
            "search_url": config.RCSB_SEARCH_URL,
            "queries": QUERIES,
            "counts": {k: len(v) for k, v in sets.items()},
            "ids": sets,
        }
        out.write_text(json.dumps(snapshot, indent=1) + "\n")

        m.add_output(out).note(**{f"n_{k}": len(v) for k, v in sets.items()})
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
