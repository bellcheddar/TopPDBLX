"""SUPERSEDED by `assign.classify` at ontology 0.3.0.

The three-level ontology this stage belongs to was withdrawn: its groups were binned
from the corpus and then had labels retrofitted, which spec 6.1 rejects, and several
were not chemically coherent (median L2 purity 49%). Classification is now the seven
JCSG Top96 precipitant classes with no sub-levels. Kept for provenance and because the
diagnostics behind that decision are worth being able to reproduce.

Stage `assign.assign_groups`: place every condition in the curated ontology.

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
import re
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


# The molecular-weight band a group's label states. Three forms exist in the vocabulary and all
# three must be recognised: a closed range ("PEG 3350-4000"), an open one ("PEG 20000+") and a
# single value ("PEG 2000"). Matching only ranges left the single-value bins unconstrained, which
# is why "PEG · PEG 2000" still held mostly PEG 3350 and PEG 3000 after the first fix.
_PEG_BAND = re.compile(r"PEG (\d+)-(\d+)")
_PEG_OPEN_BAND = re.compile(r"PEG (\d+)\+")
_PEG_EXACT_BAND = re.compile(r"PEG (\d+)(?![\d\-+])")


def peg_band_of(label: str) -> Optional[tuple[float, float]]:
    """The molecular-weight range a group's own label promises, if it states one."""
    text = label or ""
    closed = _PEG_BAND.search(text)
    if closed:
        return float(closed.group(1)), float(closed.group(2))
    open_ended = _PEG_OPEN_BAND.search(text)
    if open_ended:
        return float(open_ended.group(1)), float("inf")
    exact = _PEG_EXACT_BAND.search(text)
    if exact:
        # A single-value bin still covers the grades sold around it (PEG 2000 against PEG 2050),
        # so it is read as a narrow band rather than an exact equality.
        value = float(exact.group(1))
        return value * 0.9, value * 1.1
    return None


def label_is_true_of(features, group) -> bool:
    """Whether a group's stated molecular-weight band actually holds for this condition.

    **A categorical claim in a label must be a hard constraint, not a soft preference.** The
    distance metric scales PEG molecular weight at one unit per decade, so PEG 3350 against PEG
    8000 is only 0.378 units apart, while a 0.4 pH difference costs the same and a six-point
    concentration difference costs more. The axis that names the group therefore carried less
    weight than axes that do not appear in the name, and records scattered across bands whenever
    pH or percentage happened to fit better: a group labelled "PEG 5000-6000" held 452 records of
    PEG 8000 and 275 of PEG 3350, with only 31% of its members inside its own band.

    Distance still chooses among candidates; it just may no longer choose one whose label would
    be false of the record. Groups that state no band are unaffected.
    """
    bounds = peg_band_of(getattr(group, "label", "") or "")
    if bounds is None:
        return True
    if features.peg_log_mw is None:
        # The label promises a PEG size and this condition has no PEG to check it against.
        return False
    low, high = bounds
    mw = 10 ** features.peg_log_mw
    # A little tolerance, because the band edges are round numbers and PEG 3,350 sits against a
    # 3350 boundary: floating point should not exclude a reagent the band was drawn around.
    return low * 0.98 <= mw <= high * 1.02


def nearest(features, candidates) -> tuple[Optional[Any], Optional[float]]:
    best, best_distance = None, None
    for group in candidates:
        centroid = group.centroid.to_features()
        if not shares_precipitant_axis(features, centroid):
            continue
        if not label_is_true_of(features, group):
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
