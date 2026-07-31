"""SUPERSEDED by `assign.classify` at ontology 0.3.0.

The three-level ontology this stage belongs to was withdrawn: its groups were binned
from the corpus and then had labels retrofitted, which spec 6.1 rejects, and several
were not chemically coherent (median L2 purity 49%). Classification is now the seven
JCSG Top96 precipitant classes with no sub-levels. Kept for provenance and because the
diagnostics behind that decision are worth being able to reproduce.

Stage `assign.apply_label_answers`: fold the L2 label round back into the ontology.

Completes roadmap decision D. Reads the answers exported from `condition_courtroom_v5.html` and
rewrites the 41 L2 labels in `groups.yaml`, leaving the 122 L3 labels generated as agreed.

**Unanswered means accepted**, as in every other round here: the tool exports only the questions
that were touched, and its own instruction is to change the ones you disagree with. Each applied
label records whether it came from an explicit answer, an accepted suggestion, or a free-text
rename, so the provenance survives into the changelog.

Only the label changes. Group ids, centroids and membership are untouched, so nothing downstream
is re-assigned and the release does not need rebuilding: a label is a display string, and treating
it as one keeps this round cheap and reversible.

    ./run.sh assign.apply_label_answers --dry-run
    ./run.sh assign.apply_label_answers
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import yaml

from .. import config
from ..manifest import Manifest

STAGE = "assign.apply_label_answers"

NEW_ONTOLOGY_VERSION = "0.3.0"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--questions", type=Path,
                        default=config.INTERIM_DIR / "label_questions.json")
    parser.add_argument("--answers", type=Path,
                        default=config.INTERIM_DIR / "l2_group_labels_answers.json")
    parser.add_argument("--groups", type=Path, default=config.ONTOLOGY_DIR / "groups.yaml")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    if not args.answers.exists():
        raise SystemExit(f"{args.answers} not found. Export from the courtroom first, and pass "
                         f"--answers if the download landed elsewhere.")

    questions = {q["id"]: q for q in json.loads(args.questions.read_text())["questions"]}
    answers = {a["id"]: a for a in json.loads(args.answers.read_text())["answers"]}
    data = yaml.safe_load(args.groups.read_text())
    groups = data["groups"] if isinstance(data, dict) and "groups" in data else data
    by_id = {g["id"]: g for g in groups}

    applied = {"renamed": [], "kept_generated": [], "unchanged": []}

    with Manifest(STAGE, params={"dry_run": args.dry_run,
                                 "ontology_version": NEW_ONTOLOGY_VERSION}) as m:
        m.add_input(args.questions).add_input(args.answers).add_input(args.groups)

        for qid, question in questions.items():
            gid = qid.split("::", 1)[1]
            group = by_id.get(gid)
            if group is None:
                continue
            recommended = next(o for o in question["options"] if o["recommended"])["value"]
            answer = answers.get(qid)
            chosen = answer["chosen"] if answer else recommended
            free_text = (answer or {}).get("free_text", "").strip()
            source = "explicit" if answer else "accepted_suggestion"
            old = group["label"]

            if chosen == "__RENAME__" and free_text:
                new = free_text
            elif chosen == "__RENAME__":
                # Rename chosen with nothing typed. Silently keeping the old label would look
                # like the answer was honoured, so it is reported instead.
                applied["unchanged"].append(
                    {"id": gid, "label": old,
                     "why": "rename selected but no replacement was typed"})
                continue
            elif chosen == "__KEEP__":
                applied["kept_generated"].append({"id": gid, "label": old, "source": source})
                continue
            else:
                new = chosen  # the suggested name, carried as the option's value

            if new == old:
                applied["kept_generated"].append({"id": gid, "label": old, "source": source})
                continue

            group["label"] = new
            group["label_generated"] = old  # keep the machine name for provenance
            applied["renamed"].append({"id": gid, "from": old, "to": new, "source": source,
                                       "weight": question["weight"]})

        if applied["renamed"]:
            data["version"] = NEW_ONTOLOGY_VERSION
        if not args.dry_run:
            args.groups.write_text(
                yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))
            m.add_output(args.groups)

        totals = {k: len(v) for k, v in applied.items()}
        totals["n_conditions_relabelled"] = sum(r["weight"] for r in applied["renamed"])
        m.note(**totals)

        print(f"\n  renamed {len(applied['renamed'])} L2 labels, covering "
              f"{totals['n_conditions_relabelled']:,} conditions")
        for row in sorted(applied["renamed"], key=lambda r: -r["weight"]):
            flag = "  [typed]" if row["source"] == "explicit" else ""
            print(f"    {row['weight']:>7,}  {row['from'][:44]:<44} -> {row['to'][:40]}{flag}")
        if applied["kept_generated"]:
            print(f"\n  kept the generated label for {len(applied['kept_generated'])} groups")
        if applied["unchanged"]:
            print(f"\n  NOT applied ({len(applied['unchanged'])}):")
            for row in applied["unchanged"]:
                print(f"    {row['id']}: {row['why']}")
        print(f"\n  ontology version -> {data.get('version')}"
              f"{'  (dry run, nothing written)' if args.dry_run else ''}")
        print(f"  L3 labels untouched, as agreed in roadmap decision D")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
