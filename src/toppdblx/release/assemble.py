"""Stage `release.assemble`: build the released database from the interim tables.

Joins the parsed conditions, their components, the sequence linkage and the commercial screen
matches into the Phase 0 deliverable, in four shapes because four different people ask four
different questions of it:

  conditions.jsonl.gz   the canonical form, one nested record per (pdb_id, crystal_id)
  conditions.parquet    record level, for anyone doing analysis in pandas or polars
  components.parquet    one row per reagent, which is the grain most queries actually want
  components.csv.gz     the same, for the large population who will open it in a spreadsheet
  toppdblx.duckdb    both tables plus views, for people who would rather write SQL

Every X-ray record appears exactly once, whether it parsed or was discarded. Discarded records
keep their raw text and their reason code, because the discard distribution is a published
result in its own right (spec 5.1) and because a user who disagrees with a discard needs the
evidence to argue with.

`curated_group` is present and null throughout: Phase 1 fills it, and shipping the field now
means that is a join rather than a schema migration.

    ./run.sh release.assemble
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any, Optional

import duckdb
import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest
from ..parse.schema import ConditionRecord

STAGE = "release.assemble"

DATASET_NAME = "toppdblx-conditions"


def build_records(conditions: pl.DataFrame, components: pl.DataFrame,
                  sequences: pl.DataFrame, matches: pl.DataFrame) -> list[dict[str, Any]]:
    by_record: dict[tuple, list[dict[str, Any]]] = {}
    for row in components.iter_rows(named=True):
        by_record.setdefault((row["pdb_id"], row["crystal_id"]), []).append({
            "role": row["role"],
            "name_raw": row["name_raw"],
            "name_canonical": row["name_canonical"],
            "chem_class": row["chem_class"],
            "peg_mw": row["peg_mw"],
            "is_mme": row["is_mme"],
            "hofmeister_rank": row["hofmeister_rank"],
            "buffer_pka": row["buffer_pka"],
            "concentration": row["concentration"],
            "unit": row["unit"],
            "unit_inferred": row["unit_inferred"],
            "concentration_is_range": row["concentration_is_range"],
            "concentration_range": ([row["range_low"], row["range_high"]]
                                    if row["concentration_is_range"] else None),
            "cryo_evidence": row["cryo_evidence"],
            "premix_id": row["premix_id"],
        })

    seq_by_entry = {r["pdb_id"]: r for r in sequences.iter_rows(named=True)}
    match_by_key = {(r["pdb_id"], r["crystal_id"]): r for r in matches.iter_rows(named=True)}

    records = []
    for row in tqdm(conditions.iter_rows(named=True), total=conditions.height,
                    desc="records", unit="rec"):
        key = (row["pdb_id"], row["crystal_id"])
        sequence = seq_by_entry.get(row["pdb_id"])
        match = match_by_key.get(key)
        records.append({
            "pdb_id": row["pdb_id"],
            "crystal_id": row["crystal_id"],
            "entry_version": row["entry_version"],
            "raw_details": row["raw_details"],
            "method": row["method"],
            "diffraction_method": row["diffraction_method"],
            "temperature_k": row["temperature_k"],
            "temperature_source": row["temperature_source"],
            "ph": row["ph"],
            "ph_source": row["ph_source"],
            "ph_is_range": row["ph_is_range"],
            "ph_reported": row["ph_reported"],
            "protein_concentration_mg_ml": row["protein_concentration_mg_ml"],
            "drop_ratio": row["drop_ratio"],
            "components": by_record.get(key, []),
            "sequence": None if not sequence else {
                "entity_id": sequence["protein_entity_id"],
                "seq": sequence["protein_seq"],
                "length": sequence["protein_length"],
                "description": sequence["protein_description"],
                "uniprot_ids": sequence["protein_uniprot_ids"],
                "is_complex": sequence["is_complex"],
                "n_polymer_entities": sequence["n_polymer_entities"],
                "seq_id": sequence["seq_id"],
                "cluster_30": sequence.get("cluster_30"),
                "cluster_50": sequence.get("cluster_50"),
                "cluster_90": sequence.get("cluster_90"),
            },
            "commercial_screen_match": None if not match else {
                "screen": match["screen"], "catalogue": match["catalogue"],
                "well": match["well"], "match_type": match["match_type"],
            },
            "curated_group": None,
            "provenance": {
                "parser": row["parser"],
                "parse_confidence": row["parse_confidence"],
                "flags": row["flags"],
            },
            "discard_reason": row["discard_reason"],
            "schema_version": config.SCHEMA_VERSION,
            "ontology_version": config.ONTOLOGY_VERSION,
            "dataset_version": config.DATASET_VERSION,
        })
    return records


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=config.PROCESSED_DIR)
    parser.add_argument("--version", default=config.DATASET_VERSION)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    interim = config.INTERIM_DIR

    conditions = pl.read_parquet(interim / "parsed_conditions.parquet")
    components = pl.read_parquet(interim / "parsed_components.parquet")
    sequences = (pl.read_parquet(interim / "entry_sequence.parquet")
                 .join(pl.read_parquet(interim / "sequence_clusters.parquet"),
                       on="seq_id", how="left"))
    matches = pl.read_parquet(interim / "screen_matches.parquet")

    stem = f"{DATASET_NAME}-v{args.version}"
    jsonl_path = args.out_dir / f"{stem}.jsonl.gz"
    cond_path = args.out_dir / f"{stem}.parquet"
    comp_stem = f"{DATASET_NAME.replace('conditions', 'components')}-v{args.version}"
    comp_path = args.out_dir / f"{comp_stem}.parquet"
    # Built from the stem, not by suffix surgery: a version like "0.1.0-dev" contains dots,
    # and with_suffix would treat "0-dev" as the extension and truncate the name.
    csv_path = args.out_dir / f"{comp_stem}.csv.gz"
    db_path = args.out_dir / "toppdblx.duckdb"
    schema_path = args.out_dir / f"schema-v{config.SCHEMA_VERSION}.json"

    with Manifest(STAGE, params={"version": args.version}) as m:
        for name in ("parsed_conditions", "parsed_components", "entry_sequence",
                     "sequence_clusters", "screen_matches"):
            m.add_input(interim / f"{name}.parquet")

        records = build_records(conditions, components, sequences, matches)

        # Canonical JSONL, gzipped. One record per line so it streams.
        with gzip.open(jsonl_path, "wt", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        # Record-level and component-level tables. The sequence linkage is flattened onto
        # the record table so the common questions need no join.
        flat = (conditions
                .join(sequences.select(
                    "pdb_id",
                    pl.col("protein_entity_id").alias("entity_id"),
                    pl.col("protein_length").alias("seq_length"),
                    pl.col("protein_description").alias("description"),
                    pl.col("protein_uniprot_ids").alias("uniprot_ids"),
                    "is_complex", "n_polymer_entities", "seq_id",
                    "cluster_30", "cluster_50", "cluster_90"),
                    on="pdb_id", how="left")
                .join(matches.select("pdb_id", "crystal_id",
                                     pl.col("screen").alias("screen_match_name"),
                                     pl.col("well").alias("screen_match_well"),
                                     pl.col("match_type").alias("screen_match_type")),
                      on=["pdb_id", "crystal_id"], how="left"))
        flat.write_parquet(cond_path, compression="zstd")
        components.write_parquet(comp_path, compression="zstd")

        with gzip.open(csv_path, "wt", encoding="utf-8") as handle:
            handle.write(components.write_csv())

        # DuckDB, with the join most users will want already expressed as a view.
        if db_path.exists():
            db_path.unlink()
        con = duckdb.connect(str(db_path))
        con.execute(f"CREATE TABLE conditions AS SELECT * FROM read_parquet('{cond_path}')")
        con.execute(f"CREATE TABLE components AS SELECT * FROM read_parquet('{comp_path}')")
        con.execute("""
            CREATE VIEW usable_conditions AS
            SELECT * FROM conditions WHERE discard_reason IS NULL
        """)
        con.execute("""
            CREATE VIEW condition_components AS
            SELECT c.pdb_id, c.crystal_id, c.ph, c.temperature_k, c.cluster_30, c.cluster_50,
                   k.role, k.name_canonical, k.chem_class, k.concentration, k.unit,
                   k.unit_inferred, k.cryo_evidence
            FROM conditions c JOIN components k
              ON c.pdb_id = k.pdb_id AND c.crystal_id = k.crystal_id
            WHERE c.discard_reason IS NULL
        """)
        con.close()

        # The frozen schema, generated from the pydantic model so it cannot drift from code.
        schema_path.write_text(json.dumps(ConditionRecord.model_json_schema(), indent=1))

        kept = conditions.filter(pl.col("discard_reason").is_null())
        linked = flat.filter(pl.col("seq_id").is_not_null())
        stats = {
            "dataset_version": args.version,
            "schema_version": config.SCHEMA_VERSION,
            "n_records": conditions.height,
            "n_kept": kept.height,
            "n_discarded": conditions.height - kept.height,
            "n_components": components.height,
            "n_with_sequence": linked.height,
            "n_with_cluster_30": flat.filter(pl.col("cluster_30").is_not_null()).height,
            "n_with_screen_match": flat.filter(pl.col("screen_match_name").is_not_null()).height,
            "n_distinct_entries": conditions["pdb_id"].n_unique(),
            "n_distinct_cluster_30": flat["cluster_30"].drop_nulls().n_unique(),
            "bytes_jsonl": jsonl_path.stat().st_size,
            "bytes_parquet": cond_path.stat().st_size,
            "bytes_duckdb": db_path.stat().st_size,
        }
        for path in (jsonl_path, cond_path, comp_path, csv_path, db_path, schema_path):
            m.add_output(path)
        m.note(**stats)

        print(f"\n{stem}")
        for key, value in stats.items():
            print(f"  {key:<24} {value:>14,}" if isinstance(value, int)
                  else f"  {key:<24} {value:>14}")
        print("\nfiles:")
        for path in (jsonl_path, cond_path, comp_path, csv_path, db_path, schema_path):
            print(f"  {path.name:<48} {path.stat().st_size / 1e6:>8.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
