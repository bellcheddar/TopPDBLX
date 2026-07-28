"""Stage `parse.lexicon_coverage`: how much of the corpus does the lexicon resolve?

WP2's curation is only measurable if coverage is measurable. This stage answers two
questions and writes the second one to a file the curator works through:

  1. What share of reagent clause mass does `ontology/synonyms.yaml` currently resolve?
  2. Which unmapped candidates are worth adding next, ranked by how much they would add?

Coverage is reported by clause mass rather than by distinct candidate, because the tail is
enormous and almost entirely singletons: 41,326 of 56,014 candidates are seen exactly once.
Chasing distinct-name coverage would be a year of work for no measurable gain, whereas
clause mass is what the parser actually meets.

    ./run.sh parse.lexicon_coverage
    ./run.sh parse.lexicon_coverage --worklist 500
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import polars as pl

from .. import config
from ..manifest import Manifest
from . import lexicon as lex
from .text import normalise

STAGE = "parse.lexicon_coverage"

WORKLIST_PATH = config.INTERIM_DIR / "lexicon_worklist.csv"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidates", type=Path,
                        default=config.INTERIM_DIR / "reagent_candidates.parquet")
    parser.add_argument("--lexicon", type=Path, default=lex.LEXICON_PATH)
    parser.add_argument("--worklist", type=int, default=300,
                        help="how many unmapped candidates to export for curation")
    parser.add_argument("--out", type=Path, default=WORKLIST_PATH)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    lexicon = lex.load(args.lexicon)
    index = lexicon.index()
    candidates = pl.read_parquet(args.candidates)

    with Manifest(STAGE, params={"lexicon_version": lexicon.version,
                                 "n_reagents": len(lexicon.reagents)}) as m:
        m.add_input(args.lexicon).add_input(args.candidates)

        resolved = candidates.with_columns(
            pl.col("candidate")
              .map_elements(lambda c: index[normalise(c)].canonical_id if normalise(c) in index
                            else None, return_dtype=pl.Utf8)
              .alias("canonical_id")
        )
        total_clauses = int(resolved["n_clauses"].sum())
        matched = resolved.filter(pl.col("canonical_id").is_not_null())
        unmatched = resolved.filter(pl.col("canonical_id").is_null())

        matched_clauses = int(matched["n_clauses"].sum())
        coverage = matched_clauses / total_clauses

        # What the next tranche of curation is worth: the share of clause mass sitting in
        # the top N unmapped candidates.
        head = unmatched.head(args.worklist)
        head_gain = int(head["n_clauses"].sum()) / total_clauses

        (head.select("candidate", "n_clauses", "n_entries", "example_clause")
             .with_columns(pl.lit("").alias("canonical_id"),
                           pl.lit("").alias("chem_class"),
                           pl.lit("").alias("notes"))
             .write_csv(args.out))

        by_class = (matched.join(
            pl.DataFrame({"canonical_id": [r.canonical_id for r in lexicon.reagents],
                          "chem_class": [r.chem_class for r in lexicon.reagents]}),
            on="canonical_id", how="left")
            .group_by("chem_class")
            .agg(pl.col("n_clauses").sum().alias("clauses"),
                 pl.col("canonical_id").n_unique().alias("reagents"))
            .sort("clauses", descending=True))

        stats = {
            "lexicon_version": lexicon.version,
            "n_reagents": len(lexicon.reagents),
            "n_aliases": len(index),
            "clause_coverage": round(coverage, 4),
            "n_clauses_matched": matched_clauses,
            "n_clauses_total": total_clauses,
            "n_candidates_matched": matched.height,
            "n_candidates_unmatched": unmatched.height,
            "worklist_size": head.height,
            "worklist_potential_gain": round(head_gain, 4),
        }
        m.add_output(args.out).note(**stats)

        print(f"lexicon v{lexicon.version}: {len(lexicon.reagents)} reagents, "
              f"{len(index)} names")
        print(f"\nclause coverage: {coverage:.1%}  "
              f"({matched_clauses:,} of {total_clauses:,} reagent clauses)")
        print(f"  candidates matched:   {matched.height:,}")
        print(f"  candidates unmatched: {unmatched.height:,}")

        print("\nresolved clause mass by class:")
        for row in by_class.iter_rows(named=True):
            print(f"  {row['chem_class']:<10} {row['clauses']:>8,} clauses "
                  f"({row['clauses'] / total_clauses:>5.1%})  "
                  f"across {row['reagents']:>3} reagents")

        print(f"\ntop 25 unmapped candidates (worklist written to {args.out}):")
        print(f"  {'clauses':>8}  {'entries':>8}  candidate")
        for row in unmatched.head(25).iter_rows(named=True):
            print(f"  {row['n_clauses']:>8,}  {row['n_entries']:>8,}  {row['candidate'][:56]}")
        print(f"\ncurating the top {head.height} unmapped candidates would add up to "
              f"{head_gain:.1%} of clause mass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
