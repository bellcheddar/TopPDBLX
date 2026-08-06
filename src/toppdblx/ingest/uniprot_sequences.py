"""Stage `ingest.uniprot_sequences`: fetch full-length UniProt sequences.

R3's construct boundary model takes a **full-length** sequence in and emits a per-residue
probability of being inside a crystallised construct. The label comes from residue-level SIFTS
(which UniProt positions a PDB chain actually covers); the input has to come from UniProt itself,
because the PDB only ever holds the construct, never the protein it was cut from.

    ./run.sh ingest.uniprot_sequences              # every accession referenced by the corpus
    ./run.sh ingest.uniprot_sequences --limit 500  # a taste

**Resumable.** Sequences are appended to a JSONL as each batch lands and re-read on start, so an
interrupted run resumes from the last completed batch rather than refetching.

Accessions that return nothing are recorded as misses rather than retried forever: UniProt retires
accessions, and a demerged or deleted entry will never resolve.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterator, Optional

import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest

STAGE = "ingest.uniprot_sequences"
ENDPOINT = "https://rest.uniprot.org/uniprotkb/accessions"
# The endpoint takes a comma-separated list. 100 keeps each URL well inside any length limit and
# each retry cheap; the fetch is bounded by round trips, not by bytes.
BATCH = 100
RETRIES = 4


def parse_fasta(text: str) -> Iterator[tuple[str, str]]:
    """Yield (accession, sequence). UniProt headers are `db|ACC|NAME description`."""
    accession, chunks = None, []
    for line in text.splitlines():
        if line.startswith(">"):
            if accession:
                yield accession, "".join(chunks)
            parts = line[1:].split("|")
            accession = parts[1] if len(parts) > 2 else line[1:].split()[0]
            chunks = []
        elif line.strip():
            chunks.append(line.strip())
    if accession:
        yield accession, "".join(chunks)


def fetch_batch(accessions: list[str]) -> dict[str, str]:
    url = f"{ENDPOINT}?" + urllib.parse.urlencode(
        {"accessions": ",".join(accessions), "format": "fasta"})
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=180) as response:
                return dict(parse_fasta(response.read().decode()))
        except urllib.error.HTTPError as exc:
            # 400 means one accession in the batch is malformed or retired; splitting isolates it
            # rather than discarding the other 99.
            if exc.code == 400 and len(accessions) > 1:
                half = len(accessions) // 2
                return {**fetch_batch(accessions[:half]), **fetch_batch(accessions[half:])}
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(2 ** attempt)
    return {}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "uniprot_sequences.parquet")
    parser.add_argument("--progress", type=Path,
                        default=config.INTERIM_DIR / "uniprot_sequences.jsonl")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    config.ensure_dirs()

    sifts = pl.read_parquet(config.INTERIM_DIR / "sifts_uniprot.parquet")
    conditions = pl.read_parquet(config.INTERIM_DIR / "parsed_conditions.parquet")
    ours = set(conditions["pdb_id"].unique().to_list())
    wanted = sorted(set(sifts.filter(pl.col("pdb_id").is_in(ours))["uniprot_id"].to_list()))
    if args.limit:
        wanted = wanted[:args.limit]

    have: dict[str, str] = {}
    if args.progress.exists():
        for line in args.progress.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                have[row["accession"]] = row["sequence"]
    todo = [a for a in wanted if a not in have]
    print(f"  {len(wanted):,} accessions in scope, {len(have):,} already fetched, "
          f"{len(todo):,} to go")

    misses = 0
    with Manifest(STAGE, params={"n_wanted": len(wanted), "batch": BATCH}) as m, \
         args.progress.open("a") as sink:
        for start in tqdm(range(0, len(todo), BATCH), desc="uniprot", unit="batch"):
            batch = todo[start:start + BATCH]
            got = fetch_batch(batch)
            for accession in batch:
                sequence = got.get(accession)
                if not sequence:
                    misses += 1
                    continue
                have[accession] = sequence
                sink.write(json.dumps({"accession": accession, "sequence": sequence}) + "\n")
            sink.flush()

        frame = pl.DataFrame({
            "uniprot_id": list(have.keys()),
            "sequence": list(have.values()),
        }).with_columns(pl.col("sequence").str.len_chars().alias("length"))
        frame.write_parquet(args.out, compression="zstd")

        m.note(n_wanted=len(wanted), n_fetched=frame.height, n_missing=misses,
               median_length=int(frame["length"].median() or 0))
        m.add_output(args.out)
        print(f"\n  {frame.height:,} sequences, {misses:,} unresolved, "
              f"median length {int(frame['length'].median() or 0):,} residues")
        print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
