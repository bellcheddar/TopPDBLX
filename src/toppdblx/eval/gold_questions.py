"""Stage `eval.gold_questions`: the 96 records that become ground truth.

**Nothing in the SLM evaluation has ever had a label.** `identification` asks only whether an
emitted name exists in the lexicon; `grounded` asks only whether that name appears in the text.
Both are precision-shaped, and neither can see a component the model failed to emit at all. A
model that reads one reagent per record and skips the rest scores near-perfectly on both. That
blind spot is why the classification audits felt load-bearing: 16 of the 21 conditions flagged
across the two audit rounds were flagged for a *missed* reagent, which is recall, and no
automated metric in this project could see it.

96 labelled records fixes that, and only that. It is not a training set and must never be used as
one: it is the yardstick every future round is measured against, and a yardstick that has been
trained on measures nothing.

**Names only, not amounts.** Recall and precision over the set of reagents in a record is the
missing measurement. Asking for concentrations as well would triple the work per record for a
number the existing `unit_inferred` machinery already reports on, and 96 records is the budget.

**Sampled from the residual**, because that is the population the model is applied to, and
stratified by text length in three bands. Length is the strongest predictor of a missed reagent
in this corpus -- a long deposition holds more clauses for the splitter to lose -- so an unbanded
sample would under-represent the failure being measured.

**Seeded from the current pipeline, deliberately not from the teacher.** Each record arrives
carrying the components the rules and the model already found, so the job is to correct a list
rather than author one. That anchors the labels to the pipeline, which is acceptable because the
pipeline is not what these labels will judge: they exist to measure a *teacher* model that has
never seen them, and to measure future rounds.

    ./run.sh eval.gold_questions
    ./run.sh eval.gold_questions --n 48
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import polars as pl

from .. import config
from ..manifest import Manifest
from ..parse import lexicon as lexicon_module

STAGE = "eval.gold_questions"

DEFAULT_N = 96
BANDS = 3


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--residual", type=Path,
                        default=config.INTERIM_DIR / "slm" / "residual.jsonl")
    parser.add_argument("--components", type=Path,
                        default=config.INTERIM_DIR / "parsed_components.parquet")
    parser.add_argument("--slm-components", type=Path,
                        default=config.INTERIM_DIR / "slm_components.parquet")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "gold_questions.json")
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--seed", type=int, default=41)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    rng = random.Random(args.seed)
    lex = lexicon_module.load()

    residual = [json.loads(l) for l in args.residual.read_text().splitlines() if l.strip()]

    # One record per distinct text: two depositions of the same condition test the same thing.
    seen: set[str] = set()
    pool = []
    for row in residual:
        text = " ".join((row.get("text") or "").split())
        if len(text) < 25 or text.lower() in seen:
            continue
        seen.add(text.lower())
        pool.append({**row, "text": text})

    # Three equal length bands, so the long depositions where reagents go missing are not
    # crowded out by the short ones that dominate the corpus.
    pool.sort(key=lambda r: len(r["text"]))
    per_band, chosen = args.n // BANDS, []
    for band in range(BANDS):
        lo = band * len(pool) // BANDS
        hi = (band + 1) * len(pool) // BANDS
        slice_ = pool[lo:hi]
        rng.shuffle(slice_)
        chosen.extend(slice_[:per_band])
    for row in pool:                      # top up if a band ran short
        if len(chosen) >= args.n:
            break
        if row not in chosen:
            chosen.append(row)
    rng.shuffle(chosen)

    def proposals(frame: Path, source: str) -> dict[tuple, list[dict[str, Any]]]:
        out: dict[tuple, list[dict[str, Any]]] = {}
        if not frame.exists():
            return out
        cols = ["pdb_id", "crystal_id", "name_canonical", "concentration", "unit", "role"]
        for row in pl.read_parquet(frame).select(cols).iter_rows(named=True):
            if not row["name_canonical"] or row["role"] == "not_a_component":
                continue
            amount = (f"{row['concentration']:g} {row['unit']}"
                      if row["concentration"] is not None and row["unit"] else "amount unstated")
            out.setdefault((row["pdb_id"], row["crystal_id"]), []).append(
                {"name": row["name_canonical"], "amount": amount, "source": source})
        return out

    from_rules = proposals(args.components, "rules")
    from_model = proposals(args.slm_components, "model")

    with Manifest(STAGE, params={"n": args.n, "seed": args.seed, "bands": BANDS}) as m:
        m.add_input(args.residual)

        questions = []
        for row in chosen:
            key = (row["pdb_id"], row["crystal_id"])
            merged: dict[str, dict[str, Any]] = {}
            for item in from_rules.get(key, []) + from_model.get(key, []):
                merged.setdefault(item["name"], item)
            questions.append({
                "id": f"gold::{row['pdb_id']}::{row['crystal_id']}",
                "pdb_id": row["pdb_id"],
                "crystal_id": row["crystal_id"],
                "text": row["text"],
                "proposed": list(merged.values()),
            })

        payload = {
            "schema_version": config.SCHEMA_VERSION,
            "lexicon_version": config.ONTOLOGY_VERSION,
            "title": "Gold set: which reagents are actually in this condition",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "intro": (f"{len(questions)} conditions. Each shows the deposition text and the "
                      f"reagents the pipeline found. Remove any that are not really there, add "
                      f"any it missed, then Next. Names only, no concentrations. These become "
                      f"the yardstick every future model round is measured against, so they are "
                      f"never trained on."),
            # The picker needs every name a label could legitimately take.
            "lexicon": sorted(r.canonical_id for r in lex.reagents),
            "n_questions": len(questions),
            "questions": questions,
        }
        args.out.write_text(json.dumps(payload, indent=2) + "\n")
        m.add_output(args.out).note(n_questions=len(questions),
                                    n_lexicon=len(payload["lexicon"]),
                                    n_proposed=sum(len(q["proposed"]) for q in questions))

        proposed = sum(len(q["proposed"]) for q in questions)
        print(f"\n  {len(questions)} records, {proposed} proposed components "
              f"({proposed/max(1,len(questions)):.1f} per record)")
        lengths = [len(q["text"]) for q in questions]
        print(f"  text length: min {min(lengths)}, median {sorted(lengths)[len(lengths)//2]}, "
              f"max {max(lengths)}")
        print(f"\n  {args.out}")
        print(f"  open app/gold_bench_v1.html and drop the file on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
