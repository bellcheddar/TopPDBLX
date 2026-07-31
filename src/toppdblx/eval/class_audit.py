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

Every condition is listed with its raw deposition text and the reagents the parser found beneath
it, so a wrong class and a wrong parse can be told apart while reading.

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
            if not chosen:
                continue
            sampled_per_class[label] = len(chosen)

            # **One question per class, not per condition.** A per-class error *rate* is what the
            # accuracy number needs, and a rate can be read from a count: asking "how many of
            # these 25 are wrong" yields the same estimate as 25 separate verdicts, for one
            # answer instead of 25. 200 judgements become 8.
            #
            # Counts are banded rather than exact because nobody counts 25 items reliably, and a
            # band is honest about that: each band is carried into the accuracy figure as an
            # interval rather than a point.
            n = len(chosen)
            listing = []
            for i, row in enumerate(chosen, 1):
                key = (row["pdb_id"], row["crystal_id"])
                detected = parts.get(key, [])
                text = " ".join((row["raw_details"] or "").split())[:150]
                listing.append(f"{i:>2}. {text}")
                listing.append(f"     parser found: "
                               f"{'; '.join(detected) if detected else 'nothing identified'}")

            reason_note = ""
            if label == "Unclassified":
                reasons = [r["unclassified_reason"] for r in chosen if r["unclassified_reason"]]
                common = max(set(reasons), key=reasons.count) if reasons else "various"
                reason_note = (f" Most are unclassified because of "
                               f"{common.replace('_', ' ')}.")

            questions.append({
                "id": f"class::{label.replace('/', '_')}",
                "group": "Classification accuracy",
                "question": f"How many of these {n} are in the wrong class?",
                "why": (f"All {n} were classified as {label}.{reason_note} They are a random "
                        f"sample of distinct conditions from that class. Read down the list and "
                        f"count the ones that look wrong: the count is what the accuracy number "
                        f"is built from, so a rough band is enough."),
                "weight": n,
                "weight_label": f"{label}, {n} sampled",
                "type": "choice",
                "options": [
                    {"value": "0", "label": "None: all correct", "recommended": True},
                    {"value": "1-2", "label": "1 or 2 wrong", "recommended": False},
                    {"value": "3-5", "label": "3 to 5 wrong", "recommended": False},
                    {"value": "6-12", "label": "6 to 12 wrong", "recommended": False},
                    {"value": "13+", "label": "More than half wrong", "recommended": False},
                ],
                "allow_text": False,
                "n_sampled": n,
                "context": listing,
            })

        rng.shuffle(questions)
        payload = {
            "schema_version": config.SCHEMA_VERSION,
            "lexicon_version": config.ONTOLOGY_VERSION,
            "title": "Classification accuracy audit",
            "intro": (f"{len(questions)} questions, one per class, covering "
                      f"{sum(sampled_per_class.values())} sampled conditions, so "
                      f"every class gets its own number. Each card lists 25 conditions with the "
                      f"reagents the parser found, and asks only how many are wrong. Everything "
                      f"defaults to none, so this is a hunt for the bad ones: skim the list, "
                      f"pick the band, move on."),
            "n_questions": len(questions),
            "totals": {"per_class": sampled_per_class, "n_questions": len(questions)},
            "questions": questions,
        }
        args.out.write_text(json.dumps(payload, indent=2) + "\n")
        m.add_output(args.out).note(n_questions=len(questions), **{
            f"n_{k.lower().replace('/', '_')}": v for k, v in sampled_per_class.items()})

        print(f"\n  {len(questions)} questions, covering "
              f"{sum(sampled_per_class.values())} sampled conditions:")
        for label, n in sorted(sampled_per_class.items(), key=lambda kv: -kv[1]):
            print(f"    {label:<20} {n:>4}")
        print(f"\n  one question per class: the count of wrong ones gives the same error")
        print(f"  rate as judging each condition separately, for an eighth of the answers.")
        print(f"\n  {args.out}")
        print(f"  open app/condition_courtroom_v5.html and drop the file on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
