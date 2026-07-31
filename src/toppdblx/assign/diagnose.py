"""SUPERSEDED by `assign.classify` at ontology 0.3.0.

The three-level ontology this stage belongs to was withdrawn: its groups were binned
from the corpus and then had labels retrofitted, which spec 6.1 rejects, and several
were not chemically coherent (median L2 purity 49%). Classification is now the seven
JCSG Top96 precipitant classes with no sub-levels. Kept for provenance and because the
diagnostics behind that decision are worth being able to reproduce.

Stage `assign.diagnose`: size L2 and L3 from the data, before curating anything.

Spec 6.1 is emphatic that the groups are hand-defined and that clustering is a **diagnostic**
only: "cluster the parsed conditions, find the clusters with no curated home, and add curated
groups to cover the large orphans." This stage is that diagnostic, and it exists to answer
open decision 12.1 (how many L2 and L3 groups, and what orphan threshold triggers a new one)
with evidence instead of a guess.

It bins rather than clusters, deliberately. k-means would produce centroids that need
interpreting before anyone can curate from them; a bin is already a sentence. Every cell here
reads directly as a candidate group definition:

    Salt/PEG · PEG 3350-4000 · 15-25% · sulfate-family salt 0.1-0.3 M · pH 6-7

The bin edges are the same chemistry as the distance function: PEG molecular weight by decade
band, salt by Hofmeister family, concentration in coarse steps, pH by unit.

    ./run.sh assign.diagnose
    ./run.sh assign.diagnose --top 40
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest
from .distance import featurise

STAGE = "assign.diagnose"

# PEG bands by molecular weight. 3350 and 4000 share a band because they are
# near-interchangeable; 400 and 8000 cannot.
PEG_BANDS = [(0, 600, "PEG<=600"), (601, 1500, "PEG 1000-1500"),
             (1501, 2500, "PEG 2000"), (2501, 4500, "PEG 3350-4000"),
             (4501, 7000, "PEG 5000-6000"), (7001, 12000, "PEG 8000-10000"),
             (12001, 100000, "PEG 20000+")]

# Hofmeister families, coarse enough to group the kosmotropes together as spec 6.3 requires.
HOFMEISTER_BANDS = [(-9, -3, "sulfate/phosphate/citrate"), (-2, -1, "acetate/formate"),
                    (0, 0, "chloride"), (1, 9, "nitrate/iodide/thiocyanate")]

PERCENT_BANDS = [(0, 10, "<10%"), (10, 20, "10-20%"), (20, 30, "20-30%"),
                 (30, 101, ">30%")]
MOLAR_BANDS = [(0, 0.35, "0.1-0.3 M"), (0.35, 0.9, "0.4-0.8 M"),
               (0.9, 1.8, "1-1.5 M"), (1.8, 100, ">1.8 M")]


def band(value: Optional[float], bands: list[tuple[float, float, str]]) -> Optional[str]:
    if value is None:
        return None
    for low, high, label in bands:
        if low <= value <= high:
            return label
    return None


def peg_band(log_mw: Optional[float]) -> Optional[str]:
    if log_mw is None:
        return None
    return band(10 ** log_mw, [(lo, hi, name) for lo, hi, name in PEG_BANDS])


def ph_band(ph: Optional[float]) -> Optional[str]:
    if ph is None:
        return None
    low = int(ph)
    return f"pH {low}-{low + 1}"


def cell_for(features, l1_class: str, level: str) -> str:
    """A bin label that reads as a candidate group definition.

    L2 is coarse (class plus the dominant precipitant band); L3 adds concentration and pH,
    which is what distinguishes one screen well from its neighbours.
    """
    parts = [l1_class]
    peg = peg_band(features.peg_log_mw)
    salt = band(features.salt_hofmeister, HOFMEISTER_BANDS)
    if peg:
        parts.append(peg)
    if salt:
        parts.append(salt)
    if level == "L2":
        return " · ".join(parts)

    if features.peg_percent is not None:
        parts.append(band(features.peg_percent, PERCENT_BANDS) or "?%")
    if features.salt_log_molar is not None:
        parts.append(band(10 ** features.salt_log_molar, MOLAR_BANDS) or "?M")
    if features.organic_percent is not None:
        parts.append(f"organic {band(features.organic_percent, PERCENT_BANDS) or '?'}")
    parts.append(ph_band(features.ph) or "pH unstated")
    return " · ".join(parts)


def coverage_table(counts: dict[str, int], total: int,
                   targets=(0.5, 0.75, 0.9, 0.95, 0.99)) -> dict[str, int]:
    ordered = sorted(counts.values(), reverse=True)
    result, running = {}, 0
    for target in targets:
        needed, running = 0, 0
        for n in ordered:
            running += n
            needed += 1
            if running / total >= target:
                break
        result[f"cells_for_{int(target * 100)}pc"] = needed
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--conditions", type=Path,
                        default=config.INTERIM_DIR / "parsed_conditions.parquet")
    parser.add_argument("--l1", type=Path, default=config.INTERIM_DIR / "l1_classes.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "ontology_diagnostic.json")
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    conditions = pl.read_parquet(args.conditions).filter(pl.col("discard_reason").is_null())
    l1 = pl.read_parquet(args.l1)
    keys = set(zip(conditions["pdb_id"], conditions["crystal_id"]))
    components = pl.read_parquet(args.components).filter(
        pl.struct(["pdb_id", "crystal_id"]).map_elements(
            lambda s: (s["pdb_id"], s["crystal_id"]) in keys, return_dtype=pl.Boolean))

    ph_by_key = dict(zip(zip(conditions["pdb_id"], conditions["crystal_id"]),
                         conditions["ph"]))
    l1_by_key = dict(zip(zip(l1["pdb_id"], l1["crystal_id"]), l1["l1_precipitant_class"]))

    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for row in components.iter_rows(named=True):
        grouped.setdefault((row["pdb_id"], row["crystal_id"]), []).append(row)

    with Manifest(STAGE, params={"n_conditions": len(grouped)}) as m:
        m.add_input(args.components).add_input(args.conditions).add_input(args.l1)

        l2_counts: dict[str, int] = {}
        l3_counts: dict[str, int] = {}
        for key, parts in tqdm(grouped.items(), desc="conditions", unit="cond"):
            features = featurise(parts, ph_by_key.get(key))
            l1_class = l1_by_key.get(key, "Unassigned")
            l2_counts[cell_for(features, l1_class, "L2")] = \
                l2_counts.get(cell_for(features, l1_class, "L2"), 0) + 1
            label3 = cell_for(features, l1_class, "L3")
            l3_counts[label3] = l3_counts.get(label3, 0) + 1

        total = len(grouped)
        l2_cover = coverage_table(l2_counts, total)
        l3_cover = coverage_table(l3_counts, total)

        report = {
            "n_conditions": total,
            "n_l2_cells": len(l2_counts),
            "n_l3_cells": len(l3_counts),
            "l2_coverage": l2_cover,
            "l3_coverage": l3_cover,
            "l2_cells": sorted(l2_counts.items(), key=lambda kv: -kv[1]),
            "l3_cells": sorted(l3_counts.items(), key=lambda kv: -kv[1])[:400],
        }
        args.out.write_text(json.dumps(report, indent=1))
        m.add_output(args.out).note(n_conditions=total, n_l2_cells=len(l2_counts),
                                    n_l3_cells=len(l3_counts), **{f"l2_{k}": v for k, v in
                                                                  l2_cover.items()},
                                    **{f"l3_{k}": v for k, v in l3_cover.items()})

        print(f"\n{total:,} conditions binned\n")
        print(f"  L2 candidate cells: {len(l2_counts):,}")
        for key, value in l2_cover.items():
            print(f"    {key.replace('cells_for_', 'cells for ')}: {value}")
        print(f"\n  L3 candidate cells: {len(l3_counts):,}")
        for key, value in l3_cover.items():
            print(f"    {key.replace('cells_for_', 'cells for ')}: {value}")

        print(f"\ntop {args.top} L2 candidates (each reads as a group definition):")
        for label, n in sorted(l2_counts.items(), key=lambda kv: -kv[1])[:args.top]:
            print(f"  {n:>7,} {100 * n / total:>5.1f}%  {label}")

        print(f"\ntop 12 L3 candidates:")
        for label, n in sorted(l3_counts.items(), key=lambda kv: -kv[1])[:12]:
            print(f"  {n:>7,} {100 * n / total:>5.1f}%  {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
