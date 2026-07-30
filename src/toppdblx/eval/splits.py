"""Stage `eval.splits`: train, validation and test splits by sequence cluster.

Spec 7.3 and 10: **split by sequence cluster, never by entry**, at both 30% and 50% identity,
and report both. Splitting by entry would put near-identical proteins on both sides, and with
lysozyme, trypsin and Fab fragments contributing thousands of entries each, every metric would
be inflated by memorisation rather than generalisation.

The scale of the problem, measured: 183,623 usable conditions come from only 23,868 distinct
30%-identity clusters. A single cluster holds up to 2,876 entries. An entry-level split would
leak almost every test protein into training.

Assignment is deterministic, by hashing the cluster id rather than shuffling, so:

  * the same cluster always lands in the same fold, whatever order the data arrives in;
  * adding new PDB entries does not reshuffle existing folds;
  * the split can be reproduced from the cluster ids alone, with no stored state.

Records with no linked sequence cannot be split by cluster and are excluded rather than
dumped into training, where they would silently pad the frequency prior.

    ./run.sh eval.splits
    ./run.sh eval.splits --test-fraction 0.2
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Optional

import polars as pl

from .. import config
from ..manifest import Manifest

STAGE = "eval.splits"

DEFAULT_TEST = 0.15
DEFAULT_VAL = 0.15
SALT = "toppdblx-splits-v1"


def fold_for(cluster_id: str, test_fraction: float, val_fraction: float) -> str:
    """Deterministic fold from a stable hash of the cluster id.

    Hashing rather than shuffling means the fold is a pure function of the cluster id: no
    seed to remember, no reshuffle when the archive grows, and reproducible by anyone holding
    the released cluster ids.
    """
    digest = hashlib.sha256(f"{SALT}:{cluster_id}".encode()).hexdigest()
    position = int(digest[:16], 16) / float(1 << 64)
    if position < test_fraction:
        return "test"
    if position < test_fraction + val_fraction:
        return "val"
    return "train"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--conditions", type=Path,
                        default=config.INTERIM_DIR / "parsed_conditions.parquet")
    parser.add_argument("--linked", type=Path,
                        default=config.INTERIM_DIR / "entry_sequence.parquet")
    parser.add_argument("--clusters", type=Path,
                        default=config.INTERIM_DIR / "sequence_clusters.parquet")
    parser.add_argument("--out", type=Path, default=config.INTERIM_DIR / "splits.parquet")
    parser.add_argument("--test-fraction", type=float, default=DEFAULT_TEST)
    parser.add_argument("--val-fraction", type=float, default=DEFAULT_VAL)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    conditions = pl.read_parquet(args.conditions).filter(pl.col("discard_reason").is_null())
    linked = (pl.read_parquet(args.linked)
              .join(pl.read_parquet(args.clusters), on="seq_id", how="left")
              .select("pdb_id", "seq_id", "cluster_30", "cluster_50", "cluster_90"))

    frame = conditions.select("pdb_id", "crystal_id").join(linked, on="pdb_id", how="left")

    with Manifest(STAGE, params={"test_fraction": args.test_fraction,
                                 "val_fraction": args.val_fraction, "salt": SALT}) as m:
        m.add_input(args.conditions).add_input(args.clusters)

        # Excluded, not defaulted to train: a record with no sequence cannot be held out
        # honestly, and silently training on it would pad the frequency prior.
        unlinked = frame.filter(pl.col("cluster_30").is_null()).height
        frame = frame.filter(pl.col("cluster_30").is_not_null()
                             & pl.col("cluster_50").is_not_null())

        # MMseqs2 clusters do NOT nest. The clustering is greedy rather than hierarchical, so
        # members of one 90% cluster can land in different 30% clusters. Assigning folds by a
        # single cluster column therefore leaks: measured directly, 68 90%-identity clusters
        # straddled a 30% split, meaning a test protein had a near-identical relative in
        # training. That is exactly what splitting by cluster is supposed to prevent.
        #
        # The only leak-free grouping is the connected components of the union of all three
        # cluster relations: two records are joined if they share a cluster at ANY threshold,
        # and a whole component goes to one fold.
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for row in frame.iter_rows(named=True):
            keys = [f"c30:{row['cluster_30']}", f"c50:{row['cluster_50']}"]
            if row["cluster_90"]:
                keys.append(f"c90:{row['cluster_90']}")
            for other in keys[1:]:
                union(keys[0], other)

        component = [find(f"c30:{row['cluster_30']}") for row in frame.iter_rows(named=True)]
        frame = frame.with_columns(pl.Series("split_component", component))

        for threshold in (30, 50):
            # The component is shared, so the two thresholds now differ only in the cluster
            # column reported alongside, not in fold membership. Both are kept because spec 10
            # asks for both to be reported, and the cluster counts differ.
            frame = frame.with_columns(
                pl.col("split_component").map_elements(
                    lambda c: fold_for(c, args.test_fraction, args.val_fraction),
                    return_dtype=pl.Utf8).alias(f"fold_{threshold}"))

        frame.write_parquet(args.out, compression="zstd")

        stats = {"n_records": frame.height, "n_excluded_no_sequence": unlinked}
        print(f"\n{frame.height:,} records splittable by cluster "
              f"({unlinked:,} excluded for having no linked sequence)\n")
        for threshold in (30, 50):
            column, fold = f"cluster_{threshold}", f"fold_{threshold}"
            print(f"  split at {threshold}% identity:")
            for row in (frame.group_by(fold).agg(
                    pl.len().alias("records"),
                    pl.col(column).n_unique().alias("clusters"))
                    .sort("records", descending=True).iter_rows(named=True)):
                stats[f"n_{fold}_{row[fold]}"] = row["records"]
                print(f"    {row[fold]:<6} {row['records']:>8,} records  "
                      f"{row['clusters']:>7,} clusters  "
                      f"{100 * row['records'] / frame.height:>5.1f}%")

            # Checked at every cluster level, not just the one being split on. Checking only
            # the split column is what hid the original leak.
            for level in (30, 50, 90):
                other = f"cluster_{level}"
                leak = (frame.filter(pl.col(other).is_not_null())
                        .group_by(other).agg(pl.col(fold).n_unique().alias("folds"))
                        .filter(pl.col("folds") > 1).height)
                stats[f"leaked_{other}_in_fold_{threshold}"] = leak
                print(f"    {other} clusters spanning folds: {leak}"
                      f"{'  <- LEAK' if leak else '  (none)'}")

        m.add_output(args.out).note(**stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
