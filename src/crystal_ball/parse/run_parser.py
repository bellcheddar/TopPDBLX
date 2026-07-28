"""Stage `parse.run_parser`: run the rule parser over the whole corpus.

Writes two tables rather than one nested structure, because the two grains get asked
different questions:

    parsed_conditions.parquet   one row per (pdb_id, crystal_id): pH, temperature, method,
                                confidence, discard reason, flags
    parsed_components.parquet   one row per component: reagent, concentration, unit, role

Every X-ray record is accounted for in the first table: either it parsed, or it carries a
discard code. The discard distribution is a published result in its own right (spec 5.1).

    ./run.sh parse.run_parser
    ./run.sh parse.run_parser --limit 5000        # smoke test
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Optional

import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest
from .lexicon import load as load_lexicon
from .rules import PARSER_VERSION, RuleParser

STAGE = "parse.run_parser"

# Neutron, electron crystallography and powder diffraction entries describe real crystals
# grown from real conditions. They are admitted with the method recorded so they stay
# filterable; the EM, fibre and solution-scattering handful are excluded as mis-annotated.
CRYSTAL_METHODS = {
    "X-RAY DIFFRACTION", "NEUTRON DIFFRACTION",
    "ELECTRON CRYSTALLOGRAPHY", "POWDER DIFFRACTION",
}

CONDITION_SCHEMA = {
    "pdb_id": pl.Utf8, "crystal_id": pl.Utf8, "diffraction_method": pl.Utf8,
    "method": pl.Utf8, "temperature_k": pl.Float64, "temperature_source": pl.Utf8,
    "ph": pl.Float64, "ph_source": pl.Utf8, "ph_is_range": pl.Boolean,
    "ph_reported": pl.Float64, "protein_concentration_mg_ml": pl.Float64,
    "drop_ratio": pl.Utf8, "n_components": pl.UInt16, "n_components_resolved": pl.UInt16,
    "parse_confidence": pl.Float64, "discard_reason": pl.Utf8, "flags": pl.List(pl.Utf8),
    "parser": pl.Utf8, "entry_version": pl.Utf8, "raw_details": pl.Utf8,
}

COMPONENT_SCHEMA = {
    "pdb_id": pl.Utf8, "crystal_id": pl.Utf8, "component_index": pl.UInt16,
    "role": pl.Utf8, "name_raw": pl.Utf8, "name_canonical": pl.Utf8, "chem_class": pl.Utf8,
    "peg_mw": pl.Int32, "is_mme": pl.Boolean, "hofmeister_rank": pl.Int32,
    "buffer_pka": pl.Float64, "concentration": pl.Float64, "unit": pl.Utf8,
    "unit_inferred": pl.Boolean, "concentration_is_range": pl.Boolean,
    "range_low": pl.Float64, "range_high": pl.Float64, "cryo_evidence": pl.Utf8,
    "premix_id": pl.Utf8,
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entries", type=Path, default=config.INTERIM_DIR / "entries.parquet")
    parser.add_argument("--out-dir", type=Path, default=config.INTERIM_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--xray-only", action="store_true",
                        help="exclude neutron, electron crystallography and powder entries")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    lexicon = load_lexicon()
    engine = RuleParser(lexicon)

    entries = pl.read_parquet(args.entries)
    wanted = {"X-RAY DIFFRACTION"} if args.xray_only else CRYSTAL_METHODS
    rows = entries.filter(
        pl.col("exptl_methods").list.eval(pl.element().is_in(list(wanted))).list.any()
        & (pl.col("n_crystal_forms") > 0)
    )
    if args.limit:
        rows = rows.head(args.limit)

    conditions: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []

    with Manifest(STAGE, params={"parser": PARSER_VERSION, "n_rows": rows.height,
                                 "lexicon_version": lexicon.version,
                                 "xray_only": args.xray_only}) as m:
        m.add_input(args.entries).add_input(config.ONTOLOGY_DIR / "synonyms.yaml")

        for row in tqdm(rows.iter_rows(named=True), total=rows.height,
                        desc="records", unit="rec"):
            methods = row["exptl_methods"] or []
            record = engine.parse(
                row["pdb_id"], row["crystal_id"], row["raw_details"],
                method=row["grow_method"],
                diffraction_method=next((m_ for m_ in methods if m_ in wanted), None),
                temp_k_reported=row["temp_k"],
                ph_reported=row["ph_reported"],
                entry_version=row["revision_date"],
            )
            conditions.append({
                "pdb_id": record.pdb_id, "crystal_id": record.crystal_id,
                "diffraction_method": record.diffraction_method, "method": record.method,
                "temperature_k": record.temperature_k,
                "temperature_source": record.temperature_source,
                "ph": record.ph, "ph_source": record.ph_source,
                "ph_is_range": record.ph_is_range, "ph_reported": record.ph_reported,
                "protein_concentration_mg_ml": record.protein_concentration_mg_ml,
                "drop_ratio": record.drop_ratio,
                "n_components": len(record.components),
                "n_components_resolved": sum(1 for c in record.components if c.name_canonical),
                "parse_confidence": record.provenance.parse_confidence,
                "discard_reason": record.discard_reason,
                "flags": record.provenance.flags,
                "parser": record.provenance.parser,
                "entry_version": record.entry_version,
                "raw_details": record.raw_details,
            })
            for index, component in enumerate(record.components):
                low, high = component.concentration_range or (None, None)
                components.append({
                    "pdb_id": record.pdb_id, "crystal_id": record.crystal_id,
                    "component_index": index, "role": component.role,
                    "name_raw": component.name_raw[:200],
                    "name_canonical": component.name_canonical,
                    "chem_class": component.chem_class, "peg_mw": component.peg_mw,
                    "is_mme": component.is_mme,
                    "hofmeister_rank": component.hofmeister_rank,
                    "buffer_pka": component.buffer_pka,
                    "concentration": component.concentration, "unit": component.unit,
                    "unit_inferred": component.unit_inferred,
                    "concentration_is_range": component.concentration_is_range,
                    "range_low": low, "range_high": high,
                    "cryo_evidence": component.cryo_evidence,
                    "premix_id": component.premix_id,
                })

        cond_df = pl.DataFrame(conditions, schema=CONDITION_SCHEMA)
        comp_df = pl.DataFrame(components, schema=COMPONENT_SCHEMA)
        cond_out = args.out_dir / "parsed_conditions.parquet"
        comp_out = args.out_dir / "parsed_components.parquet"
        cond_df.write_parquet(cond_out, compression="zstd")
        comp_df.write_parquet(comp_out, compression="zstd")

        kept = cond_df.filter(pl.col("discard_reason").is_null())
        stats = {
            "n_records": cond_df.height,
            "n_kept": kept.height,
            "keep_rate": round(kept.height / max(1, cond_df.height), 4),
            "n_components": comp_df.height,
            "n_components_resolved": comp_df.filter(
                pl.col("name_canonical").is_not_null()).height,
            "component_resolution_rate": round(
                comp_df.filter(pl.col("name_canonical").is_not_null()).height
                / max(1, comp_df.height), 4),
            "median_confidence": float(kept["parse_confidence"].median() or 0),
            "n_unit_inferred": comp_df.filter(pl.col("unit_inferred")).height,
            "n_cryo_explicit": comp_df.filter(pl.col("cryo_evidence") == "explicit").height,
            "n_cryo_inferred": comp_df.filter(pl.col("cryo_evidence") == "inferred").height,
        }
        m.add_output(cond_out).add_output(comp_out).note(**stats)

        print(f"\n{cond_df.height:,} records parsed with {PARSER_VERSION}, "
              f"lexicon v{lexicon.version}")
        print(f"  kept:      {kept.height:>8,} ({stats['keep_rate']:.1%})")
        print(f"  median confidence (kept): {stats['median_confidence']:.3f}")

        print("\ndiscard reasons:")
        discards = (cond_df.filter(pl.col("discard_reason").is_not_null())
                    .group_by("discard_reason").agg(pl.len().alias("n"))
                    .sort("n", descending=True))
        for row in discards.iter_rows(named=True):
            print(f"  {row['discard_reason']:<26} {row['n']:>7,} "
                  f"({row['n'] / cond_df.height:>5.1%})")

        print(f"\ncomponents: {comp_df.height:,}, "
              f"{stats['component_resolution_rate']:.1%} resolved to a canonical reagent")
        print(f"  unit inferred:  {stats['n_unit_inferred']:>7,} "
              f"({stats['n_unit_inferred'] / max(1, comp_df.height):.1%} of components)")
        print(f"  cryo explicit:  {stats['n_cryo_explicit']:>7,}")
        print(f"  cryo inferred:  {stats['n_cryo_inferred']:>7,}")

        print("\nrole distribution:")
        for row in (comp_df.group_by("role").agg(pl.len().alias("n"))
                    .sort("n", descending=True).iter_rows(named=True)):
            print(f"  {row['role']:<12} {row['n']:>8,}")

        print("\npH source (kept records):")
        for row in (kept.group_by("ph_source").agg(pl.len().alias("n"))
                    .sort("n", descending=True).iter_rows(named=True)):
            print(f"  {row['ph_source']:<12} {row['n']:>8,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
