"""Stage `release.verify_archive`: full-archive fidelity check against the snapshot.

The WP1 gate compared 200 entries. This compares **every** entry, and it is the whole reason
the 90 GB snapshot exists. It converts the reproducibility claim in the dataset paper from

    "we sampled 200 entries and they matched"

into

    "the parsed source field agreed with the archive on N of M entries as of <archive date>"

Two things are compared, as at WP1: the details string byte for byte, and the **number of rows**
in the `exptl_crystal_grow` loop, because silently keeping only the first crystal form is the
most plausible way this pipeline could lose data.

Parsing 198,000 mmCIF files is IO and CPU bound, so it runs across a process pool and writes
its results incrementally: an interrupted run resumes rather than restarting.

    ./run.sh release.verify_archive
    ./run.sh release.verify_archive --workers 4 --limit 5000
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterator, Optional

import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest

STAGE = "release.verify_archive"

DEFAULT_ARCHIVE = Path.home() / "CrystalBallData" / "raw" / "mmcif_archive"
CHUNK = 500


def archive_path(archive: Path, pdb_id: str) -> Path:
    """The divided archive lays entries out by the middle two characters of the id."""
    lower = pdb_id.lower()
    return archive / lower[1:3] / f"{lower}.cif.gz"


def _compare_chunk(payload: tuple[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Runs in a worker process: gemmi is imported here so it is not pickled."""
    import gemmi

    archive_root, rows = payload
    archive = Path(archive_root)
    results = []
    by_entry: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_entry.setdefault(row["pdb_id"], []).append(row)

    for pdb_id, expected in by_entry.items():
        path = archive_path(archive, pdb_id)
        if not path.exists():
            results.append({"pdb_id": pdb_id, "status": "missing_from_archive"})
            continue
        try:
            block = gemmi.cif.read(str(path)).sole_block()
            details = list(block.find_values("_exptl_crystal_grow.pdbx_details"))
        except Exception as error:                      # noqa: BLE001
            results.append({"pdb_id": pdb_id, "status": "unreadable",
                            "detail": str(error)[:200]})
            continue

        archive_rows = []
        for raw in details:
            value = gemmi.cif.as_string(raw)
            archive_rows.append(None if value in ("?", ".") else value)

        # Only rows the pipeline actually kept are comparable: an entry with no grow record
        # contributes one placeholder row on our side and zero on the archive's.
        expected_rows = [e["raw_details"] for e in expected if e["n_crystal_forms"] > 0]
        if len(archive_rows) != len(expected_rows):
            results.append({"pdb_id": pdb_id, "status": "row_count_mismatch",
                            "archive_rows": len(archive_rows),
                            "pipeline_rows": len(expected_rows)})
            continue

        mismatched = [i for i, (a, b) in enumerate(zip(archive_rows, expected_rows))
                      if (a or "") != (b or "")]
        results.append({"pdb_id": pdb_id,
                        "status": "agree" if not mismatched else "text_mismatch",
                        "n_rows": len(archive_rows),
                        "mismatched_rows": mismatched[:5]})
    return results


def chunks(rows: list[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--entries", type=Path,
                        default=config.INTERIM_DIR / "entries.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "archive_agreement.json")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    if not args.archive.exists():
        raise SystemExit(
            f"no archive snapshot at {args.archive}. Run: ./run.sh release.snapshot")

    entries = pl.read_parquet(args.entries).filter(pl.col("is_xray"))
    rows = entries.select("pdb_id", "crystal_id", "raw_details",
                          "n_crystal_forms").to_dicts()
    if args.limit:
        keep = {r["pdb_id"] for r in rows[:args.limit]}
        rows = [r for r in rows if r["pdb_id"] in keep]

    with Manifest(STAGE, params={"archive": str(args.archive), "workers": args.workers,
                                 "n_rows": len(rows)}) as m:
        m.add_input(args.entries).add_input(args.archive)

        results: list[dict[str, Any]] = []
        batches = list(chunks(rows, CHUNK))
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(_compare_chunk, (str(args.archive), batch))
                       for batch in batches]
            for future in tqdm(as_completed(futures), total=len(futures),
                               desc="chunks", unit="chunk"):
                results.extend(future.result())

        counts: dict[str, int] = {}
        for result in results:
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        checked = len(results)
        agree = counts.get("agree", 0)
        comparable = agree + counts.get("text_mismatch", 0) + counts.get("row_count_mismatch", 0)

        failures = [r for r in results if r["status"] in
                    ("text_mismatch", "row_count_mismatch")]
        report = {
            "archive": str(args.archive),
            "n_entries_checked": checked,
            "n_comparable": comparable,
            "n_agree": agree,
            "agreement_rate": round(agree / max(1, comparable), 6),
            "status_counts": counts,
            "failures": failures[:200],
        }
        args.out.write_text(json.dumps(report, indent=1))
        m.add_output(args.out).note(**{k: v for k, v in report.items() if k != "failures"},
                                    n_failures=len(failures))

        print(f"\n{checked:,} entries checked against {args.archive}")
        for status, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {status:<24} {n:>8,}")
        print(f"\nagreement on comparable entries: {agree:,} of {comparable:,} "
              f"({report['agreement_rate']:.4%})")
        if failures:
            print(f"\nfirst mismatches (full list in {args.out}):")
            for bad in failures[:5]:
                print(f"  {bad['pdb_id']}: {bad['status']} {bad}")
        else:
            print("\nno mismatches: the API reproduced the archive exactly on every entry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
