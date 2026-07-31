"""SUPERSEDED by `assign.classify` at ontology 0.3.0.

The three-level ontology this stage belongs to was withdrawn: its groups were binned
from the corpus and then had labels retrofitted, which spec 6.1 rejects, and several
were not chemically coherent (median L2 purity 49%). Classification is now the seven
JCSG Top96 precipitant classes with no sub-levels. Kept for provenance and because the
diagnostics behind that decision are worth being able to reproduce.

Stage `assign.strategy_questions`: a strategic review of the ontology, not another audit.

Built after a stall. Several rounds went into naming L2 groups, and each round fixed a real
presentation fault while the underlying question went unasked: **is this the right thing to be
building at all?** Re-reading spec section 6 says no, on four counts, and every one is a decision
that belongs to Marc rather than to me:

  6.1  Groups should be *hand-defined and human-readable*, with clustering used only as a
       diagnostic to find large orphans. What exists was binned from the corpus and then had
       names retrofitted, which is the emergent approach the brief rejects in its first line.

  6.4  Morpheus and PACT style conditions carry an acid mix, an alcohol mix and a buffer system
       at once and do not fit a seven-class taxonomy. The brief says **decide before assignment
       begins**. Phase 0 deferred it as "the taxonomy is Phase 1 and can wait". Phase 1 is here
       and it was never decided, so assignment has been running on an undecided question.

  6.5  The payoff of the screen cross-reference is that output becomes *orderable*: "maps to
       PACT E6, already in the fridge" beats "PEG 3350 at 18.4%, pH 6.9". All 122 L3 groups
       carry a screen anchor. **None of the 41 L2 groups do.** The naming rounds were spent on
       the level that cannot be ordered, producing labels in exactly the form the brief calls
       worse.

  6.6  A stratified hand-audit of 1,000 to 2,000 assignments is how a real per-class accuracy
       number is obtained. It has never been run, so every argument about group quality so far,
       mine included, has been made without one.

Questions carry the measured evidence rather than an opinion, and each states what it would cost
to act on. Output feeds `app/condition_courtroom_v5.html`.

    ./run.sh assign.strategy_questions
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

import polars as pl
import yaml

from .. import config
from ..manifest import Manifest

STAGE = "assign.strategy_questions"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--assignments", type=Path,
                        default=config.INTERIM_DIR / "group_assignments.parquet")
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "strategy_questions.json")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    a = pl.read_parquet(args.assignments)
    c = pl.read_parquet(args.components)
    groups_doc = yaml.safe_load((config.ONTOLOGY_DIR / "groups.yaml").read_text())
    groups = groups_doc["groups"] if isinstance(groups_doc, dict) else groups_doc
    l2 = [g for g in groups if g["level"] == 2]
    l3 = [g for g in groups if g["level"] == 3]

    total = a.height
    at_l3 = a.filter(pl.col("assigned_level") == 3).height
    at_l2 = a.filter(pl.col("assigned_level") == 2).height
    unassigned = a.filter(pl.col("assigned_level") == 0).height
    anchored = a.filter(pl.col("screen_anchor").is_not_null()).height
    premix = c.filter(pl.col("premix_id").is_not_null())["pdb_id"].n_unique()

    def q(qid: str, group: str, question: str, why: str, weight: int,
          options: list[tuple[str, str, bool]], context: list[str]) -> dict[str, Any]:
        return {
            "id": qid, "group": group, "question": question, "why": why,
            "weight": weight, "weight_label": f"{weight:,} conditions affected",
            "type": "choice", "allow_text": True,
            "options": [{"value": v, "label": l, "recommended": r} for v, l, r in options],
            "context": context,
        }

    questions = [
        q("strategy::accuracy_first", "What to do before anything else",
          "Should the stratified assignment audit (spec 6.6) run before any more ontology work?",
          "The brief specifies hand-auditing 1,000 to 2,000 assignments to get a real per-class "
          "accuracy number. It has never been run. Every judgement about group quality so far, "
          "including mine, has been made without one.",
          total,
          [("audit_first", "Yes: run the stratified audit first, then decide the rest on data",
            True),
           ("audit_later", "No: fix the ontology first, audit the result once", False),
           ("skip_audit", "Skip it: assignment accuracy is not worth 1,000 judgements", False)],
          [f"{total:,} conditions are assigned and none of that assignment has been validated",
           "spec 6.6: 'hand-audit a stratified sample of 1,000 to 2,000 assignments to get a "
           "real per-class accuracy number. That is a weekend of work, not a year.'",
           "Cost: one audit round of roughly 300 to 500 judgements at the courtroom's usual "
           "compression, since identical conditions do not need judging twice"]),

        q("strategy::l2_purpose", "What an L2 label is for",
          "What should an L2 group be called, given none of them can be ordered?",
          "All 122 L3 groups carry a screen anchor; none of the 41 L2 groups do. The brief's "
          "payoff is that output becomes orderable, and L2 by construction cannot be. The last "
          "several rounds produced labels like 'PEG 3350 and Ammonium sulfate, 16% to 30%', "
          "which is the exact form spec 6.5 calls worse than a screen reference.",
          at_l2,
          [("nearest_well", "Name each L2 group by its nearest orderable screen well, so even "
            "the fallback level points at something you can set up", True),
           ("chemistry", "Keep chemistry descriptions: they are honest about what the group is, "
            "even though nothing can be ordered from them", False),
           ("drop_l2_names", "Do not name L2 at all: report the L1 class and the distance, and "
            "treat L2 as an internal fallback rather than a user-facing label", False),
           ("drop_l2", "Remove the L2 level entirely: assign at L3 or leave unassigned", False)],
          [f"{at_l2:,} conditions ({100 * at_l2 / total:.1f}%) currently stop at L2",
           f"{anchored:,} conditions ({100 * anchored / total:.1f}%) reach an orderable anchor",
           "spec 6.5: \"'Maps to PACT E6, already in the fridge' beats 'PEG 3350 at 18.4%, "
           "pH 6.9'\""]),

        q("strategy::multicomponent", "The decision the brief said not to defer",
          "How should Morpheus and PACT style multi-component conditions be classified?",
          "Spec 6.4 says decide this before assignment begins and do not improvise per entry. "
          "Phase 0 deferred it as 'the taxonomy is Phase 1 and can wait'. Phase 1 is here, it "
          "was never decided, and assignment has been running without it. These conditions "
          "carry an acid mix, an alcohol mix and a buffer system at once, so they land in "
          "whichever of the seven classes their expanded constituents happen to favour.",
          premix,
          [("own_class", "Give them their own top-level class, so a Morpheus condition is never "
            "forced into a seven-class taxonomy it does not fit", True),
           ("mixed_bucket", "Accept a `mixed_system` bucket at L1: simpler, but says nothing "
            "about the chemistry", False),
           ("leave", "Leave them expanded into constituents as now, and accept that they "
            "scatter across classes", False)],
          [f"{premix:,} conditions contain at least one premixed stock",
           "Phase 0 stored them expanded into constituents with a premix_id, so either choice "
           "is still available and no information was lost",
           "spec 6.4: 'Do not leave this to be improvised per entry.'"]),

        q("strategy::curated_vs_binned", "How the groups should exist at all",
          "Should the ontology be rebuilt as hand-defined groups rather than corpus bins?",
          "Spec 6.1 opens by saying groups are hand-defined and human-readable, with clustering "
          "used only as a diagnostic to find large orphans worth covering. What exists was "
          "binned from the corpus on precipitant class, PEG molecular weight and salt family, "
          "and then had names retrofitted. That is the emergent approach the brief rejects, and "
          "it is why the groups keep reading as arbitrary.",
          total,
          [("hand_define_l3", "Hand-define the L3 groups from screen wells (the Top96, JCSG "
            "Core, PACT, Morpheus), then assign conditions to them: the brief's actual design",
            True),
           ("keep_bins_fix_purity", "Keep the binned groups but tighten them until purity is "
            "acceptable, as the band constraint already did", False),
           ("hybrid", "Hand-define the large groups only, and leave the tail binned", False)],
          [f"{len(l3)} L3 groups exist, all anchored to a screen well after the fact",
           f"{len(l2)} L2 groups exist, none anchored",
           "434 wells across 9 commercial screens are already parsed and available as "
           "ready-made group definitions"]),

        q("strategy::unassigned", "The third of the corpus with no group",
          "What should happen to conditions that reach no group?",
          "Roughly a third of usable conditions are unassigned, and the band constraint added to "
          "that by refusing to place records in groups whose label would be false of them. Most "
          "have no measurable precipitant concentration at all, so no centroid can place them.",
          unassigned,
          [("report_honestly", "Leave them unassigned and report it prominently: a condition "
            "with no stated concentration cannot be placed without inventing data", True),
           ("l1_only", "Assign them at L1 only, where a precipitant class is known", False),
           ("nearest_regardless", "Assign to the nearest group regardless of distance, and rely "
            "on the confidence band to warn users", False)],
          [f"{unassigned:,} conditions ({100 * unassigned / total:.1f}%) reach no group",
           "About 22,000 precipitant components state no amount in the source text at all",
           "Phase 1 already rejected giving them a centroid: it would dress an absence of "
           "evidence as a chemical claim"]),

        q("strategy::next_work", "Where the remaining effort goes",
          "What is the single most valuable next piece of work?",
          "Curation has measurably outperformed modelling on this project: 40 lexicon decisions "
          "moved component identification about a point, while tripling training exposure moved "
          "residual identification by zero. But the assignment audit would tell us whether the "
          "ontology is even working, which nothing currently does.",
          total,
          [("assignment_audit", "The stratified assignment audit: it is the only thing that "
            "would tell us whether any of this is correct", True),
           ("more_lexicon", "More lexicon curation: 120 decisions are queued and worth about "
            "1.6 points of component identification", False),
           ("rebuild_ontology", "Rebuild the ontology hand-first from screen wells", False),
           ("publish", "Stop refining and publish 0.1.0 for a citable DOI", False),
           ("front_end", "Build the browser front end and let real use expose the problems",
            False)],
          ["Measured: lexicon curation about +1 point of identification per 40 decisions",
           "Measured: more training epochs, no detectable gain on the residual",
           "Unmeasured: whether a condition assigned to a group actually belongs there"]),
    ]

    with Manifest(STAGE, params={"n_questions": len(questions)}) as m:
        m.add_input(args.assignments).add_input(args.components)
        payload = {
            "schema_version": config.SCHEMA_VERSION,
            "lexicon_version": groups_doc.get("version", "unknown"),
            "title": "Ontology strategy review",
            "intro": (f"{len(questions)} decisions about direction, not about individual groups. "
                      f"Each one is a place where the work has drifted from spec section 6, with "
                      f"the measured evidence attached. Recommendations are mine and are argued "
                      f"from the brief; the point of this round is that they are yours to "
                      f"overrule."),
            "n_questions": len(questions),
            "totals": {
                "n_conditions": total, "n_at_l3": at_l3, "n_at_l2": at_l2,
                "n_unassigned": unassigned, "n_orderable": anchored,
                "n_l2_groups": len(l2), "n_l3_groups": len(l3),
                "n_l2_groups_anchored": sum(1 for g in l2 if g.get("screen_anchors")),
                "n_premix_conditions": premix,
            },
            "questions": questions,
        }
        args.out.write_text(json.dumps(payload, indent=2) + "\n")
        m.add_output(args.out).note(**payload["totals"], n_questions=len(questions))

        print(f"\n  {len(questions)} strategic decisions\n")
        print(f"  where the corpus currently lands:")
        print(f"    at L3, orderable      {at_l3:>8,}  ({100 * at_l3 / total:.1f}%)")
        print(f"    at L2, not orderable  {at_l2:>8,}  ({100 * at_l2 / total:.1f}%)")
        print(f"    unassigned            {unassigned:>8,}  ({100 * unassigned / total:.1f}%)")
        print(f"\n  {args.out}")
        print(f"  open app/condition_courtroom_v5.html and drop the file on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
