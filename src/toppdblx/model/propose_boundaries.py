"""Stage `model.propose_boundaries`: turn per-residue probabilities into a construct to order.

The classifier answers "would a crystallographer keep this residue". A crystallographer needs
"where do I put the primers". This turns one into the other, and it is where the domain rules
live, because a raw threshold produces spans nobody would clone.

    ./run.sh model.propose_boundaries --sequence MKTAYIAK...
    ./run.sh model.propose_boundaries --uniprot P00698
    ./run.sh model.propose_boundaries --evaluate        # score the proposer on the test split

**Read the confidence, not just the span.** Median error is 5 residues and the mean is 72: the
proposer is excellent on most proteins and badly wrong on a minority, so a single headline number
misrepresents it. Mean probability inside the span separates the two (0.97 on the good ones, 0.82
on the bad), and a `confident` flag at 0.85 covers 68% of proteins and lifts both-ends-within-25
from 56% to 71%. A low-confidence proposal is a prompt to look at the profile yourself.

Three rules that help, and one that measurably does not:

* **Smooth before thresholding.** Per-residue output is noisy at single-residue scale, and a lone
  dip below 0.5 inside a folded domain is not a place to cut. A median filter over a window of 15
  removes those without moving real edges, which is what a median filter is for.
* **Take the longest run, not every run.** Thresholding gives several islands; a construct is one
  contiguous stretch. Islands separated by a short gap are merged, because a 5-residue dip is
  noise rather than two domains.
* **Do not gate on ESMFold pLDDT either.** Folding 60 truncated test proteins with ESMFold and
  blending its per-residue confidence into the classifier output changed nothing: median error 2
  residues either way, within-25 88% against 89%, across blend weights 0.3 to 0.8. **pLDDT used
  alone is markedly worse** than the classifier: median 6 residues and 71% within 25. The reading
  is that the classifier already knows where the ordered core ends *and more besides*, because a
  construct boundary often sits in ordered sequence at a domain junction, and pLDDT has no view on
  that. Second structural heuristic to lose to the model, which is a pattern rather than an
  accident: the classifier is trained on the decision itself and the heuristics are proxies for it.
* **Do not nudge the cut to the nearest coil.** This is the textbook rule and it is switched off,
  because it was measured and it hurts: at a window of 8 it moved the median error from 5 residues
  to 8, and at 4 it gained nothing. The classifier has already learned where the ends are, and a
  Chou-Fasman propensity window knows less than it does. The code is kept with the numbers
  attached so this reads as a result rather than an omission.
* **Refuse rather than guess.** Below a minimum length the proposal is withheld. A 20-residue
  "construct" is not a construct, and returning one would be worse than saying nothing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl

from .. import config

STAGE = "model.propose_boundaries"
SMOOTH_WINDOW = 15          # residues, odd; median filter
MERGE_GAP = 12              # islands closer than this are one construct
MIN_CONSTRUCT = 50          # residues; below this, refuse
# **Measured at zero, deliberately.** Nudging a cut to the nearest coil-looking position is the
# textbook rule and it makes the answer worse: ablated over 217 truncated test proteins it moved
# the median error from 5 residues to 8 at a window of 8, and gained nothing at 4. The classifier
# has already learned where the ends are, and a propensity heuristic knows less than it does.
# Kept in the code with the measurement attached, so it is a finding rather than a gap.
COIL_SEARCH = 0             # nudge disabled; see above

# Chou-Fasman helix and sheet propensities, used only to avoid cutting mid-element. This is a
# deliberate stand-in: a real secondary structure prediction would be better, and the roadmap's
# "not cutting inside a predicted domain" rule wants one. It is enough to stop the obvious error.
HELIX = {"E":1.51,"M":1.45,"A":1.42,"L":1.21,"K":1.16,"F":1.13,"Q":1.11,"W":1.08,"I":1.08,
         "V":1.06,"D":1.01,"H":1.00,"R":0.98,"T":0.83,"S":0.77,"C":0.70,"Y":0.69,"N":0.67,
         "P":0.57,"G":0.57}
SHEET = {"V":1.70,"I":1.60,"Y":1.47,"C":1.19,"W":1.37,"F":1.38,"L":1.30,"T":1.19,"M":1.05,
         "A":0.83,"R":0.93,"G":0.75,"D":0.54,"K":0.74,"S":0.75,"H":0.87,"N":0.89,"Q":1.10,
         "P":0.55,"E":0.37}


def median_filter(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or values.size < window:
        return values
    pad = window // 2
    padded = np.pad(values, pad, mode="edge")
    strided = np.lib.stride_tricks.sliding_window_view(padded, window)
    return np.median(strided, axis=1)


def structured_score(sequence: str, window: int = 7) -> np.ndarray:
    """High where the local sequence looks helical or extended, low where it looks like coil."""
    prop = np.array([max(HELIX.get(c, 1.0), SHEET.get(c, 1.0)) for c in sequence])
    return median_filter(prop, window)


def nudge_to_coil(position: int, structure: np.ndarray, limit: int) -> int:
    """Move a cut to the least structured position within `limit` residues."""
    lo = max(0, position - limit)
    hi = min(len(structure), position + limit + 1)
    if hi <= lo:
        return position
    return int(lo + np.argmin(structure[lo:hi]))


def propose(prob: np.ndarray, sequence: str, threshold: float = 0.5) -> Optional[dict]:
    smooth = median_filter(prob, SMOOTH_WINDOW)
    inside = smooth >= threshold
    if not inside.any():
        return None

    # contiguous runs, then merge the ones separated by a short gap
    edges = np.diff(np.concatenate([[0], inside.astype(int), [0]]))
    starts = list(np.flatnonzero(edges == 1))
    ends = list(np.flatnonzero(edges == -1))
    merged: list[list[int]] = []
    for start, end in zip(starts, ends):
        if merged and start - merged[-1][1] <= MERGE_GAP:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    start, end = max(merged, key=lambda r: r[1] - r[0])

    structure = structured_score(sequence)
    start = nudge_to_coil(start, structure, COIL_SEARCH)
    end = nudge_to_coil(end - 1, structure, COIL_SEARCH) + 1
    length = end - start
    if length < MIN_CONSTRUCT:
        return None
    mean_probability = float(prob[start:end].mean())
    return {
        "start": int(start) + 1,          # 1-based, inclusive, like everything else here
        "end": int(end),
        "length": int(length),
        "mean_probability": mean_probability,
        # Above this the proposal is right about both ends to within 25 residues 71% of the time,
        # against 56% ungated. Below it, treat the span as a hint and read the profile.
        "confident": mean_probability >= 0.85,
        "trimmed_n": int(start),
        "trimmed_c": int(len(prob) - end),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sequence")
    parser.add_argument("--uniprot")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=600)
    args = parser.parse_args(argv)

    import torch
    from transformers import AutoTokenizer
    from .train_boundary import BoundaryModel, CHECKPOINT, MAX_LEN, bucketed_batches, collate

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(CHECKPOINT)
    model = BoundaryModel().to(device)
    weights = config.INTERIM_DIR / "boundary_model" / "epoch3.pt"
    model.load_state_dict(torch.load(weights, map_location=device))
    model.eval()

    @torch.no_grad()
    def probabilities(sequence: str) -> np.ndarray:
        encoded = tokenizer(sequence[:MAX_LEN], return_tensors="pt", truncation=True,
                            max_length=MAX_LEN + 2)
        encoded = {k: v.to(device) for k, v in encoded.items()}
        logits = model(**encoded).float().cpu()[0]
        return torch.sigmoid(logits).numpy()[1:1 + len(sequence[:MAX_LEN])]

    if args.evaluate:
        frame = pl.read_parquet(config.INTERIM_DIR / "boundary_labels.parquet")
        test = frame.filter(pl.col("split") == "test").head(args.limit)
        raw_err, prop_err, refused = [], [], 0
        for row in test.iter_rows(named=True):
            seq = row["sequence"][:MAX_LEN]
            truth = np.frombuffer(row["inside"][:MAX_LEN].encode(), dtype=np.uint8) - 48
            if truth.mean() >= 0.9:        # only the proteins with a real cut
                continue
            prob = probabilities(seq)
            n = min(len(prob), len(truth))
            prob, truth = prob[:n], truth[:n].astype(bool)
            if not truth.any():
                continue
            t = np.flatnonzero(truth)
            hard = prob >= args.threshold
            if hard.any():
                h = np.flatnonzero(hard)
                raw_err += [abs(int(h[0]) - int(t[0])), abs(int(h[-1]) - int(t[-1]))]
            out = propose(prob, seq, args.threshold)
            if out is None:
                refused += 1
                continue
            prop_err += [abs(out["start"] - 1 - int(t[0])), abs(out["end"] - 1 - int(t[-1]))]
        print(f"  scored {len(prop_err)//2} truncated test proteins, refused {refused}")
        print(f"    raw threshold      median {np.median(raw_err):.0f} residues, "
              f"mean {np.mean(raw_err):.0f}")
        print(f"    with the rules     median {np.median(prop_err):.0f} residues, "
              f"mean {np.mean(prop_err):.0f}")
        return 0

    sequence = args.sequence
    if args.uniprot:
        seqs = pl.read_parquet(config.INTERIM_DIR / "uniprot_sequences.parquet")
        hit = seqs.filter(pl.col("uniprot_id") == args.uniprot)
        if hit.is_empty():
            raise SystemExit(f"{args.uniprot} is not in the corpus")
        sequence = hit["sequence"][0]
    if not sequence:
        raise SystemExit("give --sequence or --uniprot")

    sequence = sequence.strip().upper()
    out = propose(probabilities(sequence), sequence, args.threshold)
    if out is None:
        print(f"  no construct proposed: nothing above {args.threshold} survives the "
              f"{MIN_CONSTRUCT}-residue minimum")
        return 0
    print(f"  full length      {len(sequence)} residues")
    print(f"  proposed construct  {out['start']}-{out['end']}  ({out['length']} residues)")
    print(f"  trims {out['trimmed_n']} from the N-terminus, {out['trimmed_c']} from the C")
    print(f"  mean probability inside the span: {out['mean_probability']:.2f}"
          f"  {'(confident)' if out['confident'] else '(LOW CONFIDENCE, check the profile)'}")
    print(f"  {sequence[out['start']-1:out['end']]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
