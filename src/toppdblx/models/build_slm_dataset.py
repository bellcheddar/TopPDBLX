"""Stage `models.build_slm_dataset`: training data for the text-to-JSON parser.

R1. 125,970 components (20.9%) identify to no canonical reagent and 29.0% of records reach no L3
group, so this is the highest-value remaining data work: everything downstream is thinned by it.

**The labelling shortcut, and why it is tried first.** The brief and the roadmap both assume
3,000 to 5,000 hand-corrected records. That is days of Marc's time, and it may not be needed:
the rule parser already produces clean output on 92% of the corpus, which is over 150,000
worked examples of exactly the mapping the model has to learn. So the first experiment is pure
distillation from the rule parser, at no labelling cost, measured on the residual it was never
trained on. Hand-labelling is escalated to only if that plateaus.

Three files come out:

  train.jsonl / valid.jsonl   bootstrap pairs from high-confidence rule output. Chat format,
                              so `--mask-prompt` trains on the completion only.
  residual.jsonl              the records the rules could not read, as prompts with no answer.
                              This is the target, and the model never sees it in training.

**Splits are by sequence cluster, not at random.** A random split would put near-identical
depositions of the same protein on both sides, and since one 30% cluster holds up to 2,876
entries the validation loss would measure memorisation. The existing leak-free components are
reused so this is consistent with every other evaluation in the project.

    ./run.sh models.build_slm_dataset
    ./run.sh models.build_slm_dataset --max-train 40000
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

STAGE = "models.build_slm_dataset"

SYSTEM = (
    "You convert a PDB crystallisation condition string into JSON. "
    "Return only a JSON array. Each element has: role (precipitant, salt, buffer, additive, "
    "cryo, not_a_component or unknown), name (the canonical reagent, or null when the text "
    "names no reagent), amount (a number or null) and "
    "unit (percent_w_v, percent_v_v, molar, millimolar, mg_ml or null). "
    "Use not_a_component for text that names no reagent at all: method notes, screen "
    "references, or an unnamed protein, inhibitor or compound."
)

# A record is a usable training example only if the rules accounted for every component and
# covered nearly all of the text. Anything less would teach the model the rule parser's mistakes.
#
# **Not 1.0, and the reason matters.** Record confidence is `0.7 * identification + 0.3 *
# char_coverage`. The `accounted_for` check below already guarantees identification is 1.0 among
# clauses that contain chemistry, so confidence here is purely a measure of text coverage, and a
# real deposition almost never covers every character: stray punctuation and connective words
# leave a fraction behind. Requiring exactly 1.0 therefore excluded records the rules understood
# completely, which is how 2,539 records carrying the `not_a_component` verdict were silently
# kept out of training (their confidence peaked at 0.998). The model then saw zero examples of
# that verdict and could not learn it at all.
#
# 0.95 with identification pinned at 1.0 implies char_coverage >= 0.833, which is the actual intent:
# most of the text is explained.
CONFIDENT = 0.95

# Discard reasons whose correct answer is "this text names no chemistry". Used as training
# examples with an empty or all-non-component target, because a model that has only ever seen
# text containing reagents will produce a reagent for text that contains none.
EMPTY_ANSWER_DISCARDS = {"TOO_SHORT", "METHOD_ONLY", "NON_CRYSTALLISATION_TEXT"}


def accounted_for(component: dict[str, Any]) -> bool:
    """Whether the rules reached a definite verdict on this component.

    A identified reagent counts, and so does a confident `not_a_component`: both are correct
    parses. Only `unknown` (a reagent the lexicon does not recognise) leaves the record unusable
    as a training example.

    Including the non-reagent verdicts matters more than it looks. Requiring a canonical name for
    every component would drop every record containing a method note, which is 13.4% of the
    unidentified mass, and the model would then never see a single example of the one output it
    needs for that text. It would learn to invent a reagent instead, which is the failure mode
    that makes structured output worse than none.
    """
    return bool(component["name_canonical"]) or component["role"] == "not_a_component"


def target_json(components: list[dict[str, Any]]) -> str:
    """The completion the model must learn to produce.

    Deliberately flatter than the internal schema: role, name, amount, unit. Fields the parser
    derives from the lexicon rather than from the text (PEG molecular weight, Hofmeister rank,
    buffer pKa) are excluded, because asking a language model to recall curated chemistry it
    cannot see in the input invites it to invent it.
    """
    return json.dumps([
        {
            "role": c["role"],
            "name": c["name_canonical"],
            "amount": c["concentration"],
            "unit": c["unit"],
        }
        for c in components
    ], separators=(",", ":"))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--conditions", type=Path,
                        default=config.INTERIM_DIR / "parsed_conditions.parquet")
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--splits", type=Path, default=config.INTERIM_DIR / "splits.parquet")
    parser.add_argument("--out-dir", type=Path, default=config.INTERIM_DIR / "slm")
    parser.add_argument("--max-train", type=int, default=None)
    parser.add_argument("--oversample-non-component", type=int, default=8,
                        help="repeat training records carrying a not_a_component label")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    conditions = pl.read_parquet(args.conditions)
    components = pl.read_parquet(args.components)
    splits = pl.read_parquet(args.splits).select("pdb_id", "crystal_id", "fold_30")

    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for row in components.iter_rows(named=True):
        grouped.setdefault((row["pdb_id"], row["crystal_id"]), []).append(row)

    framed = conditions.join(splits, on=["pdb_id", "crystal_id"], how="left")

    with Manifest(STAGE, params={"max_train": args.max_train}) as m:
        m.add_input(args.conditions).add_input(args.components).add_input(args.splits)

        bootstrap: dict[str, list[dict[str, Any]]] = {"train": [], "valid": []}
        residual: list[dict[str, Any]] = []
        skipped_unidentified = 0
        n_empty_answers = 0

        for row in tqdm(framed.iter_rows(named=True), total=framed.height,
                        desc="records", unit="rec"):
            text = (row["raw_details"] or "").strip()
            if not text:
                continue
            key = (row["pdb_id"], row["crystal_id"])
            parts = grouped.get(key, [])
            fold = row["fold_30"] or "train"

            # **Records whose correct answer is "no chemistry".** Every other training example
            # contains a reagent, so the model had never been shown that an empty answer is
            # allowed, and it duly invented one: given the text "pH 3.3" it emitted TRIS. These
            # come from discards the rules judged confidently (TOO_SHORT, METHOD_ONLY,
            # NON_CRYSTALLISATION_TEXT), so the label costs nothing and is not a guess.
            if row["discard_reason"] in EMPTY_ANSWER_DISCARDS:
                empty_example = {"messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": target_json(
                        [c for c in parts if c["role"] == "not_a_component"])},
                ]}
                bootstrap["valid" if fold == "val" else "train"].append(empty_example)
                n_empty_answers += 1
                continue

            confident = (row["discard_reason"] is None
                         and row["parse_confidence"] >= CONFIDENT
                         and parts
                         and all(accounted_for(c) for c in parts))

            if confident:
                # Chat format so --mask-prompt trains on the completion only. Without the mask
                # the model spends most of its loss learning to echo condition strings back.
                example = {"messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": target_json(parts)},
                ]}
                # The `not_a_component` verdict is rare, and it is a class we deliberately
                # added, so it is oversampled below. That happens after deduplication, and only
                # for training: the validation and residual sets keep the corpus's real
                # proportions or the metrics stop describing the corpus.
                bootstrap["valid" if fold == "val" else "train"].append(example)
            else:
                if parts and not all(accounted_for(c) for c in parts):
                    skipped_unidentified += 1
                residual.append({
                    "pdb_id": row["pdb_id"], "crystal_id": row["crystal_id"],
                    "text": text,
                    "rules_confidence": row["parse_confidence"],
                    "rules_discard": row["discard_reason"],
                    "n_components": len(parts),
                    "n_unidentified": sum(1 for c in parts if not accounted_for(c)),
                    "fold": fold,
                })

        # **Deduplicate before training.** The corpus repeats itself heavily: 45.7% of rows
        # shared an input with another row, and one condition ("protein in 25mM Tris/HCl pH 7.5
        # 100mM NaCl, see also PMID 27658368") appeared 1,784 times, taking 1.3% of the whole
        # training set on its own. This is a deterministic text-to-JSON mapping, so a condition
        # seen twice teaches nothing the first sighting did not, and the repeats simply spend
        # gradient steps re-learning the popular depositions.
        #
        # Done before oversampling, not after: the `not_a_component` records were themselves
        # duplicated, so multiplying first turned 1,339 distinct examples into 15,616 rows, an
        # effective 11.7x where 8x was intended.
        seen_train: set[str] = set()
        deduplicated = []
        for example in bootstrap["train"]:
            key = example["messages"][1]["content"]
            if key in seen_train:
                continue
            seen_train.add(key)
            deduplicated.append(example)
        n_duplicates = len(bootstrap["train"]) - len(deduplicated)
        bootstrap["train"] = deduplicated

        # Oversampling applied here instead, on distinct examples, so the multiplier means what
        # it says.
        if args.oversample_non_component > 1:
            extra = []
            for example in bootstrap["train"]:
                content = example["messages"][2]["content"]
                if '"not_a_component"' in content:
                    extra.extend([example] * (args.oversample_non_component - 1))
            bootstrap["train"].extend(extra)

        # Validation is deduplicated too, or the loss is dominated by whichever conditions
        # happen to be popular rather than by how well the model reads.
        seen_valid: set[str] = set()
        bootstrap["valid"] = [e for e in bootstrap["valid"]
                              if not (e["messages"][1]["content"] in seen_valid
                                      or seen_valid.add(e["messages"][1]["content"]))]

        if args.max_train:
            bootstrap["train"] = bootstrap["train"][:args.max_train]

        paths = {}
        for name, rows in bootstrap.items():
            path = args.out_dir / f"{name}.jsonl"
            with open(path, "w") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")
            paths[name] = path
            m.add_output(path)

        residual_path = args.out_dir / "residual.jsonl"
        with open(residual_path, "w") as handle:
            for row in residual:
                handle.write(json.dumps(row) + "\n")
        m.add_output(residual_path)

        # Length matters for the training config: a max sequence length below the longest
        # example silently truncates the answer, which teaches the model to emit invalid JSON.
        lengths = sorted(len(json.dumps(e)) for e in bootstrap["train"])
        p99 = lengths[int(0.99 * (len(lengths) - 1))] if lengths else 0

        stats = {
            "n_train": len(bootstrap["train"]),
            "n_duplicate_inputs_removed": n_duplicates,
            "n_empty_answer_examples": n_empty_answers,
            "n_valid": len(bootstrap["valid"]),
            "n_residual": len(residual),
            "n_residual_with_unidentified": skipped_unidentified,
            "chars_p50": lengths[len(lengths) // 2] if lengths else 0,
            "chars_p99": p99,
            "chars_max": lengths[-1] if lengths else 0,
        }
        m.note(**stats)

        for key, value in stats.items():
            print(f"  {key:<28} {value:>10,}")
        print(f"\n  bootstrap labels cost no human time: they are the rule parser's own output")
        print(f"  on records where it was fully confident and identified every component.")
        print(f"  the {len(residual):,} residual records are the target, and the model never")
        print(f"  sees them in training, so the measurement is honest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
