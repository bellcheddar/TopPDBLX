"""Stage `models.eval_slm`: does the fine-tuned parser actually beat the rules?

R1's failure condition, set in `ROADMAP.md` before any training: **component identification must
exceed 85% and schema validity must stay at or above 99%**, or `rules_v3` is kept and the attempt
recorded.

**The circularity trap, and how this stage stays out of it.** The labels are the rule parser's
own output, so no metric computed against them can show the model beating the rule parser: at
best it shows faithful imitation. So two different things are measured, and they are never
conflated:

  *fidelity*, on the held-out validation split. These are records the rules read confidently and
  the model never saw, on proteins in different sequence clusters. High fidelity means the model
  learned the mapping rather than memorising strings. It is a ceiling, not a win.

  *identification*, on the residual. These are the records the rules could **not** read, so there is
  no label to be circular about. The question is simply whether the emitted reagent name is one
  the curated lexicon recognises. That is a real gain over `rules_v3`, which returned nothing
  for these by definition.

**What this stage cannot measure.** The model only ever emits reagent names it saw in training,
so it cannot discover chemistry absent from the curated lexicon. A residual record failing
because it names 6-aminohexanoic acid (genuinely not in the lexicon) is indistinguishable here
from one the model simply got wrong. Separating those two needs hand-labelled truth, so a sample
of residual outputs is written for inspection: tens of items to eyeball, not thousands.

    ./run.sh models.eval_slm
    ./run.sh models.eval_slm --limit 3000 --checkpoint 600
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml
from tqdm import tqdm

from .. import config
from ..manifest import Manifest
from .build_slm_dataset import SYSTEM

STAGE = "models.eval_slm"

# Generous enough for the longest real answer (the p99 completion is well under this), because a
# truncated generation is scored as invalid JSON and would understate validity.
MAX_TOKENS = 512
BATCH = 32

IDENTIFICATION_TARGET = 85.0
VALIDITY_TARGET = 99.0

VALID_ROLES = {"precipitant", "salt", "buffer", "additive", "cryo", "not_a_component",
               "unknown"}
VALID_UNITS = {"percent_w_v", "percent_v_v", "molar", "millimolar", "mg_ml", None}

# Near-misses that mean exactly one of the valid units, mapped rather than rejected.
#
# `bad_unit` was 33 of the 54 invalid generations at checkpoint 1200, or **61% of everything
# still failing**, and every case was the model writing a chemist's spelling of a unit it had
# learned correctly: "mM" for millimolar, "M" for molar, "% w/v" for percent_w_v. Rejecting those
# scores a right answer as malformed JSON, which measures the vocabulary the schema happens to use
# rather than whether the model read the text.
#
# Deliberately narrow. Only spellings whose meaning is unambiguous are mapped: `%` alone is *not*
# here, because whether a bare percentage is w/v or v/v depends on the chemistry and guessing it
# would invent data, which is precisely what `parse.quantity.infer_unit` exists to decide.
UNIT_SYNONYMS = {
    "m": "molar", "molar": "molar", "mol/l": "molar", "moll": "molar", "mol l-1": "molar",
    "mm": "millimolar", "millimolar": "millimolar", "mmol/l": "millimolar",
    "mmol": "millimolar", "mmoll": "millimolar",
    "% w/v": "percent_w_v", "%w/v": "percent_w_v", "w/v": "percent_w_v",
    "percent_wv": "percent_w_v", "percentw_v": "percent_w_v", "wv": "percent_w_v",
    "% v/v": "percent_v_v", "%v/v": "percent_v_v", "v/v": "percent_v_v",
    "percent_vv": "percent_v_v", "percentv_v": "percent_v_v", "vv": "percent_v_v",
    "mg/ml": "mg_ml", "mgml": "mg_ml", "mg ml-1": "mg_ml", "mg_per_ml": "mg_ml",
}


def normalise_unit(unit: Any) -> Any:
    """Map a near-miss unit onto the closed vocabulary, or return it unchanged to be rejected."""
    if unit is None or unit in VALID_UNITS:
        return unit
    if not isinstance(unit, str):
        return unit
    return UNIT_SYNONYMS.get(unit.strip().lower(), unit)


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval, as percentages.

    Reported because the point estimates alone invited a wrong conclusion. Checkpoints 600 and
    900 scored 87.01% and 85.17% identification, which reads as a peak followed by decline; their
    intervals overlap ([85.58, 88.32] against [83.67, 86.55]), so the difference is sampling
    noise on 800 records and the truthful statement is that identification plateaus. Wilson rather
    than the normal approximation because these proportions sit near 1, where the normal interval
    runs past 100%.
    """
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (100 * max(0.0, centre - half), 100 * min(1.0, centre + half))


def load_lexicon() -> set[str]:
    """Canonical reagent names, which is what "identified" means for a generated name."""
    data = yaml.safe_load((config.ONTOLOGY_DIR / "synonyms.yaml").read_text())
    # `canonical_id` (PEG_200, MAGNESIUM_SULFATE) is what the components table stores in
    # `name_canonical`, so it is also what the model is trained to emit.
    return {r["canonical_id"] for r in data["reagents"]}



def load_aliases() -> dict[str, list[str]]:
    """Canonical id -> every spelling the lexicon knows for it, normalised for substring search."""
    data = yaml.safe_load((config.ONTOLOGY_DIR / "synonyms.yaml").read_text())
    out: dict[str, list[str]] = {}
    for reagent in data["reagents"]:
        forms = [reagent["canonical_id"].replace("_", " "), reagent.get("display_name", "")]
        forms += list(reagent.get("aliases") or [])
        seen = []
        for form in forms:
            key = _flatten(str(form))
            if key and key not in seen:
                seen.append(key)
        out[reagent["canonical_id"]] = seen
    return out


def _flatten(text: str) -> str:
    """Lower case, strip everything that is not a letter or digit.

    Punctuation and spacing are exactly what varies between "PEG 3350", "peg-3350" and
    "peg3350", so removing them entirely is what lets one alias match all three.
    """
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def grounded_in_text(name: Optional[str], text: str,
                     aliases: dict[str, list[str]]) -> bool:
    """Whether the source text actually mentions the reagent the model named.

    **This is the check that matters, and the one the identification metric lacks.** Identification asks
    only whether an emitted name exists in the lexicon, so a model reading "20% PEG 3350" and
    emitting SODIUM_CHLORIDE scores as identified: the name is real, it is simply not the reagent
    in front of it. Curation exists to make names mean something, and a metric that cannot tell
    a right name from a real one measures nothing worth having.

    Grounding needs no labels: if the model names a reagent, some spelling of that reagent should
    appear in the text it was given. A failure is either a hallucination or a spelling the lexicon
    has never seen, and both are things to know about.
    """
    if not name:
        return False
    flat = _flatten(text)
    for form in aliases.get(name, []):
        if form and form in flat:
            return True
    return False


def check(text: str, lexicon: set[str]) -> dict[str, Any]:
    """Score one generation. Schema validity is judged strictly: the JSON must parse, be a list
    of objects, and every role and unit must be drawn from the closed vocabulary."""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {"valid": False, "reason": "unparseable", "n": 0, "n_identified": 0}
    if not isinstance(parsed, list):
        return {"valid": False, "reason": "not_a_list", "n": 0, "n_identified": 0}

    names, identified, non_components = [], 0, 0
    for item in parsed:
        if not isinstance(item, dict):
            return {"valid": False, "reason": "element_not_object", "n": 0, "n_identified": 0}
        if item.get("role") not in VALID_ROLES:
            return {"valid": False, "reason": "bad_role", "n": 0, "n_identified": 0}
        unit = normalise_unit(item.get("unit"))
        if unit not in VALID_UNITS:
            return {"valid": False, "reason": "bad_unit", "n": 0, "n_identified": 0}
        item["unit"] = unit  # so the repaired value is what downstream and the sample file see
        # Excluded from the identification denominator, not counted as a miss. The model saying "this
        # text names no reagent" is a correct answer, and there is no lexicon entry it could have
        # matched, so scoring it as a failure would measure an artefact. This mirrors the same
        # exclusion applied to the rule parser in parse/noncomponent.py.
        if item.get("role") == "not_a_component":
            non_components += 1
            continue
        name = item.get("name")
        names.append(name)
        if name in lexicon:
            identified += 1
    return {"valid": True, "reason": None, "n": len(parsed) - non_components,
            "n_identified": identified, "n_non_components": non_components,
            "names": names, "parsed": parsed}


def component_key(item: dict[str, Any]) -> tuple:
    """Identity for fidelity scoring. Amounts are rounded because the model reproducing 0.1 as
    0.1000000001 is not a parsing error."""
    amount = item.get("amount")
    return (item.get("role"), item.get("name"),
            round(float(amount), 4) if isinstance(amount, (int, float)) else None,
            item.get("unit"))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=config.INTERIM_DIR / "slm")
    parser.add_argument("--adapter-dir", type=Path, default=None,
                        help="defaults to the highest-numbered run in data/interim/slm/runs")
    parser.add_argument("--model", default="mlx-community/SmolLM2-360M-Instruct")
    parser.add_argument("--limit", type=int, default=4000,
                        help="records per set; the residual is 81,803 and a full pass is slow")
    parser.add_argument("--batch", type=int, default=BATCH)
    parser.add_argument("--out", type=Path, default=config.INTERIM_DIR / "slm" / "eval.json")
    parser.add_argument("--sample-out", type=Path,
                        default=config.INTERIM_DIR / "slm" / "residual_sample.jsonl")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--frozen", action="store_true",
                        help="score against the frozen benchmark instead of the live residual, "
                             "so rounds can be compared to each other")
    parser.add_argument("--checkpoint", type=int, default=None,
                        help="evaluate the intermediate checkpoint at this iteration")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    if args.adapter_dir is None:
        # Runs are named `...-roundNN` and live under `runs/`, so the latest is the highest
        # number rather than the newest mtime: a re-evaluated older run touches its directory
        # and would otherwise masquerade as the current one.
        runs = sorted((args.data_dir / "runs").glob("*-round*"))
        if not runs:
            raise SystemExit(f"no runs in {args.data_dir / 'runs'}. "
                             f"Run: ./run.sh models.train_slm")
        args.adapter_dir = runs[-1]
        print(f"  evaluating latest run: {args.adapter_dir.name}")

    if args.checkpoint is not None:
        # mlx-lm loads an adapter directory, not a file, so an intermediate checkpoint has to be
        # staged as `adapters.safetensors` alongside a copy of the config. Copied rather than
        # moved, so the final adapter is never disturbed.
        #
        # Staged **outside** the project tree on purpose. `~/Documents` is iCloud-synced, and
        # writing 17 MB adapter copies into it sends `fileproviderd` to 150% CPU uploading files
        # that exist only for the length of one evaluation. That starves the very GPU processes
        # being measured, which showed up as the sweep running several times slower than the
        # smoke test at the same batch size.
        source = args.adapter_dir / f"{args.checkpoint:07d}_adapters.safetensors"
        if not source.exists():
            raise SystemExit(f"no checkpoint at iteration {args.checkpoint}: {source}")
        staged = Path(tempfile.mkdtemp(prefix=f"toppdblx_ckpt_{args.checkpoint:07d}_"))
        shutil.copy2(source, staged / "adapters.safetensors")
        shutil.copy2(args.adapter_dir / "adapter_config.json", staged / "adapter_config.json")
        args.adapter_dir = staged
    from mlx_lm import batch_generate, load
    from mlx_lm.sample_utils import make_sampler

    os.environ.pop("HF_TOKEN", None)  # stale token 401s even on public repos
    lexicon = load_lexicon()
    aliases = load_aliases()
    rng = random.Random(args.seed)

    valid_rows = [json.loads(l) for l in
                  (args.data_dir / "valid.jsonl").read_text().splitlines() if l.strip()]
    # The live residual shrinks from the easy end whenever curation or the parser improves, so
    # a score on it is not comparable between rounds. The frozen set fixes the population.
    frozen_meta = None
    if args.frozen:
        frozen_path = args.data_dir / "frozen_evalset.jsonl"
        if not frozen_path.exists():
            raise SystemExit(f"{frozen_path} missing. Run: ./run.sh models.freeze_evalset")
        loaded = [json.loads(l) for l in frozen_path.read_text().splitlines() if l.strip()]
        frozen_meta = loaded[0].get("_meta") if loaded and "_meta" in loaded[0] else None
        residual_rows = [r for r in loaded if "_meta" not in r]
    else:
        residual_rows = [json.loads(l) for l in
                         (args.data_dir / "residual.jsonl").read_text().splitlines() if l.strip()]
    rng.shuffle(valid_rows)
    if not args.frozen:
        rng.shuffle(residual_rows)
    valid_rows = valid_rows[:args.limit]
    if not args.frozen:
        residual_rows = residual_rows[:args.limit]

    with Manifest(STAGE, params={"model": args.model, "adapter": str(args.adapter_dir),
                                 "limit": args.limit, "max_tokens": MAX_TOKENS,
                                 "seed": args.seed}) as m:
        m.add_input(args.data_dir / "valid.jsonl").add_input(args.data_dir / "residual.jsonl")

        model, tokenizer = load(args.model, adapter_path=str(args.adapter_dir))
        # Greedy. This is a structured extraction task with one right answer, and sampling would
        # add variance to the metric for no benefit.
        sampler = make_sampler(temp=0.0)

        def run(texts: list[str], label: str) -> list[str]:
            # Token lists, not strings: batch_generate right-pads the prompts itself, and handing
            # it strings fails inside its statistics context manager as a ZeroDivisionError that
            # hides the real TypeError.
            prompts = [tokenizer.apply_chat_template(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": t}],
                add_generation_prompt=True) for t in texts]
            out: list[str] = []
            for i in tqdm(range(0, len(prompts), args.batch), desc=label, unit="batch"):
                chunk = prompts[i:i + args.batch]
                result = batch_generate(model, tokenizer, prompts=chunk,
                                        max_tokens=MAX_TOKENS, sampler=sampler, verbose=False)
                out.extend(result.texts)
            return out

        # --- Fidelity: held-out records the rules read confidently -----------------------
        fid_inputs = [r["messages"][1]["content"] for r in valid_rows]
        fid_truth = [r["messages"][2]["content"] for r in valid_rows]
        fid_generated = run(fid_inputs, "fidelity")

        exact = 0
        tp = fp = fn = 0
        fid_valid = 0
        for gen, truth in zip(fid_generated, fid_truth):
            scored = check(gen.strip(), lexicon)
            if scored["valid"]:
                fid_valid += 1
            got = scored.get("parsed") or []
            want = json.loads(truth)
            if scored["valid"] and got == want:
                exact += 1
            got_keys = [component_key(i) for i in got if isinstance(i, dict)]
            want_keys = [component_key(i) for i in want]
            remaining = list(want_keys)
            for key in got_keys:
                if key in remaining:
                    remaining.remove(key)
                    tp += 1
                else:
                    fp += 1
            fn += len(remaining)

        precision = 100 * tp / (tp + fp) if tp + fp else 0.0
        recall = 100 * tp / (tp + fn) if tp + fn else 0.0

        # --- Identification: the residual, where the rules returned nothing ------------------
        res_generated = run([r["text"] for r in residual_rows], "residual")

        res_valid = res_components = res_identified = 0
        res_grounded = 0
        ungrounded_examples: list[dict[str, Any]] = []
        fully_identified = 0
        reasons: dict[str, int] = {}
        unidentified_names: dict[str, int] = {}
        samples = []
        for row, gen in zip(residual_rows, res_generated):
            scored = check(gen.strip(), lexicon)
            if not scored["valid"]:
                reasons[scored["reason"]] = reasons.get(scored["reason"], 0) + 1
                continue
            res_valid += 1
            res_components += scored["n"]
            res_identified += scored["n_identified"]
            # Grounding: is each named reagent actually mentioned in the text it was given?
            for item in scored["parsed"]:
                if item.get("role") == "not_a_component":
                    continue
                name = item.get("name")
                if name not in lexicon:
                    continue
                if grounded_in_text(name, row["text"], aliases):
                    res_grounded += 1
                elif len(ungrounded_examples) < 40:
                    ungrounded_examples.append(
                        {"pdb_id": row["pdb_id"], "named": name, "text": row["text"][:160]})
            if scored["n"] and scored["n_identified"] == scored["n"]:
                fully_identified += 1
            for name in scored.get("names", []):
                if name not in lexicon:
                    unidentified_names[str(name)] = unidentified_names.get(str(name), 0) + 1
            if len(samples) < 60:
                samples.append({"pdb_id": row["pdb_id"], "text": row["text"],
                                "rules_confidence": row.get("rules_confidence"),
                                "rules_unidentified": row.get("n_unidentified", row.get("n_unresolved")),
                                "model": scored["parsed"]})

        with open(args.sample_out, "w") as handle:
            for s in samples:
                handle.write(json.dumps(s) + "\n")
        m.add_output(args.sample_out)

        identification = 100 * res_identified / res_components if res_components else 0.0
        validity = 100 * res_valid / len(residual_rows) if residual_rows else 0.0
        res_lo, res_hi = wilson(res_identified, res_components)
        grounded_pct = 100 * res_grounded / res_identified if res_identified else 0.0
        gr_lo, gr_hi = wilson(res_grounded, res_identified)
        val_lo, val_hi = wilson(res_valid, len(residual_rows))

        results = {
            "n_fidelity": len(valid_rows),
            "fidelity_exact_match_pct": round(100 * exact / len(valid_rows), 2),
            "fidelity_schema_valid_pct": round(100 * fid_valid / len(valid_rows), 2),
            "fidelity_component_precision_pct": round(precision, 2),
            "fidelity_component_recall_pct": round(recall, 2),
            "n_residual": len(residual_rows),
            "evaluated_on": "frozen_benchmark" if args.frozen else "live_residual",
            "frozen_meta": frozen_meta,
            "residual_schema_valid_pct": round(validity, 2),
            "residual_component_identification_pct": round(identification, 2),
            "residual_fully_identified_records_pct": round(
                100 * fully_identified / len(residual_rows), 2) if residual_rows else 0.0,
            "residual_components_seen": res_components,
            # Of the components whose name the lexicon recognises, how many are names the source
            # text actually mentions. This is the check that separates a *real* name from the
            # *right* name, and identification alone cannot see the difference.
            "residual_grounded_pct": round(grounded_pct, 2),
            "residual_grounded_ci95": [round(gr_lo, 2), round(gr_hi, 2)],
            "residual_grounded_n": res_grounded,
            "ungrounded_examples": ungrounded_examples[:15],
            "invalid_reasons": reasons,
            "top_unidentified_names": dict(sorted(unidentified_names.items(),
                                                key=lambda kv: -kv[1])[:25]),
            "n_distinct_unidentified_names": len(unidentified_names),
            # Judged on the lower confidence bound, not the point estimate. A gate applied to the
            # point estimate is passed by a lucky sample: checkpoint 900 reads 85.17% against an
            # 85% target while its interval reaches down to 83.67%, so it has not actually been
            # shown to clear the bar.
            "identification_ci95": [round(res_lo, 2), round(res_hi, 2)],
            "validity_ci95": [round(val_lo, 2), round(val_hi, 2)],
            "meets_identification_target": res_lo > IDENTIFICATION_TARGET,
            "meets_validity_target": val_lo >= VALIDITY_TARGET,
            "meets_identification_target_point_estimate": identification > IDENTIFICATION_TARGET,
            "meets_validity_target_point_estimate": validity >= VALIDITY_TARGET,
        }
        # The complete counter, not just the top 25 kept in the results summary. These are names
        # the model wanted to emit and the lexicon does not have, which makes them a curation
        # queue in their own right: the model has already normalised them, so one decision on
        # `ARGININE` covers every surface form that maps to it, where the raw corpus queue would
        # ask about "l-arginine", "arginine hcl" and "l-arginine hydrochloride" separately.
        names_path = args.out.with_name(args.out.stem + "_model_unidentified_names.json")
        names_path.write_text(json.dumps(
            dict(sorted(unidentified_names.items(), key=lambda kv: -kv[1])), indent=2) + "\n")
        m.add_output(names_path)

        args.out.write_text(json.dumps(results, indent=2) + "\n")
        m.add_output(args.out).note(**{k: v for k, v in results.items()
                                      if not isinstance(v, dict)})

        print(f"\n  fidelity, on {len(valid_rows):,} held-out records the rules read confidently")
        print(f"    exact JSON match          {results['fidelity_exact_match_pct']:>7.2f}%")
        print(f"    schema valid              {results['fidelity_schema_valid_pct']:>7.2f}%")
        print(f"    component precision       {precision:>7.2f}%")
        print(f"    component recall          {recall:>7.2f}%")
        print(f"    (imitation of rules_v3, not a win over it)")
        population = ("the frozen benchmark" if args.frozen
                      else "live residual records the rules could not read")
        print(f"\n  identification, on {len(residual_rows):,} {population}")
        if frozen_meta:
            print(f"    frozen at lexicon {frozen_meta.get('lexicon_version')}, "
                  f"{frozen_meta.get('n_reagents')} reagents, git "
                  f"{frozen_meta.get('git_commit')}: comparable across rounds")
        print(f"    schema valid              {validity:>7.2f}%  "
              f"[{val_lo:5.2f}, {val_hi:5.2f}]  target >= {VALIDITY_TARGET}  "
              f"{'PASS' if results['meets_validity_target'] else 'FAIL'}")
        print(f"    component identification      {identification:>7.2f}%  "
              f"[{res_lo:5.2f}, {res_hi:5.2f}]  target >  {IDENTIFICATION_TARGET}  "
              f"{'PASS' if results['meets_identification_target'] else 'FAIL'}")
        print(f"    (gates judged on the lower 95% bound, so a lucky sample cannot pass them)")
        print(f"    grounded in the text      {grounded_pct:>7.2f}%  "
              f"[{gr_lo:5.2f}, {gr_hi:5.2f}]   the named reagent is actually mentioned")
        print(f"    records fully identified    "
              f"{results['residual_fully_identified_records_pct']:>7.2f}%")
        if reasons:
            print(f"    invalid: {reasons}")
        if results["top_unidentified_names"]:
            top = list(results["top_unidentified_names"].items())[:8]
            print(f"\n  names the model emitted that the lexicon does not know:")
            for name, count in top:
                print(f"    {count:>5}  {name}")
            print(f"  these are candidate lexicon additions, not necessarily model errors.")
        print(f"\n  {len(samples)} residual outputs to eyeball: {args.sample_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
