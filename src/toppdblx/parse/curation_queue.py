"""Stage `parse.curation_queue`: the second curation round, from two sources at once.

Round 1 (`parse.lexicon_questions`) ranked raw corpus strings by frequency. That works, but it
spends an expert's attention badly: `l-arginine`, `arginine hcl` and `l-arginine hydrochloride`
arrive as three separate questions about one missing reagent.

This stage adds a second source that does not have that problem: **the names the fine-tuned model
tried to emit and the lexicon does not contain.** The model has already normalised them, so
`ARGININE` arrives once, standing for every surface form that maps to it. In effect the model
hands over a deduplicated request list for the chemistry it can see but cannot name.

The two sources are merged rather than concatenated. A model-proposed name and the raw strings
that would identify to it become **one** question carrying the combined corpus weight, so the
ranking reflects true reach and the same decision is never asked twice.

Sizing comes from the measured marginal return, not from a round number. Components gained per
decision, over the corpus as it stands:

    decisions   41-80   99 per decision
    decisions  81-160   73 per decision
    decisions 161-320   48 per decision
    decisions 321-640   28 per decision

So the value holds to roughly decision 160 and then halves. The default of 120 takes the queue to
that point in one sitting, and is worth about 1.6 points of component identification: for comparison,
tripling the training exposure moved identification by zero.

    ./run.sh parse.curation_queue
    ./run.sh parse.curation_queue --limit 200
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import polars as pl
import yaml

from .. import config
from ..manifest import Manifest
from .lexicon_questions import (build_index, is_ambiguous, normalise_for_match, propose)
from .noncomponent import classify as classify_non_component

STAGE = "parse.curation_queue"


# One bucket per repeated judgement, so the queue asks about kinds of decision rather than about
# individual strings.
BUCKET_GROUPS = {
    "map": "Names the lexicon already has, spelled differently",
    "new": "Reagents the lexicon is missing",
    "leave": "Too vague to identify",
    "not_a_reagent": "Probably not chemistry",
}


def bucket_for(basis: str, entry: dict[str, Any],
               options: list[dict[str, Any]]) -> tuple[str, str, Optional[str]]:
    """Which bucket a candidate belongs to: (key, action, chemical class).

    Grouped by the *decision* being made, not by the string. Thirty chemical formulae that all
    map onto salts already in the lexicon are one judgement; so are twelve PEG sizes the lexicon
    lacks. Splitting by chemical class keeps each bucket answerable in one go, since "add all of
    these as salts" is a claim a chemist can accept or reject at a glance.
    """
    if basis in ("normalised_match", "formula", "fuzzy"):
        return ("map", "map", None)
    if basis == "ambiguous":
        return ("leave", "leave", None)
    recommended = next((o for o in options if o["recommended"]), None)
    label = (recommended or {}).get("label", "")
    match = re.search(r"class (\w+)", label)
    chem_class = match.group(1) if match else None
    # "(class needed)" is the recommender's way of saying it could not guess, so the regex
    # matches it and reads the word "needed" as a chemical class. Left uncaught, every
    # unguessable candidate landed in one bucket labelled "class needed" and was offered for
    # bulk acceptance alongside method text.
    if chem_class == "needed":
        chem_class = None
    if chem_class is None:
        # Nothing about the name suggested a chemical class, which is exactly the population
        # that also collects method text. The non-component classifier is re-run here **without
        # its quantity veto**: that veto exists to protect a reagent sitting beside a method
        # phrase, and there is no reagent to protect when the class guess already failed. Without
        # this the largest bucket held "soaked", "ul reservoir" and "precipitant mix 4" alongside
        # real chemistry, and accepting it in bulk would have written them into the lexicon.
        display = entry.get("display") or ""
        if classify_non_component(display, has_quantity=False) or not looks_like_chemistry(display):
            return ("not_a_reagent", "not_a_reagent", None)
        return ("new::unclassed", "new", "additive")
    return (f"new::{chem_class or 'unclassed'}", "new", chem_class)


# Words and endings that mark a string as naming a substance. Used only to split the bucket of
# candidates whose chemical class could not be guessed, which is otherwise a junk drawer holding
# "acetic acid" and "dl-alanine" beside "soaked", "eg" and "precipitant mix 4". Bulk-accepting
# that would write method notes into the lexicon as reagents.
_CHEMICAL_WORDS = re.compile(
    r"\b(acid|acids|salt|sodium|potassium|ammonium|lithium|caesium|cesium|rubidium|magnesium|"
    r"calcium|barium|strontium|zinc|manganese|nickel|cobalt|copper|cadmium|iron|silver|mercury|"
    r"aluminium|aluminum|chloride|bromide|iodide|fluoride|sulfate|sulphate|nitrate|phosphate|"
    r"acetate|formate|citrate|tartrate|malate|maleate|malonate|succinate|thiocyanate|carbonate|"
    r"oxalate|cacodylate|borate|glycol|glycerol|alcohol|ethanol|methanol|propanol|butanol|"
    r"amine|amide|urea|sucrose|glucose|trehalose|sorbitol|xylitol|mannitol|inositol|dextran|"
    r"alanine|arginine|glycine|serine|proline|lysine|histidine|glutamate|aspartate|taurine|"
    r"betaine|spermine|spermidine|dithionite|dithiothreitol|azide|hcl|naoh|koh|edta|egta|dmso)\b",
    re.I)
_CHEMICAL_ENDINGS = re.compile(
    # -ose catches the sugars (galactose, xylose, mannose, fucose), which the first version
    # missed and sent to the junk bucket along with the nucleotides below.
    r"(ate|ide|ite|ol|ane|ene|one|ose|amine|amide|osine|idine)$", re.I)

# Abbreviations that name real chemistry and contain no chemical word or ending at all. Every one
# of these was in the "probably not chemistry" bucket on the first attempt.
_CHEMICAL_ABBREVIATIONS = {
    "atp", "adp", "amp", "gtp", "gdp", "gmp", "ctp", "utp", "nad", "nadh", "nadp", "nadph",
    "fad", "fmn", "coa", "sam", "plp", "plp", "dtt", "tcep", "bme", "mpd", "peg", "mme",
    "bes", "mes", "mops", "hepes", "tris", "caps", "ches", "taps", "tapso", "pipes", "epps",
    "bicine", "tricine", "adа", "water", "glycerol", "etgly", "spg", "mib", "smt",
}
_FORMULA_SHAPE = re.compile(r"^[a-z]{1,3}\d?(?:[a-z]{1,4}\d?){1,4}$", re.I)


def looks_like_chemistry(name: str) -> bool:
    """Whether a string plausibly names a substance at all."""
    text = (name or "").strip().lower()
    if not text or len(text) < 3:
        return False
    if _CHEMICAL_WORDS.search(text):
        return True
    words = [w for w in re.split(r"[^a-z0-9]+", text) if w]
    if any(w in _CHEMICAL_ABBREVIATIONS for w in words):
        return True
    # A compact formula such as gdcl3 or ch3coona: letters and digits, no spaces, and containing
    # a digit, which ordinary words in this corpus do not.
    if len(words) == 1 and any(ch.isdigit() for ch in text) and _FORMULA_SHAPE.match(text):
        return True
    return any(_CHEMICAL_ENDINGS.search(word) for word in words)


def humanise(canonical: str) -> str:
    """`MANGANESE_SULFATE` -> `manganese sulfate`, for matching against corpus text."""
    return canonical.replace("_", " ").lower()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--model-names", type=Path,
                        default=config.INTERIM_DIR / "slm"
                                / "eval_final_model_unidentified_names.json",
                        help="full unidentified-name counter written by models.eval_slm")
    parser.add_argument("--model-sample-size", type=int, default=6000,
                        help="residual records the model names were counted over, for scaling")
    parser.add_argument("--residual-size", type=int, default=76923)
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "curation_queue.json")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--min-count", type=int, default=15)
    parser.add_argument("--previous-answers", type=Path,
                        default=config.INTERIM_DIR / "lexicon_gaps_answers.json")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    data = yaml.safe_load((config.ONTOLOGY_DIR / "synonyms.yaml").read_text())
    reagents = data["reagents"]
    index = build_index(reagents)
    keys = list(index)
    known_ids = {r["canonical_id"] for r in reagents}

    # Never re-ask something already settled, whether it was answered explicitly or accepted as a
    # recommendation. Round 1 covered 40 strings and they must not reappear.
    already_asked: set[str] = set()
    if args.previous_answers.exists():
        for answer in json.loads(args.previous_answers.read_text())["answers"]:
            already_asked.add(answer["id"].split("::", 1)[1])
    previous_questions = config.INTERIM_DIR / "lexicon_questions.json"
    if previous_questions.exists():
        for question in json.loads(previous_questions.read_text())["questions"]:
            already_asked.add(question["id"].split("::", 1)[1])

    # --- source 1: raw corpus strings ------------------------------------------------------
    comp = pl.read_parquet(args.components)
    unidentified = comp.filter(pl.col("name_canonical").is_null()
                             & (pl.col("role") != "not_a_component"))
    raw_counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for name, pdb_id, conc, unit in unidentified.select(
            "name_raw", "pdb_id", "concentration", "unit").iter_rows():
        key = (name or "").strip().lower()
        if len(key) < 2 or key in already_asked:
            continue
        if classify_non_component(key, has_quantity=conc is not None):
            continue
        raw_counts[key] += 1
        if len(examples.setdefault(key, [])) < 3:
            amount = f"{conc:g} {unit}" if conc is not None and unit else "amount unstated"
            examples[key].append(f"{pdb_id}: {amount}")

    # --- source 2: names the model wanted to emit ------------------------------------------
    model_counts: Counter[str] = Counter()
    if args.model_names.exists():
        scale = args.residual_size / max(1, args.model_sample_size)
        for name, count in json.loads(args.model_names.read_text()).items():
            if name in known_ids or not name or name.lower() in already_asked:
                continue
            # Scaled to the whole residual, because the counter was built from a sample. Marked
            # as an estimate wherever it is shown, never presented as an exact count.
            model_counts[name] = int(round(count * scale))

    # --- merge: one decision per reagent, not per spelling ---------------------------------
    # A model-proposed name absorbs the raw strings that normalise onto it, so the question is
    # asked once and its weight reflects everything it would identify.
    merged: dict[str, dict[str, Any]] = {}
    claimed: set[str] = set()
    for name, model_weight in model_counts.items():
        target = normalise_for_match(humanise(name))
        surface = [s for s in raw_counts
                   if normalise_for_match(s) == target or humanise(name) in s]
        raw_weight = sum(raw_counts[s] for s in surface)
        claimed.update(surface)
        merged[name] = {
            "kind": "model", "proposed_id": name,
            "weight": max(model_weight, raw_weight),
            "model_estimate": model_weight, "corpus_exact": raw_weight,
            "surface_forms": sorted(surface, key=lambda s: -raw_counts[s])[:6],
            "examples": [e for s in surface[:2] for e in examples.get(s, [])][:3],
        }

    for name, count in raw_counts.items():
        if name in claimed or count < args.min_count:
            continue
        merged[name] = {"kind": "corpus", "proposed_id": None, "weight": count,
                        "model_estimate": 0, "corpus_exact": count,
                        "surface_forms": [name], "examples": examples.get(name, [])}

    with Manifest(STAGE, params={"limit": args.limit, "min_count": args.min_count}) as m:
        m.add_input(args.components)
        if args.model_names.exists():
            m.add_input(args.model_names)

        questions: list[dict[str, Any]] = []
        buckets: dict[tuple[str, str, Optional[str]], list[dict[str, Any]]] = {}
        kinds: Counter[str] = Counter()
        for name, entry in sorted(merged.items(), key=lambda kv: -kv[1]["weight"]):
            if len(questions) >= args.limit or entry["weight"] < args.min_count:
                break
            display = entry["surface_forms"][0] if entry["surface_forms"] else name.lower()
            options, basis = propose(display, index, keys)
            kinds[entry["kind"]] += 1

            if entry["kind"] == "model":
                # The model's own proposal leads, unless the guards already found a real match.
                if basis in ("new", "ambiguous"):
                    for option in options:
                        option["recommended"] = False
                    options.insert(0, {
                        "value": "__NEW__",
                        "label": f"Add as a new reagent: {entry['proposed_id']}",
                        "recommended": True})
                why = (f"The model emitted “{entry['proposed_id']}” for roughly "
                       f"{entry['model_estimate']:,} components but the lexicon has no such "
                       f"entry.")
                if entry["corpus_exact"]:
                    why += (f" The corpus also holds {entry['corpus_exact']:,} components under "
                            f"{len(entry['surface_forms'])} spelling(s).")
            else:
                why = (f"{entry['corpus_exact']:,} components use this exact string and the "
                       f"lexicon does not identify it.")

            # Bucketed rather than asked one at a time. 120 individual questions is more
            # attention than this is worth: the decisions repeat, and a bucket of 30 chemical
            # formulae for salts already in the lexicon is genuinely one judgement, not thirty.
            # Free text is removed for the same reason: an answer that applies to a whole bucket
            # cannot be a typed name, and every option here is already a canonical entry.
            entry["display"] = display
            bucket = bucket_for(basis, entry, options)
            buckets.setdefault(bucket, []).append(
                {"name": name, "display": display, "weight": entry["weight"],
                 "options": options, "kind": entry["kind"],
                 "surface_forms": entry["surface_forms"], "examples": entry["examples"]})

        for (bucket_key, action, chem_class), members in sorted(
                buckets.items(), key=lambda kv: -sum(x["weight"] for x in kv[1])):
            members.sort(key=lambda x: -x["weight"])
            weight = sum(x["weight"] for x in members)
            shown = ", ".join(x["display"] for x in members[:8])
            if len(members) > 8:
                shown += f", and {len(members) - 8} more"

            if action == "map":
                targets = {next((o["value"] for o in x["options"] if o["recommended"]), None)
                           for x in members}
                target_note = (f"onto {next(iter(targets))}" if len(targets) == 1
                               else f"onto {len(targets)} existing entries, one each")
                options = [
                    ("__ACCEPT__", f"Yes: add all {len(members)} as aliases {target_note}", True),
                    ("__NEW__", f"No: add all {len(members)} as new reagents instead", False),
                    ("__LEAVE__", "No: leave all unidentified", False),
                ]
                question = (f"{len(members)} unidentified names look like spellings of reagents "
                            f"the lexicon already has. Add them as aliases?")
            elif action == "new":
                label = f", class {chem_class}" if chem_class else ""
                options = [
                    ("__ACCEPT__", f"Yes: add all {len(members)} as new reagents{label}", True),
                    ("__LEAVE__", "No: leave all unidentified", False),
                    ("__NOT_A_REAGENT__", "No: none of these are reagents", False),
                ]
                question = (f"{len(members)} unidentified names are not in the lexicon at all"
                            f"{label}. Add them?")
            elif action == "not_a_reagent":
                options = [
                    ("__LEAVE__", f"Leave all {len(members)} unidentified (safe default)", True),
                    ("__NOT_A_REAGENT__",
                     f"Mark all {len(members)} as not chemistry", False),
                ]
                question = (f"{len(members)} unidentified names could not be recognised as "
                            f"chemistry. Leave them alone?")
            else:
                options = [
                    ("__LEAVE__", f"Yes: leave all {len(members)} unidentified", True),
                    ("__NEW__", f"No: add all {len(members)} as new reagents", False),
                ]
                question = (f"{len(members)} unidentified names state a chemical family without "
                            f"saying which member. Leave them unidentified?")

            questions.append({
                "id": f"curation::bucket::{bucket_key}",
                "group": BUCKET_GROUPS[action],
                "question": question,
                "why": (f"Answering once settles all {len(members)}, covering about "
                        f"{weight:,} components. Every option is a canonical lexicon entry, so "
                        f"nothing here can introduce a name the chemistry does not support."),
                "weight": weight,
                "weight_label": f"{len(members)} names, ~{weight:,} components",
                "type": "choice",
                "options": [{"value": v, "label": l, "recommended": r} for v, l, r in options],
                "allow_text": False,
                "members": [{"name": x["name"],
                             "target": next((o["value"] for o in x["options"]
                                             if o["recommended"]), None),
                             "weight": x["weight"]} for x in members],
                "context": [shown] + (members[0]["examples"][:2] if members else []),
            })

        covered = sum(q["weight"] for q in questions)
        payload = {
            "schema_version": config.SCHEMA_VERSION,
            "lexicon_version": data.get("version", "unknown"),
            "title": "Curation round 2",
            "intro": (f"{len(questions)} questions covering about {covered:,} unidentified "
                      f"components. {kinds['model']} come from names the parser model tried to "
                      f"emit and the lexicon lacks, already normalised, so one answer settles "
                      f"every spelling. The rest are the most frequent unidentified strings. "
                      f"Every dropdown is pre-set: change the ones that are wrong."),
            "n_questions": len(questions),
            "totals": {
                "n_from_model": kinds["model"], "n_from_corpus": kinds["corpus"],
                "n_components_covered": covered,
                "n_unidentified_components": unidentified.height,
                "pct_of_unidentified_covered": round(100 * covered / max(1, unidentified.height), 2),
                "n_already_asked_skipped": len(already_asked),
            },
            "questions": questions,
        }
        args.out.write_text(json.dumps(payload, indent=2) + "\n")
        m.add_output(args.out).note(**payload["totals"], n_questions=len(questions))

        print(f"\n  {len(questions)} questions covering about {covered:,} components "
              f"({payload['totals']['pct_of_unidentified_covered']}% of the unidentified mass)")
        print(f"    {kinds['model']:>3} from the model's own unidentified emissions "
              f"(pre-normalised, one answer covers every spelling)")
        print(f"    {kinds['corpus']:>3} from raw corpus frequency")
        print(f"    {len(already_asked)} already settled in round 1, not re-asked")
        print(f"\n  {args.out}")
        print(f"  open app/condition_courtroom_v5.html and drop the file on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
