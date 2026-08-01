"""Stage `eval.class_audit`: the per-class accuracy number spec 6.6 asks for.

Never run until now, which meant every argument about whether the ontology worked, mine included,
was made without evidence on the only question that matters: **is a condition in the right
class?**

The brief asks for a stratified sample of 1,000 to 2,000 assignments. That was written for a
163-group ontology where each judgement is slow. Classification is now seven classes plus
Unclassified, so a judgement is "does this text contain a PEG, a salt, an organic?" and takes
seconds.

**One condition per question, 96 of them.** An earlier version batched 25 conditions into a single
banded "how many of these are wrong" card, on the reasoning that an error *rate* is what the
accuracy figure needs and a count yields the same estimate for a fraction of the answers. That is
true and it still is, but 400 conditions spread over 16 dense cards asked the reader to hold a
running tally while skimming, which is the part that made it unusable. 96 individual yes/no
verdicts are more answers and less work, and they give exact counts rather than bands.

**Proportional within provenance, not equal per class.** The question is whether the model's
conditions are as well classified as the rules', so each half must be representative of its own
population. Equal allocation across classes would over-sample Organic at roughly 40x its real
share and produce a headline accuracy describing no population at all. Per-class figures are a
by-product at this size and should be read as indicative only.

**Deduplicated by text.** The corpus repeats itself: the same condition is deposited many times,
and judging it twice buys no evidence. Sampling distinct condition texts is what keeps the
judgements informative rather than repetitions of the popular ones.

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
    ./run.sh eval.class_audit --n 48
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
from ..assign.classify import CLASSES, UNCLASSIFIED
from ..manifest import Manifest

STAGE = "eval.class_audit"

# 96 conditions, one screen each: 48 rules-derived and 48 model-derived. At 90% accuracy that is
# about +/- 8 points on each half, which is coarse but decides the only question being asked,
# namely whether the model's conditions are materially worse than the rules'.
DEFAULT_N = 96


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
    parser.add_argument("--n", type=int, default=DEFAULT_N,
                        help="total conditions to judge, split evenly between provenances")
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

    with Manifest(STAGE, params={"n": args.n, "seed": args.seed}) as m:
        m.add_input(args.classes).add_input(args.conditions)
        if args.slm_components:
            m.add_input(args.slm_components)

        # One row per distinct deposition text: judging the same condition twice adds no evidence,
        # and the popular conditions would otherwise crowd out everything else.
        pools: dict[str, list[dict]] = {}
        seen: set[str] = set()
        for row in joined.iter_rows(named=True):
            if not row["condition_class"]:
                continue
            text = " ".join((row["raw_details"] or "").split())
            if not (20 < len(text) < 220) or text.lower() in seen:
                continue
            seen.add(text.lower())
            pools.setdefault(row["provenance"], []).append(row)

        # **Proportional within provenance, not equal per class.** The question this audit exists
        # to answer is whether the model's conditions are as well classified as the rules', and
        # that needs each half to be *representative* of its own population. Equal allocation
        # across classes would over-sample Organic at 40x its real share and give a headline
        # accuracy that describes no population at all.
        half = max(1, args.n // (2 if model_read else 1))
        chosen: list[dict] = []
        for provenance in sorted(pools):
            pool = pools[provenance]
            rng.shuffle(pool)
            chosen.extend(pool[:half])
        rng.shuffle(chosen)

        sampled_per_class: dict[str, int] = {}
        questions: list[dict[str, Any]] = []
        for row in chosen:
            label, provenance = row["condition_class"], row["provenance"]
            stratum = f"{label} ({provenance}-derived)" if model_read else label
            sampled_per_class[stratum] = sampled_per_class.get(stratum, 0) + 1

            key = (row["pdb_id"], row["crystal_id"])
            detected = parts.get(key, [])
            note = ""
            if label == "Unclassified" and row["unclassified_reason"]:
                note = f"Unclassified because of {row['unclassified_reason'].replace('_', ' ')}."

            questions.append({
                "id": f"cond::{row['pdb_id']}::{row['crystal_id']}",
                "pdb_id": row["pdb_id"],
                "provenance": provenance,
                "condition_class": label,
                "assigned": label,
                "note": note,
                "text": " ".join((row["raw_details"] or "").split()),
                "found": detected or ["nothing identified"],
                "type": "flag",
                "question": "Is this the wrong class?",
            })

        payload = {
            "schema_version": config.SCHEMA_VERSION,
            "lexicon_version": config.ONTOLOGY_VERSION,
            "title": "Classification accuracy audit",
            # Shipped so the page never hardcodes the taxonomy: the seven classes are the
            # non-empty subsets of {Organic, PEG, Salt}, and adding an eighth would otherwise
            # leave the audit UI quietly offering the old list.
            "classes": sorted(CLASSES.values()) + [UNCLASSIFIED],
            # What a wrong class can actually mean. Classification is a pure function of the
            # components, so a wrong class with correct components is impossible: every error is
            # a parse error, a lexicon chem_class error, or a gating error. Asking which routes
            # the fix, and the parse ones are the hand-labelled examples the next round needs.
            "causes": [
                {"value": "reagent_missed",
                 "label": "A reagent was missed, misread or given the wrong amount",
                 "fixes": "parser"},
                {"value": "class_does_not_follow",
                 "label": "The reagents are right, but the class does not follow from them",
                 "fixes": "lexicon chem_class or the family mapping"},
                {"value": "should_be_unclassified",
                 "label": "It should not have a class at all",
                 "fixes": "classifier gating"},
                {"value": "should_be_classified",
                 "label": "It should have a class, but was left Unclassified",
                 "fixes": "classifier gating"},
            ],
            "intro": (f"{len(questions)} conditions, one at a time. Each shows the deposition "
                      f"text, the reagents the parser found and the class it was given. Tick only "
                      f"if the class is wrong, then Next."
                      + (" Half were read by the fine-tuned model and half by the rules alone, "
                         "which is what separates the model's accuracy from the rules'."
                         if model_read else "")),
            "n_questions": len(questions),
            "totals": {"per_class": sampled_per_class, "n_questions": len(questions)},
            "questions": questions,
        }
        args.out.write_text(json.dumps(payload, indent=2) + "\n")
        m.add_output(args.out).note(n_questions=len(questions), **{
            "n_" + "".join(c if c.isalnum() else "_" for c in k.lower()): v
            for k, v in sampled_per_class.items()})

        print(f"\n  {len(questions)} conditions to judge, one per screen:")
        for label, n in sorted(sampled_per_class.items(), key=lambda kv: -kv[1]):
            print(f"    {label:<34} {n:>4}")
        print(f"\n  {args.out}")
        print(f"  open app/condition_courtroom_v7.html and drop the file on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
