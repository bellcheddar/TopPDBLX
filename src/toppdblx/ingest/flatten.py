"""Stage `ingest.flatten`: raw GraphQL batches to two Parquet tables.

    entries.parquet    one row per (pdb_id, crystal_id): the crystallisation fields
    entities.parquet   one row per (pdb_id, entity_id): the polymer sequences

Two shapes of the data are easy to get wrong here, so both are handled explicitly:

1. `exptl_crystal_grow` is a **loop**. Roughly 0.07% of entries (about 140 archive-wide)
   describe several crystal forms in several rows, with genuinely different conditions:
   1Q8I carries three at pH 5.3, 5.5 and 5.8. Taking row 0 would discard the rest silently,
   so every row becomes its own record and the key is (pdb_id, crystal_id).

2. Entries with **no** grow rows still get a row, with `n_crystal_forms = 0` and null
   details. The alternative, dropping them here, would make it impossible for WP3 to
   account for every X-ray entry with a discard reason, which the release requires.

    ./run.sh ingest.flatten
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterator, Optional

import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest
from .fetch_entries import read_batch

STAGE = "ingest.flatten"

ENTRIES_SCHEMA = {
    "pdb_id": pl.Utf8,
    "crystal_id": pl.Utf8,
    "n_crystal_forms": pl.UInt8,
    "is_xray": pl.Boolean,
    "experimental_method": pl.Utf8,
    "exptl_methods": pl.List(pl.Utf8),
    "grow_method": pl.Utf8,
    "temp_k": pl.Float64,
    "temp_details": pl.Utf8,
    "ph_reported": pl.Float64,
    "ph_range": pl.Utf8,
    "raw_details": pl.Utf8,
    "grow_details": pl.Utf8,
    "resolution_a": pl.Float64,
    "polymer_entity_count": pl.UInt16,
    "deposit_date": pl.Utf8,
    "initial_release_date": pl.Utf8,
    "revision_date": pl.Utf8,
    "major_revision": pl.Int32,
    "minor_revision": pl.Int32,
}

ENTITIES_SCHEMA = {
    "pdb_id": pl.Utf8,
    "entity_id": pl.Utf8,
    "is_xray": pl.Boolean,
    "poly_type": pl.Utf8,
    "seq": pl.Utf8,
    "seq_can": pl.Utf8,
    "seq_length": pl.UInt32,
    "strand_ids": pl.Utf8,
    "description": pl.Utf8,
    "formula_weight": pl.Float64,
    "uniprot_ids": pl.List(pl.Utf8),
    "asym_ids": pl.List(pl.Utf8),
    "auth_asym_ids": pl.List(pl.Utf8),
    "source_organisms": pl.List(pl.Utf8),
    "source_taxids": pl.List(pl.Int64),
}

XRAY = "X-RAY DIFFRACTION"


def _first_resolution(entry_info: Optional[dict[str, Any]]) -> Optional[float]:
    values = (entry_info or {}).get("resolution_combined") or []
    return float(values[0]) if values else None


def _as_float(value: Any) -> Optional[float]:
    """The API returns numbers, but a stray string here would poison the column type."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def entry_rows(entry: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield one row per crystal form, or a single empty row when there are none."""
    pdb_id = entry["rcsb_id"]
    methods = [m.get("method") for m in (entry.get("exptl") or []) if m.get("method")]
    is_xray = XRAY in methods
    info = entry.get("rcsb_entry_info") or {}
    acc = entry.get("rcsb_accession_info") or {}
    grow_rows = entry.get("exptl_crystal_grow") or []

    common = {
        "pdb_id": pdb_id,
        "n_crystal_forms": len(grow_rows),
        "is_xray": is_xray,
        "experimental_method": info.get("experimental_method"),
        "exptl_methods": methods,
        "resolution_a": _first_resolution(info),
        "polymer_entity_count": info.get("polymer_entity_count"),
        "deposit_date": acc.get("deposit_date"),
        "initial_release_date": acc.get("initial_release_date"),
        "revision_date": acc.get("revision_date"),
        "major_revision": acc.get("major_revision"),
        "minor_revision": acc.get("minor_revision"),
    }

    if not grow_rows:
        yield {
            **common,
            "crystal_id": "1",
            "grow_method": None, "temp_k": None, "temp_details": None,
            "ph_reported": None, "ph_range": None,
            "raw_details": None, "grow_details": None,
        }
        return

    for position, row in enumerate(grow_rows, start=1):
        # crystal_id is occasionally absent even when the row exists; fall back to the
        # row's position so the key stays unique and stable.
        crystal_id = row.get("crystal_id") or str(position)
        yield {
            **common,
            "crystal_id": str(crystal_id),
            "grow_method": row.get("method"),
            "temp_k": _as_float(row.get("temp")),
            "temp_details": row.get("temp_details"),
            "ph_reported": _as_float(row.get("pH")),
            "ph_range": row.get("pdbx_pH_range"),
            "raw_details": row.get("pdbx_details"),
            "grow_details": row.get("details"),
        }


def entity_rows(entry: dict[str, Any]) -> Iterator[dict[str, Any]]:
    pdb_id = entry["rcsb_id"]
    methods = [m.get("method") for m in (entry.get("exptl") or []) if m.get("method")]
    is_xray = XRAY in methods
    for entity in entry.get("polymer_entities") or []:
        poly = entity.get("entity_poly") or {}
        meta = entity.get("rcsb_polymer_entity") or {}
        ids = entity.get("rcsb_polymer_entity_container_identifiers") or {}
        organisms = entity.get("rcsb_entity_source_organism") or []
        yield {
            "pdb_id": pdb_id,
            "entity_id": entity.get("rcsb_id"),
            "is_xray": is_xray,
            "poly_type": poly.get("type"),
            # Both sequences are kept: the raw form carries modified residues in
            # parentheses (about 18% of entities differ), the canonical form is what
            # MMseqs2 and the models consume.
            "seq": poly.get("pdbx_seq_one_letter_code"),
            "seq_can": poly.get("pdbx_seq_one_letter_code_can"),
            "seq_length": poly.get("rcsb_sample_sequence_length"),
            "strand_ids": poly.get("pdbx_strand_id"),
            "description": meta.get("pdbx_description"),
            "formula_weight": _as_float(meta.get("formula_weight")),
            "uniprot_ids": ids.get("uniprot_ids") or [],
            "asym_ids": ids.get("asym_ids") or [],
            "auth_asym_ids": ids.get("auth_asym_ids") or [],
            "source_organisms": [o.get("ncbi_scientific_name") for o in organisms
                                 if o.get("ncbi_scientific_name")],
            "source_taxids": [o["ncbi_taxonomy_id"] for o in organisms
                              if o.get("ncbi_taxonomy_id") is not None],
        }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", type=Path, default=config.RAW_GRAPHQL_DIR)
    parser.add_argument("--out-dir", type=Path, default=config.INTERIM_DIR)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    batch_files = sorted(args.raw_dir.glob("*.json.gz"))
    if not batch_files:
        raise FileNotFoundError(
            f"no batches in {args.raw_dir}. Run: ./run.sh ingest.fetch_entries"
        )

    entries_out = args.out_dir / "entries.parquet"
    entities_out = args.out_dir / "entities.parquet"

    with Manifest(STAGE, params={"n_batch_files": len(batch_files)}) as m:
        m.add_input(args.raw_dir)

        entry_records: list[dict[str, Any]] = []
        entity_records: list[dict[str, Any]] = []
        seen: set[str] = set()
        duplicates: list[str] = []

        for path in tqdm(batch_files, desc="batches", unit="file"):
            for entry in read_batch(path):
                pdb_id = entry["rcsb_id"]
                if pdb_id in seen:
                    duplicates.append(pdb_id)
                    continue
                seen.add(pdb_id)
                entry_records.extend(entry_rows(entry))
                entity_records.extend(entity_rows(entry))

        entries = pl.DataFrame(entry_records, schema=ENTRIES_SCHEMA)
        entities = pl.DataFrame(entity_records, schema=ENTITIES_SCHEMA)

        # The key must be unique or every downstream join silently fans out.
        n_unique = entries.select(["pdb_id", "crystal_id"]).unique().height
        if n_unique != entries.height:
            raise RuntimeError(
                f"(pdb_id, crystal_id) is not unique: {entries.height} rows, {n_unique} keys"
            )

        entries.write_parquet(entries_out, compression="zstd")
        entities.write_parquet(entities_out, compression="zstd")

        xray = entries.filter(pl.col("is_xray"))
        stats = {
            "n_entries": len(seen),
            "n_duplicate_entries_skipped": len(duplicates),
            "n_condition_rows": entries.height,
            "n_xray_rows": xray.height,
            "n_xray_with_details": xray.filter(
                pl.col("raw_details").is_not_null()
                & (pl.col("raw_details").str.strip_chars() != "")
            ).height,
            "n_entries_no_grow_row": entries.filter(pl.col("n_crystal_forms") == 0).height,
            "n_multi_form_entries": entries.filter(pl.col("n_crystal_forms") > 1)
                                           .select("pdb_id").unique().height,
            "n_entities": entities.height,
            "n_entities_with_uniprot": entities.filter(
                pl.col("uniprot_ids").list.len() > 0).height,
        }
        m.add_output(entries_out).add_output(entities_out).note(**stats)

        width = max(len(k) for k in stats)
        for key, value in stats.items():
            print(f"  {key:<{width}}  {value:>9,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
