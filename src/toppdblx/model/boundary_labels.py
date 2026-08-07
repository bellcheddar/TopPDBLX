"""Stage `model.boundary_labels`: per-residue construct labels, split leak-free.

Turns 523,018 construct spans into one training example per protein: the full-length UniProt
sequence, and for every residue the fraction of that protein's deposited constructs which included
it. R3's model reads the sequence and predicts that fraction.

    ./run.sh model.boundary_labels

**One example per protein, not per construct.** Lysozyme has 5,542 deposited constructs and a
per-construct dataset would be most of a batch, teaching the model lysozyme rather than
crystallography. Aggregating first also turns repeated deposition into what it actually is:
evidence about consensus, since 71% of the proteins crystallised more than once use an identical
span every time.

**Non-contiguous spans are dropped, not repaired.** 3% of chains carry an internal deletion or are
chimeras. Their start and end are real, but everything between is not one construct, and training
on them teaches boundaries that were never chosen.

**The split is by 30% identity cluster, never by protein.** A protein and its close homologue in
opposite folds would let the model recognise a family it has already seen and score well without
generalising, which is the leak the roadmap's evaluation section was written to avoid.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest

STAGE = "model.boundary_labels"
# A residue counts as "inside" when at least this fraction of the protein's constructs include it.
INSIDE_AT = 0.5
# Below this, a protein has too little sequence for a boundary to mean anything.
MIN_LENGTH = 40
# ESM-2 t12-35M has a 1024-position limit; longer proteins are kept but flagged so the training
# stage can window them rather than silently truncating.
ESM_LIMIT = 1022


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "boundary_labels.parquet")
    parser.add_argument("--inside-at", type=float, default=INSIDE_AT)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    spans = pl.read_parquet(config.INTERIM_DIR / "construct_spans.parquet")
    sequences = pl.read_parquet(config.INTERIM_DIR / "uniprot_sequences.parquet")
    clusters = pl.read_parquet(config.INTERIM_DIR / "sequence_clusters.parquet")

    n_all = spans.height
    spans = spans.filter(pl.col("is_contiguous"))
    print(f"  {n_all:,} spans, {spans.height:,} contiguous "
          f"({100 * spans.height / n_all:.1f}%)")

    by_protein = spans.group_by("uniprot_id").agg(
        pl.col("start"), pl.col("end"), pl.len().alias("n_constructs"))
    frame = by_protein.join(sequences, on="uniprot_id", how="inner")
    frame = frame.filter(pl.col("length") >= MIN_LENGTH)
    print(f"  {frame.height:,} proteins with a sequence of at least {MIN_LENGTH} residues")

    rows = []
    for row in tqdm(frame.iter_rows(named=True), total=frame.height,
                    desc="labels", unit="protein"):
        length = row["length"]
        counts = np.zeros(length, dtype=np.int32)
        for start, end in zip(row["start"], row["end"]):
            # SIFTS positions are 1-based and inclusive; clip because a construct may run to the
            # end of an isoform slightly longer than the canonical sequence.
            lo = max(1, start) - 1
            hi = min(length, end)
            if hi > lo:
                counts[lo:hi] += 1
        coverage = counts / row["n_constructs"]
        inside = coverage >= args.inside_at
        if not inside.any():
            continue
        idx = np.flatnonzero(inside)
        rows.append({
            "uniprot_id": row["uniprot_id"],
            "sequence": row["sequence"],
            "length": length,
            "n_constructs": row["n_constructs"],
            # Stored as a compact string of 0/1 rather than a list column: 49k proteins by up to
            # 34k residues is large, and a bitstring round-trips through parquet cheaply.
            "inside": "".join("1" if v else "0" for v in inside),
            # **The soft target, which round 01 threw away.** `coverage` is the fraction of this
            # protein's deposited constructs that include the residue: for a protein deposited
            # many times it is the empirical distribution of where crystallographers actually
            # cut, and binarising it at 0.5 discards exactly the width a construct panel wants to
            # span. Stored quantised to 0-100 in one byte per residue, same cost as the bitstring.
            "coverage": (coverage * 100).round().astype(np.uint8).tolist(),
            "consensus_start": int(idx[0]) + 1,
            "consensus_end": int(idx[-1]) + 1,
            "inside_fraction": float(inside.mean()),
            "exceeds_esm_limit": length > ESM_LIMIT,
        })

    labels = pl.DataFrame(rows)

    # Leak-free split: cluster at 30% identity, then assign whole clusters to a fold. A protein
    # whose accession has no cluster is held out of training entirely rather than guessed at.
    #
    # **The clusters are keyed on `seq_id`, a hash of the PDB construct sequence, not on the
    # UniProt accession**, so the bridge runs uniprot -> seq_id -> cluster via `entry_sequence`.
    # Joining the cluster table on accession directly finds nothing and silently excludes every
    # protein, which looks like a clean run producing an empty training set.
    entries = pl.read_parquet(config.INTERIM_DIR / "entry_sequence.parquet")
    bridge = (entries.select("seq_id", "protein_uniprot_ids")
                     .drop_nulls("seq_id")
                     .explode("protein_uniprot_ids")
                     .drop_nulls("protein_uniprot_ids")
                     .rename({"protein_uniprot_ids": "uniprot_id"})
                     .join(clusters.select("seq_id", "cluster_30"), on="seq_id", how="inner")
                     .drop_nulls("cluster_30"))
    # One accession can appear under several constructs; at 30% identity they land in the same
    # cluster, so the first is representative. Sorted first so the choice is deterministic.
    mapping = (bridge.select("uniprot_id", "cluster_30")
                     .sort(["uniprot_id", "cluster_30"])
                     .unique(subset=["uniprot_id"], keep="first"))
    print(f"  {mapping.height:,} accessions carry a 30% identity cluster")
    labels = labels.join(mapping, on="uniprot_id", how="left")

    # Deterministic assignment by hashing the cluster id, so the split is reproducible and does
    # not depend on row order.
    labels = labels.with_columns(
        pl.when(pl.col("cluster_30").is_null())
          .then(pl.lit("unassigned"))
          .otherwise(pl.col("cluster_30").cast(pl.Utf8)).alias("cluster_key"))
    labels = labels.with_columns(
        (pl.col("cluster_key").hash(seed=17) % 100).alias("bucket"))
    labels = labels.with_columns(
        pl.when(pl.col("cluster_30").is_null()).then(pl.lit("excluded"))
          .when(pl.col("bucket") < 80).then(pl.lit("train"))
          .when(pl.col("bucket") < 90).then(pl.lit("valid"))
          .otherwise(pl.lit("test")).alias("split"))

    with Manifest(STAGE, params={"inside_at": args.inside_at,
                                 "min_length": MIN_LENGTH}) as m:
        labels.write_parquet(args.out, compression="zstd")
        m.add_output(args.out)
        counts = dict(labels.group_by("split").len().iter_rows())
        m.note(n_proteins=labels.height, n_spans_used=spans.height,
               median_inside_fraction=float(labels["inside_fraction"].median() or 0),
               n_over_esm_limit=int(labels["exceeds_esm_limit"].sum()), **{
                   f"n_{k}": v for k, v in counts.items()})
        print(f"\n  {labels.height:,} labelled proteins")
        for split in ("train", "valid", "test", "excluded"):
            n = counts.get(split, 0)
            print(f"    {split:<9} {n:>7,}")
        print(f"  median inside fraction: {labels['inside_fraction'].median():.2f}")
        print(f"  longer than ESM's {ESM_LIMIT} positions: "
              f"{int(labels['exceeds_esm_limit'].sum()):,}")
        print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
