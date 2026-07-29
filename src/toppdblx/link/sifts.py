"""Stage `link.sifts`: acquire the SIFTS PDB chain to UniProt mapping.

SIFTS is the authoritative residue-level correspondence between PDB chains and UniProt
entries. Phase 0 needs only the **segment-level** summary (`pdb_chain_uniprot.tsv.gz`),
which is a few tens of MB; the per-entry residue-level XML is a Phase 3 requirement for the
construct-boundary work and is deliberately not fetched here.

It is worth taking now anyway for the same reason as TargetTrack: it is an external
dependency that can move or change format, and the release must be able to state exactly
which version it was built against.

The RCSB API already gives a UniProt accession per polymer entity, so this is not the only
route to the mapping. It is the cross-check: where the two disagree, that is worth knowing
before the dataset is published rather than after.

    ./run.sh link.sifts
"""

from __future__ import annotations

import argparse
import gzip
from pathlib import Path
from typing import Optional

import polars as pl

from .. import config, http
from ..manifest import Manifest

STAGE = "link.sifts"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=config.RAW_SIFTS_DIR)
    parser.add_argument("--linked", type=Path,
                        default=config.INTERIM_DIR / "entry_sequence.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "sifts_uniprot.parquet")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    archive = args.out_dir / "pdb_chain_uniprot.tsv.gz"

    with Manifest(STAGE, params={"url": config.SIFTS_CHAIN_UNIPROT_URL}) as m:
        if http.download(config.SIFTS_CHAIN_UNIPROT_URL, archive,
                         skip_if_exists=True) is None:
            raise RuntimeError(f"SIFTS not available at {config.SIFTS_CHAIN_UNIPROT_URL}")
        print(f"{archive.name}: {archive.stat().st_size / 1e6:.1f} MB")

        # The file carries a comment line before the header, so it cannot be read directly.
        with gzip.open(archive, "rt") as handle:
            lines = handle.readlines()
        header_index = next(i for i, line in enumerate(lines) if line.startswith("PDB"))
        released = lines[0].strip().lstrip("# ")

        # Every column is read as text. PDB_BEG and PDB_END carry author residue numbers,
        # which include insertion codes such as "1H", so they are not integers despite
        # looking like them in most rows.
        table = pl.read_csv(
            "".join(lines[header_index:]).encode(), separator="\t", infer_schema=False,
        ).rename({"PDB": "pdb_id_lower", "CHAIN": "chain", "SP_PRIMARY": "uniprot_id"})
        table = table.with_columns(pl.col("pdb_id_lower").str.to_uppercase().alias("pdb_id"))
        table.write_parquet(args.out, compression="zstd")

        # Cross-check against the accessions the RCSB API reported per polymer entity.
        linked = pl.read_parquet(args.linked)
        api_pairs = (linked.select("pdb_id", "protein_uniprot_ids")
                     .explode("protein_uniprot_ids").drop_nulls()
                     .rename({"protein_uniprot_ids": "uniprot_id"}).unique())
        sifts_pairs = table.select("pdb_id", "uniprot_id").unique()

        both = api_pairs.join(sifts_pairs, on=["pdb_id", "uniprot_id"], how="inner").height
        api_only = api_pairs.join(sifts_pairs, on=["pdb_id", "uniprot_id"],
                                  how="anti").height
        sifts_only = sifts_pairs.join(api_pairs, on=["pdb_id", "uniprot_id"],
                                      how="anti").height

        stats = {
            "sifts_release_line": released,
            "n_sifts_rows": table.height,
            "n_sifts_entries": table["pdb_id"].n_unique(),
            "n_sifts_accessions": table["uniprot_id"].n_unique(),
            "n_pairs_agree": both,
            "n_pairs_api_only": api_only,
            "n_pairs_sifts_only": sifts_only,
        }
        m.add_output(archive).add_output(args.out).note(**stats)

        print(f"  {released}")
        print(f"  rows {table.height:,}, entries {table['pdb_id'].n_unique():,}, "
              f"accessions {table['uniprot_id'].n_unique():,}")
        print("\n(entry, accession) pairs, SIFTS against the RCSB API:")
        print(f"  agree           {both:>9,}")
        print(f"  API only        {api_only:>9,}")
        print(f"  SIFTS only      {sifts_only:>9,}")
        print("\nSIFTS-only pairs are expected: it maps every chain, including entities "
              "this pipeline did not select as the entry's protein representative.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
