"""Stage `model.train_propensity`: will this protein crystallise?

Gradient boosting on sequence-only descriptors, trained on 81,130 structural genomics targets that
all reached purified protein, 20.6% of which went on to crystallise.

    ./run.sh model.train_propensity
    ./run.sh model.train_propensity --with-esm      # add frozen ESM-2 embeddings

**Failure condition, declared before the run:** AUC below 0.65 on the cluster-split held-out
targets. That would not beat published predictors and the honest response is to report it as a
negative result rather than tune until it looks better.

**Gradient boosting rather than a neural net**, deliberately. 81k rows of 30 tabular descriptors
is squarely where boosted trees win, and the feature importances are readable, which matters more
here than a fraction of a point of AUC: a crystallographer can act on "your construct is 40%
disordered" and cannot act on a logit.

**What this model is not.** TargetTrack ends in 2017 and is dominated by structural genomics
centres, which chose tractable targets and worked at high throughput. A membrane protein or a
large complex is barely represented. The number below is honest for the population it was trained
on and should not be read as a universal probability of crystallisation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl

from .. import config
from ..manifest import Manifest

STAGE = "model.train_propensity"
AUC_FLOOR = 0.65               # roadmap failure condition
DROP = {"target_id", "lab", "sequence", "crystallised", "organism", "cluster_30",
        "bucket", "split"}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path,
                        default=config.INTERIM_DIR / "propensity_dataset.parquet")
    parser.add_argument("--out-dir", type=Path,
                        default=config.INTERIM_DIR / "propensity_model")
    parser.add_argument("--trees", type=int, default=400)
    args = parser.parse_args(argv)

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import (average_precision_score, roc_auc_score,
                                 precision_recall_curve)
    from sklearn.inspection import permutation_importance

    config.ensure_dirs()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    frame = pl.read_parquet(args.data)
    columns = [c for c in frame.columns if c not in DROP]

    train = frame.filter(pl.col("split") == "train")
    test = frame.filter(pl.col("split") == "test")
    x_train = train.select(columns).to_numpy()
    y_train = train["crystallised"].to_numpy().astype(int)
    x_test = test.select(columns).to_numpy()
    y_test = test["crystallised"].to_numpy().astype(int)
    print(f"  train {len(y_train):,} ({y_train.mean():.1%} positive), "
          f"test {len(y_test):,} ({y_test.mean():.1%} positive), {len(columns)} features")

    model = HistGradientBoostingClassifier(
        max_iter=args.trees, learning_rate=0.06, max_leaf_nodes=31,
        l2_regularization=1.0, early_stopping=True, validation_fraction=0.1,
        random_state=23)
    model.fit(x_train, y_train)
    probability = model.predict_proba(x_test)[:, 1]

    auc = roc_auc_score(y_test, probability)
    ap = average_precision_score(y_test, probability)
    baseline = float(y_test.mean())

    # **A ranked list is what a crystallographer would actually use**: given a shortlist of
    # candidate constructs, which do you try first? So the useful figure is the hit rate in the
    # top decile against the base rate, not accuracy at an arbitrary threshold.
    order = np.argsort(-probability)
    lifts = {}
    for frac in (0.05, 0.10, 0.25):
        k = max(1, int(len(order) * frac))
        lifts[frac] = float(y_test[order[:k]].mean())

    with Manifest(STAGE, params={"trees": args.trees, "auc_floor": AUC_FLOOR,
                                 "n_features": len(columns)}) as m:
        print(f"\n  AUC              {auc:.3f}   (floor {AUC_FLOOR})")
        print(f"  average precision {ap:.3f}   (base rate {baseline:.3f})")
        print(f"  top 5%  crystallise {lifts[0.05]:.1%}  "
              f"({lifts[0.05]/baseline:.2f}x the base rate)")
        print(f"  top 10% crystallise {lifts[0.10]:.1%}  "
              f"({lifts[0.10]/baseline:.2f}x)")
        print(f"  top 25% crystallise {lifts[0.25]:.1%}  "
              f"({lifts[0.25]/baseline:.2f}x)")

        print("\n  what the model is using (permutation importance, top 10):")
        imp = permutation_importance(model, x_test, y_test, n_repeats=3,
                                     random_state=23, scoring="roc_auc")
        ranked = sorted(zip(columns, imp.importances_mean), key=lambda t: -t[1])[:10]
        for name, value in ranked:
            print(f"    {value:+.4f}  {name}")

        passes = auc >= AUC_FLOOR
        print(f"\n  failure condition (AUC >= {AUC_FLOOR}): "
              f"{'PASS' if passes else 'FAIL — report as a negative result, do not tune'}")
        result = {"auc": auc, "average_precision": ap, "base_rate": baseline,
                  "lift": {str(k): v for k, v in lifts.items()},
                  "meets_criterion": bool(passes),
                  "top_features": [{"feature": n, "importance": float(v)} for n, v in ranked]}
        (args.out_dir / "results.json").write_text(json.dumps(result, indent=2))
        import pickle
        (args.out_dir / "model.pkl").write_bytes(pickle.dumps(model))
        m.add_output(args.out_dir / "results.json")
        m.note(auc=auc, average_precision=ap, base_rate=baseline,
               lift_top10=lifts[0.10], meets_criterion=bool(passes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
