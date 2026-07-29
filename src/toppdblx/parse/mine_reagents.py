"""Stage `parse.mine_reagents`: rank candidate reagent names by corpus mass.

WP2 needs a curated lexicon of 250 to 400 canonical reagents covering ~99% of the corpus.
Curating that by staring at 198,432 free-text strings is hopeless; curating a
frequency-ranked candidate list is a couple of days. This stage produces the list.

The method exploits the structure the text already has. Conditions are written as clauses:

    0.2 M sodium citrate, pH 5.0
    20 % w/v Polyethylene glycol 6,000
    1.7 to 2.1 ammonium sulfate

Strip the leading quantity and unit from a clause and **what remains is the reagent name**.
That yields real chemical phrases directly, where blind n-gram mining would yield
"m sodium" and "glycol 6". Trailing numbers are kept, because in "PEG 3350" the number is
identity rather than amount.

Clauses describing method, temperature, pH or protein concentration are bucketed separately
and reported: they are the other thing the WP3 parser must recognise.

    ./run.sh parse.mine_reagents
    ./run.sh parse.mine_reagents --top 80
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest
from .text import classify, clauses, is_noise, split_trailing_ph, strip_quantity

STAGE = "parse.mine_reagents"

COVERAGE_TARGETS = (0.50, 0.75, 0.90, 0.95, 0.99)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entries", type=Path, default=config.INTERIM_DIR / "entries.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "reagent_candidates.parquet")
    parser.add_argument("--top", type=int, default=40, help="how many candidates to print")
    parser.add_argument("--all-methods", action="store_true",
                        help="include non-X-ray entries that carry conditions")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    entries = pl.read_parquet(args.entries)
    rows = entries.filter(
        pl.col("raw_details").is_not_null()
        & (pl.col("raw_details").str.strip_chars() != "")
        & (pl.lit(True) if args.all_methods else pl.col("is_xray"))
    ).select("pdb_id", "raw_details")

    with Manifest(STAGE, params={"n_rows": rows.height, "all_methods": args.all_methods}) as m:
        m.add_input(args.entries)

        kind_counts: Counter[str] = Counter()
        candidate_clauses: Counter[str] = Counter()
        candidate_entries: defaultdict[str, set[str]] = defaultdict(set)
        examples: dict[str, str] = {}
        n_clauses = 0
        n_ph_split = 0

        for pdb_id, details in tqdm(rows.iter_rows(), total=rows.height,
                                    desc="records", unit="rec"):
            for clause in clauses(details):
                n_clauses += 1
                kind = classify(clause)
                kind_counts[kind] += 1
                if kind != "reagent":
                    continue
                body, ph = split_trailing_ph(clause)
                if ph is not None:
                    n_ph_split += 1
                name = strip_quantity(body)
                if not name or is_noise(name):
                    kind_counts["reagent_empty_after_strip"] += 1
                    continue
                candidate_clauses[name] += 1
                candidate_entries[name].add(pdb_id)
                examples.setdefault(name, clause)

        candidates = pl.DataFrame({
            "candidate": list(candidate_clauses.keys()),
            "n_clauses": [candidate_clauses[k] for k in candidate_clauses],
            "n_entries": [len(candidate_entries[k]) for k in candidate_clauses],
            "example_clause": [examples[k] for k in candidate_clauses],
        }).sort("n_clauses", descending=True)

        total = int(candidates["n_clauses"].sum())
        candidates = candidates.with_columns(
            (pl.col("n_clauses").cum_sum() / total).alias("cumulative_coverage")
        )
        candidates.write_parquet(args.out, compression="zstd")

        # How many distinct candidates cover a given share of clause mass? This is the
        # number that decides whether WP2 is two days or two weeks.
        coverage = {}
        for target in COVERAGE_TARGETS:
            reached = candidates.filter(pl.col("cumulative_coverage") >= target)
            coverage[f"candidates_for_{int(target * 100)}pc"] = (
                candidates.height if reached.is_empty()
                else candidates.height - reached.height + 1
            )

        singletons = candidates.filter(pl.col("n_clauses") == 1).height
        stats = {
            "n_records": rows.height,
            "n_clauses": n_clauses,
            "n_reagent_clauses": total,
            "n_distinct_candidates": candidates.height,
            "n_singleton_candidates": singletons,
            "n_trailing_ph_split": n_ph_split,
            **{f"clauses_{k}": v for k, v in sorted(kind_counts.items())},
            **coverage,
        }
        m.add_output(args.out).note(**stats)

        print(f"\n{rows.height:,} records -> {n_clauses:,} clauses")
        for kind, count in kind_counts.most_common():
            print(f"  {kind:<32} {count:>9,}  ({100 * count / n_clauses:5.2f}%)")
        print(f"\n  trailing pH split off a reagent clause: {n_ph_split:,}")
        print(f"  distinct candidates: {candidates.height:,} "
              f"({singletons:,} seen exactly once)")
        for target in COVERAGE_TARGETS:
            key = f"candidates_for_{int(target * 100)}pc"
            print(f"    for {int(target * 100):>2}% of clause mass: {coverage[key]:>7,} candidates")

        print(f"\ntop {args.top} candidates:")
        print(f"  {'#':>4}  {'clauses':>8}  {'entries':>8}  {'cum':>6}  candidate")
        for i, row in enumerate(candidates.head(args.top).iter_rows(named=True), start=1):
            print(f"  {i:>4}  {row['n_clauses']:>8,}  {row['n_entries']:>8,}  "
                  f"{row['cumulative_coverage']:>5.1%}  {row['candidate'][:58]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
