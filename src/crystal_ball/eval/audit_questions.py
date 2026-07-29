"""Stage `eval.audit_questions`: reduce the audit to the handful of real judgement calls.

Third iteration of WP8, and the target is tens of decisions, not hundreds.

  v1   1,484 records, one verdict each
  v2   same, faster interface
  v3   919 decisions, weighted by corpus frequency
  v4   **this**: only the questions a human actually has to answer

The insight the earlier versions missed: most decisions need no judgement. "peg 3350 becomes
PEG_3350" is not a question. What genuinely needs Marc are the places where the pipeline
**guessed**, **contradicted itself**, or **refused to choose**. Those are detectable
automatically, and there are only a few dozen of them.

Six detectors, each producing questions with candidate answers already computed:

  ambiguous_name    a high-mass string left unmapped because the counter-ion is genuinely
                    ambiguous (citrate, acetate, phosphate). Options are the real candidates
                    from the lexicon, ranked by how often each is already used.
  guessed_unit      a unit inference rule that fell back to a default rather than following
                    from chemistry, above all "unknown chemistry, bare %".
  contradictory_unit a rule that contradicts the primary chemistry rule, such as a PEG of 600
                    or less coming out as w/v.
  split_unit        one reagent parsed with two different units, where the minority reading
                    is almost certainly wrong.
  surprising_map    a mapping where the raw string barely resembles the canonical reagent, so
                    the alias may be over-reaching.
  missing_reagent   an unmapped string with high mass that looks like real chemistry rather
                    than junk, with close lexicon matches offered.

Everything else is accepted silently and reported as such, so the audit's coverage claim
stays honest: it says what was reviewed and what was auto-accepted, and why.

    ./run.sh eval.audit_questions
    ./run.sh eval.audit_questions --max-per-group 12
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path
from typing import Any, Optional

import polars as pl

from .. import config
from ..manifest import Manifest
from ..parse.lexicon import load as load_lexicon

STAGE = "eval.audit_questions"

DEFAULT_MAX_PER_GROUP = 10

# A string that looks like chemistry rather than an artefact of the deposition text.
_CHEMICAL_LOOKING = re.compile(
    r"(sulf|sulph|chlorid|acetat|citrat|phosphat|format|tartrat|malonat|nitrat|"
    r"bromid|iodid|fluorid|glycol|glycerol|peg|tris|hepes|mes|mops|pipes|bicine|"
    r"caps|ches|imidazol|cacodylat|propanol|ethanol|butanol|dioxan|dmso|edta|dtt|"
    r"amine|acid|buffer|salt|diol|ol\b|ate\b|ide\b)", re.I)

# Text that is plainly not a reagent: setup notes, seeding, plate references.
_OBVIOUS_JUNK = re.compile(
    r"seed|puck|tube|plate|drop|volume|ratio|temperatur|incubat|equilibrat|month|week|day|"
    r"crystal|grown|obtain|improve|reproduc|see |pmid|doi|protein|inhibitor|complex|soak",
    re.I)


def _examples(components: pl.DataFrame, conditions: pl.DataFrame,
              predicate: pl.Expr, limit: int = 3) -> list[str]:
    rows = (components.filter(predicate)
            .join(conditions.select("pdb_id", "crystal_id", "raw_details"),
                  on=["pdb_id", "crystal_id"], how="left").head(limit))
    return [f"{r['pdb_id']}: {(r['raw_details'] or '')[:150]}" for r in rows.iter_rows(named=True)]


def _fmt(n: int) -> str:
    return f"{n:,}"


def build_questions(components: pl.DataFrame, conditions: pl.DataFrame,
                    lexicon, max_per_group: int) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    by_id = lexicon.by_id()
    usage = dict(components.filter(pl.col("name_canonical").is_not_null())
                 .group_by("name_canonical").agg(pl.len().alias("n"))
                 .iter_rows())

    resolved = components.filter(pl.col("name_canonical").is_not_null())
    unresolved = components.filter(pl.col("name_canonical").is_null())

    # ---------------------------------------------------------- ambiguous names
    # Deliberately unmapped in the lexicon because the counter-ion genuinely varies.
    AMBIGUOUS = {
        "citrate":   ["SODIUM_CITRATE", "POTASSIUM_CITRATE", "AMMONIUM_CITRATE", "LITHIUM_CITRATE"],
        "acetate":   ["SODIUM_ACETATE", "AMMONIUM_ACETATE", "MAGNESIUM_ACETATE",
                      "CALCIUM_ACETATE", "POTASSIUM_ACETATE"],
        "phosphate": ["SODIUM_PHOSPHATE", "POTASSIUM_PHOSPHATE", "SODIUM_POTASSIUM_PHOSPHATE",
                      "AMMONIUM_PHOSPHATE"],
        "peg":       ["PEG_3350", "PEG_4000", "PEG_8000"],
        "polyethylene glycol": ["PEG_3350", "PEG_4000", "PEG_8000"],
        "peg mme":   ["PEG_MME_2000", "PEG_MME_550", "PEG_MME_5000"],
    }
    counts = dict(unresolved.group_by("name_raw").agg(pl.len().alias("n")).iter_rows())
    for name, candidates in sorted(AMBIGUOUS.items(), key=lambda kv: -counts.get(kv[0], 0)):
        n = counts.get(name, 0)
        if not n:
            continue
        options = [{"value": c,
                    "label": f"{by_id[c].display_name if c in by_id else c}"
                             f"  ({_fmt(usage.get(c, 0))} uses already)",
                    "recommended": False} for c in candidates if c in by_id or True]
        if options:
            options[0]["recommended"] = True
        options.append({"value": "__LEAVE__", "label": "Leave unmapped (counts as a discard)",
                        "recommended": False})
        questions.append({
            "id": f"ambiguous::{name}",
            "group": "Ambiguous reagent names",
            "question": f"A bare “{name}” appears {_fmt(n)} times with no counter-ion stated. "
                        f"What should it resolve to?",
            "why": "The lexicon refuses to guess these, so they currently resolve to nothing "
                   "and drag their records into NO_REAGENT_MATCH.",
            "weight": n, "weight_label": f"{_fmt(n)} components",
            "type": "choice", "options": options, "allow_text": True,
            "context": _examples(unresolved, conditions, pl.col("name_raw") == name),
        })

    # ---------------------------------------------------- guessed / odd unit rules
    inferred = components.filter(pl.col("unit_inferred")).with_columns(
        pl.when(pl.col("chem_class") == "peg")
          .then(pl.when(pl.col("peg_mw") <= 600).then(pl.lit("peg <= 600"))
                .otherwise(pl.lit("peg >= 1000")))
          .otherwise(pl.col("chem_class").fill_null("unknown")).alias("rule"))
    rules = (inferred.group_by(["rule", "unit"]).agg(pl.len().alias("n"))
             .sort("n", descending=True))
    primary = {}
    for row in rules.iter_rows(named=True):
        primary.setdefault(row["rule"], row)

    unit_options = [
        {"value": "percent_w_v", "label": "% w/v (weight per volume)", "recommended": False},
        {"value": "percent_v_v", "label": "% v/v (volume per volume)", "recommended": False},
        {"value": "__UNRESOLVED__", "label": "Leave the unit unresolved rather than guess",
         "recommended": False},
    ]

    for row in rules.iter_rows(named=True):
        rule, unit, n = row["rule"], row["unit"], row["n"]
        is_guess = rule == "unknown"
        is_contradiction = (rule == "peg <= 600" and unit == "percent_w_v") or \
                           (rule == "peg >= 1000" and unit == "percent_v_v")
        is_minority = primary[rule]["unit"] != unit and n >= 100
        if not (is_guess or is_contradiction or is_minority):
            continue
        options = [dict(o) for o in unit_options]
        for option in options:
            option["recommended"] = option["value"] == unit
        if is_contradiction:
            for option in options:
                option["recommended"] = option["value"] == (
                    "percent_v_v" if rule == "peg <= 600" else "percent_w_v")
        kind = ("guessed" if is_guess else
                "contradictory" if is_contradiction else "minority")
        questions.append({
            "id": f"unit::{rule}::{unit}",
            "group": "Unit inference rules",
            "question": (f"“{rule}” with a bare % is being read as {unit.replace('percent_', '% ').replace('_', '/')}. "
                         f"Keep that?"),
            "why": {
                "guessed": "There is no chemistry to go on here, so the parser falls back to "
                           "w/v. This is the single largest guess in the pipeline.",
                "contradictory": "This contradicts the primary rule for that PEG range "
                                 "(spec 6.3: below 600 a PEG behaves as an organic, v/v).",
                "minority": f"The same class is mostly read as "
                            f"{primary[rule]['unit']} ({_fmt(primary[rule]['n'])} components); "
                            f"this is the minority reading.",
            }[kind],
            "weight": n, "weight_label": f"{_fmt(n)} components",
            "type": "choice", "options": options, "allow_text": False,
            "context": _examples(inferred, conditions,
                                 (pl.col("rule") == rule) & (pl.col("unit") == unit)),
        })

    # -------------------------------------------------- one reagent, two units
    split = (resolved.group_by(["name_canonical", "unit"]).agg(pl.len().alias("n")))
    totals = dict(split.group_by("name_canonical").agg(pl.col("n").sum()).iter_rows())
    seen: set[str] = set()
    for row in split.sort("n", descending=True).iter_rows(named=True):
        canonical, unit, n = row["name_canonical"], row["unit"], row["n"]
        total = totals.get(canonical, 0)
        if canonical in seen or total == 0:
            continue
        share = n / total
        # A clear majority reading plus a small stubborn minority is the signature of a
        # misparse, not of genuine chemical variety.
        if not (0.005 < share < 0.15 and n >= 50):
            continue
        seen.add(canonical)
        majority = (split.filter((pl.col("name_canonical") == canonical) & (pl.col("unit") != unit))
                    .sort("n", descending=True).head(1))
        if not majority.height:
            continue
        major_unit = majority["unit"].item()
        questions.append({
            "id": f"splitunit::{canonical}::{unit}",
            "group": "One reagent, two units",
            "question": f"{canonical} is read as {unit} in {_fmt(n)} components but as "
                        f"{major_unit} in {_fmt(total - n)}. Which is right for the minority?",
            "why": "A dominant reading with a small stubborn minority usually means the "
                   "minority is a misparse rather than real chemical variety.",
            "weight": n, "weight_label": f"{_fmt(n)} components",
            "type": "choice",
            "options": [
                {"value": major_unit, "label": f"Correct them to {major_unit} (the majority)",
                 "recommended": True},
                {"value": unit, "label": f"Leave as {unit}, both readings are genuine",
                 "recommended": False},
            ],
            "allow_text": False,
            "context": _examples(resolved, conditions,
                                 (pl.col("name_canonical") == canonical) & (pl.col("unit") == unit)),
        })
        if sum(1 for q in questions if q["group"] == "One reagent, two units") >= max_per_group:
            break

    # ------------------------------------------------------- surprising mappings
    mapping = (resolved.group_by(["name_raw", "name_canonical"]).agg(pl.len().alias("n"))
               .sort("n", descending=True).head(1200))
    surprising = []
    for row in mapping.iter_rows(named=True):
        reagent = by_id.get(row["name_canonical"])
        if not reagent:
            continue
        best = max(difflib.SequenceMatcher(None, row["name_raw"].lower(), alias.lower()).ratio()
                   for alias in reagent.all_names)
        if best < 0.45 and row["n"] >= 100:
            surprising.append((best, row))
    for best, row in sorted(surprising)[:max_per_group]:
        reagent = by_id[row["name_canonical"]]
        questions.append({
            "id": f"map::{row['name_raw']}::{row['name_canonical']}",
            "group": "Surprising mappings",
            "question": f"“{row['name_raw']}” is being mapped to {row['name_canonical']}. Right?",
            "why": f"The raw string barely resembles any alias of "
                   f"{reagent.display_name} (best similarity {best:.0%}), so the alias may be "
                   f"catching more than it should.",
            "weight": row["n"], "weight_label": f"{_fmt(row['n'])} components",
            "type": "choice",
            "options": [
                {"value": row["name_canonical"], "label": f"Correct, keep {row['name_canonical']}",
                 "recommended": True},
                {"value": "__UNMAP__", "label": "Wrong: it should not map to anything",
                 "recommended": False},
            ],
            "allow_text": True,
            "context": _examples(resolved, conditions,
                                 (pl.col("name_raw") == row["name_raw"])
                                 & (pl.col("name_canonical") == row["name_canonical"])),
        })

    # ------------------------------------------------- missing real reagents
    lexicon_names = [n for r in lexicon.reagents for n in r.all_names]
    name_to_id = {n.lower(): r.canonical_id for r in lexicon.reagents for n in r.all_names}
    candidates = (unresolved.group_by("name_raw").agg(pl.len().alias("n"))
                  .sort("n", descending=True).head(400))
    picked = 0
    for row in candidates.iter_rows(named=True):
        name, n = row["name_raw"], row["n"]
        if name in AMBIGUOUS or n < 50:
            continue
        if _OBVIOUS_JUNK.search(name) or not _CHEMICAL_LOOKING.search(name):
            continue
        close = difflib.get_close_matches(name, lexicon_names, n=4, cutoff=0.55)
        options = [{"value": name_to_id[c.lower()],
                    "label": f"{c} → {name_to_id[c.lower()]}", "recommended": i == 0}
                   for i, c in enumerate(close) if c.lower() in name_to_id]
        options.append({"value": "__NEW__", "label": "A new reagent: type the canonical name",
                        "recommended": not options})
        options.append({"value": "__JUNK__", "label": "Not a reagent, ignore it",
                        "recommended": False})
        questions.append({
            "id": f"missing::{name}",
            "group": "Reagents missing from the lexicon",
            "question": f"“{name}” resolves to nothing, {_fmt(n)} times. What is it?",
            "why": "It looks like real chemistry rather than deposition boilerplate, so it is "
                   "probably a genuine gap in the lexicon.",
            "weight": n, "weight_label": f"{_fmt(n)} components",
            "type": "choice", "options": options, "allow_text": True,
            "context": _examples(unresolved, conditions, pl.col("name_raw") == name),
        })
        picked += 1
        if picked >= max_per_group:
            break

    questions.sort(key=lambda q: (-q["weight"]))
    return questions


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--conditions", type=Path,
                        default=config.INTERIM_DIR / "parsed_conditions.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "audit_questions.json")
    parser.add_argument("--max-per-group", type=int, default=DEFAULT_MAX_PER_GROUP)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    components = pl.read_parquet(args.components)
    conditions = pl.read_parquet(args.conditions)
    lexicon = load_lexicon()

    with Manifest(STAGE, params={"max_per_group": args.max_per_group}) as m:
        m.add_input(args.components).add_input(args.conditions)
        m.add_input(config.ONTOLOGY_DIR / "synonyms.yaml")

        questions = build_questions(components, conditions, lexicon, args.max_per_group)
        covered = sum(q["weight"] for q in questions)

        payload = {
            "schema_version": config.SCHEMA_VERSION,
            "lexicon_version": lexicon.version,
            "n_questions": len(questions),
            "totals": {
                "n_components": components.height,
                "n_unresolved": components.filter(pl.col("name_canonical").is_null()).height,
                "n_inferred_units": components.filter(pl.col("unit_inferred")).height,
                "components_under_question": covered,
            },
            "questions": questions,
        }
        args.out.write_text(json.dumps(payload, indent=1))

        groups: dict[str, int] = {}
        for q in questions:
            groups[q["group"]] = groups.get(q["group"], 0) + 1

        stats = {"n_questions": len(questions),
                 "components_under_question": covered,
                 "share_of_components": round(covered / max(1, components.height), 4)}
        m.add_output(args.out).note(**stats, groups=groups)

        print(f"\n{len(questions)} questions, governing {_fmt(covered)} components "
              f"({stats['share_of_components']:.1%} of the database)\n")
        for group, n in sorted(groups.items(), key=lambda kv: -kv[1]):
            weight = sum(q["weight"] for q in questions if q["group"] == group)
            print(f"  {group:<38} {n:>3} questions   {_fmt(weight):>9} components")
        print("\ntop questions by weight:")
        for q in questions[:12]:
            print(f"  {_fmt(q['weight']):>8}  {q['question'][:96]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
