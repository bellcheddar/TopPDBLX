#!/usr/bin/env bash
# Everything the round table needs, for round 09 and for round 06 on equal terms.
# Run after RUN_ROUND09.sh finishes training. Roughly 2 hours.
set -euo pipefail
cd "$(dirname "$0")"

R09=data/interim/slm/runs/r1-parse-residual-smollm2-360m-round09
R06=data/interim/slm/runs/r1-parse-residual-smollm2-360m-round06
[ -d "$R09" ] || { echo "round 09 has not trained yet"; exit 1; }

# **Each round is served with the prompt it was trained under.** Round 06 is v1, round 09 is v2.
# A mismatch does not fail; it answers slightly worse and reports nothing.
echo "=================== 1. Identification and grounding, frozen benchmark ==================="
# **Round 06 is re-scored, not quoted.** Its published 88.99% was measured on 2026-07-31 against a
# lexicon of ~560 reagents. Today's has 499 after merging what was never distinct, and
# identification asks whether an emitted name is IN THE LEXICON -- so the old number and a new one
# are not the same measurement. Both rounds are scored here, today, against the same dictionary.
./run.sh models.eval_slm --frozen --adapter-dir "$R06" --system-version v1 \
    --out data/interim/slm/eval_round06_frozen_lex092.json
./run.sh models.eval_slm --frozen --adapter-dir "$R09" --system-version v2 \
    --out data/interim/slm/eval_round09_frozen_lex092.json

echo "=================== 2. Checkpoint sweep on both gold sets ==================="
# Never on identification: it has given three wrong answers to "how long should this train".
for CKPT in 1000 2000 3000 4000 5000 6000 7000 8000; do
  [ -f "$R09/$(printf '%07d' $CKPT)_adapters.safetensors" ] || continue
  for G in gold_set_20260801 gold_set_20260803; do
    ./run.sh models.apply_slm --adapter-dir "$R09" --checkpoint "$CKPT" --system-version v2 \
        --records "data/interim/$G.json" \
        --progress "data/interim/slm/apply_r09_${CKPT}_${G}.jsonl" \
        --out "data/interim/slm_components_r09_${CKPT}_${G}.parquet"
    echo "--- round 09 @ $CKPT on $G"
    ./run.sh eval.gold_metrics --gold "data/interim/$G.json" \
        --slm-components "data/interim/slm_components_r09_${CKPT}_${G}.parquet" \
        --out "data/interim/gold_r09_${CKPT}_${G}.json" | grep -E "rules \+ model|source "
  done
done

echo
echo "Pick the checkpoint with the best F0.5 across BOTH gold sets, then set CKPT and run step 3."
echo "Round 06 stays shipped unless round 09 wins. Its current figures are 98.2% / 95.2%, F0.5 97.6."
echo
echo "=================== 3. Components shipped (edit CKPT first) ==================="
cat <<'NEXT'
  CKPT=<the winner>
  # A NEW progress file. apply_progress.jsonl holds round 06's generations, and apply_slm
  # resumes from whatever it is given -- pointing at the old one would silently re-ship round 06.
  ./run.sh models.apply_slm --adapter-dir data/interim/slm/runs/r1-parse-residual-smollm2-360m-round09 \
      --checkpoint $CKPT --system-version v2 \
      --progress data/interim/slm/apply_progress_r09.jsonl \
      --out data/interim/slm_components_r09.parquet
  ./run.sh assign.classify --slm-components data/interim/slm_components_r09.parquet
  ./run.sh assign.screen_match
  ./run.sh release.datasheet
NEXT
