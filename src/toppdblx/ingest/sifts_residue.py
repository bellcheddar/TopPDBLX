"""Stage `ingest.sifts_residue`: fetch residue-level SIFTS XML from EBI.

Segment-level SIFTS (`pdb_chain_uniprot.tsv.gz`) is already in hand and gives the UniProt range a
chain covers. **It does not give the per-residue correspondence**, which is what R3's construct
boundary model needs: where a crystallographer actually cut, residue by residue, against the
full-length UniProt sequence.

One gzipped XML per entry from EBI's `split_xml`, laid out by the middle two characters of the
PDB id exactly as the archive does, so a mirror can be rsynced over the top later.

    ./run.sh ingest.sifts_residue                 # every entry in the corpus with a UniProt map
    ./run.sh ingest.sifts_residue --limit 100     # a taste

**Resumable by design.** It skips entries already on disk, so an interrupted run costs nothing:
re-running is the recovery. At 185,839 entries this is a multi-hour fetch, and it will be
interrupted.
"""

from __future__ import annotations

import argparse
import queue
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest

STAGE = "ingest.sifts_residue"
BASE = "https://ftp.ebi.ac.uk/pub/databases/msd/sifts/split_xml"
# Polite rather than fast. EBI serves this to the whole field and the job is resumable, so there
# is nothing to gain by leaning on it.
WORKERS = 6
RETRIES = 3


def target_path(root: Path, pdb_id: str) -> Path:
    """`1abc` -> `<root>/ab/1abc.xml.gz`, matching EBI's own layout."""
    lower = pdb_id.lower()
    return root / lower[1:3] / f"{lower}.xml.gz"


def fetch_one(pdb_id: str, root: Path) -> tuple[str, str]:
    """Returns (pdb_id, outcome) where outcome is ok / skipped / missing / failed."""
    out = target_path(root, pdb_id)
    if out.exists() and out.stat().st_size > 0:
        return pdb_id, "skipped"
    url = f"{BASE}/{pdb_id.lower()[1:3]}/{pdb_id.lower()}.xml.gz"
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read()
            out.parent.mkdir(parents=True, exist_ok=True)
            # Write beside the target and rename, so an interrupted write cannot leave a
            # truncated file that the next run would treat as complete.
            tmp = out.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.rename(out)
            return pdb_id, "ok"
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return pdb_id, "missing"      # obsoleted or never mapped; not a failure
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(2 ** attempt)
    return pdb_id, "failed"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path,
                        default=config.RAW_DIR / "sifts" / "residue")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=WORKERS)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    # Only entries that are both in this corpus and carry a UniProt mapping: the boundary model
    # needs the full-length sequence to align against, so an entry without one is no use to it.
    conditions = pl.read_parquet(config.INTERIM_DIR / "parsed_conditions.parquet")
    sifts = pl.read_parquet(config.INTERIM_DIR / "sifts_uniprot.parquet")
    ours = set(conditions["pdb_id"].unique().to_list())
    mapped = {p.upper() for p in sifts["pdb_id"].unique().to_list()}
    targets = sorted(ours & mapped)
    if args.limit:
        targets = targets[:args.limit]

    work: queue.Queue = queue.Queue()
    for pdb_id in targets:
        work.put(pdb_id)
    counts = {"ok": 0, "skipped": 0, "missing": 0, "failed": 0}
    lock = threading.Lock()
    bar = tqdm(total=len(targets), desc="sifts", unit="entry")

    def worker() -> None:
        while True:
            try:
                pdb_id = work.get_nowait()
            except queue.Empty:
                return
            _, outcome = fetch_one(pdb_id, args.out_dir)
            with lock:
                counts[outcome] += 1
                bar.update(1)
                bar.set_postfix(ok=counts["ok"], skip=counts["skipped"],
                                miss=counts["missing"], fail=counts["failed"])
            work.task_done()

    with Manifest(STAGE, params={"n_targets": len(targets),
                                 "workers": args.workers}) as m:
        threads = [threading.Thread(target=worker, daemon=True)
                   for _ in range(args.workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        bar.close()

        m.note(**{f"n_{key}": value for key, value in counts.items()},
               n_on_disk=sum(1 for _ in args.out_dir.rglob("*.xml.gz")))
        print(f"\n  fetched {counts['ok']:,}, already had {counts['skipped']:,}, "
              f"{counts['missing']:,} absent from SIFTS, {counts['failed']:,} failed")
        if counts["failed"]:
            print("  re-run to retry the failures; the stage is resumable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
