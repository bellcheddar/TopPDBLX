"""Stage `models.build_teacher_dataset`: training data that is not the rule parser's own output.

`build_slm_dataset` bootstraps from records `rules_v3` read confidently, which is why it costs no
hand-labelling and why the rule parser is the ceiling. Round 06's checkpoint sweep showed that
ceiling directly: fidelity to `rules_v3` climbed to 93.6% while identification on the residual
peaked at 2,000 iterations and fell. The model was getting better at imitating and worse at
reading.

This stage adds the missing half: pairs drawn from the **residual**, where the rules produced
nothing, labelled by a local 32B teacher and the current student together.

**The target is a union, and its composition was measured rather than assumed.** Scored on the 96
gold records:

    student alone (rules + slm)                     99.6% precision   91.5% recall
    full union with the teacher                     91.4%             97.6%
    union, teacher finds needing a stated amount    92.6%             97.6%   <- used
    agreement only                                 100.0%             83.0%

Agreement is the most precise and would be the wrong choice: at 83% its recall is *below* the
student's own, so training on it would teach the model to say less than it already does. The
union buys six points of recall for seven of precision, and requiring the teacher's own finds to
carry a stated amount recovers one of those points at no cost to recall.

**7.4% of these targets contain a reagent that is not there**, and that is the accepted risk of
the round rather than a detail: the teacher hallucinates plausible chemistry (`PEG_4000`,
`SODIUM_CHLORIDE`, `HEPES`) which no name-based filter separates from its correct finds
(`GLYCEROL`, `AMMONIUM_SULFATE`, `BIS_TRIS`). The gold set exists to catch it if the student
inherits the habit, and round 07 fails if precision drops below 99%.

**The rules pairs stay in the mix, and dominate it.** They are the high-precision anchor; the
residual pairs are the new capability. Oversampling makes the residual visible to a 2,000
iteration run without letting label noise set the tone.

    ./run.sh models.build_teacher_dataset
    ./run.sh models.build_teacher_dataset --residual-share 0.35
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Optional

import polars as pl

from .. import config
from ..manifest import Manifest
from .build_slm_dataset import SYSTEM, target_json
from .eval_slm import _flatten, load_aliases

STAGE = "models.build_teacher_dataset"

# What fraction of the training set should be residual pairs. The rest is the existing
# rules-derived bootstrap. A quarter is enough for the new behaviour to be seen many times over a
# 2,000-iteration run at batch 4 (8,000 examples) without the noisier labels setting the tone.
DEFAULT_RESIDUAL_SHARE = 0.25
COMPONENT_COLUMNS = ["pdb_id", "crystal_id", "component_index", "role", "name_canonical",
                     "concentration", "unit"]


def _by_record(path: Path) -> dict[tuple, dict[str, dict[str, Any]]]:
    """Components keyed by record, then by canonical name, so sources can be merged by reagent."""
    out: dict[tuple, dict[str, dict[str, Any]]] = {}
    if not path.exists():
        return out
    for row in pl.read_parquet(path).select(COMPONENT_COLUMNS).iter_rows(named=True):
        if not row["name_canonical"]:
            continue
        out.setdefault((row["pdb_id"], row["crystal_id"]), {}).setdefault(
            row["name_canonical"], row)
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=config.INTERIM_DIR / "slm")
    parser.add_argument("--out-dir", type=Path, default=config.INTERIM_DIR / "slm_r7")
    parser.add_argument("--slm-components", type=Path,
                        default=config.INTERIM_DIR / "slm_components.parquet")
    parser.add_argument("--teacher-components", type=Path,
                        default=config.INTERIM_DIR / "teacher_components_2k.parquet")
    parser.add_argument("--exclude", type=Path,
                        default=config.INTERIM_DIR / "gold_set_20260801.json",
                        help="the gold set, which must never become training data")
    parser.add_argument("--min-alias-hit", type=int, default=8,
                        help="a teacher-only find must match at least this many characters of "
                             "the text. Measured on the gold set: 0 gives 92.6%% label precision, "
                             "6 gives 94.4%%, 8 gives 97.6%%. Short aliases match spuriously")
    parser.add_argument("--residual-share", type=float, default=DEFAULT_RESIDUAL_SHARE)
    parser.add_argument("--valid-size", type=int, default=400)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    residual_text = {}
    for line in (args.data_dir / "residual.jsonl").read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            residual_text[(row["pdb_id"], row["crystal_id"])] = row["text"]

    held_out = set()
    if args.exclude and args.exclude.exists():
        held_out = {(r["pdb_id"], r.get("crystal_id", "1"))
                    for r in json.loads(args.exclude.read_text()).get("records", [])}

    aliases = load_aliases()

    def longest_hit(name: str, text: str) -> int:
        """Longest alias of `name` that appears in the text, in characters."""
        flat = _flatten(text)
        return max((len(a) for a in aliases.get(name, []) if a and a in flat), default=0)

    from_student = _by_record(args.slm_components)
    from_teacher = _by_record(args.teacher_components)

    with Manifest(STAGE, params={"residual_share": args.residual_share,
                                 "seed": args.seed}) as m:
        m.add_input(args.teacher_components).add_input(args.slm_components)

        residual_pairs: list[dict[str, Any]] = []
        n_teacher_added = n_teacher_dropped = 0
        for key in sorted(from_teacher):
            if key in held_out or key not in residual_text:
                continue
            merged = dict(from_student.get(key, {}))
            for name, row in from_teacher[key].items():
                if name in merged:
                    continue
                # The teacher's own finds must carry a stated amount. Measured on the gold set,
                # that is worth a point of precision and costs no recall.
                # **Two filters, both measured against the gold set rather than argued.** A
                # stated amount is worth a point of label precision; requiring the reagent's
                # alias to match at least `--min-alias-hit` characters of the text is worth five
                # more, because a short alias matches almost anything once punctuation is
                # stripped. Together they take label precision from 92.6% to 97.6%, which is the
                # 7.4% false-positive rate round 07 taught the student almost exactly.
                if row["concentration"] is None:
                    n_teacher_dropped += 1
                    continue
                if longest_hit(name, residual_text[key]) < args.min_alias_hit:
                    n_teacher_dropped += 1
                    continue
                merged[name] = row
                n_teacher_added += 1
            if not merged:
                continue
            parts = sorted(merged.values(), key=lambda r: r["component_index"])
            residual_pairs.append({"messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": residual_text[key]},
                {"role": "assistant", "content": target_json(parts)},
            ]})

        rules_pairs = [json.loads(l) for l in
                       (args.data_dir / "train.jsonl").read_text().splitlines() if l.strip()]

        # Oversample the residual to the requested share. Repeating whole examples rather than
        # generating variants keeps every target something a parser actually asserted.
        share = max(0.01, min(0.9, args.residual_share))
        target_residual = int(len(rules_pairs) * share / (1 - share))
        repeats = max(1, round(target_residual / max(1, len(residual_pairs))))
        oversampled = residual_pairs * repeats

        train = rules_pairs + oversampled
        rng.shuffle(train)

        # Validation stays purely rules-derived: it measures fidelity, which is only meaningful
        # against labels that are known-good, and mixing in 7%-noisy targets would make the loss
        # curve unreadable.
        valid_src = [json.loads(l) for l in
                     (args.data_dir / "valid.jsonl").read_text().splitlines() if l.strip()]
        valid = valid_src[:args.valid_size]

        for name, rows in (("train", train), ("valid", valid)):
            (args.out_dir / f"{name}.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in rows))

        stats = {"n_residual_records": len(residual_pairs), "n_repeats": repeats,
                 "n_residual_pairs": len(oversampled), "n_rules_pairs": len(rules_pairs),
                 "n_train": len(train), "n_valid": len(valid),
                 "residual_share": round(len(oversampled) / max(1, len(train)), 3),
                 "n_teacher_components_added": n_teacher_added,
                 "n_teacher_dropped_no_amount": n_teacher_dropped,
                 "n_held_out": len(held_out)}
        m.add_output(args.out_dir / "train.jsonl").note(**stats)

        print(f"\n  residual records labelled by the teacher   {len(residual_pairs):,}")
        print(f"  reagents the teacher added to the student  {n_teacher_added:,}"
              f"   ({n_teacher_dropped:,} dropped for stating no amount)")
        print(f"  oversampled x{repeats} -> {len(oversampled):,} pairs, "
              f"{100*len(oversampled)/max(1,len(train)):.0f}% of the training set")
        print(f"  rules-derived pairs                        {len(rules_pairs):,}")
        print(f"  train {len(train):,}   valid {len(valid):,} (rules only, so loss stays readable)")
        print(f"\n  {args.out_dir}")
        print(f"  next: ./run.sh models.train_slm --data-dir {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
