"""Stage `models.teacher_label`: label the residual with a large local model.

**The SLM's ceiling is the rule parser, because the rule parser is its only teacher.**
`build_slm_dataset` bootstraps from `rules_v3` output on records the rules read confidently, so
the model learns the mapping the rules already implement and can only approximate it. The round 06
checkpoint sweep is that ceiling made visible: fidelity to `rules_v3` climbed monotonically from
80.6% to 93.6% while identification on the residual peaked at iteration 2,000 and fell away. More
training bought a better imitation and a worse reader.

This stage breaks the loop by labelling the residual with a model that can actually read it, and
training on that instead. The residual is by construction the text `rules_v3` failed on, so its
labels cannot come from `rules_v3`.

**Local, not an API.** A 4-bit 32B runs on this machine, the corpus never leaves it, and the run
costs time rather than money. `--model` takes any MLX repo; several are already cached.

**Measure the teacher before trusting it.** `--records` restricts the run to a named set, so the
first thing to do is label the 96 gold records and score them with `eval.gold_metrics`. A teacher
that does not beat the shipped pipeline on labelled truth is not worth five hours, and picking
between candidate teachers is now an experiment rather than an opinion.

Output matches `models.apply_slm` exactly -- same JSON contract, same components schema -- so
labels are drop-in training data and drop-in for scoring.

    ./run.sh models.teacher_label --records data/interim/gold_set_20260801.json --limit 96
    ./run.sh models.teacher_label --limit 5000
"""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path
from typing import Any, Optional

import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest
from ..parse import lexicon as lexicon_module, quantity
from ..parse.text import normalise, tidy_name
from .apply_slm import SCHEMA, SLM_CONFIDENCE
from .eval_slm import check, grounded_in_text, load_aliases, load_lexicon

STAGE = "models.teacher_label"

DEFAULT_MODEL = "mlx-community/Qwen2.5-32B-Instruct-4bit"

# **Batch size barely matters here, contrary to what a first look suggested.** Per record in
# steady state: 40.2 s at batch 16, 38.2 s at batch 8. The apparent 5.5x penalty for the larger
# batch was an artefact of comparing a cold 20-minute run against a hot 8-hour one -- the job
# starts near 101 s/batch and settles two to five times slower within about fifteen minutes,
# whatever the batch size. Never size this job from its first progress bar.
BATCH = 8
# **Measured, not guessed, and the guess was expensive.** Over 720 real generations the median is
# 347 characters and the longest 1,154, which is about 330 tokens. 448 clears that with room.
#
# This was 1024 for a while, on a theory that the invalid generations were truncated. They were
# not -- every one was complete, ended in `}]`, and failed on `bad_unit` -- and raising the budget
# changed the count by zero. Nor was the headroom free, which is the part that cost real time:
# `batch_generate` runs a batch until *every* sequence in it finishes or hits the cap, so an
# oversized cap is paid for on every batch that contains one sequence lacking a stop token.
MAX_TOKENS = 448
PARSER_NAME = "teacher_v1"

# Deliberately the same contract as the SLM's own system prompt, so a label can be used as a
# training target without translation. What is added is guidance the small model cannot hold and
# does not need to: the failure modes this corpus actually produces, which the audits found.
SYSTEM = (
    "You convert a PDB crystallisation condition string into JSON. "
    "Return only a JSON array, no prose and no code fence. Each element has: "
    "role (precipitant, salt, buffer, additive, cryo, not_a_component or unknown), "
    "name (the canonical reagent in UPPER_SNAKE_CASE, or null when the text names no reagent), "
    "amount (a number or null) and "
    "unit (percent_w_v, percent_v_v, molar, millimolar, mg_ml or null).\n"
    "Rules that matter for this corpus:\n"
    "- List every reagent the text names, including ones written in passing or after a line "
    "break. Missing a reagent is the most common error; inventing one is rare and worse.\n"
    "- A number attached to a polymer is its molecular weight, not an amount: 'PEG 3350' is "
    "PEG_3350, and 'PEG3350-26%' is PEG_3350 at 26 percent.\n"
    "- Use not_a_component for text naming no reagent: method notes, screen references, drop "
    "ratios, protein concentrations, or an unnamed protein, inhibitor or compound.\n"
    "- Give the reagent, never the product: 'sodium acetate trihydrate' is SODIUM_ACETATE.\n"
    "- A cryoprotectant added after growth still gets role cryo, not precipitant.\n"
    "- If the text states an impossible amount, report it as written; do not silently fix it."
)


# **Worked examples, because the instructions alone were not enough.** Zero-shot, this teacher
# scored below the 360M student it was meant to teach: it wrote units on non-components, invented
# reagents at seven times the student's rate, and reached for its own spelling of a canonical id.
# Showing the contract beats describing it.
#
# Taken verbatim from `train.jsonl`, which is drawn from records the *rules* read confidently.
# That matters twice over: the answers are known-good, and the split is disjoint from both the
# residual and the 96 gold records, so nothing the teacher is scored on appears in its prompt.
# Hardcoded rather than sampled at run time so a rebuild of the training set cannot silently
# change the prompt and with it every label the teacher has produced.
FEWSHOT: list[tuple[str, str]] = [
    ("0.2 M ammonium sulfate, 0.02 M sodium chloride, 0.02 M sodium acetate pH 4.0, 33% PEG 200",
     '[{"role":"salt","name":"AMMONIUM_SULFATE","amount":0.2,"unit":"molar"},'
     '{"role":"salt","name":"SODIUM_CHLORIDE","amount":0.02,"unit":"molar"},'
     '{"role":"buffer","name":"SODIUM_ACETATE","amount":0.02,"unit":"molar"},'
     '{"role":"precipitant","name":"PEG_200","amount":33.0,"unit":"percent_v_v"}]'),
    ("100 MM AMMONIUM ACETATE, 50 MM MAGNESIUM ACETATE, 1 MM DTT, 12-16 % PEG 6000",
     '[{"role":"salt","name":"AMMONIUM_ACETATE","amount":100.0,"unit":"millimolar"},'
     '{"role":"salt","name":"MAGNESIUM_ACETATE","amount":50.0,"unit":"millimolar"},'
     '{"role":"additive","name":"DTT","amount":1.0,"unit":"millimolar"},'
     '{"role":"precipitant","name":"PEG_6000","amount":14.0,"unit":"percent_w_v"}]'),
    # The two things it got wrong on its own: a temperature is a non-component and carries no
    # unit, and a protein concentration is setup rather than chemistry.
    ("20% PEG 3350, 0.2 M NaCl, 10 mg/mL protein, VAPOR DIFFUSION, temperature 293K",
     '[{"role":"precipitant","name":"PEG_3350","amount":20.0,"unit":"percent_w_v"},'
     '{"role":"salt","name":"SODIUM_CHLORIDE","amount":0.2,"unit":"molar"},'
     '{"role":"not_a_component","name":null,"amount":null,"unit":null}]'),
]


def _load_targets(args: argparse.Namespace) -> list[dict[str, Any]]:
    """The records to label: either a named subset or a sample of the live residual."""
    residual = {}
    for line in (args.data_dir / "residual.jsonl").read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            residual[(row["pdb_id"], row["crystal_id"])] = row

    if not args.records:
        rows = list(residual.values())
        if args.exclude:
            held = {(r["pdb_id"], r.get("crystal_id", "1"))
                    for r in json.loads(args.exclude.read_text()).get("records", [])}
            rows = [r for r in rows if (r["pdb_id"], r["crystal_id"]) not in held]
        if args.sample and args.sample < len(rows):
            # Random, not the head. `residual.jsonl` is ordered by pdb_id and pdb_id is
            # chronological, so the first 5,000 records are the oldest depositions in the
            # corpus: all-caps, differently punctuated, and not what the model will mostly meet.
            random.Random(args.seed).shuffle(rows)
            rows = rows[:args.sample]
        return rows

    doc = json.loads(args.records.read_text())
    rows = doc.get("records") or doc.get("questions") or []
    picked = []
    for row in rows:
        key = (row["pdb_id"], row.get("crystal_id", "1"))
        if key in residual:
            picked.append(residual[key])
        elif row.get("text"):
            picked.append({"pdb_id": key[0], "crystal_id": key[1], "text": row["text"]})
    return picked


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=config.INTERIM_DIR / "slm")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--records", type=Path, default=None,
                        help="a gold set or questions payload: label only those records")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "teacher_components.parquet")
    parser.add_argument("--progress", type=Path, default=None,
                        help="defaults to teacher_progress_<model>.jsonl, so candidate "
                             "teachers never overwrite each other's work")
    parser.add_argument("--sample", type=int, default=None,
                        help="label a random sample of the residual rather than its head. The "
                             "residual is sorted by pdb_id, which is chronological, so --limit "
                             "alone would train only on the oldest depositions and their "
                             "conventions")
    parser.add_argument("--exclude", type=Path, default=None,
                        help="a gold set whose records must never be labelled. Training on the "
                             "yardstick is how a yardstick stops measuring anything")
    parser.add_argument("--seed", type=int, default=41)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch", type=int, default=BATCH)
    parser.add_argument("--wired-limit-gb", type=float, default=32.0,
                        help="hold this much GPU memory resident. 0 disables it")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    import mlx.core as mx
    from mlx_lm import batch_generate, load
    from mlx_lm.sample_utils import make_sampler

    # **Keep the weights resident.** A 4-bit 32B is ~18 GB and every token of every batch reads
    # all of it. Without a raised wired limit macOS is free to page those buffers out under
    # sustained load, and the run slows from ~101 s/batch in its first minutes to 220-577 s/batch
    # for the rest -- which is not thermal throttling and not batch size, both of which were
    # blamed first. 32 GB of 64 leaves the OS ample room while holding the model and its KV cache.
    if args.wired_limit_gb:
        try:
            mx.set_wired_limit(int(args.wired_limit_gb * 1024**3))
            print(f"  wired limit: {args.wired_limit_gb} GB")
        except Exception as error:                       # the cap is a system setting we cannot raise
            print(f"  wired limit not applied ({error}); continuing unwired")

    os.environ.pop("HF_TOKEN", None)
    slug = args.model.rstrip("/").split("/")[-1].lower().replace(".", "")
    if args.progress is None:
        args.progress = args.data_dir / f"teacher_progress_{slug}.jsonl"

    lexicon = load_lexicon()
    aliases = load_aliases()
    targets = _load_targets(args)

    # **The teacher is mapped onto the lexicon, not required to have memorised it.** The SLM was
    # trained to emit canonical ids, so it mostly does; a general model has never seen them and
    # writes the chemistry the way a chemist would. Of the first run's 101 rejected names, the
    # commonest were TRIS_HCL, SODIUM_HEPES, MGCL2, KCL, PEG400 and MgSO4 -- every one a real
    # reagent the lexicon already knows under an alias. Rejecting those measures whether the
    # teacher guessed our internal spelling, which is not the question being asked of it.
    lex_full = lexicon_module.load()
    alias_index, by_id = lex_full.index(), lex_full.by_id()

    def tidy_generation(text: str) -> str:
        """Repair the two things this teacher gets wrong about the *schema*, not the chemistry.

        **A whole record was being discarded over a unit on a non-component.** The model marks
        "temperature 277K" as `not_a_component`, which is right, and then writes `"unit": "K"` or
        `"DEG_C"` on it. `check` validates units against a closed vocabulary and fails the entire
        generation, so every real reagent in that record went with it: 18 of 96 records, and not
        one of them was malformed JSON. A non-component has no amount and no unit by definition,
        so both are cleared rather than argued with.

        The second is the string `"null"` where JSON null was meant, which is a formatting slip
        and nothing more.
        """
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return text
        if not isinstance(parsed, list):
            return text
        for item in parsed:
            if not isinstance(item, dict):
                continue
            for field in ("amount", "unit"):
                value = item.get(field)
                if isinstance(value, str) and value.strip().lower() in ("null", "none", ""):
                    item[field] = None
            if item.get("role") == "not_a_component":
                item["amount"] = None
                item["unit"] = None
        return json.dumps(parsed)

    def canonicalise(name: Optional[str]) -> Optional[str]:
        """A generated reagent name to a canonical id, or None if the lexicon cannot place it."""
        if not name or not isinstance(name, str):
            return None
        if name in by_id:
            return name
        reagent = alias_index.get(normalise(tidy_name(name.replace("_", " "))))
        return reagent.canonical_id if reagent else None

    done: set[tuple] = set()
    if args.progress.exists():
        for line in args.progress.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                done.add((row["pdb_id"], row["crystal_id"]))
    pending = [r for r in targets if (r["pdb_id"], r["crystal_id"]) not in done]
    if args.limit:
        pending = pending[:args.limit]

    with Manifest(STAGE, params={"model": args.model, "limit": args.limit,
                                 "records": str(args.records) if args.records else None,
                                 "resumed_from": len(done)}) as m:
        m.add_input(args.data_dir / "residual.jsonl")
        print(f"  teacher: {args.model}")
        print(f"  {len(targets):,} target records, {len(done):,} already done, "
              f"{len(pending):,} to label")

        if pending:
            model, tokenizer = load(args.model)
            sampler = make_sampler(temp=0.0)
            with open(args.progress, "a") as handle:
                for i in tqdm(range(0, len(pending), args.batch), desc="labelling",
                              unit="batch"):
                    chunk = pending[i:i + args.batch]
                    shots: list[dict[str, str]] = []
                    for shot_user, shot_answer in FEWSHOT:
                        shots.append({"role": "user", "content": shot_user})
                        shots.append({"role": "assistant", "content": shot_answer})
                    prompts = [tokenizer.apply_chat_template(
                        [{"role": "system", "content": SYSTEM}, *shots,
                         {"role": "user", "content": r["text"]}],
                        add_generation_prompt=True) for r in chunk]
                    result = batch_generate(model, tokenizer, prompts=prompts,
                                            max_tokens=MAX_TOKENS, sampler=sampler,
                                            verbose=False)
                    for record, text in zip(chunk, result.texts):
                        handle.write(json.dumps({
                            "pdb_id": record["pdb_id"], "crystal_id": record["crystal_id"],
                            "generated": text.strip()}) + "\n")
                    handle.flush()

        # --- generations to components, on the same terms apply_slm uses ---------------------
        text_by_key = {(r["pdb_id"], r["crystal_id"]): r["text"] for r in targets}
        wanted = set(text_by_key) if args.records else None
        rows: list[dict[str, Any]] = []
        kept = invalid = unknown = ungrounded = implausible = 0
        for line in args.progress.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row["pdb_id"], row["crystal_id"])
            if wanted is not None and key not in wanted:
                continue
            scored = check(tidy_generation(row["generated"]), lexicon)
            if not scored["valid"]:
                invalid += 1
                continue
            emitted, index = [], 0
            for item in scored["parsed"]:
                role = item.get("role")
                name = canonicalise(item.get("name"))
                if role != "not_a_component" and name is None:
                    unknown += 1
                    continue
                source = text_by_key.get(key, "")
                if role != "not_a_component" and not grounded_in_text(name, source, aliases):
                    ungrounded += 1
                    continue
                bad_amount = quantity.is_implausible(item.get("amount"), item.get("unit"))
                if bad_amount:
                    implausible += 1
                emitted.append({
                    "pdb_id": key[0], "crystal_id": key[1], "component_index": index,
                    "role": role or "unknown", "name_raw": name or "",
                    "name_canonical": name if role != "not_a_component" else None,
                    "chem_class": None, "peg_mw": None, "is_mme": False,
                    "hofmeister_rank": None, "buffer_pka": None,
                    "concentration": None if bad_amount else item.get("amount"),
                    "unit": None if bad_amount else item.get("unit"),
                    "unit_inferred": False, "concentration_is_range": False,
                    "cryo_evidence": None, "premix_id": None,
                    "parse_confidence": SLM_CONFIDENCE, "non_component_reason": None,
                    "parser": PARSER_NAME,
                })
                index += 1
            if emitted:
                rows.extend(emitted)
                kept += 1

        frame = pl.DataFrame(rows, schema=SCHEMA) if rows else pl.DataFrame(schema=SCHEMA)
        frame.write_parquet(args.out, compression="zstd")
        stats = {"model": args.model, "n_targets": len(targets),
                 "n_records_with_components": kept, "n_components": frame.height,
                 "n_dropped_invalid_json": invalid, "n_dropped_unknown_name": unknown,
                 "n_dropped_ungrounded": ungrounded,
                 "n_amounts_dropped_implausible": implausible}
        m.add_output(args.out).note(**stats)

        print(f"\n  records labelled          {kept:,}")
        print(f"  components emitted        {frame.height:,}")
        print(f"  dropped, invalid JSON     {invalid:,}")
        print(f"  dropped, name not in lexicon {unknown:,}")
        print(f"  dropped, not in the text  {ungrounded:,}   inventions")
        print(f"\n  {args.out}")
        print(f"  score it: ./run.sh eval.gold_metrics --slm-components {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
