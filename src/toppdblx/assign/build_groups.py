"""SUPERSEDED by `assign.classify` at ontology 0.3.0.

The three-level ontology this stage belongs to was withdrawn: its groups were binned
from the corpus and then had labels retrofitted, which spec 6.1 rejects, and several
were not chemically coherent (median L2 purity 49%). Classification is now the seven
JCSG Top96 precipitant classes with no sub-levels. Kept for provenance and because the
diagnostics behind that decision are worth being able to reproduce.

Stage `assign.build_groups`: propose the group ontology from the corpus.

Spec 6.1 wants an ontology that is hand-defined but **derived from the full data**: "cluster
the parsed conditions, find the clusters with no curated home, and add curated groups to cover
the large orphans. The result is a human-defined ontology derived from the full data, which is
defensible in a paper."

This proposes that starting point. It does not replace curation: it produces a
`groups.yaml` where every group already has a readable label, a real centroid, its record
count and any commercial wells that fall inside it, so the human job is editing chemistry
rather than inventing a hundred definitions from a blank file.

Sizing follows the WP-diagnostic (decision 12.1): L2 to 90% coverage takes about 40 groups,
and L3 is deliberately capped where the marginal group stops earning its place, because 150
L3 groups reach only half the corpus and 1,245 would be needed for 90%. Conditions outside
any L3 group fall back to their L2 parent, which is what spec 6.2 means by predicting at
whichever level has adequate support.

    ./run.sh assign.build_groups
    ./run.sh assign.build_groups --l2 40 --l3 150
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

import polars as pl
import yaml
from tqdm import tqdm

from .. import config
from ..manifest import Manifest
from ..parse.lexicon import load as load_lexicon
from ..parse.rules import RuleParser
from . import screens
from .diagnose import cell_for
from .distance import (ConditionFeatures, distance, featurise,
                       shares_precipitant_axis)

STAGE = "assign.build_groups"

DEFAULT_L2 = 40
DEFAULT_L3 = 150

# A candidate group must hold at least this many records to earn a definition. At rank 150 an
# L3 cell holds 253 records, so 250 is where the marginal group stops paying for itself.
MIN_RECORDS = 250


def slug(label: str, taken: Optional[set[str]] = None) -> str:
    """A readable id, truncated but never colliding.

    Truncating to a fixed length made distinct labels share an id ("...10_20_0_1_0" from two
    different concentration bands). The ontology loader treats that as a hard error, so a
    numeric suffix disambiguates rather than silently merging two groups.
    """
    out = []
    for char in label.upper():
        out.append(char if char.isalnum() else "_")
    base = "_".join(part for part in "".join(out).split("_") if part)[:60]
    if taken is None or base not in taken:
        return base
    for n in range(2, 100):
        candidate = f"{base[:57]}_{n}"
        if candidate not in taken:
            return candidate
    return base


def mean_centroid(features: list[ConditionFeatures]) -> dict[str, Optional[float]]:
    """Axis-wise mean over the members that actually have each axis.

    Averaging only over the members that possess an axis matters: a group where half the
    members have no salt should have the salt centroid of the half that do, not a value
    diluted towards zero by the half that do not.
    """
    centroid: dict[str, Optional[float]] = {}
    for axis in ("peg_log_mw", "peg_percent", "salt_hofmeister", "salt_log_molar",
                 "organic_percent", "ph"):
        values = [getattr(f, axis) for f in features if getattr(f, axis) is not None]
        centroid[axis] = round(sum(values) / len(values), 4) if values else None
    return centroid


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--conditions", type=Path,
                        default=config.INTERIM_DIR / "parsed_conditions.parquet")
    parser.add_argument("--l1", type=Path, default=config.INTERIM_DIR / "l1_classes.parquet")
    parser.add_argument("--out", type=Path, default=config.ONTOLOGY_DIR / "groups.yaml")
    parser.add_argument("--l2", type=int, default=DEFAULT_L2)
    parser.add_argument("--l3", type=int, default=DEFAULT_L3)
    parser.add_argument("--min-records", type=int, default=MIN_RECORDS)
    parser.add_argument("--answers", type=Path, default=None,
                        help="curation answers to honour, from assign.group_questions")
    args = parser.parse_args(argv)

    config.ensure_dirs()

    # Curation decisions are applied as build inputs, so the ontology stays reproducible from
    # the corpus plus the answers file, and the manifest records which answers were in force.
    forced_labels: set[str] = set()
    dropped_ids: set[str] = set()
    merged_away: set[str] = set()
    if args.answers and args.answers.exists():
        for answer in json.loads(args.answers.read_text()).get("answers", []):
            kind, _, rest = answer["id"].partition("::")
            chosen = answer["chosen"]
            if kind == "orphan" and chosen == "create":
                forced_labels.add(rest)
            elif kind == "anchorless" and chosen == "drop":
                dropped_ids.add(rest)
            elif kind == "merge" and chosen.startswith("merge_into::"):
                keep = chosen.split("::", 1)[1]
                merged_away.update(i for i in rest.split("::") if i != keep)

    conditions = pl.read_parquet(args.conditions).filter(pl.col("discard_reason").is_null())
    l1 = pl.read_parquet(args.l1)
    keys = set(zip(conditions["pdb_id"], conditions["crystal_id"]))
    components = pl.read_parquet(args.components).filter(
        pl.struct(["pdb_id", "crystal_id"]).map_elements(
            lambda s: (s["pdb_id"], s["crystal_id"]) in keys, return_dtype=pl.Boolean))

    ph_by_key = dict(zip(zip(conditions["pdb_id"], conditions["crystal_id"]), conditions["ph"]))
    l1_by_key = dict(zip(zip(l1["pdb_id"], l1["crystal_id"]), l1["l1_precipitant_class"]))

    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for row in components.iter_rows(named=True):
        grouped.setdefault((row["pdb_id"], row["crystal_id"]), []).append(row)

    with Manifest(STAGE, params={"l2": args.l2, "l3": args.l3,
                                 "min_records": args.min_records}) as m:
        m.add_input(args.components).add_input(args.conditions).add_input(args.l1)

        # Bin every condition, keeping its features so centroids can be averaged.
        l2_members: dict[str, list[ConditionFeatures]] = {}
        l3_members: dict[str, list[ConditionFeatures]] = {}
        l3_parent: dict[str, str] = {}
        l2_class: dict[str, str] = {}
        for key, parts in tqdm(grouped.items(), desc="conditions", unit="cond"):
            features = featurise(parts, ph_by_key.get(key))
            l1_class = l1_by_key.get(key, "Unassigned")
            label2 = cell_for(features, l1_class, "L2")
            label3 = cell_for(features, l1_class, "L3")
            l2_members.setdefault(label2, []).append(features)
            l3_members.setdefault(label3, []).append(features)
            l3_parent[label3] = label2
            l2_class[label2] = l1_class

        # "Unassigned" is a residual outcome, not a chemical group: those conditions have no
        # identified precipitant, so giving them a centroid and a screen anchor would dress
        # up an absence of evidence as a chemical claim. They are reported and excluded.
        n_unassigned = sum(len(v) for k, v in l2_members.items()
                           if l2_class[k] == "Unassigned")
        l2_members = {k: v for k, v in l2_members.items() if l2_class[k] != "Unassigned"}
        l3_members = {k: v for k, v in l3_members.items()
                      if l2_class.get(l3_parent[k]) != "Unassigned"}

        chosen_l2 = [label for label, members in
                     sorted(l2_members.items(), key=lambda kv: -len(kv[1]))[:args.l2]
                     if len(members) >= args.min_records]
        chosen_l3 = [label for label, members in
                     sorted(l3_members.items(), key=lambda kv: -len(kv[1]))[:args.l3]
                     if len(members) >= args.min_records
                     and l3_parent[label] in chosen_l2]
        # Labels the curator asked for explicitly, even though they fell outside the top N.
        # The L2 parent is promoted alongside if needed: an L3 group with no parent in the
        # ontology would be dropped, which silently ignores the curation decision.
        for label in forced_labels:
            if label not in l3_members or label in chosen_l3:
                continue
            parent = l3_parent.get(label)
            if parent and parent not in chosen_l2 and parent in l2_members:
                chosen_l2.append(parent)
            if parent in chosen_l2:
                chosen_l3.append(label)

        # Anchor groups to real orderable wells, so the output can name something in the
        # fridge rather than a set of numbers (spec 6.5).
        library = screens.load(RuleParser(load_lexicon()))
        well_features = [
            (w, featurise([{
                "chem_class": c.chem_class, "concentration": c.concentration,
                "unit": c.unit, "peg_mw": c.peg_mw, "cryo_evidence": c.cryo_evidence,
                "hofmeister_rank": c.hofmeister_rank, "premix_id": c.premix_id,
            } for c in w.components], w.ph))
            for w in library.wells
        ]

        # A candidate whose members share no measurable axis has an empty centroid, so
        # nothing could ever be assigned to it. Dropping it is better than shipping a group
        # that can never match: the ontology loader rejects it outright.
        def has_axes(members: list[ConditionFeatures]) -> bool:
            return any(v is not None for v in mean_centroid(members).values())

        dropped_empty = [l for l in chosen_l2 if not has_axes(l2_members[l])] + \
                        [l for l in chosen_l3 if not has_axes(l3_members[l])]
        chosen_l2 = [l for l in chosen_l2 if has_axes(l2_members[l])]
        chosen_l3 = [l for l in chosen_l3 if has_axes(l3_members[l])
                     and l3_parent[l] in chosen_l2]

        groups: list[dict[str, Any]] = []
        id_for_label: dict[str, str] = {}
        used_ids: set[str] = set()

        for label in chosen_l2:
            group_id = f"L2_{slug(label, used_ids)}"
            used_ids.add(group_id[3:])
            id_for_label[label] = group_id
            centroid = mean_centroid(l2_members[label])
            groups.append({
                "id": group_id, "level": 2, "label": label,
                "l1_class": l2_class[label],
                "centroid": {k: v for k, v in centroid.items() if v is not None},
                "n_records_at_creation": len(l2_members[label]),
                "screen_anchors": [],
            })

        for label in chosen_l3:
            group_id = f"L3_{slug(label, used_ids)}"
            if group_id in dropped_ids or group_id in merged_away:
                continue
            used_ids.add(group_id[3:])
            centroid = mean_centroid(l3_members[label])
            features = ConditionFeatures(**centroid)
            # An anchor must agree on a precipitant, not merely on pH.
            anchors = sorted(
                ((distance(features, wf), w) for w, wf in well_features
                 if shares_precipitant_axis(features, wf)),
                key=lambda pair: pair[0])[:2]
            groups.append({
                "id": group_id, "level": 3, "label": label,
                "l1_class": l2_class[l3_parent[label]],
                "parent": id_for_label[l3_parent[label]],
                "centroid": {k: v for k, v in centroid.items() if v is not None},
                "n_records_at_creation": len(l3_members[label]),
                "screen_anchors": [
                    {"screen": w.screen, "catalogue": w.catalogue, "well": w.well}
                    for d, w in anchors if d < 0.5
                ],
            })

        document = {
            "version": "0.2.0" if args.answers else "0.1.0",
            "generated": date.today().isoformat(),
            "groups": groups,
        }
        # `generated` is informational; the schema forbids unknown fields, so it is dropped
        # before writing rather than silently breaking the loader.
        document.pop("generated")
        args.out.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True,
                                           width=100))

        covered_l2 = sum(len(l2_members[l]) for l in chosen_l2)
        covered_l3 = sum(len(l3_members[l]) for l in chosen_l3)
        total = len(grouped)
        anchored = sum(1 for g in groups if g["screen_anchors"])
        stats = {
            "n_groups": len(groups), "n_l2": len(chosen_l2), "n_l3": len(chosen_l3),
            "n_unassigned_excluded": n_unassigned,
            "n_dropped_no_axes": len(dropped_empty),
            "answers_applied": str(args.answers) if args.answers else None,
            "n_forced_by_curation": len(forced_labels),
            "n_dropped_by_curation": len(dropped_ids),
            "n_merged_away_by_curation": len(merged_away),
            "l2_coverage": round(covered_l2 / total, 4),
            "l3_coverage": round(covered_l3 / total, 4),
            "n_l3_with_screen_anchor": anchored,
            "min_records": args.min_records,
        }
        m.add_output(args.out).note(**stats)

        print(f"\nproposed {len(groups)} groups: {len(chosen_l2)} L2, {len(chosen_l3)} L3")
        print(f"  L2 covers {stats['l2_coverage']:.1%} of {total:,} conditions")
        print(f"  L3 covers {stats['l3_coverage']:.1%}, the rest falling back to its L2 parent")
        print(f"  {anchored} L3 groups anchored to a real screen well")
        print(f"  {n_unassigned:,} conditions ({n_unassigned / total:.1%}) have no identified "
              f"precipitant and are excluded rather than given a group")
        if dropped_empty:
            print(f"  {len(dropped_empty)} candidate group(s) dropped for having no "
                  f"measurable axis: nothing could ever match them")
        print(f"\nwritten to {args.out}: now a curation artefact, not an output")
        print("\nlargest proposed L2 groups:")
        for label in chosen_l2[:8]:
            print(f"  {len(l2_members[label]):>7,}  {label}")
        print("\nexample L3 groups with their orderable anchors:")
        shown = 0
        for group in groups:
            if group["level"] == 3 and group["screen_anchors"] and shown < 6:
                anchor = group["screen_anchors"][0]
                print(f"  {group['n_records_at_creation']:>6,}  {group['label'][:58]}")
                print(f"          -> {anchor['screen']} well {anchor['well']}")
                shown += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
