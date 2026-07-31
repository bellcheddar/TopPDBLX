"""Stage `ingest.fetch_entries`: batched GraphQL harvest of the entry payloads.

One pass over the **experimental** id set (a superset of X-ray), so crystallisation fields
and polymer entity sequences arrive together and cryo-EM/NMR entries contribute their
sequences without a second sweep. `exptl_crystal_grow` is simply null for the non-X-ray
entries, which the flattener handles.

Raw responses are written gzipped, one file per batch, and never modified again: they are
the provenance artefact standing in for the mmCIF archive until the WP9 snapshot. The stage
is resumable, so an interrupted run costs only the batch that was in flight.

Measured 2026-07-28: 300 ids per request, 1.3 s and 0.58 MB per batch, so roughly 19 minutes
and 0.5 GB uncompressed for the full archive. Requests are issued serially on purpose. The
19 minutes is a one-off, and hammering a free public API in parallel to save a quarter of an
hour is not a trade worth making.

    ./run.sh ingest.fetch_entries
    ./run.sh ingest.fetch_entries --limit 3      # first 3 batches, for a smoke test
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator, Optional

from tqdm import tqdm

from .. import config, http
from ..manifest import Manifest
from .entry_ids import latest_snapshot, load_snapshot

STAGE = "ingest.fetch_entries"

# Every field the Phase 0 database needs, verified against data.rcsb.org on 2026-07-28.
# exptl_crystal_grow is a LOOP: entries with several crystal forms return several rows, and
# the flattener keys records on (pdb_id, crystal_id) because of it. Requesting crystal_id
# explicitly is what makes that possible.
QUERY = """
query($ids:[String!]!){
  entries(entry_ids:$ids){
    rcsb_id
    exptl { method }
    exptl_crystal_grow { crystal_id method temp temp_details pH pdbx_pH_range pdbx_details details }
    rcsb_accession_info { initial_release_date revision_date deposit_date major_revision minor_revision }
    rcsb_entry_info { identification_combined polymer_entity_count experimental_method }
    polymer_entities {
      rcsb_id
      entity_poly { type pdbx_seq_one_letter_code pdbx_seq_one_letter_code_can rcsb_sample_sequence_length pdbx_strand_id }
      rcsb_polymer_entity { pdbx_description formula_weight }
      rcsb_polymer_entity_container_identifiers { uniprot_ids asym_ids auth_asym_ids }
      rcsb_entity_source_organism { ncbi_scientific_name ncbi_taxonomy_id }
    }
  }
}"""


def batches(ids: list[str], size: int) -> Iterator[tuple[int, list[str]]]:
    for i in range(0, len(ids), size):
        yield i // size, ids[i:i + size]


def batch_path(out_dir: Path, index: int, ids: list[str]) -> Path:
    """Index orders the batches; the hash identifies their content.

    If the id snapshot changes, the hash changes, so a stale batch can never be mistaken
    for a current one and silently reused.
    """
    digest = hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]
    return out_dir / f"{index:05d}_{digest}.json.gz"


def read_batch(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def write_batch(path: Path, entries: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(".part")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(entries, fh)
    tmp.replace(path)


def fetch_batch(ids: list[str]) -> list[dict[str, Any]]:
    data = http.graphql(config.RCSB_GRAPHQL_URL, QUERY, {"ids": ids})
    return data.get("entries") or []


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, default=None,
                        help="entry id snapshot to harvest (default: most recent)")
    parser.add_argument("--id-set", default="experimental", choices=("experimental", "xray"),
                        help="which id list to harvest (default: experimental, the superset)")
    parser.add_argument("--out-dir", type=Path, default=config.RAW_GRAPHQL_DIR)
    parser.add_argument("--batch-size", type=int, default=config.GRAPHQL_BATCH_SIZE)
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after N batches (smoke testing only)")
    parser.add_argument("--force", action="store_true",
                        help="refetch batches that are already on disk")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = args.snapshot or latest_snapshot()
    snapshot = load_snapshot(snapshot_path)
    ids = snapshot["ids"][args.id_set]

    params = {
        "snapshot": str(snapshot_path),
        "id_set": args.id_set,
        "batch_size": args.batch_size,
        "n_ids": len(ids),
        "limit": args.limit,
    }

    with Manifest(STAGE, params=params) as m:
        m.add_input(snapshot_path)
        (args.out_dir / "_query.graphql").write_text(QUERY.strip() + "\n")

        planned = list(batches(ids, args.batch_size))
        if args.limit is not None:
            planned = planned[:args.limit]

        requested = 0
        returned = 0
        fetched_batches = 0
        skipped_batches = 0
        missing: list[str] = []

        for index, chunk in tqdm(planned, desc="batches", unit="batch"):
            path = batch_path(args.out_dir, index, chunk)
            if path.exists() and not args.force:
                entries = read_batch(path)
                skipped_batches += 1
            else:
                entries = fetch_batch(chunk)
                write_batch(path, entries)
                fetched_batches += 1

            requested += len(chunk)
            returned += len(entries)
            # Ids the API declined to return: obsolete or withdrawn between the snapshot
            # and the harvest. Recorded rather than ignored, because an unexplained gap
            # between requested and returned would otherwise surface as a mystery later.
            got = {e["rcsb_id"] for e in entries}
            missing.extend(sorted(set(chunk) - got))

        if missing:
            missing_path = args.out_dir / "_missing_ids.json"
            missing_path.write_text(json.dumps(sorted(missing), indent=1) + "\n")
            m.add_output(missing_path)

        m.add_output(args.out_dir).note(
            n_batches_planned=len(planned),
            n_batches_fetched=fetched_batches,
            n_batches_reused=skipped_batches,
            n_ids_requested=requested,
            n_entries_returned=returned,
            n_missing=len(missing),
        )
        print(f"requested {requested:,} ids, received {returned:,} entries, "
              f"{len(missing):,} missing ({fetched_batches} fetched, {skipped_batches} reused)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
