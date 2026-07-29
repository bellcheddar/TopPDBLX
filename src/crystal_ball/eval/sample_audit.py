"""Stage `eval.sample_audit`: draw the stratified hand-audit sample.

Spec 6.6 and 10: do not hand-curate 130,000 entries. Hand-audit a stratified sample of 1,000
to 2,000 and get a real per-class accuracy number from it.

This sample is the **only** defensible source of parse accuracy in the whole project, for a
reason worth restating: the WP6 language model will be trained on labels bootstrapped from
the rule parser, so measuring it against rule-derived labels would be circular. This sample
is hand-labelled from raw text, held out, and must never be used for training or for tuning
a threshold.

Stratification is deliberate rather than uniform. A uniform draw would be ~90% high-confidence
PEG-and-salt records, and would say almost nothing about the cases that actually fail. The
strata are:

  leading precipitant class   peg, salt, organic, polyol, premix, other, none
  release era                 pre-2000, 2000s, 2010s, 2020s
  confidence band             perfect, high, medium, low
  outcome                     kept or discarded (with the discard reason)

Every stratum with any population gets at least `--floor` records, so the rare and awkward
cells are represented at all; the remainder is allocated proportionally.

    ./run.sh eval.sample_audit
    ./run.sh eval.sample_audit --size 300 --seed 7
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

import polars as pl

from .. import config
from ..manifest import Manifest

STAGE = "eval.sample_audit"

DEFAULT_SIZE = 1500
DEFAULT_FLOOR = 3
SEED = 20260729

CLASS_PRIORITY = ["peg", "premix", "organic", "polyol", "salt", "buffer",
                  "detergent", "additive", "other"]


def confidence_band(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    if value >= 0.999:
        return "perfect"
    if value >= 0.75:
        return "high"
    if value >= 0.5:
        return "medium"
    return "low"


def era(date: Optional[str]) -> str:
    if not date:
        return "unknown"
    year = int(date[:4])
    if year < 2000:
        return "pre-2000"
    if year < 2010:
        return "2000s"
    if year < 2020:
        return "2010s"
    return "2020s"


def allocate(counts: dict[tuple, int], size: int, floor: int) -> dict[tuple, int]:
    """Give every populated stratum a floor, then share the rest proportionally."""
    allocation = {key: min(floor, n) for key, n in counts.items()}
    remaining = size - sum(allocation.values())
    if remaining <= 0:
        return allocation
    headroom = {k: counts[k] - allocation[k] for k in counts}
    total_headroom = sum(headroom.values())
    if total_headroom == 0:
        return allocation
    for key, spare in headroom.items():
        allocation[key] += int(remaining * spare / total_headroom)
    return allocation


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--conditions", type=Path,
                        default=config.INTERIM_DIR / "parsed_conditions.parquet")
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--matches", type=Path,
                        default=config.INTERIM_DIR / "screen_matches.parquet")
    parser.add_argument("--out-json", type=Path,
                        default=config.INTERIM_DIR / "audit_sample.json")
    parser.add_argument("--out-parquet", type=Path,
                        default=config.INTERIM_DIR / "audit_sample.parquet")
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    parser.add_argument("--floor", type=int, default=DEFAULT_FLOOR)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    conditions = pl.read_parquet(args.conditions)
    components = pl.read_parquet(args.components)

    # Era must come from the *release* date, not `entry_version`, which is the latest
    # revision. An entry deposited in 1997 and revised in 2021 would otherwise be
    # stratified as a 2020s record, and era exists as a stratum precisely because older
    # depositions have sparser, less structured text.
    release = (pl.read_parquet(config.INTERIM_DIR / "entries.parquet")
               .select("pdb_id", "initial_release_date").unique(subset=["pdb_id"]))
    conditions = conditions.join(release, on="pdb_id", how="left")

    # Leading precipitant class: the first class present in priority order, so a
    # PEG-and-salt record is stratified as PEG rather than by whichever row came first.
    class_rank = pl.DataFrame({"chem_class": CLASS_PRIORITY,
                               "rank": list(range(len(CLASS_PRIORITY)))})
    leading = (components.filter(pl.col("chem_class").is_not_null())
               .join(class_rank, on="chem_class", how="inner")
               .sort("rank")
               .group_by(["pdb_id", "crystal_id"], maintain_order=True)
               .agg(pl.col("chem_class").first().alias("leading_class")))

    framed = (conditions
              .join(leading, on=["pdb_id", "crystal_id"], how="left")
              .with_columns(
                  pl.col("leading_class").fill_null("none"),
                  pl.col("parse_confidence").map_elements(
                      confidence_band, return_dtype=pl.Utf8).alias("confidence_band"),
                  pl.col("initial_release_date").map_elements(era, return_dtype=pl.Utf8).alias("era"),
                  pl.when(pl.col("discard_reason").is_null()).then(pl.lit("kept"))
                    .otherwise(pl.col("discard_reason")).alias("outcome"),
              ))

    strata_columns = ["leading_class", "era", "confidence_band", "outcome"]

    with Manifest(STAGE, params={"size": args.size, "floor": args.floor,
                                 "seed": args.seed, "strata": strata_columns}) as m:
        m.add_input(args.conditions).add_input(args.components)

        counts = {tuple(row[c] for c in strata_columns): row["n"]
                  for row in framed.group_by(strata_columns).agg(
                      pl.len().alias("n")).iter_rows(named=True)}
        allocation = allocate(counts, args.size, args.floor)

        picked = []
        for key, take in sorted(allocation.items(), key=lambda kv: str(kv[0])):
            if take <= 0:
                continue
            predicate = pl.lit(True)
            for column, value in zip(strata_columns, key):
                predicate = predicate & (pl.col(column) == value)
            # Seeded per stratum so adding a stratum does not reshuffle the others.
            stratum = framed.filter(predicate).sample(
                n=min(take, counts[key]), seed=args.seed, shuffle=True)
            picked.append(stratum)

        sample = pl.concat(picked).sort(["pdb_id", "crystal_id"])
        sample.write_parquet(args.out_parquet, compression="zstd")

        # The JSON the audit tool loads: self-contained, one object per record.
        keys = set(zip(sample["pdb_id"], sample["crystal_id"]))
        relevant = components.filter(
            pl.struct(["pdb_id", "crystal_id"]).map_elements(
                lambda s: (s["pdb_id"], s["crystal_id"]) in keys, return_dtype=pl.Boolean))
        by_record: dict[tuple, list[dict[str, Any]]] = {}
        for row in relevant.iter_rows(named=True):
            by_record.setdefault((row["pdb_id"], row["crystal_id"]), []).append({
                "role": row["role"], "name_raw": row["name_raw"],
                "name_canonical": row["name_canonical"], "chem_class": row["chem_class"],
                "concentration": row["concentration"], "unit": row["unit"],
                "unit_inferred": row["unit_inferred"],
                "concentration_is_range": row["concentration_is_range"],
                "cryo_evidence": row["cryo_evidence"],
            })

        matches: dict[tuple, dict[str, Any]] = {}
        if args.matches.exists():
            for row in pl.read_parquet(args.matches).iter_rows(named=True):
                key = (row["pdb_id"], row["crystal_id"])
                if key in keys:
                    matches[key] = {"screen": row["screen"], "well": row["well"],
                                    "match_type": row["match_type"]}

        records = []
        for row in sample.iter_rows(named=True):
            key = (row["pdb_id"], row["crystal_id"])
            records.append({
                "pdb_id": row["pdb_id"], "crystal_id": row["crystal_id"],
                "raw_details": row["raw_details"],
                "ph": row["ph"], "ph_source": row["ph_source"],
                "ph_reported": row["ph_reported"],
                "temperature_k": row["temperature_k"],
                "method": row["method"],
                "parse_confidence": row["parse_confidence"],
                "discard_reason": row["discard_reason"],
                "flags": row["flags"],
                "stratum": {c: row[c] for c in strata_columns},
                "components": by_record.get(key, []),
                "screen_match": matches.get(key),
            })

        payload = {
            "generated": config.DATASET_VERSION,
            "schema_version": config.SCHEMA_VERSION,
            "seed": args.seed,
            "n_records": len(records),
            "strata_columns": strata_columns,
            "records": records,
        }
        args.out_json.write_text(json.dumps(payload, indent=1))

        stats = {
            "n_sampled": sample.height,
            "n_strata_populated": len(counts),
            "n_strata_sampled": sum(1 for v in allocation.values() if v > 0),
            "n_components_in_sample": relevant.height,
            "n_with_screen_match": len(matches),
            "json_bytes": args.out_json.stat().st_size,
        }
        m.add_output(args.out_json).add_output(args.out_parquet).note(**stats)

        for key, value in stats.items():
            print(f"  {key:<26} {value:>10,}")

        print("\nsample composition by leading precipitant class:")
        for row in (sample.group_by("leading_class").agg(pl.len().alias("n"))
                    .sort("n", descending=True).iter_rows(named=True)):
            print(f"  {row['leading_class']:<12} {row['n']:>5}")
        print("\nby outcome:")
        for row in (sample.group_by("outcome").agg(pl.len().alias("n"))
                    .sort("n", descending=True).iter_rows(named=True)):
            print(f"  {row['outcome']:<26} {row['n']:>5}")
        print("\nby confidence band:")
        for row in (sample.group_by("confidence_band").agg(pl.len().alias("n"))
                    .sort("n", descending=True).iter_rows(named=True)):
            print(f"  {row['confidence_band']:<12} {row['n']:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
