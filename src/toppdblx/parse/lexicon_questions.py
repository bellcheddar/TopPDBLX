"""Stage `parse.lexicon_questions`: the lexicon gaps worth an expert's time.

R1 found that the unresolved residual is a long tail (45,573 distinct strings, top 1,000 covering
only 38.5%), so most of it can only be closed by generalisation. But the *head* of that tail is
different in kind: it is reagents the lexicon simply lacks a name for, and no model can invent
curated chemistry it has never seen. `methyl-2,4-pentanediol` appears 216 times and is just MPD
under a fuller name; `ca(oac)2` appears 200 times and is calcium acetate as a formula.

Built to the pattern that worked for the two previous audits: **rank distinct decisions by corpus
frequency, pre-select a recommendation, and keep the whole thing to tens of questions.** Never a
row-per-instance review. Each question here is one string standing for every occurrence of it, so
answering 40 questions resolves thousands of components.

The recommendation does the work. Three sources, in order of confidence:

  *formula expansion*  `bacl2` -> barium chloride, by cation and anion table. Deterministic.
  *close string match* against every canonical id and alias, after chemistry-aware
                       normalisation that drops hydration state and punctuation.
  *new reagent*        when nothing matches, with a chemical class guessed from the name so the
                       entry can be added rather than merely flagged.

Output feeds `app/condition_courtroom_v5.html`, which is payload-driven.

    ./run.sh parse.lexicon_questions
    ./run.sh parse.lexicon_questions --limit 60
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import polars as pl
import yaml

from .. import config
from ..manifest import Manifest
from .noncomponent import classify as classify_non_component

STAGE = "parse.lexicon_questions"

# Formula fragments common in deposition text. Enough to expand the frequent cases; not a general
# formula parser, because guessing wrongly here would put bad chemistry in front of an expert as
# a recommendation, which is worse than offering none.
_CATIONS = {
    "na": "sodium", "na2": "disodium", "k": "potassium", "k2": "dipotassium",
    "li": "lithium", "cs": "caesium", "rb": "rubidium", "nh4": "ammonium",
    "(nh4)2": "diammonium", "mg": "magnesium", "ca": "calcium", "ba": "barium",
    "sr": "strontium", "zn": "zinc", "mn": "manganese", "ni": "nickel", "co": "cobalt",
    "cu": "copper", "cd": "cadmium", "fe": "iron", "al": "aluminium",
}
_ANIONS = {
    "cl": "chloride", "cl2": "chloride", "cl3": "chloride", "br": "bromide", "i": "iodide",
    "f": "fluoride", "so4": "sulfate", "no3": "nitrate", "po4": "phosphate",
    "hpo4": "hydrogen phosphate", "h2po4": "dihydrogen phosphate",
    "oac": "acetate", "ac": "acetate", "ch3coo": "acetate", "cooch3": "acetate",
    "scn": "thiocyanate", "cit": "citrate", "tart": "tartrate", "mal": "malate",
    "form": "formate", "hcoo": "formate", "cac": "cacodylate",
}
_FORMULA = re.compile(
    r"^\(?(" + "|".join(sorted(_CATIONS, key=len, reverse=True)).replace("(", r"\(").replace(")", r"\)")
    + r")\)?(\d*)\s*[-·.]?\s*\(?(" + "|".join(sorted(_ANIONS, key=len, reverse=True))
    + r")\)?(\d*)$", re.I)

# Hydration state and grade words. Present in the text, absent from lexicon names, and never the
# thing that distinguishes one reagent from another.
_NOISE = re.compile(
    r"\b(?:anhydrous|anhydrate|monohydrate|dihydrate|dehydrate|trihydrate|tetrahydrate|"
    r"pentahydrate|hexahydrate|heptahydrate|octahydrate|decahydrate|hydrate|hydrous|"
    r"\d+\s*h2o|xh2o|solution|soln|buffer|salt|reagent|grade|pure|purified|tribasic|"
    r"dibasic|monobasic|basic|acid free|free acid|sodium free)\b", re.I)

_CLASS_HINTS = [
    (r"peg|polyethylene glycol|polyglycol|jeffamine|polypropylene glycol", "peg"),
    (r"tris|hepes|mes|mops|bis-?tris|imidazole|cacodylate|citrate|acetate buffer|"
     r"phosphate buffer|bicine|tricine|taps|tapso|caps|ches|adа|pipes|hepps|glycine|"
     r"succinate|maleate|malonate|borate|barbital|epps", "buffer"),
    (r"chloride|sulfate|sulphate|nitrate|phosphate|acetate|formate|citrate|tartrate|"
     r"malate|malonate|thiocyanate|bromide|iodide|fluoride|carbonate|oxalate", "salt"),
    (r"glycerol|ethylene glycol|propanediol|butanediol|pentanediol|mpd|hexanediol|"
     r"erythritol|xylitol|sorbitol|sucrose|trehalose|glucose|mannitol|inositol", "polyol"),
    (r"ethanol|methanol|propanol|isopropanol|acetonitrile|dioxane|dmso|dmf|acetone|"
     r"hexanediol|butanol|tert-butanol|phenol|trifluoroethanol", "organic"),
    (r"triton|tween|chaps|lda[ou]|octyl|glucoside|maltoside|nonidet|brij|"
     r"lauryl|dodecyl|detergent|c8e|c12e|dm\b|ddm\b|og\b", "detergent"),
]


def normalise_for_match(name: str) -> str:
    """Chemistry-aware normalisation for string comparison only, never for storage."""
    text = (name or "").lower()
    text = _NOISE.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def expand_formula(name: str) -> Optional[str]:
    """`bacl2` -> "barium chloride". None when the string is not a simple binary formula."""
    stripped = re.sub(r"[\s·]+", "", (name or "").strip())
    match = _FORMULA.match(stripped)
    if not match:
        return None
    cation, _, anion, _ = match.groups()
    return f"{_CATIONS[cation.lower()]} {_ANIONS[anion.lower()]}"


# --- guards on the fuzzy matcher ----------------------------------------------------------
#
# Fuzzy string similarity is chemically illiterate, and a wrong recommendation is worse than none
# because these dropdowns are pre-selected and can be accepted in bulk. Unguarded, the matcher
# proposed `peg 3400` -> PEG_400 (an eightfold molecular weight error), `1,3-propanediol` ->
# PROPANEDIOL_12 (the wrong isomer), `strontium chloride` -> SODIUM_CHLORIDE (the wrong cation),
# and `sodium maleate` -> MALIC_ACID (a different compound). Each guard below exists because the
# matcher actually made that mistake.

# Numbers in a reagent name are never decoration: they are molecular weight (PEG 400 against PEG
# 3350) or substitution position (1,2- against 1,3-propanediol). Two names may only match if their
# numbers are identical.
_DIGITS = re.compile(r"\d+")

# Words that identify which element or acid is present. If both names carry one of these from the
# same family and they differ, the names are different chemicals however similar they look.
_CATION_WORDS = {"sodium", "disodium", "potassium", "dipotassium", "lithium", "caesium", "cesium",
                 "rubidium", "ammonium", "diammonium", "magnesium", "calcium", "barium",
                 "strontium", "zinc", "manganese", "nickel", "cobalt", "copper", "cadmium",
                 "iron", "aluminium", "aluminum"}
_ANION_WORDS = {"chloride", "bromide", "iodide", "fluoride", "sulfate", "sulphate", "nitrate",
                "phosphate", "acetate", "formate", "citrate", "tartrate", "malate", "maleate",
                "malonate", "succinate", "thiocyanate", "carbonate", "oxalate", "cacodylate",
                "borate", "glutamate", "aspartate", "benzoate", "lactate", "propionate"}

# Names that state a chemical family without saying which member. The depositor genuinely did not
# specify, so picking a member is invention, not resolution.
_AMBIGUOUS = {
    "peg", "polyethylene glycol", "polyethyleneglycol", "poly ethylene glycol",
    "phosphate", "citrate", "acetate", "sulfate", "sulphate", "tartrate", "formate",
    "citrate buffer", "phosphate buffer", "acetate buffer", "tris buffer", "buffer",
    "propanediol", "butanediol", "pentanediol", "hexanediol", "glycol",
    "alcohol", "salt", "detergent", "polymer", "precipitant", "cryoprotectant",
    "amino acid", "sugar", "polyol", "organic", "chloride", "nitrate",
}


def _numbers(text: str) -> list[str]:
    return _DIGITS.findall(text or "")


def _words_from(text: str, vocabulary: set[str]) -> set[str]:
    return {w for w in normalise_for_match(text).split() if w in vocabulary}


def match_is_safe(name: str, candidate_key: str) -> bool:
    """Whether a fuzzy match between two names is chemically permissible."""
    if _numbers(name) != _numbers(candidate_key):
        return False
    for vocabulary in (_CATION_WORDS, _ANION_WORDS):
        left, right = _words_from(name, vocabulary), _words_from(candidate_key, vocabulary)
        if left and right and left != right:
            return False
    return True


def is_ambiguous(name: str) -> bool:
    """A family name with no member specified. Recommending a specific entry would invent data."""
    return normalise_for_match(name) in {normalise_for_match(a) for a in _AMBIGUOUS}


def guess_class(name: str) -> Optional[str]:
    lowered = (name or "").lower()
    for pattern, chem_class in _CLASS_HINTS:
        if re.search(pattern, lowered):
            return chem_class
    return None


def build_index(reagents: list[dict[str, Any]]) -> dict[str, str]:
    """Normalised name or alias -> canonical id."""
    index: dict[str, str] = {}
    for reagent in reagents:
        for candidate in ([reagent["canonical_id"], reagent.get("display_name", "")]
                          + list(reagent.get("aliases") or [])):
            key = normalise_for_match(str(candidate).replace("_", " "))
            if key:
                index.setdefault(key, reagent["canonical_id"])
    return index


def propose(name: str, index: dict[str, str], keys: list[str]) -> tuple[list[dict], str]:
    """Options for one unresolved string, with exactly one marked recommended."""
    options: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(canonical: str, label: str, recommended: bool = False) -> None:
        if canonical in seen:
            return
        seen.add(canonical)
        options.append({"value": canonical, "label": label, "recommended": recommended})

    basis = "none"
    expanded = expand_formula(name)
    direct = index.get(normalise_for_match(name))
    via_formula = index.get(normalise_for_match(expanded)) if expanded else None

    if is_ambiguous(name):
        # "peg", "phosphate", "propanediol": a family with no member named. Any specific entry
        # would be invented, so nothing is recommended and the honest option leads.
        options.append({"value": "__LEAVE__",
                        "label": "Ambiguous as written, leave unresolved",
                        "recommended": True})
        basis = "ambiguous"
    elif direct:
        add(direct, f"{direct} (name matches after normalisation)", True)
        basis = "normalised_match"
    elif via_formula:
        add(via_formula, f"{via_formula} (formula reads as {expanded})", True)
        basis = "formula"
    else:
        # Fuzzy, on the normalised forms, and only among candidates that survive the chemistry
        # guards. Cutoff deliberately high: a wrong recommendation accepted in bulk is worse than
        # no recommendation at all.
        close = [c for c in difflib.get_close_matches(normalise_for_match(name), keys,
                                                      n=8, cutoff=0.82)
                 if match_is_safe(name, c)]
        if close:
            add(index[close[0]], f"{index[close[0]]} (closest existing entry)", True)
            basis = "fuzzy"
            for other in close[1:3]:
                add(index[other], f"{index[other]} (also similar)")
        else:
            chem_class = guess_class(expanded or name)
            options.append({
                "value": "__NEW__",
                "label": f"Add as a new reagent"
                         + (f", class {chem_class}" if chem_class else " (class needed)")
                         + (f" [{expanded}]" if expanded else ""),
                "recommended": True,
            })
            basis = "new"

    # Candidates the guards refused are still offered, just never pre-selected. The guards exist
    # to stop a wrong answer being accepted in bulk, not to hide a right one: "methyl-2,4-
    # pentanediol" really is MPD, and the digit rule rejects it only because the deposition
    # dropped the leading "2-". Removing MPD from the dropdown would make the correct answer
    # unreachable, so it appears flagged instead.
    for other in difflib.get_close_matches(normalise_for_match(name), keys, n=6, cutoff=0.66):
        if match_is_safe(name, other):
            add(index[other], f"{index[other]} (weaker match)")
        else:
            reason = ("numbers differ, so molecular weight or isomer may differ"
                      if _numbers(name) != _numbers(other) else "different element or acid")
            add(index[other], f"{index[other]} (NOT recommended: {reason})")

    if basis != "new":
        options.append({"value": "__NEW__", "label": "Add as a new reagent instead",
                        "recommended": False})
    options.append({"value": "__NOT_A_REAGENT__",
                    "label": "Not a reagent (method text, screen name, unnamed ligand)",
                    "recommended": False})
    options.append({"value": "__LEAVE__", "label": "Leave unresolved rather than guess",
                    "recommended": False})
    return options, basis


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "lexicon_questions.json")
    parser.add_argument("--limit", type=int, default=40,
                        help="questions to ask; keep it to tens, not thousands")
    parser.add_argument("--min-count", type=int, default=40)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    data = yaml.safe_load((config.ONTOLOGY_DIR / "synonyms.yaml").read_text())
    reagents = data["reagents"]
    index = build_index(reagents)
    keys = list(index)

    comp = pl.read_parquet(args.components)
    unresolved = comp.filter(pl.col("name_canonical").is_null()
                             & (pl.col("role") != "not_a_component"))

    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for name, pdb_id, conc, unit in unresolved.select(
            "name_raw", "pdb_id", "concentration", "unit").iter_rows():
        key = (name or "").strip().lower()
        if not key or len(key) < 2:
            continue
        # Skip anything the non-component classifier would already have taken, so the expert is
        # not asked about text the pipeline can handle itself.
        if classify_non_component(key, has_quantity=conc is not None):
            continue
        counts[key] += 1
        if len(examples.setdefault(key, [])) < 3:
            amount = f"{conc:g} {unit}" if conc is not None and unit else "amount unstated"
            examples[key].append(f"{pdb_id}: {amount}")

    with Manifest(STAGE, params={"limit": args.limit, "min_count": args.min_count}) as m:
        m.add_input(args.components)

        questions: list[dict[str, Any]] = []
        basis_counts: Counter[str] = Counter()
        for name, count in counts.most_common():
            if len(questions) >= args.limit or count < args.min_count:
                break
            options, basis = propose(name, index, keys)
            basis_counts[basis] += 1
            recommended = next(o for o in options if o["recommended"])
            questions.append({
                "id": f"lexicon::{name}",
                "group": {"normalised_match": "Names that already exist, spelled differently",
                          "formula": "Chemical formulae the lexicon reads as names",
                          "fuzzy": "Close to an existing entry, needs your call",
                          "ambiguous": "Too vague to resolve, unless you disagree",
                          "new": "Not in the lexicon at all"}[basis],
                "question": f"“{name}” is unresolved in {count:,} components. "
                            f"What is it?",
                "why": f"Recommendation from {basis.replace('_', ' ')}: "
                       f"{recommended['label']}.",
                "weight": count,
                "weight_label": f"{count:,} components",
                "type": "choice",
                "options": options,
                "allow_text": True,
                "context": examples.get(name, []),
            })

        covered = sum(q["weight"] for q in questions)
        payload = {
            "schema_version": config.SCHEMA_VERSION,
            "lexicon_version": data.get("version", "unknown"),
            "title": "Lexicon gaps",
            "intro": (f"{len(questions)} questions covering {covered:,} unresolved components. "
                      f"Each one stands for every occurrence of that name, so these are the "
                      f"decisions with the most reach. Every dropdown is pre-set to a "
                      f"recommendation; change the ones that are wrong."),
            "n_questions": len(questions),
            "totals": {
                "n_unresolved_components": unresolved.height,
                "n_distinct_strings": len(counts),
                "n_components_covered": covered,
                "pct_of_unresolved_covered": round(100 * covered / max(1, unresolved.height), 2),
                "recommendation_basis": dict(basis_counts),
            },
            "questions": questions,
        }
        args.out.write_text(json.dumps(payload, indent=2) + "\n")
        m.add_output(args.out).note(**payload["totals"], n_questions=len(questions))

        print(f"\n  {len(questions)} questions covering {covered:,} components "
              f"({payload['totals']['pct_of_unresolved_covered']}% of the unresolved mass)")
        print(f"  recommendation basis: {dict(basis_counts)}")
        print(f"\n  {args.out}")
        print(f"  open app/condition_courtroom_v5.html and drop the file on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
