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

        for threshold in (30, 50):
            column = f"cluster_{threshold}"
            frame = frame.with_columns(
                pl.col(column).map_elements(
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

            # The check that matters: no cluster may appear in more than one fold, or the
            # split leaks and every downstream metric is inflated.
            leak = (frame.group_by(column).agg(pl.col(fold).n_unique().alias("folds"))
                    .filter(pl.col("folds") > 1).height)
            stats[f"leaked_clusters_{threshold}"] = leak
            print(f"    clusters spanning more than one fold: {leak}"
                  f"{'  <- LEAK' if leak else '  (none, as required)'}")

        m.add_output(args.out).note(**stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
