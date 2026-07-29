"""Stage `link.cluster`: MMseqs2 sequence clustering at 30%, 50% and 90% identity.

Spec 7.3: redundancy will destroy the metrics if ignored. Lysozyme, trypsin and their
variants contribute thousands of entries, and the same protein appears in the same condition
hundreds of times. Splits must be by **sequence cluster, never by entry**.

All three thresholds are computed and shipped, not just the two the evaluation needs:

  30% and 50%   the split thresholds the evaluation protocol reports against (spec 10)
  90%           needed so decision 12.4 (whether a protein's multi-label set is drawn from
                its entry, its 90% cluster or its 50% cluster) stays answerable in Phase 2
                without re-clustering the archive

Clustering runs on distinct sequences rather than entries, so a protein solved 400 times
costs one comparison rather than 400.

    ./run.sh link.cluster
    ./run.sh link.cluster --identity 0.5      # one threshold only
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import polars as pl

from .. import config
from ..manifest import Manifest

STAGE = "link.cluster"

IDENTITIES = (0.30, 0.50, 0.90)

# Coverage of 80% on the shorter sequence. Without a coverage requirement, a short peptide
# matching one domain of a large multi-domain protein would join its cluster and silently
# merge unrelated families.
COVERAGE = 0.8
COVERAGE_MODE = 0

# MMseqs2's default sensitivity is tuned for high-identity search. At 30% identity it misses
# real homologues, which would inflate the cluster count and weaken the split.
LOW_IDENTITY_SENSITIVITY = 7.5
SENSITIVITY_THRESHOLD = 0.4


def write_fasta(sequences: pl.DataFrame, path: Path) -> int:
    with open(path, "w") as handle:
        for seq_id, sequence in sequences.iter_rows():
            handle.write(f">{seq_id}\n{sequence}\n")
    return sequences.height


def run_mmseqs(fasta: Path, out_prefix: Path, identity: float, threads: int) -> Path:
    binary = shutil.which("mmseqs")
    if not binary:
        raise RuntimeError("mmseqs not found on PATH. Install with: brew install mmseqs2")
    with tempfile.TemporaryDirectory(prefix="mmseqs_") as tmp:
        command = [
            binary, "easy-cluster", str(fasta), str(out_prefix), tmp,
            "--min-seq-id", str(identity),
            "-c", str(COVERAGE), "--cov-mode", str(COVERAGE_MODE),
            "--threads", str(threads), "-v", "1",
        ]
        if identity < SENSITIVITY_THRESHOLD:
            command += ["-s", str(LOW_IDENTITY_SENSITIVITY)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"mmseqs failed at identity {identity}:\n{result.stderr[-2000:]}"
            )
    cluster_tsv = out_prefix.with_name(out_prefix.name + "_cluster.tsv")
    if not cluster_tsv.exists():
        raise RuntimeError(f"mmseqs produced no cluster file at {cluster_tsv}")
    return cluster_tsv


def read_clusters(tsv: Path, column: str) -> pl.DataFrame:
    """MMseqs writes one row per (representative, member) pair."""
    return (pl.read_csv(tsv, separator="\t", has_header=False,
                        new_columns=["representative", "seq_id"])
            .select(pl.col("seq_id"), pl.col("representative").alias(column)))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--linked", type=Path,
                        default=config.INTERIM_DIR / "entry_sequence.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "sequence_clusters.parquet")
    parser.add_argument("--work-dir", type=Path, default=config.INTERIM_DIR / "mmseqs")
    parser.add_argument("--identity", type=float, action="append", default=None)
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    identities = args.identity or list(IDENTITIES)

    linked = pl.read_parquet(args.linked)
    sequences = (linked.filter(pl.col("protein_seq").is_not_null())
                 .select("seq_id", "protein_seq").unique(subset=["seq_id"])
                 .sort("seq_id"))

    with Manifest(STAGE, params={"identities": identities, "coverage": COVERAGE,
                                 "threads": args.threads}) as m:
        m.add_input(args.linked)

        fasta = args.work_dir / "sequences.fasta"
        n_sequences = write_fasta(sequences, fasta)
        print(f"{n_sequences:,} distinct protein sequences written to {fasta.name}")

        clusters = sequences.select("seq_id")
        stats: dict[str, float] = {"n_sequences": n_sequences}

        for identity in identities:
            column = f"cluster_{int(identity * 100)}"
            print(f"  clustering at {identity:.0%} identity ...", flush=True)
            tsv = run_mmseqs(fasta, args.work_dir / column, identity, args.threads)
            mapping = read_clusters(tsv, column)
            clusters = clusters.join(mapping, on="seq_id", how="left")
            n_clusters = mapping[column].n_unique()
            stats[f"n_{column}"] = n_clusters
            print(f"    {n_clusters:,} clusters "
                  f"({n_sequences / max(1, n_clusters):.1f} sequences each on average)")

        clusters.write_parquet(args.out, compression="zstd")
        m.add_output(args.out).note(**stats)

        # What redundancy control actually buys: entries collapse far harder than sequences,
        # because the same protein is deposited over and over.
        joined = linked.join(clusters, on="seq_id", how="left")
        print(f"\nredundancy at each level (entries -> distinct groups):")
        print(f"  entries with a protein          {joined.filter(pl.col('protein_seq').is_not_null()).height:>9,}")
        print(f"  distinct sequences              {n_sequences:>9,}")
        for identity in identities:
            column = f"cluster_{int(identity * 100)}"
            if column in joined.columns:
                # drop_nulls: entries with no protein entity carry a null cluster, and
                # counting that as a group inflates every total by one.
                print(f"  distinct {column} groups   "
                      f"{joined[column].drop_nulls().n_unique():>9,}")

        largest = (joined.filter(pl.col("cluster_30").is_not_null())
                   .group_by("cluster_30").agg(pl.len().alias("n_entries"))
                   .sort("n_entries", descending=True).head(5))
        print("\nlargest 30% identity clusters by entry count "
              "(this is the redundancy the spec warns about):")
        for row in largest.iter_rows(named=True):
            # Take the commonest non-null description in the cluster, not the first:
            # a single unannotated entity should not label 5,000 entries.
            members = joined.filter(pl.col("cluster_30") == row["cluster_30"])
            named = (members.select("protein_description").drop_nulls()
                     .group_by("protein_description").agg(pl.len().alias("n"))
                     .sort("n", descending=True).head(1))
            label = named["protein_description"].item() if named.height else "(no description)"
            print(f"  {row['n_entries']:>6,} entries   {str(label)[:58]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
