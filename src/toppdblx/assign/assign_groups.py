"""Stage `assign.assign_groups`: place every condition in the curated ontology.

Fills the `curated_group` block that Phase 0 deliberately shipped as null, so this is a join
rather than a schema migration. Per spec 5.5 the block carries the three levels, the distance
to the assigned group and a confidence band.

Two rules matter more than the arithmetic:

**Assign at whichever level has adequate support** (spec 6.2). An L3 group is used when one is
genuinely close; otherwise the record falls back to its L2 parent, and if no L2 is close either
it is left unassigned. 122 L3 groups cover under 40% of the corpus by design, so the fallback
is the normal path, not an error case.

**A near miss on one axis is not a match.** Assignment requires agreement on an axis that
actually precipitates protein. Without that, a condition with only a pH would be placed by pH
alone, which is how a condition with no identified precipitant came to be anchored to a real
screen well earlier in Phase 1.

Confidence bands come from the distance function's own scale rather than being invented:

  high     < 0.25   inside a 4-point PEG step, the same threshold used to ask whether two
                    groups should merge at all
  medium   < 0.50
  low      < 1.00
  unassigned        further than 1.0, or sharing no precipitant axis with any group

    ./run.sh assign.assign_groups
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Optional

import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest
from . import groups as groups_module
from .distance import distance, featurise, shares_precipitant_axis

STAGE = "assign.assign_groups"

HIGH = 0.25
MEDIUM = 0.50
LOW = 1.00

SCHEMA = {
    "pdb_id": pl.Utf8, "crystal_id": pl.Utf8,
    "l1_precipitant_class": pl.Utf8, "l2_subclass": pl.Utf8, "l3_group_id": pl.Utf8,
    "l2_label": pl.Utf8, "l3_label": pl.Utf8,
    "assignment_distance": pl.Float64, "assignment_confidence": pl.Utf8,
    "assigned_level": pl.UInt8,
    "screen_anchor": pl.Utf8,
}


def band(value: Optional[float]) -> str:
    if value is None:
        return "unassigned"
    if value < HIGH:
        return "high"
    if value < MEDIUM:
        return "medium"
    if value < LOW:
        return "low"
    return "unassigned"


def nearest(features, candidates) -> tuple[Optional[Any], Optional[float]]:
    best, best_distance = None, None
    for group in candidates:
        centroid = group.centroid.to_features()
        if not shares_precipitant_axis(features, centroid):
            continue
        d = distance(features, centroid)
        if best_distance is None or d < best_distance:
            best, best_distance = group, d
    return best, best_distance


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--conditions", type=Path,
                        default=config.INTERIM_DIR / "parsed_conditions.parquet")
    parser.add_argument("--l1", type=Path, default=config.INTERIM_DIR / "l1_classes.parquet")
    parser.add_argument("--groups", type=Path, default=groups_module.GROUPS_PATH)
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "group_assignments.parquet")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    ontology = groups_module.load(args.groups)
    by_id = ontology.by_id()
    l2_groups = ontology.by_level(2)
    l3_by_parent: dict[str, list] = {}
    for group in ontology.by_level(3):
        l3_by_parent.setdefault(group.parent, []).append(group)

    conditions = pl.read_parquet(args.conditions).filter(pl.col("discard_reason").is_null())
    l1 = pl.read_parquet(args.l1)
    keys = set(zip(conditions["pdb_id"], conditions["crystal_id"]))
    components = pl.read_parquet(args.components).filter(
        pl.struct(["pdb_id", "crystal_id"]).map_elements(
            lambda s: (s["pdb_id"], s["crystal_id"]) in keys, return_dtype=pl.Boolean))

    ph_by_key = dict(zip(zip(conditions["pdb_id"], conditions["crystal_id"]), conditions["ph"]))
    l1_by_key = dict(zip(zip(l1["pdb_id"], l1["crystal_id"]), l1["l1_precipitant_class"]))

    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for row in components.iter_rows(named=True):
        grouped.setdefault((row["pdb_id"], row["crystal_id"]), []).append(row)

    with Manifest(STAGE, params={"ontology_version": ontology.version,
                                 "n_groups": len(ontology.groups),
                                 "bands": {"high": HIGH, "medium": MEDIUM, "low": LOW}}) as m:
        m.add_input(args.groups).add_input(args.components).add_input(args.l1)

        rows: list[dict[str, Any]] = []
        for key, parts in tqdm(grouped.items(), desc="conditions", unit="cond"):
            features = featurise(parts, ph_by_key.get(key))
            l1_class = l1_by_key.get(key, "Unassigned")

            # Only groups of the same L1 class are candidates: a PEG condition is never
            # assigned to a Salt group however close the arithmetic happens to be.
            l2_candidates = [g for g in l2_groups if g.l1_class == l1_class]
            l2_group, l2_distance = nearest(features, l2_candidates)

            l3_group = l3_distance = None
            if l2_group is not None:
                l3_group, l3_distance = nearest(features, l3_by_parent.get(l2_group.id, []))

            # Prefer L3 when it is genuinely close, otherwise fall back to the L2 parent.
            if l3_group is not None and l3_distance is not None and l3_distance < MEDIUM:
                chosen, chosen_distance, level = l3_group, l3_distance, 3
            elif l2_group is not None and l2_distance is not None and l2_distance < LOW:
                chosen, chosen_distance, level = l2_group, l2_distance, 2
            else:
                chosen, chosen_distance, level = None, None, 0

            anchor = None
            if level == 3 and chosen.screen_anchors:
                first = chosen.screen_anchors[0]
                anchor = f"{first.screen} {first.well}"

            rows.append({
                "pdb_id": key[0], "crystal_id": key[1],
                "l1_precipitant_class": l1_class,
                "l2_subclass": l2_group.id if l2_group else None,
                "l3_group_id": l3_group.id if level == 3 else None,
                "l2_label": l2_group.label if l2_group else None,
                "l3_label": l3_group.label if level == 3 else None,
                "assignment_distance": round(chosen_distance, 4) if chosen_distance is not None else None,
                "assignment_confidence": band(chosen_distance),
                "assigned_level": level,
                "screen_anchor": anchor,
            })

        table = pl.DataFrame(rows, schema=SCHEMA)
        table.write_parquet(args.out, compression="zstd")

        total = table.height
        stats = {
            "ontology_version": ontology.version,
            "n_conditions": total,
            "n_assigned_l3": table.filter(pl.col("assigned_level") == 3).height,
            "n_assigned_l2": table.filter(pl.col("assigned_level") == 2).height,
            "n_unassigned": table.filter(pl.col("assigned_level") == 0).height,
            "n_with_screen_anchor": table.filter(pl.col("screen_anchor").is_not_null()).height,
        }
        for name in ("high", "medium", "low", "unassigned"):
            stats[f"n_confidence_{name}"] = table.filter(
                pl.col("assignment_confidence") == name).height
        m.add_output(args.out).note(**stats)

        print(f"\n{total:,} conditions assigned against ontology v{ontology.version}\n")
        print(f"  at L3 (a specific condition group) {stats['n_assigned_l3']:>8,} "
              f"{100 * stats['n_assigned_l3'] / total:>5.1f}%")
        print(f"  at L2 (fallback, adequate support)  {stats['n_assigned_l2']:>8,} "
              f"{100 * stats['n_assigned_l2'] / total:>5.1f}%")
        print(f"  unassigned                          {stats['n_unassigned']:>8,} "
              f"{100 * stats['n_unassigned'] / total:>5.1f}%")
        print(f"\n  with an orderable screen anchor    {stats['n_with_screen_anchor']:>8,}")
        print("\nconfidence:")
        for name in ("high", "medium", "low", "unassigned"):
            n = stats[f"n_confidence_{name}"]
            print(f"  {name:<12} {n:>8,} {100 * n / total:>5.1f}%  {'#' * int(50 * n / total)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
