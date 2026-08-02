"""Stage `eval.gold_metrics`: precision and recall against labelled truth, at last.

Every number this project has reported about the parser was precision-shaped. `identification`
asks whether an emitted name is in the lexicon. `grounded` asks whether that name appears in the
text. Neither can see a reagent that was never emitted, so a parser that reads one component per
record and stops scores well on both. This stage measures the thing they cannot: **recall**.

    precision  of the reagents we claim, how many are really there
    recall     of the reagents really there, how many did we find

**Reported for the rules alone and for the shipped pipeline**, because the question the model has
to answer is not "is it good" but "does it add anything the rules did not already have". The gap
between those two rows is the model's entire contribution, measured against truth rather than
against the rules' own output.

**Gold names are resolved through the lexicon before comparison.** A label typed as `PEG4000` or
`TRIS_HCL` is the same reagent as `PEG_4000` and `TRIS`, and scoring them as misses would invent
a recall failure out of a spelling. Anything that still does not resolve is reported separately:
those are real gaps in the lexicon, and they are the additions worth making.

    ./run.sh eval.gold_metrics
    ./run.sh eval.gold_metrics --gold data/interim/gold_set_20260801.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Optional

import polars as pl

from .. import config
from ..manifest import Manifest
from ..parse import lexicon as lexicon_module
from ..parse.text import normalise, tidy_name

STAGE = "eval.gold_metrics"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return (0.0, 0.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (100 * (centre - half), 100 * (centre + half))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path,
                        default=config.INTERIM_DIR / "gold_set_20260801.json")
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--slm-components", type=Path,
                        default=config.INTERIM_DIR / "slm_components.parquet")
    parser.add_argument("--teacher-components", type=Path, default=None,
                        help="a third source. Adds the union and the agreement rows, which is "
                             "where the interesting result lives: two parsers can score the same "
                             "and still be wrong about different reagents")
    parser.add_argument("--keep-incomplete", action="store_true",
                        help="score records whose gold labels could not all be resolved. Off by "
                             "default: their truth set is known to be missing a reagent, so a "
                             "correct prediction there counts as a false positive")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "gold_metrics.json")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    gold_doc = json.loads(args.gold.read_text())
    records = [r for r in gold_doc["records"]]
    lex = lexicon_module.load()
    index, by_id = lex.index(), lex.by_id()

    def resolve(name: str) -> Optional[str]:
        """A gold label to a canonical id, or None if the lexicon cannot place it.

        **The canonical id is tried first, and that is not optional.** The picker offers canonical
        ids, so most labels arrive as one; putting them through the alias path turns
        `BUTANEDIOL_14` into "butanediol 14", which matches no alias, and a reagent that is
        plainly in the lexicon is reported as a gap in it.
        """
        if name in by_id:
            return name
        # **Lowercase before tidying.** `tidy_name`'s polymer rule is a lowercase-only regex, so
        # "PEG4000" passes through untouched and misses `peg 4000`, while "peg4000" resolves. A
        # label typed in capitals was being reported as a gap in the lexicon.
        reagent = index.get(normalise(tidy_name(name.replace("_", " ").lower())))
        return reagent.canonical_id if reagent else None

    def predicted(frame: Path) -> dict[tuple, set[str]]:
        out: dict[tuple, set[str]] = {}
        if not frame.exists():
            return out
        cols = ["pdb_id", "crystal_id", "name_canonical", "role"]
        for row in pl.read_parquet(frame).select(cols).iter_rows(named=True):
            if not row["name_canonical"] or row["role"] == "not_a_component":
                continue
            out.setdefault((row["pdb_id"], row["crystal_id"]), set()).add(row["name_canonical"])
        return out

    from_rules = predicted(args.components)
    from_model = predicted(args.slm_components)

    # **A record whose gold is incomplete cannot judge a false positive.** If a label could not be
    # resolved, the truth set for that record is missing a reagent, so a parser naming it correctly
    # is scored as wrong. That penalty falls hardest on whichever model says the most, which is a
    # bias in favour of the quieter one -- exactly backwards for a metric meant to reward recall.
    # Such records are excluded from the comparison and counted, rather than silently distorting it.
    unresolved: dict[str, int] = {}
    truth: dict[tuple, set[str]] = {}
    n_excluded = 0
    for record in records:
        key = (record["pdb_id"], record["crystal_id"])
        names, incomplete = set(), False
        for raw in record["gold"]:
            canonical = resolve(raw)
            if canonical:
                names.add(canonical)
            else:
                unresolved[raw] = unresolved.get(raw, 0) + 1
                incomplete = True
        if incomplete and not args.keep_incomplete:
            n_excluded += 1
            continue
        truth[key] = names

    def score(get: Any, label: str) -> dict[str, Any]:
        tp = fp = fn = 0
        misses: dict[str, int] = {}
        for key, gold in truth.items():
            found = get(key)
            tp += len(found & gold)
            fp += len(found - gold)
            fn += len(gold - found)
            for name in gold - found:
                misses[name] = misses.get(name, 0) + 1
        precision = 100 * tp / (tp + fp) if tp + fp else 0.0
        recall = 100 * tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        # **F0.5 weights precision twice as heavily as recall**, which is the honest summary for a
        # released dataset: a missed reagent is recoverable by re-reading the deposition, an
        # invented one propagates into everything built on the data. F1 treats the two as equal
        # and so flatters a model that trades precision for recall.
        beta2 = 0.25
        fbeta = (((1 + beta2) * precision * recall) / (beta2 * precision + recall)
                 if beta2 * precision + recall else 0.0)
        return {"label": label, "tp": tp, "fp": fp, "fn": fn,
                "precision": round(precision, 2), "recall": round(recall, 2),
                "f1": round(f1, 2), "f0_5": round(fbeta, 2),
                "precision_ci95": [round(v, 2) for v in wilson(tp, tp + fp)],
                "recall_ci95": [round(v, 2) for v in wilson(tp, tp + fn)],
                "misses": misses}

    rules_only = score(lambda k: from_rules.get(k, set()), "rules only")
    shipped = score(lambda k: from_rules.get(k, set()) | from_model.get(k, set()),
                    "rules + model (shipped)")
    reported = [rules_only, shipped]

    # **Two sources scoring alike can still be wrong about different reagents, and that is worth
    # more than either score.** The union row is the recall available if nothing is thrown away;
    # the agreement row is the precision available if only what both assert is kept. On the first
    # gold set the SLM and a 32B teacher scored within noise of each other on recall (p = 0.33)
    # while the union missed 7 reagents against the SLM's 25 -- so the teacher was not a weaker
    # parser, it was a differently wrong one, which is exactly what makes it useful as a labeller.
    if args.teacher_components:
        from_teacher = predicted(args.teacher_components)
        reported.append(score(
            lambda k: from_rules.get(k, set()) | from_teacher.get(k, set()), "rules + teacher"))
        reported.append(score(
            lambda k: from_rules.get(k, set()) | from_model.get(k, set()) | from_teacher.get(k, set()),
            "union of all three"))
        reported.append(score(
            lambda k: from_rules.get(k, set()) | (from_model.get(k, set()) & from_teacher.get(k, set())),
            "rules + where both agree"))

    with Manifest(STAGE, params={"gold": str(args.gold)}) as m:
        m.add_input(args.gold).add_input(args.components)
        payload = {
            "gold_generated_at": gold_doc.get("generated_at"),
            "n_records": len(truth), "n_excluded_incomplete": n_excluded,
            "n_gold_reagents": sum(len(v) for v in truth.values()),
            "rows": reported,
            "unresolved_gold_names": dict(sorted(unresolved.items(),
                                                 key=lambda kv: -kv[1])),
        }
        args.out.write_text(json.dumps(payload, indent=2) + "\n")
        m.add_output(args.out).note(
            n_records=len(records), n_gold=payload["n_gold_reagents"],
            rules_recall=rules_only["recall"], shipped_recall=shipped["recall"])

        if n_excluded:
            print(f"\n  {n_excluded} records excluded: a gold label there could not be resolved, "
                  f"so their truth is incomplete")
        print(f"\n  {len(truth)} labelled records, {payload['n_gold_reagents']} gold reagents "
              f"({payload['n_gold_reagents']/max(1,len(truth)):.2f} per record)\n")
        print(f"  {'source':<26}{'precision':>22}{'recall':>22}{'F1':>8}{'F0.5':>8}")
        for row in reported:
            p, r = row["precision_ci95"], row["recall_ci95"]
            print(f"  {row['label']:<26}{row['precision']:>9.1f}% [{p[0]:.0f},{p[1]:.0f}]"
                  f"{row['recall']:>10.1f}% [{r[0]:.0f},{r[1]:.0f}]{row['f1']:>8.1f}{row['f0_5']:>8.1f}")
        gain = shipped["recall"] - rules_only["recall"]
        print(f"\n  the model adds {gain:+.1f} points of recall, "
              f"{shipped['tp'] - rules_only['tp']} reagents the rules never found")

        if unresolved:
            print(f"\n  {sum(unresolved.values())} gold labels the lexicon cannot place. These are "
                  f"the entries worth adding,\n  and they are excluded from the scores above "
                  f"rather than counted as misses:")
            for name, count in list(sorted(unresolved.items(), key=lambda kv: -kv[1]))[:20]:
                print(f"    {count:>3}  {name}")

        worst = sorted(shipped["misses"].items(), key=lambda kv: -kv[1])[:12]
        if worst:
            print(f"\n  most-missed reagents, shipped pipeline:")
            for name, count in worst:
                print(f"    {count:>3}  {name}")
        print(f"\n  {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
