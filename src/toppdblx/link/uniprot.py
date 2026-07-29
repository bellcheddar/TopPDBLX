"""Stage `link.uniprot`: fetch full-length reference sequences for the mapped accessions.

The construct sequence is what actually crystallised; the UniProt sequence is the full-length
reference it was cut from. Keeping both is what makes "where did successful crystallographers
cut?" answerable at all, and spec 8.2 turns that into tens of thousands of training examples
at no labelling cost.

**This is a Phase 3 input, not a Phase 0 release requirement.** The Phase 0 deliverable needs
the construct sequence, the UniProt accession and the cluster ids, all of which exist without
this stage. It is run now for the same reason as TargetTrack: it is cheap today and it pins
the reference version the boundary work will be built against.

    ./run.sh link.uniprot
    ./run.sh link.uniprot --limit 2000        # smoke test
"""

from __future__ import annotations

import argparse
import gzip
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import quote

import polars as pl
from tqdm import tqdm

from .. import config, http
from ..manifest import Manifest

STAGE = "link.uniprot"

# UniProt rejects queries with more than 100 OR conditions, and says so plainly:
# "Too many OR conditions in query. Maximum allowed is 100." Anything larger returns HTTP
# 400 with an empty result, which is silent unless the failure is checked for.
BATCH_SIZE = 100


def batches(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def parse_fasta(text: str) -> Iterator[tuple[str, str]]:
    accession, chunks = None, []
    for line in text.splitlines():
        if line.startswith(">"):
            if accession:
                yield accession, "".join(chunks)
            # ">sp|P00698|LYSC_CHICK ..." -> P00698
            parts = line[1:].split("|")
            accession = parts[1] if len(parts) > 2 else line[1:].split()[0]
            chunks = []
        elif line.strip():
            chunks.append(line.strip())
    if accession:
        yield accession, "".join(chunks)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--linked", type=Path,
                        default=config.INTERIM_DIR / "entry_sequence.parquet")
    parser.add_argument("--cache", type=Path,
                        default=config.RAW_UNIPROT_DIR / "reference_sequences.fasta.gz")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "uniprot_reference.parquet")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.cache.parent.mkdir(parents=True, exist_ok=True)

    linked = pl.read_parquet(args.linked)
    accessions = sorted(
        linked.select(pl.col("protein_uniprot_ids").explode())
              .drop_nulls().unique().to_series().to_list()
    )
    if args.limit:
        accessions = accessions[:args.limit]

    with Manifest(STAGE, params={"n_accessions": len(accessions),
                                 "batch_size": BATCH_SIZE}) as m:
        m.add_input(args.linked)

        sequences: dict[str, str] = {}
        if args.cache.exists():
            with gzip.open(args.cache, "rt") as handle:
                sequences = dict(parse_fasta(handle.read()))
            print(f"reusing {len(sequences):,} cached sequences from {args.cache.name}")

        outstanding = [a for a in accessions if a not in sequences]
        print(f"{len(accessions):,} accessions, {len(outstanding):,} still to fetch")

        planned = list(batches(outstanding, BATCH_SIZE))
        failures: list[dict[str, str]] = []
        for batch in tqdm(planned, desc="batches", unit="batch"):
            query = quote(" OR ".join(f"accession:{a}" for a in batch))
            url = f"{config.UNIPROT_STREAM_URL}?query={query}&format=fasta"
            response = http.get(url)
            if response.status_code != 200:
                # Recorded, never swallowed: a silently skipped batch looks identical to a
                # batch of obsolete accessions, and the two need very different responses.
                failures.append({"status": str(response.status_code),
                                 "first_accession": batch[0],
                                 "body": response.text[:200]})
                continue
            sequences.update(dict(parse_fasta(response.text)))

        if planned and len(failures) > len(planned) * 0.1:
            raise RuntimeError(
                f"{len(failures)} of {len(planned)} UniProt batches failed, "
                f"e.g. HTTP {failures[0]['status']}: {failures[0]['body']}"
            )

        with gzip.open(args.cache, "wt") as handle:
            for accession in sorted(sequences):
                handle.write(f">{accession}\n{sequences[accession]}\n")

        table = pl.DataFrame({
            "uniprot_id": list(sequences),
            "reference_seq": [sequences[a] for a in sequences],
        }).with_columns(pl.col("reference_seq").str.len_chars().alias("reference_length"))
        table.write_parquet(args.out, compression="zstd")

        missing = [a for a in accessions if a not in sequences]
        stats = {
            "n_requested": len(accessions),
            "n_retrieved": table.height,
            "n_missing": len(missing),
            "retrieval_rate": round(table.height / max(1, len(accessions)), 4),
            "median_reference_length": float(table["reference_length"].median() or 0),
        }
        m.add_output(args.cache).add_output(args.out).note(
            **stats, n_failed_batches=len(failures), failed_batches=failures[:5],
            missing_examples=missing[:20])

        for key, value in stats.items():
            print(f"  {key:<28} {value:>10,}" if isinstance(value, int)
                  else f"  {key:<28} {value:>10}")
        if missing:
            print(f"\n{len(missing):,} accessions returned nothing. Obsolete or demerged "
                  f"entries are the usual cause, e.g. {missing[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
