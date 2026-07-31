"""Stage `assign.classify`: the seven JCSG Top96 classes, and nothing else.

Replaces a three-level ontology of binned centroids that had grown complicated enough to be
unreadable, and which nobody had chosen: groups were binned from the corpus on PEG molecular
weight and salt family, then had names retrofitted. Spec 6.1 asks for hand-defined, human-
readable groups; this is the simplest thing that satisfies it.

**The whole classification.** A condition is described by which precipitant families it contains:

    Organic · PEG · Salt · Organic/PEG · Organic/Salt · Salt/PEG · Organic/PEG/Salt

These are the non-empty subsets of {Organic, PEG, Salt}, which is why there are seven.

**Presence only. No thresholds.** A PEG is a PEG whatever its molecular weight and whatever its
concentration; a salt is a salt whatever its chemistry and whatever its concentration. The
previous version applied three cutoffs that all made the classification harder to reason about
and none of which are in the brief: PEG below 600 was reclassified as an organic, salt below
0.2 M was ignored, and any PEG or organic below 4% was ignored. All three are gone. The cost is
that a 2% PEG now makes a condition a PEG condition; the benefit is that the rule fits in one
sentence and gives the same answer whoever applies it.

**Unclassified is a real answer, and a common one.** Anything that cannot be classified honestly
is left alone rather than forced into a class:

  *mixtures*             Morpheus, PACT and Tacsimate style premixes carry an acid mix, an
                         alcohol mix and a buffer system at once and do not fit a seven-class
                         taxonomy. This settles spec 6.4, which asked for the decision to be made
                         before assignment and never got one.
  *unidentified reagents*  a component whose name the lexicon does not recognise could be anything,
                         so the condition it belongs to cannot be classified on the evidence.
  *no amount stated*     a precipitant with no concentration or no unit is not a measured
                         condition. About 22,000 components name a precipitant and never say how
                         much.

Buffers are excluded from the classification entirely, per spec 6.3: at 0.1 M a buffer sets the
pH rather than precipitating anything, and the pH is carried separately.

    ./run.sh assign.classify
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import polars as pl

from .. import config
from ..manifest import Manifest

STAGE = "assign.classify"

CLASSES: dict[frozenset[str], str] = {
    frozenset({"organic"}): "Organic",
    frozenset({"peg"}): "PEG",
    frozenset({"salt"}): "Salt",
    frozenset({"organic", "peg"}): "Organic/PEG",
    frozenset({"organic", "salt"}): "Organic/Salt",
    frozenset({"peg", "salt"}): "Salt/PEG",
    frozenset({"organic", "peg", "salt"}): "Organic/PEG/Salt",
}

UNCLASSIFIED = "Unclassified"

# Which chemical classes count towards which precipitant family. Buffers are absent on purpose.
FAMILY_OF_CHEM_CLASS = {
    "peg": "peg",
    "salt": "salt",
    "organic": "organic",
    "polyol": "organic",
}

# Reasons a condition cannot be classified, reported so the unclassified share is explicable
# rather than a single opaque bucket.
REASONS = ("mixture", "unidentified_reagent", "no_amount", "no_precipitant")


def classify_condition(components: list[dict[str, Any]]) -> tuple[str, Optional[str]]:
    """The class of one condition, or Unclassified with the reason why."""
    families: set[str] = set()
    for component in components:
        role = component.get("role")
        if role == "not_a_component":
            # Method text and screen references were never chemistry; they say nothing about
            # the condition either way.
            continue

        if component.get("premix_id"):
            return UNCLASSIFIED, "mixture"

        name = component.get("name_canonical")
        if not name:
            return UNCLASSIFIED, "unidentified_reagent"

        family = FAMILY_OF_CHEM_CLASS.get(component.get("chem_class") or "")
        if family is None:
            # A buffer, a detergent, an additive: real chemistry, but not a precipitant family,
            # so it neither classifies the condition nor disqualifies it.
            continue

        # A precipitant with no stated amount is not a measured condition. Checked only for the
        # families that decide the class, so a missing additive concentration is not fatal.
        #
        # **Decided deliberately, 2026-07-31, and it is the expensive choice.** 24,327 conditions
        # (13.1% of the corpus) name their precipitant unambiguously and simply never say how
        # much: "PEG6000, Sodium Chloride, VAPOR DIFFUSION". Classifying those as Salt/PEG would
        # take classified coverage from 58.9% to about 72% at a stroke, and the rule that a PEG
        # is a PEG whatever its concentration would arguably support it. They stay Unclassified
        # anyway: a condition with no concentration is not a measured condition, and asserting a
        # class for it claims more than the deposition does. Coverage is not worth buying with a
        # claim the text does not make.
        if component.get("concentration") is None or not component.get("unit"):
            return UNCLASSIFIED, "no_amount"

        families.add(family)

    if not families:
        return UNCLASSIFIED, "no_precipitant"
    return CLASSES[frozenset(families)], None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--conditions", type=Path,
                        default=config.INTERIM_DIR / "parsed_conditions.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "condition_classes.parquet")
    parser.add_argument("--slm-components", type=Path, default=None,
                        help="components read by the fine-tuned model, for records the rules "
                             "could not read")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    conditions = pl.read_parquet(args.conditions).filter(pl.col("discard_reason").is_null())
    keys = set(zip(conditions["pdb_id"], conditions["crystal_id"]))
    components = pl.read_parquet(args.components)

    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for row in components.iter_rows(named=True):
        key = (row["pdb_id"], row["crystal_id"])
        if key in keys:
            grouped.setdefault(key, []).append(row)

    # **The rules are authoritative.** Model output is used only for records where the rules left
    # something unidentified, and it replaces that record's components wholesale rather than being
    # interleaved: mixing two parsers' readings of one string would produce a component list that
    # neither of them actually asserted. A record the rules read cleanly is never touched.
    n_from_model = 0
    if args.slm_components and args.slm_components.exists():
        from_model: dict[tuple, list[dict[str, Any]]] = {}
        for row in pl.read_parquet(args.slm_components).iter_rows(named=True):
            key = (row["pdb_id"], row["crystal_id"])
            if key in keys:
                from_model.setdefault(key, []).append(row)
        for key, model_parts in from_model.items():
            existing = grouped.get(key, [])
            rules_left_a_gap = any(
                not c["name_canonical"] and c["role"] != "not_a_component" for c in existing)
            if rules_left_a_gap or not existing:
                grouped[key] = model_parts
                n_from_model += 1

    with Manifest(STAGE, params={"classes": sorted(CLASSES.values())}) as m:
        m.add_input(args.components).add_input(args.conditions)

        if args.slm_components:
            print(f"  {n_from_model:,} records re-read by the model where the rules left a gap")
        rows, class_counts, reason_counts = [], Counter(), Counter()
        for key in keys:
            label, reason = classify_condition(grouped.get(key, []))
            class_counts[label] += 1
            if reason:
                reason_counts[reason] += 1
            rows.append({"pdb_id": key[0], "crystal_id": key[1],
                         "condition_class": label, "unclassified_reason": reason})

        frame = pl.DataFrame(rows, schema={"pdb_id": pl.Utf8, "crystal_id": pl.Utf8,
                                           "condition_class": pl.Utf8,
                                           "unclassified_reason": pl.Utf8})
        frame.write_parquet(args.out, compression="zstd")

        total = frame.height
        classified = total - class_counts[UNCLASSIFIED]
        stats = {"n_conditions": total, "n_classified": classified,
                 "n_records_from_model": n_from_model,
                 "n_unclassified": class_counts[UNCLASSIFIED],
                 **{f"n_{k.lower().replace('/', '_')}": v for k, v in class_counts.items()},
                 **{f"n_unclassified_{k}": v for k, v in reason_counts.items()}}
        m.add_output(args.out).note(**stats)

        print(f"\n{total:,} conditions classified into the seven JCSG Top96 classes\n")
        for label, n in class_counts.most_common():
            bar = "#" * round(60 * n / total)
            print(f"  {label:<18} {n:>8,}  {100 * n / total:>5.1f}%  {bar}")
        print(f"\n  classified   {classified:>8,}  {100 * classified / total:.1f}%")
        print(f"  unclassified {class_counts[UNCLASSIFIED]:>8,}  "
              f"{100 * class_counts[UNCLASSIFIED] / total:.1f}%   because:")
        for reason, n in reason_counts.most_common():
            print(f"    {reason:<22} {n:>8,}  {100 * n / total:>5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
