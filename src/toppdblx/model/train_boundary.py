"""Stage `model.train_boundary`: ESM-2 as a per-residue construct boundary classifier.

Full-length sequence in, per-residue probability of being inside a crystallised construct out.
The labels cost nothing to collect: 18,680 proteins carry a genuinely truncated consensus, which
is a real decision a crystallographer made and wrote down by depositing.

    ./run.sh model.train_boundary                    # the real run
    ./run.sh model.train_boundary --limit 400 --epochs 1   # a shape check in minutes

**The failure condition is declared before the run, not after.** Per-residue MCC below 0.40, or
median boundary error worse than 20 residues, means the boundary prior is not learnable from
sequence at this scale and R3 stops. Both are reported every epoch so a doomed run can be killed
early rather than tuned into looking successful.

**Accuracy is not reported as a headline and should not be.** 61.5% of residues are inside a
construct, so a model that predicts "inside" everywhere scores 61.5% while being worthless. That
is exactly the trap MCC exists to catch: the same model scores 0.00.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl
import torch
import torch.nn as nn
from sklearn.metrics import matthews_corrcoef
from torch.utils.data import DataLoader, Dataset
from tqdm import tqdm
from transformers import AutoModel, AutoTokenizer

from .. import config
from ..manifest import Manifest

STAGE = "model.train_boundary"
CHECKPOINT = "facebook/esm2_t12_35M_UR50D"
MAX_LEN = 1022                      # ESM-2 positions, before the two special tokens
MCC_FLOOR = 0.40                    # roadmap failure condition
BOUNDARY_CEILING = 20               # residues, median absolute error
# **Every distinct tensor shape costs a Metal kernel compilation.** Batching purely by token
# budget varies batch and width together, so almost every batch is a new shape and MPS recompiles:
# measured at 0.68 s/batch for the first thirty and 5.03 by batch sixty, climbing. Quantising the
# width into eight buckets and fixing the batch size per bucket leaves eight shapes in total, each
# compiled once.
WIDTH_BUCKETS = [128, 256, 384, 512, 640, 768, 896, 1024]


def bucket_width(length: int) -> int:
    """Smallest bucket that holds `length` residues plus ESM's two special tokens."""
    need = min(length, MAX_LEN) + 2
    for width in WIDTH_BUCKETS:
        if need <= width:
            return width
    return WIDTH_BUCKETS[-1]


def bucketed_batches(lengths: list[int], budget: int, rng: np.random.Generator
                     ) -> list[list[int]]:
    """Length-homogeneous batches with only a handful of distinct shapes.

    Two things are being bounded at once. Padding waste, by grouping similar lengths: sorting by
    length and then passing `shuffle=True` does nothing, because the sampler re-randomises, and
    one 1,022-residue protein then pads its whole batch to 1,024. And **kernel recompilation**,
    by quantising the width, which is what actually dominated: unique shapes made MPS rebuild its
    graph almost every step.

    Batch order is shuffled each epoch; batch contents stay inside one width bucket.
    """
    by_bucket: dict[int, list[int]] = {}
    for i, length in enumerate(lengths):
        by_bucket.setdefault(bucket_width(length), []).append(i)
    batches: list[list[int]] = []
    for width, members in by_bucket.items():
        size = max(1, budget // width)
        rng.shuffle(members)
        batches.extend(members[j:j + size] for j in range(0, len(members), size))
    rng.shuffle(batches)
    return batches


class Boundaries(Dataset):
    def __init__(self, frame: pl.DataFrame):
        self.seq = frame["sequence"].to_list()
        self.inside = frame["inside"].to_list()

    def __len__(self) -> int:
        return len(self.seq)

    def __getitem__(self, i: int) -> tuple[str, str]:
        # Long proteins are windowed from the start rather than dropped. A silent truncation
        # would teach the model that every protein ends at residue 1022.
        return self.seq[i][:MAX_LEN], self.inside[i][:MAX_LEN]


def collate(batch, tokenizer):
    seqs = [b[0] for b in batch]
    width = bucket_width(max(len(x) for x in seqs))
    # padding to a fixed bucket width, not to the batch's longest member, is what keeps the
    # number of distinct shapes down to one per bucket
    encoded = tokenizer(seqs, return_tensors="pt", padding="max_length", truncation=True,
                        max_length=width)
    width = encoded["input_ids"].shape[1]
    labels = torch.full((len(batch), width), -100, dtype=torch.float)
    for i, (_, inside) in enumerate(batch):
        # **One vectorised write per sequence, not one per residue.** The obvious loop
        # (`for j, ch in enumerate(inside): labels[i, j+1] = ...`) issues a separate tensor
        # assignment for every amino acid. At batch sizes of 90-odd short proteins that is tens
        # of thousands of individual writes per batch, and it dominated everything else: 16.2
        # s/batch, against 1.4 for the same work done through numpy.
        # +1 skips the CLS token ESM prepends; labels line up with residues, not tokens.
        bits = np.frombuffer(inside.encode("ascii"), dtype=np.uint8).astype(np.float32) - 48.0
        n = min(len(bits), width - 1)
        if n > 0:
            labels[i, 1:1 + n] = torch.from_numpy(bits[:n])
    return encoded, labels


class BoundaryModel(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.esm = AutoModel.from_pretrained(CHECKPOINT)
        hidden = self.esm.config.hidden_size
        self.head = nn.Sequential(nn.Dropout(0.1), nn.Linear(hidden, 1))

    def forward(self, **encoded):
        out = self.esm(**encoded).last_hidden_state
        return self.head(out).squeeze(-1)


def boundary_error(prob: np.ndarray, truth: np.ndarray) -> Optional[tuple[int, int]]:
    """Absolute error in residues at each end of the predicted span."""
    pred = prob >= 0.5
    if not pred.any() or not truth.any():
        return None
    p = np.flatnonzero(pred)
    t = np.flatnonzero(truth)
    return abs(int(p[0]) - int(t[0])), abs(int(p[-1]) - int(t[-1]))


@torch.no_grad()
def evaluate(model, loader, device, limit_batches: Optional[int] = None) -> dict:
    model.eval()
    flat_pred, flat_true, starts, ends = [], [], [], []
    # **Kept separately for the proteins that carry a real boundary.** Half the corpus is
    # crystallised full-length, where predicted and true spans both run end to end and the error
    # is trivially zero. Pooling them drags the median to 0 and hides whether the model can
    # actually place a cut.
    trunc_err = []
    for n, (encoded, labels) in enumerate(loader):
        if limit_batches and n >= limit_batches:
            break
        encoded = {k: v.to(device) for k, v in encoded.items()}
        logits = model(**encoded).float().cpu()
        prob = torch.sigmoid(logits).numpy()
        lab = labels.numpy()
        for i in range(lab.shape[0]):
            keep = lab[i] != -100
            if keep.sum() == 0:
                continue
            p, t = prob[i][keep], lab[i][keep].astype(int)
            flat_pred.append((p >= 0.5).astype(int))
            flat_true.append(t)
            err = boundary_error(p, t.astype(bool))
            if err:
                starts.append(err[0]); ends.append(err[1])
                if t.mean() < 0.9:          # a genuinely truncated construct
                    trunc_err.extend(err)
    if not flat_pred:
        return {"mcc": 0.0, "n": 0}
    pred = np.concatenate(flat_pred); true = np.concatenate(flat_true)
    both = np.concatenate([starts, ends]) if starts else np.array([0])
    return {
        "mcc": float(matthews_corrcoef(true, pred)),
        "accuracy": float((pred == true).mean()),
        "positive_rate": float(pred.mean()),
        "median_boundary_error": float(np.median(both)),
        "median_boundary_error_truncated": float(np.median(trunc_err)) if trunc_err else None,
        "n_truncated": len(trunc_err) // 2,
        "n": int(len(flat_pred)),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels", type=Path,
                        default=config.INTERIM_DIR / "boundary_labels.parquet")
    parser.add_argument("--out-dir", type=Path,
                        default=config.INTERIM_DIR / "boundary_model")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--token-budget", type=int, default=6144,
                        help="max padded tokens per batch; bounds peak MPS memory")
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--eval-batches", type=int, default=120)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

    frame = pl.read_parquet(args.labels)
    train = frame.filter(pl.col("split") == "train")
    valid = frame.filter(pl.col("split") == "valid")
    if args.limit:
        train, valid = train.head(args.limit), valid.head(max(64, args.limit // 8))
    # Shortest first inside each split keeps padding down without shuffling away the split.
    train = train.sort("length")
    valid = valid.sort("length")
    print(f"  train {train.height:,} proteins, valid {valid.height:,}, device {device}")

    tokenizer = AutoTokenizer.from_pretrained(CHECKPOINT)
    model = BoundaryModel().to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"  {CHECKPOINT}: {n_params/1e6:.1f}M parameters")

    fn = lambda b: collate(b, tokenizer)
    rng = np.random.default_rng(17)
    train_batches = bucketed_batches(train["length"].to_list(), args.token_budget, rng)
    valid_batches = bucketed_batches(valid["length"].to_list(), args.token_budget, rng)
    print(f"  token budget {args.token_budget:,}: {len(train_batches):,} train batches, "
          f"median size {int(np.median([len(b) for b in train_batches]))}")
    train_loader = DataLoader(Boundaries(train), batch_sampler=train_batches, collate_fn=fn)
    valid_loader = DataLoader(Boundaries(valid), batch_sampler=valid_batches, collate_fn=fn)

    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr)
    steps = args.epochs * len(train_batches)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimiser, max_lr=args.lr,
                                                   total_steps=max(1, steps))
    loss_fn = nn.BCEWithLogitsLoss(reduction="none")

    history = []
    with Manifest(STAGE, params={"checkpoint": CHECKPOINT, "epochs": args.epochs,
                                 "batch": args.batch, "lr": args.lr,
                                 "mcc_floor": MCC_FLOOR,
                                 "boundary_ceiling": BOUNDARY_CEILING}) as m:
        started = time.time()
        for epoch in range(1, args.epochs + 1):
            model.train()
            running, seen = 0.0, 0
            bar = tqdm(train_loader, desc=f"epoch {epoch}", unit="batch")
            for encoded, labels in bar:
                encoded = {k: v.to(device) for k, v in encoded.items()}
                labels = labels.to(device)
                logits = model(**encoded)
                mask = labels != -100
                loss = (loss_fn(logits, labels.clamp(min=0)) * mask).sum() / mask.sum().clamp(min=1)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimiser.step(); schedule.step(); optimiser.zero_grad()
                running += loss.item(); seen += 1
                if seen % 20 == 0:
                    bar.set_postfix(loss=f"{running/seen:.4f}")
                # The MPS caching allocator keeps the largest allocation it has ever made. Left
                # alone across an epoch of varying widths that becomes the peak, permanently.
                if seen % 200 == 0 and device.type == "mps":
                    torch.mps.empty_cache()

            stats = evaluate(model, valid_loader, device, args.eval_batches)
            stats["epoch"] = epoch
            stats["train_loss"] = running / max(1, seen)
            history.append(stats)
            te = stats.get("median_boundary_error_truncated")
            print(f"\n  epoch {epoch}: loss {stats['train_loss']:.4f}  MCC {stats['mcc']:.3f}")
            print(f"    boundary error: {stats['median_boundary_error']:.0f} residues overall, "
                  f"{'n/a' if te is None else f'{te:.0f}'} on the "
                  f"{stats['n_truncated']} truncated proteins (the ones that matter)")
            print(f"    accuracy {stats['accuracy']:.3f}, predicts inside "
                  f"{stats['positive_rate']:.2f} of residues")

            torch.save(model.state_dict(), args.out_dir / f"epoch{epoch}.pt")
            (args.out_dir / "history.json").write_text(json.dumps(history, indent=2))

        best = max(history, key=lambda h: h["mcc"])
        judged = best.get("median_boundary_error_truncated")
        judged = best["median_boundary_error"] if judged is None else judged
        passes = best["mcc"] >= MCC_FLOOR and judged <= BOUNDARY_CEILING
        m.note(best_mcc=best["mcc"], best_epoch=best["epoch"],
               median_boundary_error=best["median_boundary_error"],
               median_boundary_error_truncated=judged,
               meets_failure_criteria=bool(passes),
               wall_seconds=round(time.time() - started, 1), n_params=n_params)
        print(f"\n  best MCC {best['mcc']:.3f} at epoch {best['epoch']}, "
              f"boundary error {judged:.0f} residues on truncated proteins")
        print(f"  roadmap criteria (MCC >= {MCC_FLOOR}, error <= {BOUNDARY_CEILING}): "
              f"{'PASS' if passes else 'FAIL — the prior is not learnable at this scale'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
