"""Stage `eval.audit_decisions`: build a decision-level audit worklist.

The first cut of WP8 asked for a verdict on 1,484 individual records. That was the wrong
unit of work. This corpus is enormously redundant, so judging "20% peg 3350 becomes PEG_3350
at 20% w/v" for the thirty-thousandth time buys nothing.

Measured on the parsed corpus:

  unit inference   fired 187,390 times, from **19 distinct rules**
  reagent naming   47,251 distinct mappings, of which **28 cover half** the component mass
                   and **209 cover three quarters**

So the same evidential coverage costs roughly 250 judgements instead of 1,484, and each one
arrives with the number of components it governs, which a record-by-record pass never shows.

Three kinds of decision are emitted, each carrying its corpus weight:

  unit_rule   "a PEG of 1000 or more, written as a bare %, means w/v". 19 of these.
  mapping     "this raw string becomes this canonical reagent". Ranked by frequency.
  unmapped    "this raw string resolved to nothing". The same worklist as the lexicon
              curation, so one pass through the interface does both jobs.

Structural errors (clause splitting, a missed component, pH attribution) are invisible at
this level and still need records. They get a much smaller sample, drawn separately.

    ./run.sh eval.audit_decisions
    ./run.sh eval.audit_decisions --mappings 600 --records 200
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

import polars as pl

from .. import config
from ..manifest import Manifest

STAGE = "eval.audit_decisions"

DEFAULT_MAPPINGS = 400
DEFAULT_UNMAPPED = 300
DEFAULT_RECORDS = 200
EXAMPLES_PER_DECISION = 3


def unit_rule_expression() -> pl.Expr:
    """The rule that produced an inferred unit, as a label.

    PEG is split at 600 because that is where the chemistry changes: below it a PEG behaves
    as an organic precipitant and is reported v/v, above it as a polymer reported w/v.
    """
    return (pl.when(pl.col("chem_class") == "peg")
            .then(pl.when(pl.col("peg_mw") <= 600)
                  .then(pl.lit("peg <= 600"))
                  .otherwise(pl.lit("peg >= 1000")))
            .otherwise(pl.col("chem_class").fill_null("unknown chemistry"))
            .alias("rule"))


def examples_for(components: pl.DataFrame, conditions: pl.DataFrame,
                 predicate: pl.Expr, limit: int) -> list[dict[str, Any]]:
    rows = (components.filter(predicate)
            .join(conditions.select("pdb_id", "crystal_id", "raw_details"),
                  on=["pdb_id", "crystal_id"], how="left")
            .head(limit))
    return [{
        "pdb_id": r["pdb_id"],
        "raw_details": (r["raw_details"] or "")[:240],
        "name_raw": r["name_raw"],
        "concentration": r["concentration"],
        "unit": r["unit"],
        "unit_inferred": r["unit_inferred"],
        "role": r["role"],
    } for r in rows.iter_rows(named=True)]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--conditions", type=Path,
                        default=config.INTERIM_DIR / "parsed_conditions.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "audit_decisions.json")
    parser.add_argument("--mappings", type=int, default=DEFAULT_MAPPINGS)
    parser.add_argument("--unmapped", type=int, default=DEFAULT_UNMAPPED)
    parser.add_argument("--records", type=int, default=DEFAULT_RECORDS)
    parser.add_argument("--seed", type=int, default=20260729)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    components = pl.read_parquet(args.components)
    conditions = pl.read_parquet(args.conditions)
    total_components = components.height

    with Manifest(STAGE, params={"mappings": args.mappings, "unmapped": args.unmapped,
                                 "records": args.records}) as m:
        m.add_input(args.components).add_input(args.conditions)

        # --- unit inference rules ---------------------------------------
        inferred = components.filter(pl.col("unit_inferred")).with_columns(unit_rule_expression())
        rule_table = (inferred.group_by(["rule", "unit"]).agg(pl.len().alias("n"))
                      .sort("n", descending=True))
        n_inferred = inferred.height
        unit_rules = []
        for row in rule_table.iter_rows(named=True):
            unit_rules.append({
                "id": f"unit::{row['rule']}::{row['unit']}",
                "rule": row["rule"], "unit": row["unit"],
                "n_components": row["n"],
                "share_of_inferred": round(row["n"] / max(1, n_inferred), 4),
                "examples": examples_for(
                    inferred, conditions,
                    (pl.col("rule") == row["rule"]) & (pl.col("unit") == row["unit"]),
                    EXAMPLES_PER_DECISION),
            })

        # --- reagent mappings -------------------------------------------
        resolved = components.filter(pl.col("name_canonical").is_not_null())
        mapping_table = (resolved.group_by(["name_raw", "name_canonical", "chem_class"])
                         .agg(pl.len().alias("n")).sort("n", descending=True))
        mapping_total = int(mapping_table["n"].sum())
        mapping_table = mapping_table.with_columns(
            (pl.col("n").cum_sum() / mapping_total).alias("cum"))
        mappings = []
        for row in mapping_table.head(args.mappings).iter_rows(named=True):
            predicate = ((pl.col("name_raw") == row["name_raw"])
                         & (pl.col("name_canonical") == row["name_canonical"]))
            units = (resolved.filter(predicate).group_by("unit").agg(pl.len().alias("n"))
                     .sort("n", descending=True).head(3))
            mappings.append({
                "id": f"map::{row['name_raw']}::{row['name_canonical']}",
                "name_raw": row["name_raw"],
                "name_canonical": row["name_canonical"],
                "chem_class": row["chem_class"],
                "n_components": row["n"],
                "share_of_components": round(row["n"] / max(1, total_components), 5),
                "cumulative_coverage": round(row["cum"], 4),
                "units": [{"unit": u["unit"], "n": u["n"]} for u in units.iter_rows(named=True)],
                "examples": examples_for(resolved, conditions, predicate, EXAMPLES_PER_DECISION),
            })

        # --- unmapped strings (the lexicon worklist, same interface) -----
        unresolved = components.filter(pl.col("name_canonical").is_null())
        unmapped_table = (unresolved.group_by("name_raw").agg(pl.len().alias("n"))
                          .sort("n", descending=True))
        unresolved_total = int(unmapped_table["n"].sum())
        unmapped = []
        for row in unmapped_table.head(args.unmapped).iter_rows(named=True):
            unmapped.append({
                "id": f"unmapped::{row['name_raw']}",
                "name_raw": row["name_raw"],
                "n_components": row["n"],
                "share_of_unresolved": round(row["n"] / max(1, unresolved_total), 5),
                "examples": examples_for(unresolved, conditions,
                                         pl.col("name_raw") == row["name_raw"],
                                         EXAMPLES_PER_DECISION),
            })

        # --- structural sample -------------------------------------------
        # Clause splitting, missed components and pH attribution are invisible at the
        # decision level, so a small record sample still earns its place. It is drawn from
        # records with several components, where structural mistakes actually show up.
        structural = (conditions.filter(pl.col("discard_reason").is_null()
                                        & (pl.col("n_components") >= 2))
                      .sample(n=min(args.records,
                                    conditions.filter(pl.col("discard_reason").is_null()
                                                      & (pl.col("n_components") >= 2)).height),
                              seed=args.seed, shuffle=True))
        keys = set(zip(structural["pdb_id"], structural["crystal_id"]))
        by_record: dict[tuple, list[dict[str, Any]]] = {}
        for row in components.filter(
                pl.struct(["pdb_id", "crystal_id"]).map_elements(
                    lambda s: (s["pdb_id"], s["crystal_id"]) in keys,
                    return_dtype=pl.Boolean)).iter_rows(named=True):
            by_record.setdefault((row["pdb_id"], row["crystal_id"]), []).append({
                "role": row["role"], "name_raw": row["name_raw"],
                "name_canonical": row["name_canonical"],
                "concentration": row["concentration"], "unit": row["unit"],
                "unit_inferred": row["unit_inferred"],
                "cryo_evidence": row["cryo_evidence"],
            })
        records = [{
            "id": f"rec::{r['pdb_id']}::{r['crystal_id']}",
            "pdb_id": r["pdb_id"], "crystal_id": r["crystal_id"],
            "raw_details": r["raw_details"], "ph": r["ph"], "ph_source": r["ph_source"],
            "temperature_k": r["temperature_k"],
            "parse_confidence": r["parse_confidence"],
            "components": by_record.get((r["pdb_id"], r["crystal_id"]), []),
        } for r in structural.iter_rows(named=True)]

        covered = sum(d["n_components"] for d in mappings)
        payload = {
            "schema_version": config.SCHEMA_VERSION,
            "seed": args.seed,
            "totals": {
                "n_components": total_components,
                "n_inferred_units": n_inferred,
                "n_unresolved_components": unresolved.height,
            },
            "unit_rules": unit_rules,
            "mappings": mappings,
            "unmapped": unmapped,
            "records": records,
        }
        args.out.write_text(json.dumps(payload, indent=1))

        stats = {
            "n_unit_rules": len(unit_rules),
            "n_mappings": len(mappings),
            "n_unmapped": len(unmapped),
            "n_records": len(records),
            "n_decisions_total": len(unit_rules) + len(mappings) + len(unmapped) + len(records),
            "mapping_component_coverage": round(covered / max(1, total_components), 4),
            "unit_rule_coverage": 1.0,
            "json_bytes": args.out.stat().st_size,
        }
        m.add_output(args.out).note(**stats)

        for key, value in stats.items():
            print(f"  {key:<30} {value:>12,}")
        print(f"\n{stats['n_decisions_total']:,} decisions replace a 1,484-record pass.")
        print(f"  {len(unit_rules)} unit rules govern all {n_inferred:,} inferred units")
        print(f"  {len(mappings)} mappings cover {stats['mapping_component_coverage']:.1%} "
              f"of all {total_components:,} components")
        print(f"  {len(unmapped)} unmapped strings cover "
              f"{sum(d['share_of_unresolved'] for d in unmapped):.1%} of the unresolved mass")
        print(f"  {len(records)} multi-component records for structural errors only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
