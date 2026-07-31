"""Stage `models.freeze_evalset`: a fixed benchmark, so rounds can be compared at all.

Every training round so far was scored against the *live* residual, which is the set of records
the rule parser could not read at that moment. That set shrinks whenever the lexicon grows or the
parser improves, and it shrinks from the easy end: curation and prose stripping took it from
76,923 records to 59,673, removing exactly the ones that had become readable.

So the numbers in the round-by-round table were never comparable. Round 03 scored 88.41% against
76,923 records; round 05 scored 82.38% against 59,673 harder ones. Read side by side that looks
like a regression, and it may be nothing but the population moving underneath the metric.

**A frozen set fixes the denominator.** Records are sampled once, written with the lexicon and
parser versions they were drawn under, and never resampled. Every future round is scored against
the same text, so a difference between rounds is a difference in the model.

Two properties make it honest:

  *drawn from the hardest state*  sampled from the residual as it stands after curation, so the
                                  benchmark cannot get easier over time. A later model scoring
                                  well on it is doing well on text that defeated the rules even
                                  with a 502-reagent lexicon.
  *never trained on*              these records carry no rule-parser answer by construction, so
                                  they cannot leak into the bootstrap training set.

The live residual is still reported alongside, because it is what the model would actually be
applied to. The frozen set answers "is this round better than the last"; the live residual
answers "what would running it now achieve". Neither replaces the other.

    ./run.sh models.freeze_evalset
    ./run.sh models.freeze_evalset --size 3000
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
from pathlib import Path
from typing import Optional

import yaml

from .. import config
from ..manifest import Manifest

STAGE = "models.freeze_evalset"

DEFAULT_SIZE = 2000


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--residual", type=Path,
                        default=config.INTERIM_DIR / "slm" / "residual.jsonl")
    parser.add_argument("--out", type=Path,
                        default=config.INTERIM_DIR / "slm" / "frozen_evalset.jsonl")
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--force", action="store_true",
                        help="resample even though a frozen set already exists")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    if args.out.exists() and not args.force:
        existing = [json.loads(l) for l in args.out.read_text().splitlines() if l.strip()]
        meta = existing[0].get("_meta", {}) if existing else {}
        raise SystemExit(
            f"{args.out} already exists with {max(0, len(existing) - 1):,} records, frozen at "
            f"lexicon {meta.get('lexicon_version', '?')}.\n"
            f"That is the point: resampling would break comparability with every round already "
            f"scored against it. Pass --force only if you intend to start a new benchmark.")

    residual = [json.loads(l) for l in args.residual.read_text().splitlines() if l.strip()]
    lexicon = yaml.safe_load((config.ONTOLOGY_DIR / "synonyms.yaml").read_text())

    # Deduplicated by text, for the same reason the training set is: a benchmark that repeats a
    # popular condition 40 times measures how well the model does on that one condition.
    seen: set[str] = set()
    unique = []
    for record in residual:
        key = " ".join((record["text"] or "").split()).lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(record)

    rng = random.Random(args.seed)
    rng.shuffle(unique)
    chosen = unique[:args.size]

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True).stdout.strip() or None
    except Exception:                                             # noqa: BLE001
        commit = None

    with Manifest(STAGE, params={"size": args.size, "seed": args.seed}) as m:
        m.add_input(args.residual)
        meta = {
            "_meta": {
                "frozen_at": "the residual after lexicon curation rounds 1 and 2 and the "
                             "prose-stripping parser fix",
                "lexicon_version": lexicon.get("version"),
                "n_reagents": len(lexicon["reagents"]),
                "n_residual_at_freeze": len(residual),
                "n_distinct_residual": len(unique),
                "size": len(chosen),
                "seed": args.seed,
                "git_commit": commit,
            }
        }
        with open(args.out, "w") as handle:
            handle.write(json.dumps(meta) + "\n")
            for record in chosen:
                handle.write(json.dumps(record) + "\n")
        m.add_output(args.out).note(**meta["_meta"])

        print(f"\n  frozen benchmark: {len(chosen):,} records")
        print(f"    drawn from {len(residual):,} residual records "
              f"({len(unique):,} distinct)")
        print(f"    at lexicon {lexicon.get('version')}, {len(lexicon['reagents'])} reagents")
        print(f"    seed {args.seed}, git {commit}")
        print(f"\n  every future round is scored against exactly these records, so a change")
        print(f"  between rounds is a change in the model rather than in the population.")
        print(f"\n  {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
