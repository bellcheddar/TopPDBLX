"""Stage `models.apply_slm`: use the trained parser on the records the rules could not read.

Until now the model has only been measured. This is where it does work: it reads the 59,673
residual records and emits components for them, which is the largest single lever left on
coverage. 22.9% of the whole corpus is Unclassified purely because one reagent name went
unidentified, and the model identifies 88.6% of residual components.

**The rules always win.** The model is never allowed to overwrite a component the rule parser
identified. It only ever supplies components for records the rules gave up on, and its output has
to clear two gates before it is kept at all:

  *schema valid*   the generation must parse as JSON, be a list of objects, and use only the
                   closed role and unit vocabularies.
  *lexicon known*  every reagent name must exist in `synonyms.yaml`. A plausible-looking name
                   the lexicon has never heard of is a hallucination, not a discovery, and is
                   dropped rather than promoted into the dataset.

**Provenance is per component, not per dataset.** Every row records `parser`, so a model-derived
component can be filtered out by anyone who does not want it, and any statistic can be recomputed
on rules only. `parse_confidence` is capped below the rules' own, because a model reading text no
rule could read is exactly the case where confidence should be lower.

Resumable: results are appended as they are produced and completed records are skipped on a
rerun, because a three hour generation should not have to start again after an interruption.

    ./run.sh models.apply_slm
    ./run.sh models.apply_slm --limit 2000        # a slice, to check before committing hours
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

import polars as pl
from tqdm import tqdm

from .. import config
from ..parse import lexicon as lexicon_module, quantity
from ..parse.text import normalise, tidy_name
from ..manifest import Manifest
from .build_slm_dataset import PROMPTS, SYSTEM
from .eval_slm import (BATCH, MAX_TOKENS, _flatten, check, grounded_in_text,
                        load_aliases, load_lexicon)

STAGE = "models.apply_slm"

# Below the rules' 1.0 and above their 0.2 for an unidentified clause: this is a real reading of
# the text, by a model measured at 88.6% on exactly this population, but it is not a rule.
SLM_CONFIDENCE = 0.7
PARSER_NAME = "slm_v1"

# How close a reagent name must come to something in the text to be kept when no exact alias
# matches. Measured on the frozen benchmark: of the components that were identified but not
# grounded, roughly half were the model correctly reading through a depositor's typo
# ("AMMONIUN ACETATE", "(NH)4SO 2", "Tasimate") and half were inventions with no support at all
# ("TRIS" for the text "pH 3.3"). Fuzzy similarity separates them cleanly: the typo corrections
# scored 0.75 to 0.93, the inventions 0.33 to 0.71.
#
# The threshold keeps the corrections, which are the one thing the model does that no rule or
# alias ever could, and discards the inventions, which are the one thing that would make the
# dataset worse than not running it at all.
GROUNDING_FUZZY_MIN = 0.75

SCHEMA = {
    "pdb_id": pl.Utf8, "crystal_id": pl.Utf8, "component_index": pl.Int64,
    "role": pl.Utf8, "name_raw": pl.Utf8, "name_canonical": pl.Utf8,
    "chem_class": pl.Utf8, "peg_mw": pl.Int64, "is_mme": pl.Boolean,
    "hofmeister_rank": pl.Int64, "buffer_pka": pl.Float64,
    "concentration": pl.Float64, "unit": pl.Utf8, "unit_inferred": pl.Boolean,
    "concentration_is_range": pl.Boolean, "cryo_evidence": pl.Utf8,
    "premix_id": pl.Utf8, "parse_confidence": pl.Float64,
    "non_component_reason": pl.Utf8, "parser": pl.Utf8,
}



def _best_similarity(name: str, text: str, aliases: dict) -> float:
    """Closest match between any spelling of `name` and any window of `text`.

    A sliding comparison rather than a whole-string one: a condition names several reagents, so
    similarity against the entire text would be swamped by the parts describing other things.
    """
    flat = _flatten(text)
    best = 0.0
    for form in aliases.get(name, []):
        if not form or not flat:
            continue
        for i in range(0, max(1, len(flat) - len(form) + 1)):
            best = max(best, difflib.SequenceMatcher(None, form, flat[i:i + len(form)]).ratio())
            if best >= 1.0:
                return best
    return best


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=config.INTERIM_DIR / "slm")
    parser.add_argument("--adapter-dir", type=Path, default=None,
                        help="defaults to the highest-numbered run")
    parser.add_argument("--checkpoint", type=int, default=None)
    parser.add_argument("--model", default="mlx-community/SmolLM2-360M-Instruct")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "slm_components.parquet")
    parser.add_argument("--progress", type=Path,
                        default=config.INTERIM_DIR / "slm" / "apply_progress.jsonl")
    parser.add_argument("--records", type=Path, default=None,
                        help="a gold set or questions payload: apply to only those records. "
                             "Scoring a new round against 96 labelled records takes minutes; "
                             "applying to the whole residual to find out takes hours")
    parser.add_argument("--system-version", choices=sorted(PROMPTS), default="v1",
                        help="which system prompt to serve the adapter with. This MUST match the version its training data was built under: v1 for round 06 and earlier, v2 for any round trained after the scope roles. A mismatch does not fail, it quietly answers worse")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch", type=int, default=BATCH)
    args = parser.parse_args(argv)
    system_prompt = PROMPTS[args.system_version]

    config.ensure_dirs()
    from mlx_lm import batch_generate, load
    from mlx_lm.sample_utils import make_sampler

    os.environ.pop("HF_TOKEN", None)
    if args.adapter_dir is None:
        runs = sorted((args.data_dir / "runs").glob("*-round*"))
        if not runs:
            raise SystemExit("no trained runs found. Run: ./run.sh models.train_slm")
        args.adapter_dir = runs[-1]

    # **`--checkpoint` was accepted and ignored here until 2026-08-02**, while `eval_slm`
    # honoured it. So a sweep would choose a checkpoint on the evidence and then this stage would
    # quietly apply the *final* adapter instead: the 163,353 components released on 2026-08-01
    # were produced by round 06's 6,000-iteration adapter, not the 2,000-iteration one the sweep
    # had promoted, and every note saying otherwise was wrong. Mirrors eval_slm's staging exactly,
    # including writing outside the project tree, which is iCloud-synced.
    if args.checkpoint is not None:
        source = args.adapter_dir / f"{args.checkpoint:07d}_adapters.safetensors"
        if not source.exists():
            raise SystemExit(f"no checkpoint at iteration {args.checkpoint}: {source}")
        staged = Path(tempfile.mkdtemp(prefix=f"toppdblx_apply_{args.checkpoint:07d}_"))
        shutil.copy2(source, staged / "adapters.safetensors")
        shutil.copy2(args.adapter_dir / "adapter_config.json", staged / "adapter_config.json")
        args.adapter_dir = staged

    lexicon_by_id = {}
    import yaml
    for reagent in yaml.safe_load((config.ONTOLOGY_DIR / "synonyms.yaml").read_text())["reagents"]:
        lexicon_by_id[reagent["canonical_id"]] = reagent
    lexicon = set(lexicon_by_id)
    aliases = load_aliases()
    alias_index = lexicon_module.load().index()

    def canonicalise(name: Optional[str]) -> Optional[str]:
        """A generated reagent name to a canonical id, or None if the lexicon cannot place it.

        Deliberately identical to `teacher_label.canonicalise`, because the two stages write the
        same schema and a name accepted by one and refused by the other would make teacher and
        student incomparable on the measurement that decides between them.
        """
        if not name or not isinstance(name, str):
            return None
        if name in lexicon:
            return name
        reagent = alias_index.get(normalise(tidy_name(name.replace("_", " ").lower())))
        return reagent.canonical_id if reagent else None

    residual = [json.loads(l) for l in
                (args.data_dir / "residual.jsonl").read_text().splitlines() if l.strip()]

    # Resume: anything already generated is not generated again.
    done: set[tuple] = set()
    if args.progress.exists():
        for line in args.progress.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                done.add((row["pdb_id"], row["crystal_id"]))
    if args.records:
        doc = json.loads(args.records.read_text())
        wanted = {(r["pdb_id"], r.get("crystal_id", "1"))
                  for r in (doc.get("records") or doc.get("questions") or [])}
        residual = [r for r in residual if (r["pdb_id"], r["crystal_id"]) in wanted]
    pending = [r for r in residual if (r["pdb_id"], r["crystal_id"]) not in done]
    if args.limit:
        pending = pending[:args.limit]

    with Manifest(STAGE, params={"adapter": str(args.adapter_dir), "limit": args.limit,
                                 "resumed_from": len(done)}) as m:
        m.add_input(args.data_dir / "residual.jsonl")
        print(f"  run: {args.adapter_dir.name}")
        print(f"  {len(residual):,} residual records, {len(done):,} already done, "
              f"{len(pending):,} to generate")

        if pending:
            model, tokenizer = load(args.model, adapter_path=str(args.adapter_dir))
            sampler = make_sampler(temp=0.0)
            with open(args.progress, "a") as handle:
                for i in tqdm(range(0, len(pending), args.batch), desc="generating",
                              unit="batch"):
                    chunk = pending[i:i + args.batch]
                    prompts = [tokenizer.apply_chat_template(
                        [{"role": "system", "content": system_prompt},
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

        # --- turn generations into components ----------------------------------------------
        rows: list[dict[str, Any]] = []
        kept = dropped_invalid = dropped_unknown = dropped_ungrounded = 0
        dropped_implausible = 0
        rescued_typos = 0
        rescued_aliases = 0
        dropped_stale = 0
        text_by_key = {(r["pdb_id"], r["crystal_id"]): r["text"] for r in residual}
        for line in args.progress.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)

            # **A cached generation whose record has left the residual is not a result.** The
            # progress file is keyed to the residual as it stood when generation ran, and the
            # residual shrinks whenever the rules learn to read something: lexicon 0.8.x and
            # `parse.scope` moved 1,268 records out of it. Their text is no longer here, so the
            # grounding guard saw an empty string and scored every component an invention --
            # 5,000 of them, which looked like a precision collapse and was an accounting error.
            # The rules now parse these records, so dropping the model's answer is right; saying
            # why is the part that was missing.
            if (row["pdb_id"], row["crystal_id"]) not in text_by_key:
                dropped_stale += 1
                continue

            scored = check(row["generated"], lexicon)
            if not scored["valid"]:
                dropped_invalid += 1
                continue
            index = 0
            emitted = []
            for item in scored["parsed"]:
                role = item.get("role")
                name = item.get("name")
                implausible = quantity.is_implausible(item.get("amount"), item.get("unit"))
                if implausible:
                    dropped_implausible += 1
                # **Mapped onto the lexicon, not required to have memorised its spelling.**
                # `teacher_label` has argued this since it was written and this stage never got
                # it: the model emits `PVP`, `TDP`, `MERCAPTOETHANOL` and `COH18N6`, every one a
                # reagent the lexicon knows under an alias, and matching canonical ids alone
                # threw 658 correct readings away for spelling. That measures whether the model
                # guessed our internal identifier, which is not the question being asked of it.
                if role != "not_a_component":
                    resolved = canonicalise(name)
                    if resolved is None:
                        # A name the lexicon cannot place at all is a hallucination, not a
                        # discovery.
                        dropped_unknown += 1
                        continue
                    if resolved != name:
                        rescued_aliases += 1
                    name = resolved

                # The grounding guard. A named reagent must be traceable to the text it was read
                # from, exactly or as a near miss. Without this the run would write inventions
                # into the dataset alongside the genuine readings.
                source = text_by_key.get((row["pdb_id"], row["crystal_id"]), "")
                if role != "not_a_component":
                    if grounded_in_text(name, source, aliases):
                        pass
                    elif _best_similarity(name, source, aliases) >= GROUNDING_FUZZY_MIN:
                        rescued_typos += 1
                    else:
                        dropped_ungrounded += 1
                        continue
                reagent = lexicon_by_id.get(name) if name else None
                emitted.append({
                    "pdb_id": row["pdb_id"], "crystal_id": row["crystal_id"],
                    "component_index": index,
                    "role": role,
                    "name_raw": name or "",
                    "name_canonical": name if role != "not_a_component" else None,
                    "chem_class": (reagent or {}).get("chem_class"),
                    "peg_mw": (reagent or {}).get("peg_mw"),
                    "is_mme": bool((reagent or {}).get("is_mme", False)),
                    "hofmeister_rank": (reagent or {}).get("hofmeister_rank"),
                    "buffer_pka": (reagent or {}).get("buffer_pka"),
                    # Same plausibility floor the rule parser applies. The model reads the same
                    # depositions and reproduces the same impossible numbers, and an amount it
                    # cannot be right about is worth less than no amount at all.
                    "concentration": None if implausible else item.get("amount"),
                    "unit": None if implausible else item.get("unit"),
                    "unit_inferred": False,
                    "concentration_is_range": False,
                    "cryo_evidence": None,
                    "premix_id": name if (reagent or {}).get("chem_class") == "premix" else None,
                    "parse_confidence": SLM_CONFIDENCE,
                    "non_component_reason": None,
                    "parser": PARSER_NAME,
                })
                index += 1
            if emitted:
                rows.extend(emitted)
                kept += 1

        frame = pl.DataFrame(rows, schema=SCHEMA) if rows else pl.DataFrame(schema=SCHEMA)
        frame.write_parquet(args.out, compression="zstd")

        stats = {
            "n_residual": len(residual), "n_generated": len(done) + len(pending),
            "n_records_with_components": kept,
            "n_components": frame.height,
            "n_dropped_invalid_json": dropped_invalid,
            "n_skipped_left_residual": dropped_stale,
            "n_dropped_unknown_name": dropped_unknown,
            "n_resolved_via_alias": rescued_aliases,
            "n_dropped_ungrounded": dropped_ungrounded,
            "n_kept_as_typo_correction": rescued_typos,
            "n_amounts_dropped_implausible": dropped_implausible,
        }
        m.add_output(args.out).note(**stats)
        print(f"\n  records the model read      {kept:,}")
        print(f"  components emitted          {frame.height:,}")
        print(f"  dropped, invalid JSON       {dropped_invalid:,}")
        print(f"  skipped, no longer residual {dropped_stale:,}   the rules read these now")
        print(f"  dropped, name not in lexicon {dropped_unknown:,}")
        print(f"  resolved via an alias        {rescued_aliases:,}")
        print(f"  dropped, not in the text     {dropped_ungrounded:,}   inventions")
        print(f"  kept as a typo correction    {rescued_typos:,}   read through a misspelling")
        print(f"\n  {args.out}")
        print(f"  next: ./run.sh assign.classify --slm-components {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
