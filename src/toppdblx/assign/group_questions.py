"""Stage `assign.group_questions`: reduce group curation to the calls only a human can make.

The same discipline that took the Phase 0 parse audit from 1,484 records to 35 questions,
applied to the 181 proposed groups. Reviewing 181 machine-generated definitions one by one
would mostly be confirming that obviously distinct groups are distinct. What actually needs
Marc are the places where the proposal is **uncertain, redundant, incomplete, or where the
brief left a decision open**.

Six detectors, each with candidate answers computed:

  merge_l2 / merge_l3   two groups whose centroids are so close they are probably one group.
                        The distance function decides closeness; the chemistry is Marc's call.
  orphan                a bin holding more records than the group threshold that received no
                        group, because it fell outside the top N. Spec 6.1 says exactly this
                        should be checked: "find the clusters with no curated home, and add
                        curated groups to cover the large orphans".
  anchorless            an L3 group with no orderable well near it. Either a real condition
                        family the vendors do not sell, or an artefact.
  weak_anchor           an anchor that matched but not closely, so naming it may mislead.
  premix_taxonomy       open decision 12.2, still unanswered: Morpheus and PACT style
                        multi-component systems do not fit a seven-class precipitant
                        taxonomy. Own class, or a mixed_system bucket?

Output is deliberately in the same JSON shape as `eval.audit_questions`, so the existing
Condition Courtroom loads it with no new interface to learn.

    ./run.sh assign.group_questions
"""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path
from typing import Any, Optional

import polars as pl

from .. import config
from ..manifest import Manifest
from . import groups as groups_module
from .distance import distance, shares_precipitant_axis

STAGE = "assign.group_questions"

# Below this, two centroids are close enough that they are probably the same group. Chosen
# from the distance function's own scale: 0.25 is roughly a 4-point PEG step, well inside the
# optimisation range for one protein.
MERGE_THRESHOLD = 0.25

# An anchor further than this is not really naming the same condition.
WEAK_ANCHOR_DISTANCE = 0.35

MAX_PER_GROUP = 8


def _fmt(n: int) -> str:
    return f"{n:,}"


def build(ontology, diagnostic: dict[str, Any],
          components: pl.DataFrame) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    by_id = ontology.by_id()

    # ---------------------------------------------------------- merge candidates
    for level in (2, 3):
        pairs = []
        for a, b in combinations(ontology.by_level(level), 2):
            if a.l1_class != b.l1_class:
                continue
            if level == 3 and a.parent != b.parent:
                continue
            fa, fb = a.centroid.to_features(), b.centroid.to_features()
            if not shares_precipitant_axis(fa, fb):
                continue
            d = distance(fa, fb)
            if d < MERGE_THRESHOLD:
                pairs.append((d, a, b))
        pairs.sort(key=lambda t: -(t[1].n_records_at_creation + t[2].n_records_at_creation))
        for d, a, b in pairs[:MAX_PER_GROUP]:
            bigger, smaller = ((a, b) if a.n_records_at_creation >= b.n_records_at_creation
                               else (b, a))
            questions.append({
                "id": f"merge::{a.id}::{b.id}",
                "group": f"Groups that may be one group (L{level})",
                "question": f"Are these the same condition group?\n"
                            f"A: {a.label}\nB: {b.label}",
                "why": f"Their centroids are {d:.2f} apart, which on this distance scale is "
                       f"about a 4-point PEG step: inside the optimisation range for one "
                       f"protein, so they may be one family split in two. Flagged rather than "
                       f"merged: the default here is to change nothing, because a merge cannot "
                       f"be undone without re-deriving the groups.",
                "weight": a.n_records_at_creation + b.n_records_at_creation,
                "weight_label": f"{_fmt(a.n_records_at_creation + b.n_records_at_creation)} records",
                "type": "choice",
                # The default is deliberately the status quo, not the merge. Accepting every
                # recommendation in one click must never silently restructure the ontology:
                # merging is the destructive direction and loses a distinction that cannot be
                # recovered without re-deriving the groups.
                "options": [
                    {"value": "keep_both",
                     "label": "Keep both as proposed (no change)", "recommended": True},
                    {"value": f"merge_into::{bigger.id}",
                     "label": f"Merge, keeping “{bigger.label}” ({_fmt(bigger.n_records_at_creation)} records)",
                     "recommended": False},
                    {"value": f"merge_into::{smaller.id}",
                     "label": f"Merge, keeping “{smaller.label}” ({_fmt(smaller.n_records_at_creation)} records)",
                     "recommended": False},
                ],
                "allow_text": True,
                "context": [f"A centroid: {_centroid_text(a)}", f"B centroid: {_centroid_text(b)}"],
            })

    # ---------------------------------------------------------------- orphans
    existing_labels = {g.label for g in ontology.groups}
    orphans = [(label, n) for label, n in diagnostic["l3_cells"]
               if label not in existing_labels and n >= 250
               and not label.startswith("Unassigned")]
    for label, n in orphans[:MAX_PER_GROUP]:
        questions.append({
            "id": f"orphan::{label}",
            "group": "Large bins with no group",
            "question": f"This condition family has no group. Create one?\n{label}",
            "why": f"It holds {_fmt(n)} records, above the {250}-record threshold, but fell "
                   f"outside the top 141 by size. Spec 6.1 asks specifically that large "
                   f"orphans be given a curated home.",
            "weight": n, "weight_label": f"{_fmt(n)} records",
            "type": "choice",
            "options": [
                {"value": "create", "label": "Create a group for it", "recommended": True},
                {"value": "fold_into_l2",
                 "label": "Leave it: falling back to its L2 parent is good enough",
                 "recommended": False},
            ],
            "allow_text": True, "context": [],
        })

    # ------------------------------------------------------------- anchorless
    anchorless = sorted((g for g in ontology.by_level(3) if not g.screen_anchors),
                        key=lambda g: -g.n_records_at_creation)
    for group in anchorless[:MAX_PER_GROUP]:
        questions.append({
            "id": f"anchorless::{group.id}",
            "group": "Groups with nothing orderable",
            "question": f"No commercial well sits near this group. Is it real?\n{group.label}",
            "why": "Either a genuine condition family that vendors do not sell as a well, or "
                   "an artefact of binning. Vendors cover the productive space well, so a "
                   "sizeable family with no nearby well is worth a second look.",
            "weight": group.n_records_at_creation,
            "weight_label": f"{_fmt(group.n_records_at_creation)} records",
            "type": "choice",
            "options": [
                {"value": "keep", "label": "Real: keep it, unanchored", "recommended": True},
                {"value": "drop", "label": "Artefact: drop it and fall back to L2",
                 "recommended": False},
            ],
            "allow_text": True,
            "context": [f"centroid: {_centroid_text(group)}"],
        })

    # ------------------------------------------------------- decision 12.2
    premix_count = components.filter(pl.col("chem_class") == "premix").height
    premix_names = (components.filter(pl.col("chem_class") == "premix")
                    .group_by("name_canonical").agg(pl.len().alias("n"))
                    .sort("n", descending=True).head(5))
    questions.append({
        "id": "decision::12.2_premix_taxonomy",
        "group": "Open decision from the brief",
        "question": "Morpheus and PACT style premixes carry a carboxylic acid mix, an alcohol "
                    "mix and a buffer system at once. They do not fit a seven-class "
                    "precipitant taxonomy. How should they sit in the ontology?",
        "why": "Open decision 12.2, which the brief says must be settled before assignment "
               "begins rather than improvised per entry. Phase 0 fixed the representation "
               "(premixes expand to their constituents) but left the taxonomy open.",
        "weight": premix_count, "weight_label": f"{_fmt(premix_count)} components",
        "type": "choice",
        "options": [
            {"value": "expand_to_constituents",
             "label": "Keep current behaviour: expand to constituents, classify on those",
             "recommended": True},
            {"value": "mixed_system_bucket",
             "label": "Add a mixed_system L1 class for them", "recommended": False},
            {"value": "own_top_level_class",
             "label": "Give each premix family its own top-level class", "recommended": False},
        ],
        "allow_text": True,
        "context": [f"{r['name_canonical']}: {_fmt(r['n'])} components"
                    for r in premix_names.iter_rows(named=True)],
    })

    questions.sort(key=lambda q: -q["weight"])
    return questions


def _centroid_text(group) -> str:
    parts = []
    for axis, value in group.centroid.model_dump().items():
        if value is None:
            continue
        if axis == "peg_log_mw":
            parts.append(f"PEG ~{int(10 ** value)}")
        elif axis == "salt_log_molar":
            parts.append(f"salt {10 ** value:.2f} M")
        elif axis == "salt_hofmeister":
            parts.append(f"Hofmeister {value:+.1f}")
        elif axis == "ph":
            parts.append(f"pH {value:.1f}")
        else:
            parts.append(f"{axis.replace('_', ' ')} {value:.1f}")
    return ", ".join(parts)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--groups", type=Path, default=groups_module.GROUPS_PATH)
    parser.add_argument("--diagnostic", type=Path,
                        default=config.INTERIM_DIR / "ontology_diagnostic.json")
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "group_questions.json")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    ontology = groups_module.load(args.groups)
    diagnostic = json.loads(args.diagnostic.read_text())
    components = pl.read_parquet(args.components)

    with Manifest(STAGE, params={"n_groups": len(ontology.groups)}) as m:
        m.add_input(args.groups).add_input(args.diagnostic)

        questions = build(ontology, diagnostic, components)
        payload = {
            # The Condition Courtroom reads these to title itself, so one interface serves
            # both the parse audit and group curation.
            "title": "Group curation",
            "intro": (f"{len(questions)} questions about the {len(ontology.groups)} proposed "
                      f"condition groups. Everything else was accepted automatically. These "
                      f"are the places where the proposal is redundant, incomplete, or where "
                      f"the brief left a decision open."),
            "schema_version": config.SCHEMA_VERSION,
            "lexicon_version": ontology.version,
            "n_questions": len(questions),
            "totals": {"n_groups": len(ontology.groups),
                       "n_components": components.height,
                       "components_under_question": sum(q["weight"] for q in questions)},
            "questions": questions,
        }
        args.out.write_text(json.dumps(payload, indent=1))

        by_group: dict[str, int] = {}
        for q in questions:
            by_group[q["group"]] = by_group.get(q["group"], 0) + 1
        m.add_output(args.out).note(n_questions=len(questions), groups=by_group)

        print(f"\n{len(questions)} questions about {len(ontology.groups)} groups\n")
        for label, n in sorted(by_group.items(), key=lambda kv: -kv[1]):
            weight = sum(q["weight"] for q in questions if q["group"] == label)
            print(f"  {label:<40} {n:>2} questions  {_fmt(weight):>9} records")
        print("\nheaviest questions:")
        for q in questions[:6]:
            first = q["question"].splitlines()[0]
            print(f"  {_fmt(q['weight']):>8}  {first[:88]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
