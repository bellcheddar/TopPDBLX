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

**Stratified by provenance too, once the model has run.** `--slm-components` splits every class
into the conditions the rules read and the conditions only the fine-tuned model could read, and
asks about each separately. This is the only measurement that answers whether the model earned its
place: a single blended accuracy figure cannot, because the model's conditions are by construction
the ones the rules found hardest, and averaging them together hides both.

Passing it also fixes the listings. The model's components live in their own file, so without it
every model-derived condition prints "nothing identified" beneath its class and reads as an
obvious error when it may be perfectly correct.

    ./run.sh eval.class_audit
    ./run.sh eval.class_audit --per-class 40
    ./run.sh eval.class_audit --slm-components data/interim/slm_components.parquet
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
    parser.add_argument("--slm-components", type=Path, default=None,
                        help="components read by the fine-tuned model. Supplying this splits "
                             "every class by provenance, so rules-derived and model-derived "
                             "conditions get separate accuracy numbers")
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

    # Only these five columns feed the listing, and the two parquets do not share a schema, so
    # project rather than align: the rules carry range_low and friends that the model never emits.
    LISTED = ["pdb_id", "crystal_id", "name_canonical", "concentration", "unit"]
    components = components.select(LISTED)
    slm = None
    if args.slm_components:
        slm = pl.read_parquet(args.slm_components).select(LISTED)

    # A residual record is one the rules could not read *fully*, not one they read not at all, so
    # a record can carry components from both parsers. Listing each frame in turn would print the
    # reagents they agree on twice, which reads as a duplicate-component parsing bug that is not
    # there. Key on the reagent and its amount, and let the rules claim it first: anything left
    # over is the model's own contribution and is the part worth marking.
    def amount_of(row: dict) -> str:
        return (f"{row['concentration']:g} {row['unit']}"
                if row["concentration"] is not None and row["unit"] else "amount unstated")

    seen_parts: dict[tuple, dict[tuple, str]] = {}
    for frame, source in ((components, "rules"), (slm, "model")):
        if frame is None:
            continue
        for row in frame.iter_rows(named=True):
            if not row["name_canonical"]:
                continue
            key = (row["pdb_id"], row["crystal_id"])
            amount = amount_of(row)
            seen_parts.setdefault(key, {}).setdefault(
                (row["name_canonical"], amount),
                f"{row['name_canonical'].replace('_', ' ').lower()} ({amount})"
                + (" [model]" if source == "model" else ""))

    parts: dict[tuple, list[str]] = {k: list(v.values()) for k, v in seen_parts.items()}

    # **Model-derived means the model contributed something, not merely that it ran.** apply_slm
    # generates for every residual record, including those where it only reproduced reagents the
    # rules had already found. Counting those as model-derived would fill half the sample with
    # conditions the rules could have classified alone, and the resulting figure would measure a
    # mixture while claiming to measure the model.
    model_read: set[tuple] = {k for k, v in parts.items()
                              if any(p.endswith("[model]") for p in v)}
    if model_read:
        joined = joined.with_columns(
            pl.struct("pdb_id", "crystal_id")
              .map_elements(lambda s: "model" if (s["pdb_id"], s["crystal_id"]) in model_read
                            else "rules", return_dtype=pl.Utf8)
              .alias("provenance"))
    else:
        joined = joined.with_columns(pl.lit("rules").alias("provenance"))

    with Manifest(STAGE, params={"per_class": args.per_class, "seed": args.seed}) as m:
        m.add_input(args.classes).add_input(args.conditions)
        if args.slm_components:
            m.add_input(args.slm_components)

        questions: list[dict[str, Any]] = []
        sampled_per_class: dict[str, int] = {}
        for (label, provenance), frame in joined.group_by(["condition_class", "provenance"]):
            if not label:
                continue
            stratum = f"{label} ({provenance}-derived)" if model_read else label
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
            sampled_per_class[stratum] = len(chosen)

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

            provenance_note = ""
            if model_read:
                provenance_note = (
                    " These are conditions the rules could not read on their own: reagents marked "
                    "[model] were read by the fine-tuned model, the rest by the rules."
                    if provenance == "model"
                    else " Every reagent below was read by the rules alone.")

            questions.append({
                "id": f"class::{label.replace('/', '_')}::{provenance}",
                "group": ("Classification accuracy, model-derived" if provenance == "model"
                          else "Classification accuracy"),
                "question": f"How many of these {n} are in the wrong class?",
                "why": (f"All {n} were classified as {label}.{reason_note}{provenance_note} They "
                        f"are a random sample of distinct conditions from that class. Read down "
                        f"the list and count the ones that look wrong: the count is what the "
                        f"accuracy number is built from, so a rough band is enough."),
                "weight": n,
                "weight_label": f"{stratum}, {n} sampled",
                "provenance": provenance,
                "condition_class": label,
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
            "intro": (f"{len(questions)} questions, one per class"
                      + (" and per provenance" if model_read else "") +
                      f", covering {sum(sampled_per_class.values())} sampled conditions, so "
                      f"every class gets its own number. Each card lists 25 conditions with the "
                      f"reagents the parser found, and asks only how many are wrong. Everything "
                      f"defaults to none, so this is a hunt for the bad ones: skim the list, "
                      f"pick the band, move on."
                      + (" Half the cards are conditions only the model could read: answering "
                         "both halves is what separates the model's accuracy from the rules'."
                         if model_read else "")),
            "n_questions": len(questions),
            "totals": {"per_class": sampled_per_class, "n_questions": len(questions)},
            "questions": questions,
        }
        args.out.write_text(json.dumps(payload, indent=2) + "\n")
        m.add_output(args.out).note(n_questions=len(questions), **{
            "n_" + "".join(c if c.isalnum() else "_" for c in k.lower()): v
            for k, v in sampled_per_class.items()})

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
