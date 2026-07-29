"""Stage `ingest.validate_fidelity`: the WP1 gate. API text versus archive mmCIF.

Phase 0 harvests crystallisation text from the RCSB Data API rather than the 90 GB mmCIF
archive. That is a bet that the API reproduces `_exptl_crystal_grow.pdbx_details` verbatim.
This stage tests the bet instead of assuming it: a sample of entries is downloaded as
mmCIF, parsed with gemmi, and compared against what was ingested.

Two things are compared, not one:

  * the details string, byte for byte, so whitespace normalisation or unicode transcoding
    cannot slip through
  * the **number of rows** in the loop, because the single most likely bug in this pipeline
    is silently keeping only the first crystal form

Multi-form entries are deliberately oversampled. They are 0.07% of the archive, so a
uniform sample of 200 would be expected to contain none at all, and the rare case is
exactly the one worth testing.

Nothing downstream should run until this passes.

    ./run.sh ingest.validate_fidelity
    ./run.sh ingest.validate_fidelity --sample 500
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path
from typing import Any, Optional

import gemmi
import polars as pl
from tqdm import tqdm

from .. import config, http
from ..manifest import Manifest

STAGE = "ingest.validate_fidelity"

DEFAULT_SAMPLE = 200
DEFAULT_MULTI_FORM = 25


def fetch_mmcif(pdb_id: str, cache_dir: Path) -> Optional[Path]:
    dest = cache_dir / f"{pdb_id}.cif.gz"
    url = config.RCSB_FILE_URL.format(entry_id=pdb_id, fmt="cif.gz")
    return http.download(url, dest, skip_if_exists=True)


def parse_mmcif_grow(path: Path) -> list[dict[str, Optional[str]]]:
    """Extract the `_exptl_crystal_grow` loop from an mmCIF file.

    `find_values` is used rather than `find_loop` because the category appears as a plain
    key/value pair when an entry has exactly one crystal form, and as a loop when it has
    several. Both must yield the same shape here or the comparison is meaningless.
    """
    doc = gemmi.cif.read(str(path))
    block = doc.sole_block()
    details = list(block.find_values("_exptl_crystal_grow.pdbx_details"))
    if not details:
        return []
    ids = list(block.find_values("_exptl_crystal_grow.crystal_id"))
    rows = []
    for position, raw in enumerate(details, start=1):
        crystal_id = gemmi.cif.as_string(ids[position - 1]) if position <= len(ids) else None
        value = gemmi.cif.as_string(raw)
        # gemmi returns the CIF null tokens as literal "?" and "." strings.
        if value in ("?", "."):
            value = None
        rows.append({
            "crystal_id": (crystal_id if crystal_id not in (None, "?", ".") else str(position)),
            "raw_details": value,
        })
    return rows


def choose_sample(entries: pl.DataFrame, n_sample: int, n_multi: int, seed: int) -> list[str]:
    multi = (entries.filter(pl.col("n_crystal_forms") > 1)
                    .select("pdb_id").unique().to_series().to_list())
    single = (entries.filter(pl.col("n_crystal_forms") == 1)
                     .select("pdb_id").unique().to_series().to_list())
    rng = random.Random(seed)
    rng.shuffle(multi)
    rng.shuffle(single)
    picked_multi = multi[:n_multi]
    picked_single = single[:max(0, n_sample - len(picked_multi))]
    return picked_multi + picked_single


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entries", type=Path, default=config.INTERIM_DIR / "entries.parquet")
    parser.add_argument("--cache-dir", type=Path, default=config.RAW_MMCIF_DIR)
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--multi-form", type=int, default=DEFAULT_MULTI_FORM,
                        help="how many multi-crystal-form entries to force into the sample")
    parser.add_argument("--seed", type=int, default=20260728)
    parser.add_argument("--allow-revised", action="store_true",
                        help="tolerate mismatches on entries revised since the harvest")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    entries = pl.read_parquet(args.entries)

    params = {"sample": args.sample, "multi_form": args.multi_form, "seed": args.seed}
    with Manifest(STAGE, params=params) as m:
        m.add_input(args.entries)

        sample_ids = choose_sample(entries, args.sample, args.multi_form, args.seed)
        # polars group_by yields the key as a one-element tuple; normalise it here rather
        # than at every lookup site.
        indexed: dict[str, pl.DataFrame] = {}
        for key, group in entries.filter(pl.col("pdb_id").is_in(sample_ids)).group_by("pdb_id"):
            indexed[key[0] if isinstance(key, tuple) else key] = group.sort("crystal_id")

        checked = row_count_ok = details_ok = 0
        row_count_mismatches: list[dict[str, Any]] = []
        details_mismatches: list[dict[str, Any]] = []
        unavailable: list[str] = []

        for pdb_id in tqdm(sample_ids, desc="entries", unit="entry"):
            path = fetch_mmcif(pdb_id, args.cache_dir)
            if path is None:
                unavailable.append(pdb_id)
                continue
            archive_rows = parse_mmcif_grow(path)
            group = indexed[pdb_id]
            api_rows = [
                {"crystal_id": r["crystal_id"], "raw_details": r["raw_details"]}
                for r in group.iter_rows(named=True)
                if r["n_crystal_forms"] > 0
            ]
            checked += 1

            if len(archive_rows) != len(api_rows):
                row_count_mismatches.append({
                    "pdb_id": pdb_id,
                    "archive_rows": len(archive_rows),
                    "api_rows": len(api_rows),
                })
                continue
            row_count_ok += 1

            mismatched = [
                {"pdb_id": pdb_id, "crystal_id": a["crystal_id"],
                 "archive": (a["raw_details"] or "")[:200],
                 "api": (b["raw_details"] or "")[:200]}
                for a, b in zip(archive_rows, api_rows)
                if (a["raw_details"] or "") != (b["raw_details"] or "")
            ]
            if mismatched:
                details_mismatches.extend(mismatched)
            else:
                details_ok += 1

        stats = {
            "n_sampled": len(sample_ids),
            "n_checked": checked,
            "n_unavailable": len(unavailable),
            "n_row_count_ok": row_count_ok,
            "n_details_ok": details_ok,
            "n_row_count_mismatch": len(row_count_mismatches),
            "n_details_mismatch": len(details_mismatches),
        }
        m.note(**stats, row_count_mismatches=row_count_mismatches[:20],
               details_mismatches=details_mismatches[:20], unavailable=unavailable[:20])

        for key, value in stats.items():
            print(f"  {key:<22} {value:>6,}")

        if row_count_mismatches or details_mismatches:
            print("\nGATE FAILED. The API is not reproducing the archive verbatim.")
            for bad in row_count_mismatches[:5]:
                print(f"  rows  {bad['pdb_id']}: archive {bad['archive_rows']}, api {bad['api_rows']}")
            for bad in details_mismatches[:5]:
                print(f"  text  {bad['pdb_id']}/{bad['crystal_id']}")
                print(f"        archive: {bad['archive']!r}")
                print(f"        api    : {bad['api']!r}")
            print("\nA handful of text mismatches can be a benign revision race: the entry "
                  "changed between harvest and check. Compare revision_date before "
                  "concluding the API is at fault. Systematic whitespace or unicode "
                  "differences are not benign and mean falling back to the rsync route.")
            if not args.allow_revised:
                raise SystemExit(1)
        else:
            print("\nGATE PASSED: details strings and loop row counts agree exactly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
