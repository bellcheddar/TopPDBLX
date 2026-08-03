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
from ..parse import lexicon as lexicon_module
from ..parse.text import normalise, tidy_name

STAGE = "models.build_slm_dataset"

# **This string is a released interface, not an editable constant.** `apply_slm` and `eval_slm`
# both import it, so it is the prompt the shipped round 06 adapter was trained under *and* the one
# it is served with. Changing it re-prompts a model that cannot be retrained to match, and the
# damage would be silent: the model would still answer, slightly worse, with nothing failing.
#
# `protein_buffer` and `soak` are absent here and present in `SYSTEM_V2` below. When this comment
# was first written the rules could not produce those roles at all; `parse.scope` changed that the
# same day, and the dataset now carries 6,800 of them. That is a reason to add a version, not to
# edit this one — a round trained under v1 must keep being served under v1.
SYSTEM = (
    "You convert a PDB crystallisation condition string into JSON. "
    "Return only a JSON array. Each element has: role (precipitant, salt, buffer, additive, "
    "cryo, not_a_component or unknown), name (the canonical reagent, or null when the text "
    "names no reagent), amount (a number or null) and "
    "unit (percent_w_v, percent_v_v, molar, millimolar, mg_ml or null). "
    "Use not_a_component for text that names no reagent at all: method notes, screen "
    "references, or an unnamed protein, inhibitor or compound."
)

# **v2 exists because v1 is frozen, not because v1 was wrong.** `SYSTEM` above is the contract a
# shipped, public adapter was trained and is served under, so it cannot change. This is the
# version for the first round trained after the rules learned to tell the drop from the protein
# sample, and the only difference is the two roles that distinction produces.
#
# Every round trained on `SYSTEM_V2` data must be *served* with `SYSTEM_V2`. Serving it under v1
# would not fail, it would answer slightly worse for a reason nothing reports.
SYSTEM_V2 = (
    "You convert a PDB crystallisation condition string into JSON. "
    "Return only a JSON array. Each element has: role (precipitant, salt, buffer, additive, "
    "cryo, protein_buffer, soak, not_a_component or unknown), name (the canonical reagent, or "
    "null when the text names no reagent), amount (a number or null) and "
    "unit (percent_w_v, percent_v_v, molar, millimolar, mg_ml or null). "
    "Use not_a_component for text that names no reagent at all: method notes, screen "
    "references, or an unnamed protein, inhibitor or compound. "
    "Use protein_buffer for a reagent in the protein solution, stock or storage buffer, and "
    "soak for one a grown crystal was moved into: both name a real reagent the crystal did not "
    "grow in."
)

PROMPTS = {"v1": SYSTEM, "v2": SYSTEM_V2}

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


def _group_components(
    frame: "pl.DataFrame",
) -> tuple[dict[tuple, list[dict[str, Any]]], int, int]:
    """Teacher components by record, re-canonicalised against the lexicon as it stands now.

    **A teacher parquet is a snapshot of a lexicon as much as of a model.** `teacher_label`
    canonicalises its generations against whatever lexicon was loaded when it ran, so a file
    produced on 2 August names ids that 0.8.x merged away -- `PEG_MME_2K`, `PEG_5K_MME`,
    `COBALT_HEXAMINE`, `HGCL2` and ten more, across 17 rows. Training on those would teach the
    model to emit reagent ids the pipeline itself no longer recognises.

    Re-resolving here rather than regenerating the file is cheaper *and* better: twelve of the
    fourteen orphans are now aliases of the entry they were merged into, so they recover to the
    right answer instead of being dropped. The two that cannot be placed are discarded, which is
    the correct treatment of a name the lexicon does not know.
    """
    lexicon = lexicon_module.load()
    known = {r.canonical_id for r in lexicon.reagents}
    alias_index = lexicon.index()

    def canonicalise(name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        if name in known:
            return name
        reagent = alias_index.get(normalise(tidy_name(name.replace("_", " ").lower())))
        return reagent.canonical_id if reagent else None

    out: dict[tuple, list[dict[str, Any]]] = {}
    remapped = dropped = 0
    cols = ["pdb_id", "crystal_id", "component_index", "role", "name_canonical",
            "concentration", "unit"]
    for row in frame.select(cols).sort("component_index").iter_rows(named=True):
        if row["role"] == "unknown":
            # `unknown` is the parser's way of saying the lexicon failed, not a target. Nine of
            # these reached an earlier build and would have taught the model to emit it.
            continue
        if row["role"] != "not_a_component":
            resolved = canonicalise(row["name_canonical"])
            if resolved is None:
                dropped += 1
                continue
            if resolved != row["name_canonical"]:
                remapped += 1
                row = {**row, "name_canonical": resolved}
        out.setdefault((row["pdb_id"], row["crystal_id"]), []).append(row)
    return out, remapped, dropped


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
    parser.add_argument("--oversample-scope", type=int, default=8,
                        help="repeat training records carrying a protein_buffer or soak label. "
                             "Same argument as the not_a_component oversample and the same "
                             "arithmetic: the rules find scope roles in 2,641 usable records "
                             "against ~100,000, so at 1x the model sees the distinction in under "
                             "3%% of its examples and will not learn a class it barely meets")
    parser.add_argument("--teacher-components", type=Path, nargs="*", default=None,
                        help="one or more teacher components parquets. Their records are added "
                             "as ADDITIONAL training rows, never replacing a rules label. This is "
                             "what rounds 07 and 08 got wrong: they substituted noisier teacher "
                             "labels for good rules labels across the board")
    parser.add_argument("--oversample-teacher", type=int, default=4,
                        help="repeat teacher-labelled rows. They are residual-like text, which "
                             "is the population the model is applied to and the one thing a "
                             "rules-only set cannot supply, but they are only ~3%% of the data. "
                             "Held at 4x deliberately: the teacher invents a reagent in 3.7%% of "
                             "its components, and the multiplier scales that contribution too")
    parser.add_argument("--exclude-gold", type=Path, nargs="*", default=None,
                        help="gold sets whose records must never become training rows. NOT "
                             "optional when passing --teacher-components: 96 records of "
                             "teacher_components_2k.parquet ARE the contested gold set, and "
                             "training on them would leave the yardstick measuring itself")
    parser.add_argument("--system-version", choices=sorted(PROMPTS), default="v1",
                        help="v1 is the frozen prompt the shipped round 06 adapter is served "
                             "under and must stay the default. v2 adds the protein_buffer and "
                             "soak roles; a round trained on it must also be SERVED with it")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    system_prompt = PROMPTS[args.system_version]

    conditions = pl.read_parquet(args.conditions)
    components = pl.read_parquet(args.components)
    splits = pl.read_parquet(args.splits).select("pdb_id", "crystal_id", "fold_30")

    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for row in components.iter_rows(named=True):
        grouped.setdefault((row["pdb_id"], row["crystal_id"]), []).append(row)

    framed = conditions.join(splits, on=["pdb_id", "crystal_id"], how="left")

    with Manifest(STAGE, params={"max_train": args.max_train}) as m:
        m.add_input(args.conditions).add_input(args.components).add_input(args.splits)

        # **The gold hold-out, applied to every row and not only the teacher's.**
        #
        # The gold set is drawn from the residual, so up to round 08 no gold text could reach
        # training by construction: a residual record is one the rules failed on. Lexicon 0.7.0
        # and `parse.scope` changed that -- records that now parse confidently include some whose
        # text is a verbatim duplicate of a gold record deposited under a different pdb_id, and
        # 11 of them entered training as ordinary *rules* rows. A rules-only round would have
        # leaked just as much as this one.
        #
        # Matched on normalised text, not on `(pdb_id, crystal_id)`: this corpus deposits the
        # same condition string under many entries, one of them 1,784 times, so the key catches
        # almost none of it.
        held_keys: set[tuple] = set()
        held_text: set[str] = set()
        for path in (args.exclude_gold or []):
            for record in json.loads(Path(path).read_text()).get("records", []):
                held_keys.add((record["pdb_id"], record.get("crystal_id", "1")))
                if record.get("text"):
                    held_text.add(" ".join(record["text"].split()).lower())

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
            # **Held out of training, but NOT out of the residual**, and the difference is the
            # whole point of the gold set. `continue`-ing here skipped the record before it
            # reached either branch, so all 192 gold records vanished from `residual.jsonl` --
            # which is the list `apply_slm` reads. The model then had nothing to say about them
            # and `eval.gold_metrics` scored "rules + model" identically to "rules only",
            # reporting a 13-point recall collapse that was entirely an artefact of this line.
            #
            # The yardstick must never be *trained* on and must always be *read*.
            held_out = key in held_keys or " ".join(text.split()).lower() in held_text
            parts = grouped.get(key, [])
            fold = row["fold_30"] or "train"

            # **Records whose correct answer is "no chemistry".** Every other training example
            # contains a reagent, so the model had never been shown that an empty answer is
            # allowed, and it duly invented one: given the text "pH 3.3" it emitted TRIS. These
            # come from discards the rules judged confidently (TOO_SHORT, METHOD_ONLY,
            # NON_CRYSTALLISATION_TEXT), so the label costs nothing and is not a guess.
            if row["discard_reason"] in EMPTY_ANSWER_DISCARDS and not held_out:
                empty_example = {"messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": target_json(
                        [c for c in parts if c["role"] == "not_a_component"])},
                ]}
                bootstrap["valid" if fold == "val" else "train"].append(empty_example)
                n_empty_answers += 1
                continue

            confident = (not held_out
                         and row["discard_reason"] is None
                         and row["parse_confidence"] >= CONFIDENT
                         and parts
                         and all(accounted_for(c) for c in parts))

            if confident:
                # Chat format so --mask-prompt trains on the completion only. Without the mask
                # the model spends most of its loss learning to echo condition strings back.
                example = {"messages": [
                    {"role": "system", "content": system_prompt},
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

        # **Teacher-labelled residual rows, added rather than substituted.**
        #
        # Every row above comes from a record the *rules* read confidently, which is the
        # distillation ceiling stated at the top of this file: the model can approximate the
        # mapping the rules already implement and no more. These rows are the opposite -- text the
        # rules could not read, labelled by a local 32B -- and they are the only ingredient that
        # can teach something the rules do not already know.
        #
        # Filtered on exactly the guards `apply_slm` applies to model output, because a label the
        # pipeline would refuse to keep is not a label worth training on.
        n_teacher = 0
        teacher_rows: list[dict[str, Any]] = []
        if args.teacher_components:
            # **Held out by text as well as by key, and the key alone is not enough.** This
            # corpus repeats itself: the same condition string is deposited under many different
            # pdb_ids, one of them 1,784 times. Excluding only `(pdb_id, crystal_id)` let 15 gold
            # records back into training and 4 into validation under a different entry's id --
            # the yardstick, verbatim, as a training example.
            #
            # Rounds up to 08 were never exposed to this: the gold set is drawn from the residual,
            # and residual records cannot become rules-derived training rows by construction. The
            # hole opens only when teacher-labelled residual text is added, which is new here.
            held = held_keys
            by_key = {(r["pdb_id"], r["crystal_id"]): r for r in residual}
            seen_keys: set[tuple] = set()
            for path in args.teacher_components:
                if not Path(path).exists():
                    print(f"  teacher source missing, skipped: {path}")
                    continue
                frame = pl.read_parquet(path)
                grouped_teacher, remapped, dropped = _group_components(frame)
                if remapped or dropped:
                    print(f"  {Path(path).name}: {remapped} names re-canonicalised to the "
                          f"current lexicon, {dropped} dropped as unplaceable")
                for key, group in grouped_teacher.items():
                    if key in held or key in seen_keys or key not in by_key:
                        continue
                    if " ".join(by_key[key]["text"].split()).lower() in held_text:
                        continue
                    if not any(c["name_canonical"] for c in group):
                        continue          # the teacher found nothing either; nothing to teach
                    seen_keys.add(key)
                    teacher_rows.append({"messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": by_key[key]["text"]},
                        {"role": "assistant", "content": target_json(group)},
                    ]})
            n_teacher = len(teacher_rows)

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
            # **Normalised exactly as the gold hold-out key is.** Keying on the raw string let
            # 3,114 rows (2.2%) that differ only in case or whitespace survive as though they
            # were independent examples -- and then be oversampled as such. Two keys over the
            # same field in one function must not disagree about what "the same text" means.
            key = " ".join(example["messages"][1]["content"].split()).lower()
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

        # Same treatment for the scope roles, and for the same reason the non-component
        # oversample exists: a class the model meets in under 3% of its examples is a class it
        # will answer with the majority label instead. These are rarer still.
        if args.oversample_scope > 1:
            extra = []
            for example in bootstrap["train"]:
                content = example["messages"][2]["content"]
                if '"protein_buffer"' in content or '"soak"' in content:
                    extra.extend([example] * (args.oversample_scope - 1))
            bootstrap["train"].extend(extra)
            n_scope_examples = len(extra) // max(1, args.oversample_scope - 1)
        else:
            n_scope_examples = 0

        # **Teacher rows are added here, after dedup and after the other oversamplers, and the
        # ordering is the whole point.**
        #
        # Appended before dedup they were collapsed straight back to one copy each, so
        # `--oversample-teacher` did nothing at all. Worse, the `not_a_component` oversampler runs
        # after dedup and 29.8% of teacher targets carry that label against 2.7% of the rules
        # corpus, so nearly every teacher row was silently multiplied 8x instead of the 4x asked
        # for. Two multipliers compounding on the noisiest rows in the set is the opposite of the
        # intent, which was to keep the teacher's 3.7% invention rate on a short leash.
        #
        # They are also dropped where their text already appears with a rules label. The same
        # condition string is deposited under many entries, so a residual record can share text
        # with a confidently-parsed one -- and training the same input against two different
        # answers teaches noise, whichever answer is right.
        if teacher_rows:
            def _input_key(example: dict[str, Any]) -> str:
                return " ".join(example["messages"][1]["content"].split()).lower()
            existing = ({_input_key(e) for e in bootstrap["train"]}
                        | {_input_key(e) for e in bootstrap["valid"]})
            kept = [e for e in teacher_rows if _input_key(e) not in existing]
            n_conflict = len(teacher_rows) - len(kept)
            n_teacher = len(kept)
            bootstrap["train"].extend(kept * max(1, args.oversample_teacher))
            print(f"  teacher rows: {n_teacher:,} distinct x{args.oversample_teacher} = "
                  f"{n_teacher * max(1, args.oversample_teacher):,} rows"
                  + (f"; {n_conflict:,} dropped for clashing with a rules label"
                     if n_conflict else ""))

        # Validation is deduplicated too, or the loss is dominated by whichever conditions
        # happen to be popular rather than by how well the model reads.
        seen_valid: set[str] = set()
        bootstrap["valid"] = [e for e in bootstrap["valid"]
                              if not (_input_key(e) in seen_valid
                                      or seen_valid.add(_input_key(e)))]

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
            "n_scope_examples": n_scope_examples,
            "n_teacher_rows": n_teacher,
            "system_version": args.system_version,
            "n_valid": len(bootstrap["valid"]),
            "n_residual": len(residual),
            "n_residual_with_unidentified": skipped_unidentified,
            "chars_p50": lengths[len(lengths) // 2] if lengths else 0,
            "chars_p99": p99,
            "chars_max": lengths[-1] if lengths else 0,
        }
        m.note(**stats)

        for key, value in stats.items():
            shown = f"{value:,}" if isinstance(value, (int, float)) else str(value)
            print(f"  {key:<28} {shown:>10}")
        print(f"\n  bootstrap labels cost no human time: they are the rule parser's own output")
        print(f"  on records where it was fully confident and identified every component.")
        print(f"  the {len(residual):,} residual records are the target, and the model never")
        print(f"  sees them in training, so the measurement is honest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
