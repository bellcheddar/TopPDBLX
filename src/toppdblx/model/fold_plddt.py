"""Stage `model.fold_plddt`: ESMFold pLDDT for boundary refinement.

**Why pLDDT and not a propensity scale.** The textbook "do not cut mid-helix" rule was tried with
Chou-Fasman propensities and made the boundary error worse (median 5 residues to 8), because a
local propensity window knows less about the ends than the classifier already does. pLDDT is a
different signal: it is a confidence, and low confidence in a single-sequence fold is a good
proxy for the disorder crystallographers actually trim. That is worth testing on its own terms
rather than assuming it inherits the propensity result.

    ./run.sh model.fold_plddt --limit 40        # cache pLDDT for 40 truncated test proteins
    ./run.sh model.fold_plddt --uniprot P00698

**Cached and resumable, because it is slow.** ESMFold skips the MSA search that makes AlphaFold
expensive, which is what "fast" means here, but it is still a 3.5B model and folding is roughly
cubic in length: 129 residues takes ~30 s on this M1 Max and a 500-residue chain takes minutes.
Every result is appended to a JSONL as it lands, so an interrupted run resumes and nothing is
folded twice.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl

from .. import config
from ..manifest import Manifest

STAGE = "model.fold_plddt"
MODEL = "facebook/esmfold_v1"
# Folding cost climbs steeply with length and the tail is where the time goes, not the value.
MAX_FOLD_LEN = 500


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "plddt.jsonl")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--max-len", type=int, default=MAX_FOLD_LEN)
    parser.add_argument("--uniprot")
    parser.add_argument("--split", default="test")
    args = parser.parse_args(argv)

    import torch
    from transformers import EsmForProteinFolding

    config.ensure_dirs()
    done: set[str] = set()
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["uniprot_id"])

    frame = pl.read_parquet(config.INTERIM_DIR / "boundary_labels.parquet")
    if args.uniprot:
        targets = frame.filter(pl.col("uniprot_id") == args.uniprot)
    else:
        # Only the proteins that carry a real cut: a full-length construct has no boundary to
        # refine, so folding one buys nothing.
        targets = (frame.filter((pl.col("split") == args.split)
                                & (pl.col("inside_fraction") < 0.9)
                                & (pl.col("length") <= args.max_len))
                        .filter(~pl.col("uniprot_id").is_in(list(done)))
                        .head(args.limit))
    if targets.is_empty():
        print("  nothing to fold: all targets already cached")
        return 0

    print(f"  loading {MODEL}")
    model = EsmForProteinFolding.from_pretrained(MODEL, low_cpu_mem_usage=True).eval()

    folded = 0
    with Manifest(STAGE, params={"model": MODEL, "max_len": args.max_len}) as m, \
         args.out.open("a") as sink:
        for row in targets.iter_rows(named=True):
            sequence = row["sequence"][:args.max_len]
            started = time.time()
            with torch.no_grad():
                out = model.infer([sequence])
            # index 1 is the CA atom; pLDDT arrives on 0-1 and is reported on the usual 0-100
            plddt = (out["plddt"][0, :len(sequence), 1].numpy() * 100).round(1)
            sink.write(json.dumps({
                "uniprot_id": row["uniprot_id"],
                "length": len(sequence),
                "seconds": round(time.time() - started, 1),
                "plddt": plddt.tolist(),
            }) + "\n")
            sink.flush()
            folded += 1
            print(f"  {row['uniprot_id']}  {len(sequence):>4} residues  "
                  f"{time.time()-started:>5.0f}s  mean pLDDT {plddt.mean():.0f}  "
                  f"disordered(<70) {100*(plddt<70).mean():.0f}%")
        m.note(n_folded=folded, n_cached=len(done) + folded)
        m.add_output(args.out)
    print(f"\n  folded {folded}, cache now {len(done)+folded} proteins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
