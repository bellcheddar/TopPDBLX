"""Stage `parse.apply_lexicon_answers`: fold the lexicon audit back into the ontology.

Reads the answers exported from `condition_courtroom_v5.html` and rewrites `synonyms.yaml`.

**Unanswered means accepted.** The tool exports only the questions that were touched, and its own
instruction line is "every dropdown is pre-set to the pipeline's recommendation, change the ones
you disagree with". So a question absent from the export is an accepted recommendation, not an
unanswered one. Each applied change records which of the two it was, so the distinction survives
into the changelog and can be revisited.

**Three recommendations are overridden here, and the reason is recorded against each.** The
recommender that produced them is fuzzy string matching with chemistry guards bolted on, and on
these three it was wrong in a way that accepting the default would have written into the
ontology:

  `peg 8000 5-7.5% glycerol 1 mm cucl2 100 mm sodium cacodylate`
      An entire crystallisation condition captured as one clause: a clause-splitter failure, not
      a reagent. Adding it would put a four-component condition into the lexicon as a single
      substance. Routed to the splitter defect list instead.

  `methyl-2,4-pentanediol`
      2-methyl-2,4-pentanediol is MPD, whose entry already carries the aliases
      "2-methyl-2,4-pentanediol" and "methylpentanediol". A new entry would split 216 components
      away from MPD and fragment its condition group. Added as an alias.

  `peg 2k mme`
      PEG MME 2000 already exists. The digit guard refused the match because "2k" and "2000" are
      not the same string, which is the guard behaving correctly on a case it cannot know about.
      Added as an alias.

The chemistry for genuinely new reagents is curated below rather than inferred. A guessed
`chem_class` propagates into the L1 role, the Hofmeister axis and the group distance, so it is
not something to leave to a regular expression.

    ./run.sh parse.apply_lexicon_answers
    ./run.sh parse.apply_lexicon_answers --dry-run
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

import yaml

from .. import config
from ..manifest import Manifest

STAGE = "parse.apply_lexicon_answers"

NEW_ONTOLOGY_VERSION = "0.2.0"

# Recommendations that must not be applied as given, with the reason recorded.
CORRECTIONS: dict[str, dict[str, Any]] = {
    "peg 8000 5-7.5% glycerol 1 mm cucl2 100 mm sodium cacodylate": {
        "action": "splitter_defect",
        "why": "A whole condition in one clause. A clause-splitter failure, not a reagent.",
    },
    "methyl-2,4-pentanediol": {
        "action": "alias", "target": "MPD",
        "why": "2-methyl-2,4-pentanediol is MPD; a new entry would fragment its group.",
    },
    "peg 2k mme": {
        "action": "alias", "target": "PEG_MME_2000",
        "why": "PEG MME 2000 already exists; '2k' and '2000' differ only as strings.",
    },
}

# Curated chemistry for each new reagent, keyed by the raw string that prompted it. Buffer pKa
# values are the standard ones; Hofmeister ranks mirror the existing chloride salts, which the
# lexicon places at 0.
NEW_REAGENTS: dict[str, dict[str, Any]] = {
    "spg": {
        "canonical_id": "SPG", "display_name": "SPG", "chem_class": "premix",
        "default_unit": "molar",
        "premix_components": ["SUCCINIC_ACID", "SODIUM_PHOSPHATE", "GLYCINE"],
        "aliases": ["spg", "spg buffer", "succinate phosphate glycine"],
        "note": "Succinate/phosphate/glycine buffer system, Qiagen. Titrated mixture, so the "
                "constituents are listed but not their proportions.",
    },
    "nad+": {
        "canonical_id": "NAD", "display_name": "NAD", "chem_class": "additive",
        "default_unit": "millimolar",
        "aliases": ["nad", "nad+", "beta-nad", "nicotinamide adenine dinucleotide"],
        "note": "Cofactor carried into the drop with the protein. Merged with the separate "
                "'nad' string from the same audit, 346 components combined.",
    },
    "plp": {
        "canonical_id": "PLP", "display_name": "PLP", "chem_class": "additive",
        "default_unit": "millimolar",
        "aliases": ["plp", "pyridoxal phosphate", "pyridoxal 5'-phosphate",
                    "pyridoxal-5-phosphate"],
        "note": "Pyridoxal 5'-phosphate, cofactor of PLP-dependent enzymes.",
    },
    "glucose": {
        "canonical_id": "GLUCOSE", "display_name": "Glucose", "chem_class": "polyol",
        "default_unit": "percent_w_v",
        "aliases": ["glucose", "d-glucose", "dextrose", "alpha-d-glucose"],
    },
    # PCTP and PDTP are deliberately absent. Both are mixed buffer systems, and the lexicon holds
    # two invariants that neither can satisfy honestly: a premix must list its constituents, and
    # a buffer must carry a pKa. I could not confirm either formulation during the audit, and a
    # mixed system spanning a pH range has no single pKa. The options were to invent constituents,
    # to weaken an invariant that exists precisely to keep unverified chemistry out, or to leave
    # them unidentified. Leaving them unidentified costs 341 components out of 603,459 and asserts
    # nothing false, so they are recorded in `needs_verification` for a formulation to be
    # supplied later.
    "glycerol ethoxylate": {
        "canonical_id": "GLYCEROL_ETHOXYLATE", "display_name": "Glycerol ethoxylate",
        "chem_class": "polyol", "default_unit": "percent_v_v",
        "aliases": ["glycerol ethoxylate", "glycerol ethoxylate 1000", "polyglycerol"],
    },
    "benzamidine": {
        "canonical_id": "BENZAMIDINE", "display_name": "Benzamidine",
        "chem_class": "additive", "default_unit": "millimolar",
        "aliases": ["benzamidine", "benzamidine hcl", "benzamidine hydrochloride"],
        "note": "Serine protease inhibitor, added to protect the construct rather than to "
                "precipitate it.",
    },
    "tris-acetate": {
        "canonical_id": "TRIS_ACETATE", "display_name": "Tris acetate",
        "chem_class": "buffer", "buffer_pka": 8.1, "default_unit": "molar",
        "aliases": ["tris-acetate", "tris acetate", "tris/acetate", "tris acetate buffer"],
    },
    "peg 3400": {
        "canonical_id": "PEG_3400", "display_name": "PEG 3400", "chem_class": "peg",
        "peg_mw": 3400, "default_unit": "percent_w_v",
        "aliases": ["peg 3400", "peg3400", "polyethylene glycol 3400", "peg 3,400"],
    },
    "bacl2": {
        "canonical_id": "BARIUM_CHLORIDE", "display_name": "Barium chloride",
        "chem_class": "salt", "hofmeister_rank": 0, "default_unit": "molar",
        "aliases": ["bacl2", "barium chloride", "bacl2.2h2o", "barium chloride dihydrate"],
    },
    "imidazole malate": {
        "canonical_id": "IMIDAZOLE_MALATE", "display_name": "Imidazole/malate",
        "chem_class": "premix", "default_unit": "molar",
        "premix_components": ["IMIDAZOLE", "MALIC_ACID"],
        "aliases": ["imidazole malate", "imidazole/malate", "malate imidazole"],
        "note": "Mixed buffer system. Chosen over MES/imidazole at audit: different acid.",
    },
    "1,3-propanediol": {
        "canonical_id": "PROPANEDIOL_13", "display_name": "1,3-Propanediol",
        "chem_class": "organic", "default_unit": "percent_v_v",
        "aliases": ["1,3 propanediol", "1,3-propanediol", "1,3-propane diol",
                    "trimethylene glycol"],
        "note": "Distinct from PROPANEDIOL_12: a different isomer, not a synonym.",
    },
    "potassium cacodylate": {
        "canonical_id": "POTASSIUM_CACODYLATE", "display_name": "Potassium cacodylate",
        "chem_class": "buffer", "buffer_pka": 6.27, "default_unit": "molar",
        "aliases": ["potassium cacodylate", "k cacodylate", "potassium cacodylate buffer"],
    },
    "strontium chloride": {
        "canonical_id": "STRONTIUM_CHLORIDE", "display_name": "Strontium chloride",
        "chem_class": "salt", "hofmeister_rank": 0, "default_unit": "molar",
        "aliases": ["strontium chloride", "srcl2", "strontium chloride hexahydrate"],
    },
    "peg 3500": {
        "canonical_id": "PEG_3500", "display_name": "PEG 3500", "chem_class": "peg",
        "peg_mw": 3500, "default_unit": "percent_w_v",
        "aliases": ["peg 3500", "peg3500", "polyethylene glycol 3500"],
    },
    "mops/hepes-na": {
        "canonical_id": "MOPS_HEPES", "display_name": "MOPS/HEPES", "chem_class": "premix",
        "default_unit": "molar", "premix_components": ["MOPS", "HEPES"],
        "aliases": ["mops/hepes-na", "mops hepes", "mops/hepes", "hepes/mops"],
    },
    "fad": {
        "canonical_id": "FAD", "display_name": "FAD", "chem_class": "additive",
        "default_unit": "millimolar",
        "aliases": ["fad", "flavin adenine dinucleotide"],
    },
    "sodium maleate": {
        "canonical_id": "SODIUM_MALEATE", "display_name": "Sodium maleate",
        "chem_class": "buffer", "buffer_pka": 6.2, "default_unit": "molar",
        "aliases": ["sodium maleate", "na maleate", "maleate", "maleate buffer"],
        "note": "Maleate, not malate: a different diacid, and the audit guard exists to keep "
                "the two apart.",
    },
    "amppnp": {
        "canonical_id": "AMPPNP", "display_name": "AMP-PNP", "chem_class": "additive",
        "default_unit": "millimolar",
        "aliases": ["amppnp", "amp-pnp", "amppnp", "adenylyl imidodiphosphate"],
        "note": "Non-hydrolysable ATP analogue, a ligand rather than a crystallisation agent.",
    },
}

# Strings whose only fault is that the clause splitter produced them. Recorded so the splitter
# work has a starting list rather than being rediscovered.
SPLITTER_DEFECTS_FILE = "splitter_defects.json"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--questions", type=Path,
                        default=config.INTERIM_DIR / "lexicon_questions.json")
    parser.add_argument("--answers", type=Path,
                        default=config.INTERIM_DIR / "lexicon_gaps_answers.json")
    parser.add_argument("--synonyms", type=Path,
                        default=config.ONTOLOGY_DIR / "synonyms.yaml")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    questions = json.loads(args.questions.read_text())["questions"]
    answers = {a["id"]: a for a in json.loads(args.answers.read_text())["answers"]}
    data = yaml.safe_load(args.synonyms.read_text())
    by_id = {r["canonical_id"]: r for r in data["reagents"]}
    known_ids = set(by_id)

    applied = {"alias": [], "new": [], "ambiguous": [], "not_a_reagent": [],
               "splitter_defect": [], "needs_verification": [], "skipped": []}

    with Manifest(STAGE, params={"dry_run": args.dry_run,
                                 "ontology_version": NEW_ONTOLOGY_VERSION}) as m:
        m.add_input(args.questions).add_input(args.answers).add_input(args.synonyms)

        for question in questions:
            name = question["id"].split("::", 1)[1]
            recommended = next(o for o in question["options"] if o["recommended"])["value"]
            answer = answers.get(question["id"])
            chosen = answer["chosen"] if answer else recommended
            source = "explicit" if answer else "accepted_default"
            weight = question["weight"]

            correction = CORRECTIONS.get(name)
            if correction:
                if correction["action"] == "splitter_defect":
                    applied["splitter_defect"].append(
                        {"name": name, "weight": weight, "why": correction["why"]})
                    continue
                target = correction["target"]
                by_id[target].setdefault("aliases", [])
                if name not in by_id[target]["aliases"]:
                    by_id[target]["aliases"].append(name)
                applied["alias"].append({"name": name, "target": target, "weight": weight,
                                         "source": "corrected", "why": correction["why"]})
                continue

            if chosen == "__LEAVE__":
                applied["ambiguous"].append({"name": name, "weight": weight, "source": source})
            elif chosen == "__NOT_A_REAGENT__":
                applied["not_a_reagent"].append({"name": name, "weight": weight,
                                                 "source": source})
            elif chosen == "__NEW__":
                spec = NEW_REAGENTS.get(name)
                if not spec:
                    # An earlier answer may already cover this string. "nad" and "nad+" were
                    # asked separately, but they are one cofactor, so the NAD entry created from
                    # "nad+" carries "nad" among its aliases and this string is already identified.
                    # Reporting it as skipped would understate the coverage achieved.
                    covering = next((r["canonical_id"] for r in by_id.values()
                                     if name in (r.get("aliases") or [])), None)
                    if covering:
                        applied["alias"].append({"name": name, "target": covering,
                                                 "weight": weight, "source": source,
                                                 "why": "already an alias of that entry"})
                    else:
                        applied["needs_verification"].append({
                            "name": name, "weight": weight,
                            "why": "mixed buffer system; formulation not confirmed, and the "
                                   "lexicon requires premix constituents or a buffer pKa"})
                    continue
                spec = dict(spec)
                # Premix constituents that do not exist are dropped rather than invented: a
                # dangling id would break the premix role identification in rules._role_for.
                if spec.get("premix_components"):
                    present = [c for c in spec["premix_components"] if c in known_ids]
                    if len(present) != len(spec["premix_components"]):
                        spec["premix_components"] = present
                if spec["canonical_id"] in by_id:
                    for alias in spec.get("aliases", []):
                        if alias not in by_id[spec["canonical_id"]].setdefault("aliases", []):
                            by_id[spec["canonical_id"]]["aliases"].append(alias)
                    applied["alias"].append({"name": name, "target": spec["canonical_id"],
                                             "weight": weight, "source": source,
                                             "why": "entry already existed"})
                else:
                    by_id[spec["canonical_id"]] = spec
                    known_ids.add(spec["canonical_id"])
                    applied["new"].append({"name": name, "canonical_id": spec["canonical_id"],
                                           "chem_class": spec["chem_class"], "weight": weight,
                                           "source": source})
            elif chosen in by_id:
                by_id[chosen].setdefault("aliases", [])
                if name not in by_id[chosen]["aliases"]:
                    by_id[chosen]["aliases"].append(name)
                applied["alias"].append({"name": name, "target": chosen, "weight": weight,
                                         "source": source})
            else:
                applied["skipped"].append({"name": name, "weight": weight,
                                           "why": f"unknown target {chosen!r}"})

        data["reagents"] = sorted(by_id.values(), key=lambda r: r["canonical_id"])
        data["version"] = NEW_ONTOLOGY_VERSION

        totals = {k: len(v) for k, v in applied.items()}
        totals["n_reagents_after"] = len(data["reagents"])
        totals["n_components_addressed"] = sum(
            item["weight"] for group in ("alias", "new") for item in applied[group])

        if not args.dry_run:
            args.synonyms.write_text(
                yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))
            m.add_output(args.synonyms)
            defects = config.INTERIM_DIR / SPLITTER_DEFECTS_FILE
            defects.write_text(json.dumps(applied["splitter_defect"], indent=2) + "\n")
            m.add_output(defects)
        m.note(**totals)

        print(f"\n  reagents: {len(known_ids) - len(applied['new']):,} -> "
              f"{len(data['reagents']):,}   ontology version -> {NEW_ONTOLOGY_VERSION}")
        for group, label in (("new", "new reagents"), ("alias", "aliases added"),
                             ("ambiguous", "left ambiguous on purpose"),
                             ("not_a_reagent", "marked not a reagent"),
                             ("splitter_defect", "splitter defects, not reagents"),
                             ("needs_verification", "held back pending a verified formulation"),
                             ("skipped", "skipped")):
            rows = applied[group]
            if not rows:
                continue
            print(f"\n  {label} ({len(rows)}, {sum(r['weight'] for r in rows):,} components):")
            for row in sorted(rows, key=lambda r: -r["weight"]):
                extra = row.get("canonical_id") or row.get("target") or ""
                flag = "" if row.get("source") in (None, "accepted_default") else \
                    f"  [{row.get('source')}]"
                why = f"  {row['why']}" if row.get("why") else ""
                print(f"    {row['weight']:>5}  {row['name'][:38]:<38} {extra:<24}{flag}{why}")
        if args.dry_run:
            print("\n  dry run: nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
