"""Stage `models.build_slm_dataset`: training data for the text-to-JSON parser.

R1. 125,970 components (20.9%) resolve to no canonical reagent and 29.0% of records reach no L3
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
# **Not 1.0, and the reason matters.** Record confidence is `0.7 * resolution + 0.3 *
# char_coverage`. The `accounted_for` check below already guarantees resolution is 1.0 among
# clauses that contain chemistry, so confidence here is purely a measure of text coverage, and a
# real deposition almost never covers every character: stray punctuation and connective words
# leave a fraction behind. Requiring exactly 1.0 therefore excluded records the rules understood
# completely, which is how 2,539 records carrying the `not_a_component` verdict were silently
# kept out of training (their confidence peaked at 0.998). The model then saw zero examples of
# that verdict and could not learn it at all.
#
# 0.95 with resolution pinned at 1.0 implies char_coverage >= 0.833, which is the actual intent:
# most of the text is explained.
CONFIDENT = 0.95


def accounted_for(component: dict[str, Any]) -> bool:
    """Whether the rules reached a definite verdict on this component.

    A resolved reagent counts, and so does a confident `not_a_component`: both are correct
    parses. Only `unknown` (a reagent the lexicon does not recognise) leaves the record unusable
    as a training example.

    Including the non-reagent verdicts matters more than it looks. Requiring a canonical name for
    every component would drop every record containing a method note, which is 13.4% of the
    unresolved mass, and the model would then never see a single example of the one output it
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
        skipped_unresolved = 0

        for row in tqdm(framed.iter_rows(named=True), total=framed.height,
                        desc="records", unit="rec"):
            text = (row["raw_details"] or "").strip()
            if not text:
                continue
            key = (row["pdb_id"], row["crystal_id"])
            parts = grouped.get(key, [])
            fold = row["fold_30"] or "train"

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
                split = "valid" if fold == "val" else "train"
                bootstrap[split].append(example)
                # The `not_a_component` verdict appears on only about 1.2% of records, because
                # most depositions that mention their own method also name a reagent the lexicon
                # does not know, which disqualifies the record entirely. A class that rare is
                # barely learnable, and it is one we deliberately added. Repeated in training
                # only: the validation and residual sets must keep the corpus's real proportions
                # or the metrics stop describing the corpus.
                if split == "train" and any(c["role"] == "not_a_component" for c in parts):
                    for _ in range(max(0, args.oversample_non_component - 1)):
                        bootstrap["train"].append(example)
            else:
                if parts and not all(accounted_for(c) for c in parts):
                    skipped_unresolved += 1
                residual.append({
                    "pdb_id": row["pdb_id"], "crystal_id": row["crystal_id"],
                    "text": text,
                    "rules_confidence": row["parse_confidence"],
                    "rules_discard": row["discard_reason"],
                    "n_components": len(parts),
                    "n_unresolved": sum(1 for c in parts if not accounted_for(c)),
                    "fold": fold,
                })

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
            "n_valid": len(bootstrap["valid"]),
            "n_residual": len(residual),
            "n_residual_with_unresolved": skipped_unresolved,
            "chars_p50": lengths[len(lengths) // 2] if lengths else 0,
            "chars_p99": p99,
            "chars_max": lengths[-1] if lengths else 0,
        }
        m.note(**stats)

        for key, value in stats.items():
            print(f"  {key:<28} {value:>10,}")
        print(f"\n  bootstrap labels cost no human time: they are the rule parser's own output")
        print(f"  on records where it was fully confident and resolved every component.")
        print(f"  the {len(residual):,} residual records are the target, and the model never")
        print(f"  sees them in training, so the measurement is honest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
