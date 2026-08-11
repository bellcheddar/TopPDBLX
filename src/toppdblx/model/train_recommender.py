"""Stage `model.train_recommender`: can sequence predict the condition, at all?

Frozen ESM-2 embeddings plus a shallow multi-label head, scored against the two baselines
`eval.baselines` already built, on the same leak-free splits.

    ./run.sh model.train_recommender --level l2

**Kill criterion, agreed before starting and not negotiable afterwards:** if this does not beat
the frequency prior at L2 hit@5 by at least 3 percentage points, it stops and the negative result
is published. It does not get tuned until it looks good.

**This is expected to fail, and the roadmap says so.** The finding that reshaped the whole plan is
that homology retrieval already loses to a query-independent frequency prior at every level, on
both split thresholds, under all three definitions of ground truth. If the nearest sequence
neighbour tells you nothing about the condition, a learned embedding has to find signal that
survived that test, which is a strong prior against.

**Multi-label, not single-label.** A protein crystallises in several unrelated conditions, so
"correct" is any label that worked for that protein's cluster, matching how the baselines are
already scored. Single-label would punish a recommender for suggesting a condition that genuinely
works.

**Frozen embeddings, not fine-tuned.** If the signal is not linearly accessible in a
650M-parameter protein language model's representation, fine-tuning a head on 100k records will
not conjure it, and the compute is better spent knowing that sooner.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest

STAGE = "model.train_recommender"
KS = (1, 5, 10)
MARGIN = 0.03            # kill criterion: must beat the prior at hit@5 by this much
ESM = "facebook/esm2_t12_35M_UR50D"


def embed(sequences: list[str], batch: int = 16) -> np.ndarray:
    """Mean-pooled frozen ESM-2 embeddings, one vector per sequence."""
    import torch
    from transformers import AutoModel, AutoTokenizer

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(ESM)
    model = AutoModel.from_pretrained(ESM).to(device).eval()
    out = []
    with torch.no_grad():
        for start in tqdm(range(0, len(sequences), batch), desc="embed", unit="batch"):
            chunk = [s[:1022] for s in sequences[start:start + batch]]
            encoded = tokenizer(chunk, return_tensors="pt", padding=True,
                                truncation=True, max_length=1024)
            encoded = {k: v.to(device) for k, v in encoded.items()}
            hidden = model(**encoded).last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).float()
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
            out.append(pooled.float().cpu().numpy())
    return np.concatenate(out)


def hit_at_k(ranked: list[str], labels: set[str], k: int) -> bool:
    return bool(labels & set(ranked[:k]))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--assignments", type=Path,
                        default=config.INTERIM_DIR / "group_assignments.parquet")
    parser.add_argument("--splits", type=Path,
                        default=config.INTERIM_DIR / "splits.parquet")
    parser.add_argument("--level", default="l2_subclass",
                        choices=["l1_precipitant_class", "l2_subclass", "l3_group_id"])
    parser.add_argument("--threshold", type=int, default=30)
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "recommender_results.json")
    parser.add_argument("--max-train", type=int, default=None)
    args = parser.parse_args(argv)

    import torch
    import torch.nn as nn

    config.ensure_dirs()
    assignments = pl.read_parquet(args.assignments)
    splits = pl.read_parquet(args.splits)
    # **Sequences live in entry_sequence, keyed on seq_id**, not in either of the other two.
    entries = (pl.read_parquet(config.INTERIM_DIR / "entry_sequence.parquet")
                 .select("seq_id", "protein_seq").drop_nulls().unique(subset=["seq_id"]))
    frame = (assignments.join(splits, on=["pdb_id", "crystal_id"], how="inner")
                        .join(entries, on="seq_id", how="inner"))

    level, cluster_col = args.level, f"cluster_{args.threshold}"
    split_col = f"fold_{args.threshold}"
    frame = frame.filter(pl.col(level).is_not_null() & pl.col("protein_seq").is_not_null())
    train = frame.filter(pl.col(split_col) == "train")
    test = frame.filter(pl.col(split_col) == "test")
    if args.max_train:
        train = train.head(args.max_train)
    print(f"  level {level.upper()}, {args.threshold}% split: "
          f"train {train.height:,}, test {test.height:,}")

    classes = sorted(frame[level].drop_nulls().unique().to_list())
    index = {c: i for i, c in enumerate(classes)}
    print(f"  {len(classes)} classes")

    # Multi-label truth: any group that worked for this protein's cluster counts, matching how
    # eval.baselines scores both baselines.
    by_cluster: dict[str, set[str]] = defaultdict(set)
    for row in test.iter_rows(named=True):
        if row[cluster_col] and row[level]:
            by_cluster[row[cluster_col]].add(row[level])
    truth = {(r["pdb_id"], r["crystal_id"]): by_cluster.get(r[cluster_col], set())
             for r in test.iter_rows(named=True)}

    # **The baseline, computed here rather than quoted**, so it is on exactly these rows.
    prior = [c for c, _ in Counter(train[level].to_list()).most_common()]

    x_train = embed(train["protein_seq"].to_list())
    x_test = embed(test["protein_seq"].to_list())
    y_train = np.zeros((train.height, len(classes)), dtype=np.float32)
    for i, label in enumerate(train[level].to_list()):
        y_train[i, index[label]] = 1.0

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    head = nn.Sequential(nn.Linear(x_train.shape[1], 512), nn.GELU(), nn.Dropout(0.2),
                         nn.Linear(512, len(classes))).to(device)
    optimiser = torch.optim.AdamW(head.parameters(), lr=1e-3, weight_decay=1e-4)
    loss_fn = nn.BCEWithLogitsLoss()
    xt = torch.from_numpy(x_train).to(device)
    yt = torch.from_numpy(y_train).to(device)
    for epoch in range(30):
        head.train()
        permutation = torch.randperm(len(xt), device=device)
        for start in range(0, len(xt), 256):
            idx = permutation[start:start + 256]
            loss = loss_fn(head(xt[idx]), yt[idx])
            loss.backward(); optimiser.step(); optimiser.zero_grad()
    head.eval()
    with torch.no_grad():
        scores = head(torch.from_numpy(x_test).to(device)).cpu().numpy()

    results = {}
    for name, ranker in (("frequency prior", None), ("esm + head", scores)):
        scored = {k: 0 for k in KS}
        total = 0
        for i, row in enumerate(test.iter_rows(named=True)):
            labels = truth[(row["pdb_id"], row["crystal_id"])]
            if not labels:
                continue
            total += 1
            ranked = (prior if ranker is None
                      else [classes[j] for j in np.argsort(-ranker[i])])
            for k in KS:
                if hit_at_k(ranked, labels, k):
                    scored[k] += 1
        results[name] = {f"hit@{k}": scored[k] / total for k in KS} | {"n": total}

    with Manifest(STAGE, params={"level": level, "threshold": args.threshold,
                                 "kill_margin": MARGIN, "esm": ESM}) as m:
        print(f"\n  {'source':<18} " + "  ".join(f"hit@{k}" for k in KS))
        for name, row in results.items():
            print(f"  {name:<18} " + "  ".join(f"{row[f'hit@{k}']:.3f} " for k in KS))
        delta = results["esm + head"]["hit@5"] - results["frequency prior"]["hit@5"]
        passes = delta >= MARGIN
        print(f"\n  hit@5 margin over the prior: {delta:+.3f} "
              f"(needs {MARGIN:+.2f} to continue)")
        print(f"  kill criterion: {'PASS' if passes else 'FAIL — publish the negative result'}")
        results["margin_hit5"] = delta
        results["meets_kill_criterion"] = bool(passes)
        args.out.write_text(json.dumps(results, indent=2))
        m.add_output(args.out)
        m.note(margin_hit5=delta, meets_kill_criterion=bool(passes),
               prior_hit5=results["frequency prior"]["hit@5"],
               model_hit5=results["esm + head"]["hit@5"], n_classes=len(classes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
