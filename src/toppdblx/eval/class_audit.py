"""Stage `eval.class_audit`: the per-class accuracy number spec 6.6 asks for.

Never run until now, which meant every argument about whether the ontology worked, mine included,
was made without evidence on the only question that matters: **is a condition in the right
class?**

The brief asks for a stratified sample of 1,000 to 2,000 assignments. That was written for a
163-group ontology where each judgement is slow. Classification is now seven classes plus
Unclassified, so a judgement is "does this text contain a PEG, a salt, an organic?" and takes
seconds. The sample is therefore sized for the interval it buys rather than for the brief's
number:

    n = 200 overall            +/- 3.5 points at 90% accuracy
    n = 25 per class            +/- 12 points, coarse but enough to find a broken class

**Stratified by class, not by frequency.** A proportional sample would be 24% Salt/PEG and 1.4%
Organic, so the small classes would get too few judgements to say anything about. Equal
allocation costs nothing here and gives every class its own number.

**Deduplicated by text.** The corpus repeats itself: the same condition is deposited many times,
and judging it twice buys no evidence. Sampling distinct condition texts is what keeps 200
judgements informative rather than 200 repetitions of the popular ones.

Each question shows the raw deposition text and what the classifier made of it. The answer is
whether that is right, so a wrong answer localises the fault: the class itself, or the parse
underneath it.

    ./run.sh eval.class_audit
    ./run.sh eval.class_audit --per-class 40
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Optional

import polars as pl

from .. import config
from ..manifest import Manifest

STAGE = "eval.class_audit"

DEFAULT_PER_CLASS = 25


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--classes", type=Path,
                        default=config.INTERIM_DIR / "condition_classes.parquet")
    parser.add_argument("--conditions", type=Path,
                        default=config.INTERIM_DIR / "parsed_conditions.parquet")
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "class_audit_questions.json")
    parser.add_argument("--per-class", type=int, default=DEFAULT_PER_CLASS)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    rng = random.Random(args.seed)
    classes = pl.read_parquet(args.classes)
    conditions = pl.read_parquet(args.conditions).select("pdb_id", "crystal_id", "raw_details")
    components = pl.read_parquet(args.components)

    joined = classes.join(conditions, on=["pdb_id", "crystal_id"], how="left")

    parts: dict[tuple, list[str]] = {}
    for row in components.iter_rows(named=True):
        if row["name_canonical"]:
            amount = (f"{row['concentration']:g} {row['unit']}"
                      if row["concentration"] is not None and row["unit"] else "amount unstated")
            parts.setdefault((row["pdb_id"], row["crystal_id"]), []).append(
                f"{row['name_canonical'].replace('_', ' ').lower()} ({amount})")

    with Manifest(STAGE, params={"per_class": args.per_class, "seed": args.seed}) as m:
        m.add_input(args.classes).add_input(args.conditions)

        questions: list[dict[str, Any]] = []
        sampled_per_class: dict[str, int] = {}
        for (label,), frame in joined.group_by(["condition_class"]):
            if not label:
                continue
            # One row per distinct deposition text: judging the same condition twice adds no
            # evidence, and the popular conditions would otherwise crowd out everything else.
            seen: set[str] = set()
            candidates = []
            for row in frame.iter_rows(named=True):
                text = " ".join((row["raw_details"] or "").split())
                if not (20 < len(text) < 220) or text.lower() in seen:
                    continue
                seen.add(text.lower())
                candidates.append(row)
            rng.shuffle(candidates)
            chosen = candidates[:args.per_class]
            sampled_per_class[label] = len(chosen)

            for row in chosen:
                key = (row["pdb_id"], row["crystal_id"])
                detected = parts.get(key, [])
                reason = row["unclassified_reason"]
                verdict = (f"Unclassified, because {reason.replace('_', ' ')}"
                           if label == "Unclassified" else label)
                questions.append({
                    "id": f"class::{row['pdb_id']}::{row['crystal_id']}",
                    "group": label,
                    "question": f"Classified as {verdict}. Is that right?",
                    "why": (f"Reagents the parser found: "
                            f"{'; '.join(detected) if detected else 'none'}."),
                    "weight": 1,
                    "weight_label": label,
                    "type": "choice",
                    "options": [
                        {"value": "correct", "label": "Correct", "recommended": True},
                        {"value": "wrong_class",
                         "label": "Wrong class (the parse looks right)", "recommended": False},
                        {"value": "wrong_parse",
                         "label": "Wrong parse (a reagent was missed or misread)",
                         "recommended": False},
                    ],
                    "allow_text": False,
                    "context": [" ".join((row["raw_details"] or "").split())[:200]],
                })

        rng.shuffle(questions)
        payload = {
            "schema_version": config.SCHEMA_VERSION,
            "lexicon_version": config.ONTOLOGY_VERSION,
            "title": "Classification accuracy audit",
            "intro": (f"{len(questions)} conditions, sampled evenly across the eight outcomes so "
                      f"every class gets its own number. Each shows the raw deposition text and "
                      f"what the classifier made of it. Everything defaults to Correct, so this "
                      f"is a hunt for the wrong ones. Separating a wrong class from a wrong "
                      f"parse matters: the first is an ontology problem, the second a parser "
                      f"one."),
            "n_questions": len(questions),
            "totals": {"per_class": sampled_per_class, "n_questions": len(questions)},
            "questions": questions,
        }
        args.out.write_text(json.dumps(payload, indent=2) + "\n")
        m.add_output(args.out).note(n_questions=len(questions), **{
            f"n_{k.lower().replace('/', '_')}": v for k, v in sampled_per_class.items()})

        print(f"\n  {len(questions)} judgements, {args.per_class} per class where available:")
        for label, n in sorted(sampled_per_class.items(), key=lambda kv: -kv[1]):
            print(f"    {label:<20} {n:>4}")
        print(f"\n  at n={len(questions)} overall, a 90% accuracy reading carries a 95% interval")
        print(f"  of roughly plus or minus 4 points; per class it is nearer 12.")
        print(f"\n  {args.out}")
        print(f"  open app/condition_courtroom_v5.html and drop the file on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
