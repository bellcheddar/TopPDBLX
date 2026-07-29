"""Stage `eval.audit_metrics`: turn hand verdicts into the headline accuracy numbers.

Consumes the JSON exported by the Condition Courtroom and produces the figures the dataset
paper reports. This is the **only** defensible source of parse accuracy in Phase 0: the WP6
language model is trained on labels bootstrapped from the rule parser, so measuring it
against rule-derived labels would be circular.

Two things are reported separately rather than folded into one number:

  record accuracy       the share of audited records judged wholly correct
  component precision   the share of parsed components not flagged as wrong

They answer different questions. A record with four components and one bad reagent is a
failed record but a 75% component score, and both matter: the first for "can I trust this
row", the second for "how much of the database is right".

**Unit inference is reported on its own**, because it fires on 31.5% of components and a
systematic error there would be invisible in an aggregate score.

Accuracy is also broken down by stratum. The sample is stratified, not uniform, so the
overall figure is **not** an estimate of archive-wide accuracy unless it is reweighted by
stratum population. That reweighted estimate is computed here and labelled as such.

    ./run.sh eval.audit_metrics --verdicts ~/Downloads/audit_verdicts_seed20260729.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import polars as pl

from .. import config
from ..manifest import Manifest

STAGE = "eval.audit_metrics"


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval: sane at small n and near 0 or 1, unlike the normal one."""
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verdicts", type=Path, required=True,
                        help="JSON exported by the Condition Courtroom")
    parser.add_argument("--sample", type=Path,
                        default=config.INTERIM_DIR / "audit_sample.parquet")
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--conditions", type=Path,
                        default=config.INTERIM_DIR / "parsed_conditions.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "audit_metrics.json")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    payload = json.loads(args.verdicts.read_text())
    verdicts = [v for v in payload.get("verdicts", []) if v.get("verdict") in ("correct", "incorrect")]
    if not verdicts:
        raise SystemExit("No judged verdicts in that export.")

    sample = pl.read_parquet(args.sample)
    components = pl.read_parquet(args.components)
    conditions = pl.read_parquet(args.conditions)

    with Manifest(STAGE, params={"verdicts": str(args.verdicts),
                                 "sample_seed": payload.get("sample_seed"),
                                 "n_judged": len(verdicts)}) as m:
        m.add_input(args.verdicts).add_input(args.sample)

        judged = pl.DataFrame([
            {"pdb_id": v["pdb_id"], "crystal_id": v["crystal_id"],
             "verdict": v["verdict"],
             "n_bad_components": len(v.get("bad_components") or []),
             "errors": v.get("errors") or []}
            for v in verdicts
        ])
        framed = judged.join(sample, on=["pdb_id", "crystal_id"], how="left")

        n = framed.height
        correct = framed.filter(pl.col("verdict") == "correct").height
        low, high = wilson_interval(correct, n)

        # Component precision over the audited records only.
        keys = set(zip(framed["pdb_id"], framed["crystal_id"]))
        audited_components = components.filter(
            pl.struct(["pdb_id", "crystal_id"]).map_elements(
                lambda s: (s["pdb_id"], s["crystal_id"]) in keys, return_dtype=pl.Boolean))
        n_components = audited_components.height
        n_bad = int(framed["n_bad_components"].sum())
        comp_low, comp_high = wilson_interval(n_components - n_bad, n_components)

        error_counts = Counter(e for v in verdicts for e in (v.get("errors") or []))

        # Per-stratum accuracy, and the reweighted archive-wide estimate. The sample is
        # deliberately not uniform, so the raw figure over-represents hard cases.
        population = (conditions
                      .with_columns(pl.when(pl.col("discard_reason").is_null())
                                    .then(pl.lit("kept")).otherwise(pl.col("discard_reason"))
                                    .alias("outcome"))
                      .group_by("outcome").agg(pl.len().alias("n_population")))
        by_outcome = (framed.group_by("outcome").agg(
            pl.len().alias("n_audited"),
            (pl.col("verdict") == "correct").sum().alias("n_correct")))
        weighted = by_outcome.join(population, on="outcome", how="left").with_columns(
            (pl.col("n_correct") / pl.col("n_audited")).alias("accuracy"))
        total_population = int(weighted["n_population"].fill_null(0).sum())
        reweighted = float(
            (weighted["accuracy"] * weighted["n_population"].fill_null(0)).sum()
            / max(1, total_population))

        # Unit inference, measured on its own: it fires on ~31.5% of components and a
        # systematic error there would vanish inside an aggregate score.
        unit_flagged = sum(1 for v in verdicts if "unit" in (v.get("errors") or []))
        n_inferred = audited_components.filter(pl.col("unit_inferred")).height

        results: dict[str, Any] = {
            "sample_seed": payload.get("sample_seed"),
            "n_judged": n,
            "record_accuracy": round(correct / n, 4),
            "record_accuracy_ci95": [round(low, 4), round(high, 4)],
            "n_components_audited": n_components,
            "n_components_flagged_wrong": n_bad,
            "component_precision": round((n_components - n_bad) / max(1, n_components), 4),
            "component_precision_ci95": [round(comp_low, 4), round(comp_high, 4)],
            "reweighted_record_accuracy": round(reweighted, 4),
            "error_tag_counts": dict(error_counts.most_common()),
            "n_records_with_unit_error": unit_flagged,
            "n_components_with_inferred_unit": n_inferred,
            "coverage_of_sample": round(n / max(1, sample.height), 4),
        }
        args.out.write_text(json.dumps(results, indent=1))
        m.add_output(args.out).note(**{k: v for k, v in results.items()
                                       if not isinstance(v, (dict, list))})

        print(f"\n{n:,} records judged of {sample.height:,} in the sample "
              f"({results['coverage_of_sample']:.1%})\n")
        print(f"  record accuracy       {results['record_accuracy']:.1%}  "
              f"(95% CI {low:.1%} to {high:.1%})")
        print(f"  component precision   {results['component_precision']:.1%}  "
              f"(95% CI {comp_low:.1%} to {comp_high:.1%})   "
              f"{n_bad:,} of {n_components:,} flagged")
        print(f"  reweighted by outcome {reweighted:.1%}   <- the archive-wide estimate")
        print(f"\n  the raw figure is NOT archive-wide: the sample deliberately "
              f"over-samples hard cases.")

        if error_counts:
            print("\nerror tags (a record may carry several):")
            for tag, count in error_counts.most_common():
                print(f"  {tag:<16} {count:>5}  ({count / n:>5.1%} of judged records)")

        print("\naccuracy by outcome stratum:")
        for row in weighted.sort("n_audited", descending=True).iter_rows(named=True):
            print(f"  {str(row['outcome']):<26} {row['n_correct']:>4}/{row['n_audited']:<4} "
                  f"= {row['accuracy']:>5.1%}   (population {row['n_population'] or 0:,})")

        print("\naccuracy by leading precipitant class:")
        for row in (framed.group_by("leading_class").agg(
                pl.len().alias("n"), (pl.col("verdict") == "correct").sum().alias("ok"))
                .sort("n", descending=True).iter_rows(named=True)):
            print(f"  {str(row['leading_class']):<12} {row['ok']:>4}/{row['n']:<4} "
                  f"= {row['ok'] / row['n']:>5.1%}")

        print("\naccuracy by era:")
        for row in (framed.group_by("era").agg(
                pl.len().alias("n"), (pl.col("verdict") == "correct").sum().alias("ok"))
                .sort("era").iter_rows(named=True)):
            print(f"  {str(row['era']):<12} {row['ok']:>4}/{row['n']:<4} "
                  f"= {row['ok'] / row['n']:>5.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
