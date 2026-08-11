"""Stage `ingest.targettrack_parse`: TargetTrack XML into one row per target.

TargetTrack is the **only** substantial source of crystallisation *failures*. Every other input to
this project is conditioned on success, because the PDB only records what worked. Without these
negatives there is no answer to "will this protein crystallise", only "what crystallised this
protein once it had".

    ./run.sh ingest.targettrack_parse
    ./run.sh ingest.targettrack_parse --limit 20000     # a taste

Each target carries a sequence and a status history. The history matters more than the current
status: a target sitting at `work stopped` tells you nothing on its own, but a target that reached
`purified` and then stopped is a **failed crystallisation attempt**, which is exactly the negative
the propensity model needs. So every status a target ever reached is kept, not just its last.

**Era limitation, to be published rather than hidden.** TargetTrack's final release is 1 July 2017
and it is dominated by structural genomics targets, which were selected for tractability and
worked on by a handful of high-throughput centres. It is not a random sample of proteins anybody
might want to crystallise.
"""

from __future__ import annotations

import argparse
import gzip
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest

STAGE = "ingest.targettrack_parse"
DEFAULT_XML = (config.INTERIM_DIR / "targettrack" / "TargetTrack XML files" / "tt.xml.gz")


def local(tag: str) -> str:
    return tag.split("}")[-1]


def parse_target(element: ET.Element) -> Optional[dict]:
    """One row per target: identity, sequence, and every status it ever reached."""
    target_id = None
    labs: set[str] = set()
    statuses: set[str] = set()
    current = None
    sequence = None
    organism = None

    for child in element.iter():
        tag = local(child.tag)
        text = (child.text or "").strip()
        if tag == "targetId" and text and target_id is None:
            target_id = text
        elif tag == "lab" and text:
            labs.add(text)
        elif tag == "status" and text:
            # The target-level status appears first and is the current one; everything after is
            # trial and history. Both are kept, because reaching a stage then regressing still
            # means the stage was reached.
            lowered = text.lower()
            if current is None and child in list(element):
                current = lowered
            statuses.add(lowered)
        elif tag == "oneLetterCode" and text and sequence is None:
            sequence = "".join(text.split()).upper()
        elif tag == "scientificName" and text and organism is None:
            organism = text

    if not target_id:
        return None
    return {
        "target_id": target_id,
        "lab": sorted(labs)[0] if labs else None,
        "sequence": sequence,
        "length": len(sequence) if sequence else 0,
        "status_current": current,
        "statuses": sorted(statuses),
        "organism": organism,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--xml", type=Path, default=DEFAULT_XML)
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "targettrack_targets.parquet")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    if not args.xml.exists():
        raise SystemExit(f"{args.xml} missing. Unpack it from the archive fetched by "
                         f"ingest.targettrack first.")

    rows: list[dict] = []
    bar = tqdm(desc="targets", unit="target")
    with gzip.open(args.xml, "rb") as handle:
        for _, element in ET.iterparse(handle, events=("end",)):
            if local(element.tag) != "target":
                continue
            row = parse_target(element)
            # **Clearing is not optional at this size.** iterparse keeps every parsed element
            # alive otherwise, and this file expands to several GB in memory.
            element.clear()
            if row:
                rows.append(row)
                bar.update(1)
            if args.limit and len(rows) >= args.limit:
                break
    bar.close()

    with Manifest(STAGE, params={"xml": str(args.xml)}) as m:
        frame = pl.DataFrame(rows)
        frame.write_parquet(args.out, compression="zstd")
        m.add_input(args.xml)
        m.add_output(args.out)
        with_seq = frame.filter(pl.col("length") > 0)
        m.note(n_targets=frame.height, n_with_sequence=with_seq.height,
               n_labs=frame["lab"].n_unique(),
               median_length=int(with_seq["length"].median() or 0))
        print(f"\n  {frame.height:,} targets, {with_seq.height:,} with a sequence")
        print(f"  {frame['lab'].n_unique()} contributing labs, "
              f"median sequence {int(with_seq['length'].median() or 0)} residues")
        print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
