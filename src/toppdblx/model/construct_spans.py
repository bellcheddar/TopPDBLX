"""Stage `model.construct_spans`: turn residue-level SIFTS into construct boundary labels.

**Where a crystallographer cut**, per chain, in the coordinates of the full-length protein. For
every PDB chain that maps to UniProt, this records the span of UniProt positions the deposited
construct covers. That span is the label R3's boundary model learns to predict from sequence
alone, and it costs nothing to collect: tens of thousands of real decisions, already made.

    ./run.sh model.construct_spans
    ./run.sh model.construct_spans --limit 2000

Three distinctions the SIFTS XML makes, and this stage keeps:

* **Expression tags carry no UniProt cross-reference at all.** A His-tag or a thrombin site is not
  in the natural protein, so it self-excludes: the span is the natural sequence the depositor
  chose, not the artefact of the vector they chose it with.
* **`Not_Observed` means disordered, not absent.** Those residues *are* in the construct; the
  crystallographer cloned them and they failed to appear in the density. Counting them as outside
  would teach the model to predict order, which is a different question. They stay inside the span
  and are counted separately so a later stage can ask that question honestly.
* **A chain can map to several UniProt accessions** (a fusion, a chimera). Each accession gets its
  own row rather than one span spuriously stretching across two proteins.
"""

from __future__ import annotations

import argparse
import gzip
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Optional

import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest

STAGE = "model.construct_spans"
NS = {"s": "http://www.ebi.ac.uk/pdbe/docs/sifts/eFamily.xsd"}


def parse_file(path: Path) -> list[dict]:
    """One row per (pdb_id, chain, uniprot_id)."""
    try:
        root = ET.fromstring(gzip.decompress(path.read_bytes()))
    except Exception:
        return []

    rows: list[dict] = []
    for entity in root.findall(".//s:entity", NS):
        if entity.get("type") != "protein":
            continue
        chain = entity.get("entityId")
        # positions[accession] -> set of UniProt residue numbers; observed counts the subset that
        # actually appeared in the density.
        positions: dict[str, set[int]] = defaultdict(set)
        observed: dict[str, int] = defaultdict(int)
        pdb_id = None

        for residue in entity.findall(".//s:residue", NS):
            is_missing = any(d.text == "Not_Observed"
                             for d in residue.findall("s:residueDetail", NS))
            accession = number = None
            for ref in residue.findall("s:crossRefDb", NS):
                source = ref.get("dbSource")
                if source == "PDB" and pdb_id is None:
                    pdb_id = ref.get("dbAccessionId")
                elif source == "UniProt":
                    accession = ref.get("dbAccessionId")
                    raw = ref.get("dbResNum")
                    if raw and raw.lstrip("-").isdigit():
                        number = int(raw)
            if accession and number is not None:
                positions[accession].add(number)
                if not is_missing:
                    observed[accession] += 1

        for accession, numbers in positions.items():
            if not numbers:
                continue
            low, high = min(numbers), max(numbers)
            rows.append({
                "pdb_id": (pdb_id or path.stem.split(".")[0]).upper(),
                "chain": chain,
                "uniprot_id": accession,
                "start": low,
                "end": high,
                "n_positions": len(numbers),
                "n_observed": observed[accession],
                # A construct is normally one contiguous run. A gap means an internal deletion or
                # a chimera, and a span that quietly spans one is a bad label.
                "is_contiguous": len(numbers) == (high - low + 1),
            })
    return rows


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sifts-dir", type=Path,
                        default=config.RAW_DIR / "sifts" / "residue")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "construct_spans.parquet")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    files = sorted(args.sifts_dir.rglob("*.xml.gz"))
    if args.limit:
        files = files[:args.limit]
    print(f"  parsing {len(files):,} SIFTS files with {args.workers} workers")

    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for result in tqdm(pool.map(parse_file, files, chunksize=200),
                           total=len(files), desc="spans", unit="file"):
            rows.extend(result)

    with Manifest(STAGE, params={"n_files": len(files)}) as m:
        frame = pl.DataFrame(rows)
        frame = frame.with_columns(
            (pl.col("end") - pl.col("start") + 1).alias("span_length"),
            (pl.col("n_observed") / pl.col("n_positions")).alias("observed_fraction"),
        )
        frame.write_parquet(args.out, compression="zstd")
        m.add_output(args.out)
        m.note(n_files=len(files), n_spans=frame.height,
               n_entries=frame["pdb_id"].n_unique(),
               n_accessions=frame["uniprot_id"].n_unique(),
               n_contiguous=int(frame["is_contiguous"].sum()),
               median_span=int(frame["span_length"].median() or 0))
        print(f"\n  {frame.height:,} spans over {frame['pdb_id'].n_unique():,} entries "
              f"and {frame['uniprot_id'].n_unique():,} accessions")
        print(f"  contiguous: {int(frame['is_contiguous'].sum()):,} "
              f"({100 * frame['is_contiguous'].mean():.1f}%)")
        print(f"  median span {int(frame['span_length'].median() or 0)} residues, "
              f"median observed fraction {frame['observed_fraction'].median():.2f}")
        print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
