"""SUPERSEDED by `assign.classify` at ontology 0.3.0.

The three-level ontology this stage belongs to was withdrawn: its groups were binned
from the corpus and then had labels retrofitted, which spec 6.1 rejects, and several
were not chemically coherent (median L2 purity 49%). Classification is now the seven
JCSG Top96 precipitant classes with no sub-levels. Kept for provenance and because the
diagnostics behind that decision are worth being able to reproduce.

Stage `assign.label_questions`: give the 41 L2 groups names a crystallographer would use.

Roadmap decision D, settled 2026-07-30: **refine the 41 L2 labels only**; the 122 L3 labels stay
machine-generated. This is the stage that does it.

The generated labels describe the axes that formed the group, not the chemistry a person would
recognise. `Organic/PEG/Salt · PEG 3350-4000 · acetate/formate` is accurate and unreadable. Since
L2 is where 26.5% of the corpus lands (48,801 conditions that reach no L3 group), these names are
what a user actually reads, so they are worth an expert hour.

**Independent of everything else.** No model output, no residual, no lexicon: it needs only the
ontology and the assignments. It is therefore the curation to do while a GPU job is running.

Each question shows what the group *is* rather than only what it is called: the centroid in
readable units, the real record count, and the most common actual conditions in it, so the name
can be judged against the chemistry rather than against the generated string.

    ./run.sh assign.label_questions
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import polars as pl
import yaml

from .. import config
from ..manifest import Manifest

STAGE = "assign.label_questions"


def summarise_members(
        members: pl.DataFrame) -> tuple[list[str], list[str],
                                        list[tuple[str, int]], list[tuple[str, int, float]]]:
    """Describe a group from **what is actually in it**, not from its centroid coordinates.

    The first version of this stage described groups by centroid and illustrated them with the
    first three members it happened to encounter. Both were wrong in the same way. A centroid is
    an average over every member, so it describes no real condition; and arbitrary members are
    mostly outliers, because a group formed by nearest-centroid assignment across six axes has a
    long tail of records that landed there on the balance of other axes. The result was a group
    labelled "PEG 3350-4000" illustrated with a PEG 2000 condition, and a "chloride" group
    illustrated with a phosphate one: impossible to judge a name against.

    So the group is now described by the reagents its members actually contain, ranked by how
    many members contain them, with the middle 80% range of each measured axis. That is a claim
    about the group rather than about its geometric centre.
    """
    lines: list[str] = []
    n = members.height
    if not n:
        return lines, [], [], []

    named = members.filter(pl.col("name_canonical").is_not_null())
    counts = (named.group_by("name_canonical", "chem_class")
              .agg(pl.col("pdb_id").n_unique().alias("n"))
              .sort("n", descending=True))
    n_records = members["pdb_id"].n_unique()
    rows = list(counts.head(12).iter_rows(named=True))
    top = [f"{r['name_canonical'].replace('_', ' ').lower()} "
           f"({100 * r['n'] / max(1, n_records):.0f}%)" for r in rows[:6]]
    # Buffers are excluded from *naming* (though still shown under CONTAINS): Tris and HEPES
    # appear in most groups, so they cannot distinguish one from another, and the ontology
    # deliberately collapses buffer identity to the pH it sets.
    #
    # **But buffer is an intrinsic class, and acting as a buffer is contextual.** Sodium citrate
    # is classed as a buffer and appears at 0.5 M or more in 2,834 components; Tris manages that
    # 85 times and HEPES 58. At 1.5 M citrate is precipitating the protein, not setting the pH.
    # Excluding it by class deleted the dominant reagent of the bare "Salt" group, which is 35%
    # sodium citrate, and left sodium formate at 14% as the recommended name. So a buffer-class
    # reagent is admitted when *in this group* it is typically present at precipitating strength,
    # which is the same distinction spec 6.3 draws between a buffer and a precipitant.
    PRECIPITATING_MOLAR = 0.5
    molar_median = {}
    molar_rows = members.filter((pl.col("unit") == "molar")
                                & pl.col("concentration").is_not_null())
    if molar_rows.height:
        molar_median = dict(molar_rows.group_by("name_canonical")
                            .agg(pl.col("concentration").median().alias("m"))
                            .iter_rows())
    # Reagents that qualify to *lead* a name: not a buffer, or a buffer present at
    # precipitating strength in this group.
    namers, buffer_namers = [], []
    for r in rows:
        entry = (r["name_canonical"].replace("_", " ").lower(),
                 round(100 * r["n"] / max(1, n_records)))
        median_molar = molar_median.get(r["name_canonical"]) or 0
        if r["chem_class"] != "buffer" or median_molar >= PRECIPITATING_MOLAR:
            namers.append(entry)
        else:
            # Offered but never recommended, with its concentration shown so the call can be
            # made on evidence. Excluding it outright is what hid sodium acetate from a group
            # that is 38% sodium acetate, and a guard must not make the right answer unreachable.
            buffer_namers.append((*entry, median_molar))

    def band(frame: pl.DataFrame, label: str, fmt: str) -> None:
        values = frame["concentration"].drop_nulls()
        if values.len() >= 20:
            lo, mid, hi = (values.quantile(q) for q in (0.1, 0.5, 0.9))
            lines.append(f"{label} {fmt.format(mid)} (most between "
                         f"{fmt.format(lo)} and {fmt.format(hi)})")

    before = len(lines)
    band(members.filter((pl.col("chem_class") == "peg")
                        & pl.col("unit").is_in(["percent_w_v", "percent_v_v"])),
         "PEG at", "{:.0f}%")
    # Molecular weight only when PEG is actually a feature of the group. A salt group has a few
    # PEG-containing members, and quoting "PEG molecular weight mostly 400 to 6,000" for it
    # describes the tail as though it were the group.
    if len(lines) > before:
        pegs = members.filter(pl.col("peg_mw").is_not_null())["peg_mw"].drop_nulls()
        if pegs.len() >= 20:
            lines.append(f"PEG molecular weight mostly {pegs.quantile(0.1):,.0f} "
                         f"to {pegs.quantile(0.9):,.0f}")
    band(members.filter((pl.col("chem_class") == "salt") & (pl.col("unit") == "molar")),
         "salt at", "{:.2f} M")
    return lines, top, namers, buffer_namers


def candidate_labels(namers: list[tuple[str, int]],
                     measured: list[str]) -> list[tuple[str, str]]:
    """Every plausible name for a group, each built only from reagents actually in it.

    Returned as (name, evidence) pairs, most likely first. A free-text box invites a label that
    names a reagent the group does not contain, or spells one a way the lexicon will not match;
    offering canonical names as a closed list makes every choice verifiably true of the group and
    consistent with `synonyms.yaml`. The share of records carrying each reagent is shown, so the
    choice is made against evidence rather than against a guess.
    """
    if not namers:
        return []

    def pretty(text: str) -> str:
        words = [w.upper() if w.lower() in {"peg", "mme", "mpd", "dtt", "dmso", "hepes",
                                            "mes", "mops", "tris", "bis", "spg"} else w
                 for w in text.split()]
        first = words[0]
        return " ".join([first if first.isupper() else first.capitalize()] + words[1:])

    def with_amount(name: str, lead_names: list[str]) -> str:
        wants_peg = any("peg" in n.lower() for n in lead_names)
        amount = next((m for m in measured
                       if m.startswith("PEG at" if wants_peg else "salt at")), None)
        if not amount:
            return name
        low, _, high = amount.split("(most between ")[-1].rstrip(")").partition(" and ")
        return f"{name}, {low} to {high}"

    # Only reagents in at least a tenth of the group's records are offered: below that a name
    # would describe a minority and mislead exactly as the old examples did.
    eligible = [(n, s) for n, s in namers if s >= 10][:5]
    if not eligible:
        return []

    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(names: list[str], shares: list[int]) -> None:
        label = with_amount(" and ".join(pretty(n) for n in names), names)
        if label in seen:
            return
        seen.add(label)
        evidence = ", ".join(f"{pretty(n)} in {s}% of records"
                             for n, s in zip(names, shares))
        candidates.append((label, evidence))

    lead, lead_share = eligible[0]
    # Pairs first, since a second reagent is usually what distinguishes this group from its
    # neighbours, then the dominant reagent alone.
    for other, share in eligible[1:]:
        add([lead, other], [lead_share, share])
    add([lead], [lead_share])
    for other, share in eligible[1:3]:
        add([other], [share])
    return candidates


def describe_centroid(centroid: dict[str, float]) -> list[str]:
    """The centroid in units a crystallographer reads, not model space.

    `peg_log_mw` and `salt_log_molar` are stored logged because the distance metric needs them
    that way; shown logged they are meaningless to a reader, so they are inverted here.
    """
    # A centroid averages over every member, so a salt group with a handful of PEG-containing
    # members shows a small non-zero PEG percentage. Reporting that as "PEG 1,500 at about 2%"
    # describes a salt group as if PEG defined it, which is exactly the wrong cue when someone is
    # judging the name. Nothing below a concentration that could actually precipitate a protein
    # is worth mentioning.
    MEANINGFUL_PERCENT = 5.0

    lines: list[str] = []
    peg_percent = centroid.get("peg_percent") or 0
    if peg_percent >= MEANINGFUL_PERCENT:
        mw = round(10 ** centroid["peg_log_mw"]) if centroid.get("peg_log_mw") else None
        lines.append(f"PEG {mw:,} at about {peg_percent:.0f}%" if mw
                     else f"PEG at about {peg_percent:.0f}%")
    salt_log = centroid.get("salt_log_molar")
    if salt_log is not None and abs(salt_log) > 0.01:
        molar = 10 ** salt_log
        rank = centroid.get("salt_hofmeister")
        # The Hofmeister axis runs sulfate (-4) to thiocyanate (+4), so its sign says which end
        # of the series the group sits at, which is the chemically meaningful part.
        where = ("strongly salting-out" if rank is not None and rank <= -3 else
                 "salting-out" if rank is not None and rank < -1 else
                 "salting-in" if rank is not None and rank > 1 else "mid-series")
        lines.append(f"salt at about {molar:.2f} M, {where}")
    organic = centroid.get("organic_percent") or 0
    if organic >= MEANINGFUL_PERCENT:
        lines.append(f"organic at about {organic:.0f}%")
    ph = centroid.get("ph")
    if ph:
        lines.append(f"pH about {ph:.1f}")
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--groups", type=Path, default=config.ONTOLOGY_DIR / "groups.yaml")
    parser.add_argument("--assignments", type=Path,
                        default=config.INTERIM_DIR / "group_assignments.parquet")
    parser.add_argument("--conditions", type=Path,
                        default=config.INTERIM_DIR / "parsed_conditions.parquet")
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "label_questions.json")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    data = yaml.safe_load(args.groups.read_text())
    groups = data["groups"] if isinstance(data, dict) and "groups" in data else data
    l2_groups = [g for g in groups if g.get("level") == 2]

    assignments = pl.read_parquet(args.assignments)
    conditions = pl.read_parquet(args.conditions).select("pdb_id", "crystal_id", "raw_details")
    joined = assignments.join(conditions, on=["pdb_id", "crystal_id"], how="left")

    # Real counts now, not the count at creation: the ontology has been rebuilt against a larger
    # lexicon since, so `n_records_at_creation` understates several groups.
    live_counts = dict(joined.group_by("l2_subclass").agg(pl.len().alias("n"))
                       .iter_rows())
    landing_here = dict(joined.filter(pl.col("assigned_level") == 2)
                        .group_by("l2_subclass").agg(pl.len().alias("n")).iter_rows())

    # Examples must be typical of the L2 group being named. Two earlier attempts were not.
    #
    # Taking whichever rows came first surfaced the tail. Sorting by `assignment_distance` looked
    # principled and was worse: that distance is measured to the group a record was *assigned*
    # to, which for 8,370 of 8,424 records in one group was an L3 sub-group, not the L2 group on
    # the card. Sorting by it therefore returns whichever L3 niche is tightest, so a group that
    # is 75% PEG 3350 was illustrated with three PEG 4000 conditions: an internally contradictory
    # card, and the reason this stage had to be rewritten twice.
    #
    # A condition deposited many times over is typical by definition, so examples are chosen by
    # how often their text recurs within the group, and only from records carrying the group's
    # own most common reagent.
    examples: dict[str, list[str]] = {}

    def normalise_text(value: str) -> str:
        return " ".join((value or "").split())

    dominant_reagent: dict[str, str] = {}
    # Component rows for every assigned record, so each group can be described by its contents.
    components = pl.read_parquet(args.components).select(
        "pdb_id", "crystal_id", "name_canonical", "chem_class", "concentration", "unit",
        "peg_mw")
    member_components = components.join(
        assignments.select("pdb_id", "crystal_id", "l2_subclass"),
        on=["pdb_id", "crystal_id"], how="inner")

    from collections import Counter as _Counter

    text_by_key = {(r["pdb_id"], r["crystal_id"]): normalise_text(r["raw_details"])
                   for r in joined.filter(pl.col("raw_details").is_not_null())
                                  .select("pdb_id", "crystal_id", "raw_details")
                                  .iter_rows(named=True)}

    for gid in {g["id"] for g in l2_groups}:
        members = member_components.filter(pl.col("l2_subclass") == gid)
        if not members.height:
            continue
        named_rows = members.filter(pl.col("name_canonical").is_not_null())
        if not named_rows.height:
            continue
        ranked = (named_rows.group_by("name_canonical")
                  .agg(pl.col("pdb_id").n_unique().alias("n"))
                  .sort("n", descending=True))
        lead = ranked["name_canonical"][0]
        dominant_reagent[gid] = lead
        # Only records that actually contain the group's commonest reagent may illustrate it.
        carriers = set(zip(*named_rows.filter(pl.col("name_canonical") == lead)
                           .select("pdb_id", "crystal_id").to_dict(as_series=False).values()))
        counts = _Counter(text_by_key[k] for k in carriers
                          if k in text_by_key and 25 < len(text_by_key[k]) < 160)
        examples[gid] = [text for text, _ in counts.most_common(3)]

    with Manifest(STAGE, params={"n_l2_groups": len(l2_groups)}) as m:
        m.add_input(args.groups).add_input(args.assignments)

        questions: list[dict[str, Any]] = []
        for group in sorted(l2_groups, key=lambda g: -live_counts.get(g["id"], 0)):
            gid = group["id"]
            total = live_counts.get(gid, 0)
            stops_here = landing_here.get(gid, 0)
            if not total:
                continue
            measured, top_reagents, namers, buffer_namers = summarise_members(
                member_components.filter(pl.col("l2_subclass") == gid))
            candidates = candidate_labels(namers, measured)
            for bname, bshare, bmolar in buffer_namers:
                if bshare >= 10:
                    candidates.append((
                        bname[0].upper() + bname[1:],
                        f"{bname} in {bshare}% of records, but mostly at {bmolar:.2f} M, "
                        f"so probably the buffer rather than the precipitant"))
            questions.append({
                "id": f"label::{gid}",
                "group": f"{group.get('l1_class', 'Other')} groups",
                "question": f"What should this group of {total:,} conditions be called?",
                "why": (f"Currently labelled “{group['label']}”, which names the axes that "
                        f"formed the group rather than its chemistry. {stops_here:,} conditions "
                        f"stop at this level and never reach a more specific group, so this is "
                        f"the most precise name they are ever given."),
                "weight": total,
                "weight_label": f"{total:,} conditions",
                "type": "choice",
                "options": [{"value": name,
                             "label": f"{name}   ({evidence})",
                             "recommended": i == 0}
                            for i, (name, evidence) in enumerate(candidates)]
                           + [{"value": "__KEEP__",
                               "label": f"Keep the generated label: {group['label']}",
                               "recommended": not candidates},
                              {"value": "__RENAME__",
                               "label": "Something else (type it below)",
                               "recommended": False}],
                "allow_text": True,
                # Ordered so the group can be understood before the name is judged: what is in
                # it, then how much, then three conditions typical of it.
                "context": ([f"CONTAINS: {', '.join(top_reagents)}"] if top_reagents else [])
                           + ([f"AMOUNTS: {'; '.join(measured)}"] if measured else [])
                           + ([f"SCREEN ANCHOR: {', '.join(group['screen_anchors'][:2])}"]
                              if group.get("screen_anchors") else [])
                           + [f"TYPICAL: {e}" for e in examples.get(gid, [])[:3]],
            })

        payload = {
            "schema_version": config.SCHEMA_VERSION,
            "lexicon_version": data.get("version", "unknown"),
            "title": "L2 group labels",
            "intro": (f"{len(questions)} groups to name. The generated labels describe the axes "
                      f"that formed each group, which is accurate but unreadable. Only the 41 L2 "
                      f"labels are being refined: the 122 L3 labels stay generated. Each question "
                      f"shows the chemistry and real examples, so the name can be judged against "
                      f"what is actually in the group. Keep the generated label where it is "
                      f"already fine."),
            "n_questions": len(questions),
            "totals": {
                "n_l2_groups": len(questions),
                "n_conditions_covered": sum(q["weight"] for q in questions),
                "n_conditions_stopping_at_l2": sum(landing_here.get(g["id"], 0)
                                                   for g in l2_groups),
            },
            "questions": questions,
        }
        args.out.write_text(json.dumps(payload, indent=2) + "\n")
        m.add_output(args.out).note(**payload["totals"], n_questions=len(questions))

        print(f"\n  {len(questions)} L2 labels to refine, covering "
              f"{payload['totals']['n_conditions_covered']:,} conditions")
        print(f"  {payload['totals']['n_conditions_stopping_at_l2']:,} of those stop at L2 and "
              f"never reach a more specific group,")
        print(f"  so this label is the most precise name they are ever given.")
        print(f"\n  {args.out}")
        print(f"  open app/condition_courtroom_v5.html and drop the file on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
