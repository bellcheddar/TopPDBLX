"""Stage `parse.apply_curation_queue`: fold the bucketed curation round into the lexicon.

Round 2 asked ten grouped questions instead of 120 individual ones, so an answer here applies to
every name in its bucket. That makes application the dangerous half: accepting "add all 82 PEG
sizes" is one click and 82 lexicon entries.

**The lexicon's invariants are not negotiable, and they bite hardest in bulk.** A `peg` entry must
carry a molecular weight and a `buffer` must carry a pKa; both are enforced on load, so a bucket
accepted wholesale can only be applied to the members that can satisfy them:

  *PEG*     the molecular weight is read from the name. "peg 1450" yields 1450 and is added;
            "peg smear medium" names no weight and is reported rather than invented. The default
            unit follows the same rule the lexicon already applies, v/v at or below 600 and w/v
            above, so a new entry cannot contradict the existing ones.
  *buffer*  a pKa cannot be derived from a string. Members that are really spellings of a buffer
            already present are added as aliases of it, which needs no pKa; the rest are reported
            for a value to be supplied, exactly as PCTP and PDTP were in round 1.

Everything skipped is listed with its reason and its corpus weight, so the cost of each refusal
is visible rather than silently absorbed.

    ./run.sh parse.apply_curation_queue --dry-run
    ./run.sh parse.apply_curation_queue
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from .. import config
from ..manifest import Manifest
from .lexicon_questions import normalise_for_match

STAGE = "parse.apply_curation_queue"

NEW_LEXICON_VERSION = "0.3.0"

# Units a new entry defaults to, by class. PEG is decided per entry from its molecular weight.
DEFAULT_UNIT = {
    "salt": "molar", "buffer": "molar", "additive": "millimolar",
    "polyol": "percent_v_v", "organic": "percent_v_v", "detergent": "percent_w_v",
}

_PEG_MW = re.compile(r"(\d{3,6})")
# The plausible range for a PEG sold as a precipitant. Bounding by range rather than by a list of
# catalogue grades: "peg 3550" and "peg 4600" are real and were skipped by a grade whitelist,
# while a range still rejects a stray percentage or a catalogue number.
_PEG_MW_RANGE = (200, 40000)


# Buffer pKa values at 25 degrees, for the entries that are genuinely new reagents rather than
# spellings of ones already present. Only buffers whose value is standard and unambiguous are
# listed: a pKa is a measured constant, and guessing one would put a false number into a field
# the ontology uses to collapse buffer identity to pH.
#
# Everything not in this table stays unidentified and is reported, which is the same treatment
# PCTP and PDTP received when their formulations could not be confirmed.
BUFFER_PKA = {
    "taps": 8.4,
    "epps": 8.0,
    "hepps": 8.0,          # HEPPS and EPPS are the same buffer under two names
    "mopso": 6.9,
    "homopipes": 4.55,     # homopiperazine-N,N'-bis(2-ethanesulfonic acid), first pKa
    "glycyl-glycyl-glycine": 8.1,
    "triglycine": 8.1,
}

# Decoration that says how a buffer was supplied or titrated, not which buffer it is. Stripped
# before matching a name against the lexicon, so "hepes ph 7.5", "mes/sodium hydroxide" and
# "tris base/hydrochloric acid" identify to the buffers already present instead of demanding a
# pKa each.
_BUFFER_DECORATION = re.compile(
    r"\b(ph\s*=?\s*\d+(\.\d+)?(\s*(to|-)\s*\d+(\.\d+)?)?|buffer|buffered|solution|"
    r"hcl|hydrochloric acid|naoh|koh|csoh|sodium hydroxide|potassium hydroxide|base|"
    r"h2so4|sulfate|sulphate|so4|oac|acetate|maleate|chloride|phosphate|puffer)\b", re.I)


def buffer_alias_target(name: str, by_normalised: dict, by_id: dict) -> Optional[str]:
    """The existing buffer a decorated name refers to, or None.

    Deliberately strict: the remainder after stripping decoration must be *exactly* an existing
    buffer. "cacl2 in a 30 mm hepes" contains HEPES but also calcium chloride, so aliasing it
    would silently discard the salt; it is a clause-splitter failure, not a synonym.
    """
    stripped = _BUFFER_DECORATION.sub(" ", (name or "").lower())
    stripped = re.sub(r"[^a-z\s\-]+", " ", stripped)
    key = normalise_for_match(stripped)
    if not key:
        return None
    target = by_normalised.get(key)
    if target and by_id[target]["chem_class"] == "buffer":
        return target
    return None


def peg_mw_from(name: str) -> Optional[int]:
    """The molecular weight a PEG name states, or None when it states none."""
    # "peg 20 000" and "peg 20,000" are the same reagent as "peg 20000".
    text = re.sub(r"(?<=\d)[\s,](?=\d)", "", (name or "").lower())
    compact = re.search(r"(\d{1,3})\s*k\b", text)
    if compact:
        value = int(compact.group(1)) * 1000
        if _PEG_MW_RANGE[0] <= value <= _PEG_MW_RANGE[1]:
            return value
    for match in _PEG_MW.finditer(text):
        value = int(match.group(1))
        if _PEG_MW_RANGE[0] <= value <= _PEG_MW_RANGE[1]:
            return value
    return None


def canonical_id_for(name: str, taken: set[str]) -> Optional[str]:
    """An upper snake case id, or None when the name cannot make a legal one."""
    lowered = (name or "").lower()
    # Chemical names commonly begin with locants ("1,2,3-heptanetriol", "6-aminohexanoic acid")
    # and an id may not start with a digit. The shipped lexicon already moves them to the end,
    # as in PROPANEDIOL_12, so the same convention is followed rather than inventing another.
    leading = re.match(r"^[\d,\-\s]+", lowered)
    suffix = ""
    if leading:
        digits = re.sub(r"\D", "", leading.group(0))
        if digits:
            lowered, suffix = lowered[leading.end():], f"_{digits}"
    slug = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_").upper()
    slug = re.sub(r"_+", "_", slug) + suffix
    # Locants nest: "4-(2-hydroxyethyl)-1-piperazineethanesulfonic acid" still begins with a
    # digit after the first is moved. Keep moving leading digit runs to the end until a letter
    # leads or nothing is left.
    while slug and slug[0].isdigit():
        head, _, rest = slug.partition("_")
        if not rest:
            return None
        slug = f"{rest}_{head}"
    if not slug or not slug[0].isalpha():
        return None
    candidate, n = slug, 2
    while candidate in taken:
        candidate, n = f"{slug}_{n}", n + 1
    return candidate


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queue", type=Path,
                        default=config.INTERIM_DIR / "curation_queue.json")
    parser.add_argument("--answers", type=Path,
                        default=config.INTERIM_DIR / "curation_round_2_answers.json")
    parser.add_argument("--synonyms", type=Path, default=config.ONTOLOGY_DIR / "synonyms.yaml")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    queue = {q["id"]: q for q in json.loads(args.queue.read_text())["questions"]}
    answers = {a["id"]: a for a in json.loads(args.answers.read_text())["answers"]}
    data = yaml.safe_load(args.synonyms.read_text())
    by_id = {r["canonical_id"]: r for r in data["reagents"]}
    taken_ids = set(by_id)
    alias_owner = {a.lower(): r["canonical_id"]
                   for r in data["reagents"] for a in (r.get("aliases") or [])}
    by_normalised = {normalise_for_match(r["display_name"]): r["canonical_id"]
                     for r in data["reagents"]}

    applied: dict[str, list[dict[str, Any]]] = {"alias": [], "new": [], "left": [], "skipped": []}

    with Manifest(STAGE, params={"dry_run": args.dry_run,
                                 "lexicon_version": NEW_LEXICON_VERSION}) as m:
        m.add_input(args.queue).add_input(args.answers).add_input(args.synonyms)

        for qid, question in queue.items():
            recommended = next(o for o in question["options"] if o["recommended"])["value"]
            answer = answers.get(qid)
            chosen = answer["chosen"] if answer else recommended
            # The bucket *key* is not the chemical class. `new::unclassed` is the bucket of
            # candidates whose class could not be guessed from the name, and its members are
            # added as additives; reading the key directly wrote `chem_class: unclassed` into
            # 150 entries and the lexicon refused to load, which is the invariant doing its job.
            chem_class = qid.rsplit("::", 1)[-1]
            recommended_label = next(
                (o["label"] for o in question["options"] if o["recommended"]), "")
            from_label = re.search(r"class (\w+)", recommended_label)
            if from_label:
                chem_class = from_label.group(1)
            elif chem_class == "unclassed":
                chem_class = "additive"
            members = question.get("members", [])

            if chosen in ("__LEAVE__", "__NOT_A_REAGENT__"):
                applied["left"].extend(
                    {"name": mem["name"], "weight": mem["weight"], "action": chosen}
                    for mem in members)
                continue

            for mem in members:
                name, target, weight = mem["name"], mem.get("target"), mem["weight"]
                lowered = name.lower()

                # An alias already owned by another reagent cannot be re-pointed without
                # silently changing what that reagent matches.
                if lowered in alias_owner:
                    applied["skipped"].append({"name": name, "weight": weight,
                                               "why": f"already an alias of {alias_owner[lowered]}"})
                    continue

                if chosen == "__ACCEPT__" and target and not target.startswith("__"):
                    if target not in by_id:
                        applied["skipped"].append({"name": name, "weight": weight,
                                                   "why": f"target {target} not in lexicon"})
                        continue
                    by_id[target].setdefault("aliases", []).append(name)
                    alias_owner[lowered] = target
                    applied["alias"].append({"name": name, "target": target, "weight": weight})
                    continue

                # A new reagent. Everything below is an invariant the lexicon enforces on load.
                spec: dict[str, Any] = {"chem_class": chem_class}
                if chem_class == "peg":
                    mw = peg_mw_from(name)
                    if mw is None:
                        applied["skipped"].append({
                            "name": name, "weight": weight,
                            "why": "no molecular weight in the name, and a peg entry requires one"})
                        continue
                    spec["peg_mw"] = mw
                    # Matches the rule the shipped lexicon already follows, so a new PEG cannot
                    # contradict the existing ones.
                    spec["default_unit"] = "percent_v_v" if mw <= 600 else "percent_w_v"
                    if "mme" in lowered or "monomethyl" in lowered:
                        spec["is_mme"] = True
                elif chem_class == "buffer":
                    # A pKa cannot be derived from a string. If the name is really a spelling of
                    # a buffer already present, add it as an alias of that instead.
                    # "bis-tris-hcl", "bis-tris:hcl" and "tris base" are spellings of buffers
                    # already present. The counter-ion and the word "base" say how the buffer was
                    # supplied, not which buffer it is, so they are stripped before matching.
                    existing = (by_normalised.get(normalise_for_match(name))
                                or buffer_alias_target(name, by_normalised, by_id))
                    if existing and by_id[existing]["chem_class"] == "buffer":
                        by_id[existing].setdefault("aliases", []).append(name)
                        alias_owner[lowered] = existing
                        applied["alias"].append({"name": name, "target": existing,
                                                 "weight": weight})
                        continue
                    pka = BUFFER_PKA.get(lowered.strip())
                    if pka is None:
                        applied["skipped"].append({
                            "name": name, "weight": weight,
                            "why": "a buffer entry requires a pKa, and this one is not a known "
                                   "buffer or a spelling of one already present"})
                        continue
                    spec["buffer_pka"] = pka
                    spec["default_unit"] = "molar"
                else:
                    spec["default_unit"] = DEFAULT_UNIT.get(chem_class, "millimolar")

                canonical = canonical_id_for(name, taken_ids)
                if canonical is None:
                    applied["skipped"].append({"name": name, "weight": weight,
                                               "why": "name cannot form a legal canonical id"})
                    continue
                spec.update({"canonical_id": canonical,
                             "display_name": name[0].upper() + name[1:],
                             "aliases": [name]})
                by_id[canonical] = spec
                taken_ids.add(canonical)
                alias_owner[lowered] = canonical
                applied["new"].append({"name": name, "canonical_id": canonical,
                                       "chem_class": chem_class, "weight": weight})

        data["reagents"] = sorted(by_id.values(), key=lambda r: r["canonical_id"])
        data["version"] = NEW_LEXICON_VERSION

        if not args.dry_run:
            args.synonyms.write_text(
                yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))
            m.add_output(args.synonyms)

        totals = {k: len(v) for k, v in applied.items()}
        totals["n_reagents_after"] = len(data["reagents"])
        totals["n_components_addressed"] = sum(
            r["weight"] for k in ("alias", "new") for r in applied[k])
        m.note(**totals)

        print(f"\n  reagents {len(data['reagents']):,}   lexicon -> {NEW_LEXICON_VERSION}"
              f"{'   (dry run, nothing written)' if args.dry_run else ''}")
        print(f"  new entries      {len(applied['new']):>5}  "
              f"{sum(r['weight'] for r in applied['new']):>7,} components")
        print(f"  aliases added    {len(applied['alias']):>5}  "
              f"{sum(r['weight'] for r in applied['alias']):>7,} components")
        print(f"  left unidentified  {len(applied['left']):>5}  "
              f"{sum(r['weight'] for r in applied['left']):>7,} components")
        print(f"  skipped          {len(applied['skipped']):>5}  "
              f"{sum(r['weight'] for r in applied['skipped']):>7,} components")
        if applied["skipped"]:
            reasons: dict[str, list] = {}
            for row in applied["skipped"]:
                reasons.setdefault(row["why"], []).append(row)
            print("\n  skipped, by reason:")
            for why, rows in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
                print(f"    {len(rows):>4}  {sum(r['weight'] for r in rows):>7,} components  {why}")
                print(f"          e.g. {', '.join(r['name'] for r in rows[:5])[:88]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
