"""Stage `eval.baselines`: the two baselines any learned recommender must beat.

Spec 10 sets the bar explicitly: "Compare against (a) the homology retrieval baseline, (b) a
frequency prior that always returns the most common conditions. Beating the frequency prior is
the minimum bar; beating homology retrieval is the real bar."

Building these **before** any model exists is the point. The brief predicts homology retrieval
will be strong, and if it is, that is a cheap and useful negative result: it says a learned
recommender is not obviously worth building, and it says so for the price of an MMseqs2 search
rather than a fortnight of training.

  frequency_prior     ignores the query entirely and returns the commonest groups in train.
                      Any recommender that cannot beat this has learned nothing.
  homology_retrieval  MMseqs2 search of each test sequence against the training sequences,
                      pooling the groups that worked for its closest relatives, ranked by how
                      many relatives used each and how similar they were.

Both are scored as top-1, top-5 and top-10 accuracy at L1, L2 and L3, on splits by sequence
cluster at 30% and 50% identity. The 30% split is the harder one: no test protein has even a
30%-identity relative in training, so homology must work from remote similarity alone.

    ./run.sh eval.baselines
    ./run.sh eval.baselines --threshold 50 --max-queries 3000
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

import polars as pl

from .. import config
from ..manifest import Manifest

STAGE = "eval.baselines"

LEVELS = ("l1_precipitant_class", "l2_subclass", "l3_group_id")
LEVEL_NAMES = {"l1_precipitant_class": "L1", "l2_subclass": "L2", "l3_group_id": "L3"}
KS = (1, 5, 10)

# How many homologues to pool per query. Beyond about 25 the tail contributes noise rather
# than signal, because remote hits start disagreeing with each other.
TOP_HITS = 25


def write_fasta(rows: list[tuple[str, str]], path: Path) -> int:
    with open(path, "w") as handle:
        for name, sequence in rows:
            handle.write(f">{name}\n{sequence}\n")
    return len(rows)


def run_search(query: Path, target: Path, out: Path, threads: int) -> Path:
    binary = shutil.which("mmseqs")
    if not binary:
        raise RuntimeError("mmseqs not found on PATH. Install with: brew install mmseqs2")
    with tempfile.TemporaryDirectory(prefix="mmseqs_search_") as tmp:
        command = [binary, "easy-search", str(query), str(target), str(out), tmp,
                   "--threads", str(threads), "-v", "1",
                   # Sensitive enough to find remote homologues, which is the whole point of
                   # the 30% split; the default is tuned for close matches.
                   "-s", "7.0", "--max-seqs", "300",
                   "--format-output", "query,target,fident,evalue,bits"]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"mmseqs easy-search failed:\n{result.stderr[-2000:]}")
    return out


def top_k_accuracy(ranked: list[str], truth: str, k: int) -> bool:
    return truth in ranked[:k]


def evaluate(predictions: dict[tuple, list[str]], truth: dict[tuple, str],
             n_classes: int) -> dict[str, Any]:
    """Top-k accuracy, plus the two figures needed to read it honestly.

    `n_classes` matters: top-10 over 8 possible L1 classes is 100% by construction and says
    nothing. Any k at or above the number of classes is reported as None rather than as a
    score, because a guaranteed answer is not an accuracy.

    `coverage` is the share of queries the method could answer at all. A method that returns
    nothing for a fifth of queries is not comparable to one that always answers, so both the
    overall score and the score on answered queries are reported.
    """
    scored = {k: 0 for k in KS}
    scored_covered = {k: 0 for k in KS}
    total = covered = 0
    for key, true_label in truth.items():
        if true_label is None:
            continue
        total += 1
        ranked = predictions.get(key, [])
        if ranked:
            covered += 1
        for k in KS:
            if top_k_accuracy(ranked, true_label, k):
                scored[k] += 1
                if ranked:
                    scored_covered[k] += 1
    out: dict[str, Any] = {"n": total, "coverage": (covered / total if total else 0.0),
                           "n_classes": n_classes}
    for k in KS:
        # Degenerate: k covers every class, so the answer is free.
        out[f"top{k}"] = None if k >= n_classes else (scored[k] / total if total else 0.0)
        out[f"top{k}_covered"] = (None if k >= n_classes
                                  else (scored_covered[k] / covered if covered else 0.0))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--assignments", type=Path,
                        default=config.INTERIM_DIR / "group_assignments.parquet")
    parser.add_argument("--splits", type=Path, default=config.INTERIM_DIR / "splits.parquet")
    parser.add_argument("--linked", type=Path,
                        default=config.INTERIM_DIR / "entry_sequence.parquet")
    parser.add_argument("--work-dir", type=Path, default=config.INTERIM_DIR / "baselines")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "baseline_results.json")
    parser.add_argument("--threshold", type=int, action="append", default=None,
                        help="split threshold(s) to evaluate; default both 30 and 50")
    parser.add_argument("--max-queries", type=int, default=None,
                        help="cap the test set, for a quicker run")
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    thresholds = args.threshold or [50, 30]

    assignments = pl.read_parquet(args.assignments)
    splits = pl.read_parquet(args.splits)
    linked = pl.read_parquet(args.linked).select("pdb_id", "protein_seq", "seq_id")

    frame = (splits.join(assignments, on=["pdb_id", "crystal_id"], how="inner")
             .join(linked, on="pdb_id", how="left")
             .filter(pl.col("protein_seq").is_not_null()))

    results: dict[str, Any] = {}
    with Manifest(STAGE, params={"thresholds": thresholds, "top_hits": TOP_HITS,
                                 "max_queries": args.max_queries}) as m:
        m.add_input(args.assignments).add_input(args.splits)

        for threshold in thresholds:
            fold = f"fold_{threshold}"
            train = frame.filter(pl.col(fold) == "train")
            test = frame.filter(pl.col(fold) == "test")
            if args.max_queries:
                keep = set(test["seq_id"].unique().to_list()[:args.max_queries])
                test = test.filter(pl.col("seq_id").is_in(list(keep)))

            print(f"\n=== split at {threshold}% identity: "
                  f"{train.height:,} train, {test.height:,} test ===")

            level_results: dict[str, Any] = {}
            priors: dict[str, list[str]] = {}

            # ------------------------------------------------ frequency prior
            for level in LEVELS:
                order = [label for label, _ in
                         Counter(train[level].drop_nulls().to_list()).most_common()]
                priors[level] = order
                truth = {(r["pdb_id"], r["crystal_id"]): r[level]
                         for r in test.iter_rows(named=True)}
                prior = {key: order for key in truth}
                level_results.setdefault(LEVEL_NAMES[level], {})["frequency_prior"] = \
                    evaluate(prior, truth, len(order))

            # -------------------------------------------- homology retrieval
            train_seqs = {r["seq_id"]: r["protein_seq"]
                          for r in train.select("seq_id", "protein_seq").unique(
                              subset=["seq_id"]).iter_rows(named=True)}
            test_seqs = {r["seq_id"]: r["protein_seq"]
                         for r in test.select("seq_id", "protein_seq").unique(
                             subset=["seq_id"]).iter_rows(named=True)}
            target = args.work_dir / f"train_{threshold}.fasta"
            query = args.work_dir / f"test_{threshold}.fasta"
            write_fasta(list(train_seqs.items()), target)
            write_fasta(list(test_seqs.items()), query)
            hits_path = args.work_dir / f"hits_{threshold}.m8"
            print(f"  searching {len(test_seqs):,} test sequences against "
                  f"{len(train_seqs):,} training sequences ...", flush=True)
            run_search(query, target, hits_path, args.threads)

            # Groups used by each training sequence.
            groups_for_seq: dict[str, dict[str, list[str]]] = defaultdict(
                lambda: {level: [] for level in LEVELS})
            for row in train.iter_rows(named=True):
                for level in LEVELS:
                    if row[level]:
                        groups_for_seq[row["seq_id"]][level].append(row[level])

            hits: dict[str, list[tuple[str, float]]] = defaultdict(list)
            with open(hits_path) as handle:
                for line in handle:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) < 3:
                        continue
                    q, t, fident = parts[0], parts[1], float(parts[2])
                    if q == t:
                        continue
                    if len(hits[q]) < TOP_HITS:
                        hits[q].append((t, fident))

            n_with_hits = sum(1 for q in test_seqs if hits.get(q))
            print(f"  test sequences with at least one training homologue: "
                  f"{n_with_hits:,} of {len(test_seqs):,} "
                  f"({100 * n_with_hits / max(1, len(test_seqs)):.1f}%)")

            for level in LEVELS:
                predictions: dict[tuple, list[str]] = {}
                hybrid: dict[tuple, list[str]] = {}
                truth = {}
                for row in test.iter_rows(named=True):
                    key = (row["pdb_id"], row["crystal_id"])
                    truth[key] = row[level]
                    votes: Counter[str] = Counter()
                    for target_seq, fident in hits.get(row["seq_id"], []):
                        for label in groups_for_seq.get(target_seq, {}).get(level, []):
                            # Weighted by identity: a closer relative is better evidence.
                            votes[label] += fident
                    ranked = [label for label, _ in votes.most_common()]
                    predictions[key] = ranked
                    # What a real recommender would do: use homology where it has something to
                    # say, then pad with the prior. Pure homology is scored too, but comparing
                    # it against a method that always answers is not a fair fight.
                    tail = [label for label in priors[level] if label not in set(ranked)]
                    hybrid[key] = ranked + tail
                n_classes = len(priors[level])
                level_results[LEVEL_NAMES[level]]["homology_retrieval"] = \
                    evaluate(predictions, truth, n_classes)
                level_results[LEVEL_NAMES[level]]["homology_then_prior"] = \
                    evaluate(hybrid, truth, n_classes)

            results[f"{threshold}pc"] = {
                "n_train": train.height, "n_test": test.height,
                "n_test_sequences": len(test_seqs),
                "n_test_with_homologue": n_with_hits,
                "levels": level_results,
            }

            def cell(value):
                return "    n/a" if value is None else f"{value:>6.1%}"

            print(f"\n  {'level':<5} {'baseline':<22} {'top-1':>8} {'top-5':>8} "
                  f"{'top-10':>8} {'cover':>7} {'classes':>8}")
            for level_name in ("L1", "L2", "L3"):
                for baseline in ("frequency_prior", "homology_retrieval",
                                 "homology_then_prior"):
                    r = level_results[level_name][baseline]
                    covered = ("" if r["coverage"] > 0.999
                               else f"   (on answered: top-1 {cell(r['top1_covered']).strip()})")
                    print(f"  {level_name:<5} {baseline:<22} {cell(r['top1'])} "
                          f"{cell(r['top5'])} {cell(r['top10'])} "
                          f"{r['coverage']:>6.1%} {r['n_classes']:>8}{covered}")
            print("  n/a: k is at or above the number of classes, so the answer is free")

        args.out.write_text(json.dumps(results, indent=1))
        flat = {}
        for split, payload in results.items():
            for level_name, baselines in payload["levels"].items():
                for baseline, scores in baselines.items():
                    if scores["top1"] is not None:
                        flat[f"{split}_{level_name}_{baseline}_top1"] = round(scores["top1"], 4)
        m.add_output(args.out).note(**flat)

        print("\nthe bar the brief sets: beating the frequency prior is the minimum, "
              "beating homology retrieval is the real test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
