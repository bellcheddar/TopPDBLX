"""Stage `model.propensity_dataset`: features and labels for crystallisation propensity.

The only part of this project that addresses **P(crystallised | sequence)** rather than
P(condition | crystallised), because TargetTrack is the only source of failures.

    ./run.sh model.propensity_dataset

## The positive definition, which the roadmap requires be fixed before training

**Positive:** the target ever reached `crystallized`, `diffraction`, `diffraction-quality
crystals`, `crystal structure`, `phasing diffraction-data`, `hkl data`, `structure successful`
or `in pdb`. 21,173 targets.

**Negative:** the target reached `purified`, `soluble`, `mass spec verified` or `hsqc
satisfactory` and never reached any of the above. 79,926 targets.

**Conditioning the negatives on having reached purified protein is the load-bearing choice**, and
it is what makes this a crystallisation model rather than an expression model. Every target here
got far enough that crystallisation was actually on the table, so the question the model answers
is "given soluble purified protein, will it crystallise". Using all 335,771 targets as the
denominator instead drops the positive rate from 20.9% to 6.3% and the model would spend most of
its capacity predicting whether a gene expresses at all, which is a different and much easier
question that would inflate the AUC while answering nothing a crystallographer asked.

## The split

Sequences are clustered at 30% identity with MMseqs2 and whole clusters are assigned to a fold.
Splitting on targets rather than clusters would put near-identical structural genomics constructs
on both sides: these centres deliberately targeted homologous families, so a random split leaks
badly and flatters the model.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest

STAGE = "model.propensity_dataset"

CRYSTALLISED = {"crystallized", "diffraction", "diffraction-quality crystals",
                "crystal structure", "in pdb", "structure successful",
                "phasing diffraction-data", "hkl data"}
PURIFIED = {"purified", "soluble", "mass spec verified", "hsqc satisfactory"}

# Kyte-Doolittle hydropathy, and the Uversky-style charge/hydropathy pair that XtalPred and
# every disorder heuristic since is built on.
KD = {"A": 1.8, "R": -4.5, "N": -3.5, "D": -3.5, "C": 2.5, "Q": -3.5, "E": -3.5, "G": -0.4,
      "H": -3.2, "I": 4.5, "L": 3.8, "K": -3.9, "M": 1.9, "F": 2.8, "P": -1.6, "S": -0.8,
      "T": -0.7, "W": -0.9, "Y": -1.3, "V": 4.2}
AAS = "ACDEFGHIKLMNPQRSTVWY"


def features(sequence: str) -> dict:
    """XtalPred-style physicochemical descriptors, all sequence-only and cheap."""
    n = len(sequence)
    counts = {a: sequence.count(a) for a in AAS}
    freq = {f"f_{a}": counts[a] / n for a in AAS}
    pos = counts["K"] + counts["R"]
    neg = counts["D"] + counts["E"]
    hydro = np.array([KD.get(c, 0.0) for c in sequence])
    # Longest run of a single residue: poly-Q, poly-A and similar low-complexity stretches are a
    # classic reason a construct will not crystallise.
    longest, run, prev = 1, 1, ""
    for c in sequence:
        run = run + 1 if c == prev else 1
        longest = max(longest, run)
        prev = c
    return {
        **freq,
        "length": n,
        "gravy": float(hydro.mean()),
        "hydro_sd": float(hydro.std()),
        "net_charge": (pos - neg) / n,
        "abs_charge": (pos + neg) / n,
        "cys_frac": counts["C"] / n,
        "aromatic_frac": (counts["F"] + counts["W"] + counts["Y"]) / n,
        "loop_formers": (counts["G"] + counts["P"] + counts["S"]) / n,
        "longest_repeat": longest,
        # Shannon entropy of the composition: low entropy is low complexity.
        "entropy": float(-sum(p * np.log2(p) for p in
                              (counts[a] / n for a in AAS) if p > 0)),
    }


def cluster_at_30(sequences: dict[str, str], workdir: Path) -> dict[str, str]:
    """MMseqs2 easy-cluster at 30% identity. Returns target_id -> cluster representative."""
    fasta = workdir / "targets.fasta"
    with fasta.open("w") as handle:
        for name, seq in sequences.items():
            handle.write(f">{name}\n{seq}\n")
    out = workdir / "clu"
    subprocess.run(["mmseqs", "easy-cluster", str(fasta), str(out), str(workdir / "tmp"),
                    "--min-seq-id", "0.3", "-c", "0.8", "--cov-mode", "1", "-v", "1"],
                   check=True, capture_output=True)
    mapping: dict[str, str] = {}
    for line in (out.with_name("clu_cluster.tsv")).read_text().splitlines():
        if "\t" in line:
            representative, member = line.split("\t", 1)
            mapping[member.strip()] = representative.strip()
    return mapping


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "propensity_dataset.parquet")
    parser.add_argument("--min-length", type=int, default=50)
    parser.add_argument("--max-length", type=int, default=2000)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    targets = pl.read_parquet(config.INTERIM_DIR / "targettrack_targets.parquet")

    frame = targets.with_columns(
        pl.col("statuses").list.eval(
            pl.element().is_in(list(CRYSTALLISED))).list.any().alias("crystallised"),
        pl.col("statuses").list.eval(
            pl.element().is_in(list(PURIFIED))).list.any().alias("reached_purified"))
    # Eligible: crystallisation was actually attempted or attemptable. See the module docstring.
    frame = frame.filter(pl.col("crystallised") | pl.col("reached_purified"))
    frame = frame.filter((pl.col("length") >= args.min_length)
                         & (pl.col("length") <= args.max_length))
    # Only the 20 standard residues: X, B, Z and U appear in a handful of records and the
    # descriptors are undefined for them.
    frame = frame.filter(pl.col("sequence").str.contains(r"^[ACDEFGHIKLMNPQRSTVWY]+$"))
    print(f"  {frame.height:,} eligible targets, "
          f"{frame['crystallised'].sum():,} positive "
          f"({100 * frame['crystallised'].mean():.1f}%)")

    rows = [features(s) for s in tqdm(frame["sequence"].to_list(), desc="features",
                                      unit="seq")]
    feats = pl.DataFrame(rows)
    frame = pl.concat([frame.select("target_id", "lab", "sequence", "length",
                                    "crystallised", "organism").rename({"length": "seq_length"}),
                       feats.drop("length")], how="horizontal")

    with tempfile.TemporaryDirectory() as tmp:
        print("  clustering at 30% identity with MMseqs2")
        mapping = cluster_at_30(dict(zip(frame["target_id"].to_list(),
                                         frame["sequence"].to_list())), Path(tmp))
    frame = frame.with_columns(
        pl.col("target_id").replace_strict(mapping, default=None).alias("cluster_30"))
    frame = frame.with_columns(
        pl.when(pl.col("cluster_30").is_null()).then(pl.col("target_id"))
          .otherwise(pl.col("cluster_30")).alias("cluster_30"))
    frame = frame.with_columns((pl.col("cluster_30").hash(seed=23) % 100).alias("bucket"))
    frame = frame.with_columns(
        pl.when(pl.col("bucket") < 80).then(pl.lit("train"))
          .otherwise(pl.lit("test")).alias("split"))

    with Manifest(STAGE, params={"min_length": args.min_length,
                                 "max_length": args.max_length,
                                 "positive_statuses": sorted(CRYSTALLISED),
                                 "negative_requires": sorted(PURIFIED)}) as m:
        frame.write_parquet(args.out, compression="zstd")
        m.add_output(args.out)
        train = frame.filter(pl.col("split") == "train")
        test = frame.filter(pl.col("split") == "test")
        m.note(n_total=frame.height, n_positive=int(frame["crystallised"].sum()),
               n_clusters=frame["cluster_30"].n_unique(),
               n_train=train.height, n_test=test.height,
               positive_rate_train=float(train["crystallised"].mean()),
               positive_rate_test=float(test["crystallised"].mean()))
        print(f"\n  {frame.height:,} targets in {frame['cluster_30'].n_unique():,} clusters")
        print(f"    train {train.height:,} ({100 * train['crystallised'].mean():.1f}% positive)")
        print(f"    test  {test.height:,} ({100 * test['crystallised'].mean():.1f}% positive)")
        print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
