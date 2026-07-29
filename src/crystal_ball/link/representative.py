"""Stage `link.representative`: choose one sequence per entry and record the complex.

Spec 7.2 fixes the policy for multi-entity crystals: label the condition with the **largest
polymer entity**, carry an `is_complex` flag and the full entity list, and **never duplicate
the record per chain**. Duplicating would let a ribosome contribute fifty copies of one
condition and quietly dominate every downstream statistic.

Two representatives are recorded, not one:

  representative_entity   the largest polymer entity of any type, exactly as the spec says
  protein_entity          the largest polypeptide entity

They differ for protein/nucleic-acid complexes where the nucleic acid is longer. The second
is what MMseqs2 clusters, because protein clustering against an RNA chain is meaningless;
the first is what the spec asks the record to be labelled with. Keeping both costs one
column and avoids having to choose between correctness and the spec's wording.

    ./run.sh link.representative
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Optional

import polars as pl

from .. import config
from ..manifest import Manifest

STAGE = "link.representative"

PROTEIN_TYPES = ("polypeptide(L)", "polypeptide(D)")


def sequence_id(sequence: str) -> str:
    """Stable short id for a sequence, so identical sequences share a cluster entry."""
    return hashlib.sha1(sequence.encode()).hexdigest()[:16]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entities", type=Path, default=config.INTERIM_DIR / "entities.parquet")
    parser.add_argument("--out", type=Path, default=config.INTERIM_DIR / "entry_sequence.parquet")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    entities = pl.read_parquet(args.entities).filter(
        pl.col("seq_can").is_not_null() & (pl.col("seq_can").str.len_chars() > 0)
    )

    with Manifest(STAGE, params={"n_entities": entities.height}) as m:
        m.add_input(args.entities)

        # Deterministic ordering: longest first, then entity id, so the choice of
        # representative never depends on row order in the parquet file.
        ordered = entities.sort(["pdb_id", "seq_length", "entity_id"],
                                descending=[False, True, False])

        per_entry = ordered.group_by("pdb_id", maintain_order=True).agg(
            pl.col("entity_id").alias("entity_ids"),
            pl.col("entity_id").first().alias("representative_entity_id"),
            pl.col("poly_type").first().alias("representative_type"),
            pl.col("seq_length").first().alias("representative_length"),
            pl.len().alias("n_polymer_entities"),
            pl.col("is_xray").first().alias("is_xray"),
        )

        proteins = ordered.filter(pl.col("poly_type").is_in(PROTEIN_TYPES))
        per_entry_protein = proteins.group_by("pdb_id", maintain_order=True).agg(
            pl.col("entity_id").first().alias("protein_entity_id"),
            pl.col("seq_can").first().alias("protein_seq"),
            pl.col("seq_length").first().alias("protein_length"),
            pl.col("uniprot_ids").first().alias("protein_uniprot_ids"),
            pl.col("description").first().alias("protein_description"),
            pl.len().alias("n_protein_entities"),
        )

        linked = (per_entry.join(per_entry_protein, on="pdb_id", how="left")
                  .with_columns(
                      (pl.col("n_polymer_entities") > 1).alias("is_complex"),
                      pl.col("n_protein_entities").fill_null(0),
                      pl.col("protein_seq").map_elements(
                          lambda s: sequence_id(s) if s else None,
                          return_dtype=pl.Utf8).alias("seq_id"),
                  ))

        linked.write_parquet(args.out, compression="zstd")

        stats = {
            "n_entries": linked.height,
            "n_with_protein": linked.filter(pl.col("protein_seq").is_not_null()).height,
            "n_complexes": linked.filter(pl.col("is_complex")).height,
            "n_distinct_protein_sequences": linked["seq_id"].drop_nulls().n_unique(),
            "n_representative_not_protein": linked.filter(
                pl.col("protein_entity_id").is_not_null()
                & (pl.col("representative_entity_id") != pl.col("protein_entity_id"))
            ).height,
        }
        m.add_output(args.out).note(**stats)

        for key, value in stats.items():
            print(f"  {key:<32} {value:>9,}")

        print("\nrepresentative entity type:")
        for row in (linked.group_by("representative_type").agg(pl.len().alias("n"))
                    .sort("n", descending=True).head(6).iter_rows(named=True)):
            print(f"  {str(row['representative_type']):<52} {row['n']:>8,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
