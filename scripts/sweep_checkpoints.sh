#!/usr/bin/env bash
# Does more training actually help the metric that matters?
#
# Validation loss saturates within 400 iterations, but it measures fidelity to the rule parser,
# which is R1's ceiling and not its goal. This sweeps residual identification across checkpoints so
# the decision to extend to a full epoch is made on evidence rather than on the loss curve.
#
# Reading the result:
#   identification still climbing at the last checkpoint -> extend, resume with --iter-offset
#   identification flat, JSON mostly invalid             -> capacity limited, raise LoRA rank
#   identification flat, JSON valid but names unknown    -> lexicon limited, more training cannot help
#
#   bash scripts/sweep_checkpoints.sh 100 400 1000 2000 3000

set -euo pipefail
cd "$(dirname "$0")/.."

LIMIT="${LIMIT:-1200}"
OUT_DIR="data/interim/slm/sweep"
mkdir -p "$OUT_DIR"

for iter in "$@"; do
  echo "=== checkpoint ${iter} ==="
  ./run.sh models.eval_slm --checkpoint "$iter" --limit "$LIMIT" \
      --out "$OUT_DIR/eval_${iter}.json" \
      --sample-out "$OUT_DIR/sample_${iter}.jsonl" 2>&1 \
    | grep -E "identification|schema valid|exact JSON|fully identified" || true
done

echo
echo "=== summary ==="
.venv/bin/python - "$@" <<'PY'
import json, sys
from pathlib import Path
rows = []
for it in sys.argv[1:]:
    path = Path(f"data/interim/slm/sweep/eval_{it}.json")
    if path.exists():
        d = json.loads(path.read_text())
        rows.append((int(it), d))
print(f"{'iter':>6}  {'fidelity exact':>14}  {'resid valid':>11}  {'resid identification':>16}  "
      f"{'resid full':>10}")
for it, d in rows:
    print(f"{it:>6}  {d['fidelity_exact_match_pct']:>13.2f}%  "
          f"{d['residual_schema_valid_pct']:>10.2f}%  "
          f"{d['residual_component_identification_pct']:>15.2f}%  "
          f"{d['residual_fully_identified_records_pct']:>9.2f}%")
if len(rows) >= 2:
    delta = (rows[-1][1]['residual_component_identification_pct']
             - rows[-2][1]['residual_component_identification_pct'])
    print(f"\nidentification change over the last interval: {delta:+.2f} points")
    print("still climbing -> extend to a full epoch" if delta > 0.5
          else "flat -> a full epoch would not pay for itself")
PY
