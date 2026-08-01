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
from pathlib import Path
from typing import Any, Optional

import polars as pl
from tqdm import tqdm

from .. import config
from ..manifest import Manifest
from ..parse import quantity
from .apply_slm import SCHEMA, SLM_CONFIDENCE
from .eval_slm import check, grounded_in_text, load_aliases, load_lexicon

STAGE = "models.teacher_label"

DEFAULT_MODEL = "mlx-community/Qwen2.5-32B-Instruct-4bit"
BATCH = 8
MAX_TOKENS = 512
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


def _load_targets(args: argparse.Namespace) -> list[dict[str, Any]]:
    """The records to label: either a named subset or the live residual."""
    residual = {}
    for line in (args.data_dir / "residual.jsonl").read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            residual[(row["pdb_id"], row["crystal_id"])] = row

    if not args.records:
        return list(residual.values())

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
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch", type=int, default=BATCH)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    from mlx_lm import batch_generate, load
    from mlx_lm.sample_utils import make_sampler

    os.environ.pop("HF_TOKEN", None)
    slug = args.model.rstrip("/").split("/")[-1].lower().replace(".", "")
    if args.progress is None:
        args.progress = args.data_dir / f"teacher_progress_{slug}.jsonl"

    lexicon = load_lexicon()
    aliases = load_aliases()
    targets = _load_targets(args)

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
                    prompts = [tokenizer.apply_chat_template(
                        [{"role": "system", "content": SYSTEM},
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
            scored = check(row["generated"], lexicon)
            if not scored["valid"]:
                invalid += 1
                continue
            emitted, index = [], 0
            for item in scored["parsed"]:
                role, name = item.get("role"), item.get("name")
                if role != "not_a_component" and name not in lexicon:
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
