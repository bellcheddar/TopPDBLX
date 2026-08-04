#!/usr/bin/env bash
# Everything the round table needs, for round 09 and for round 06 on equal terms.
# Run after RUN_ROUND09.sh finishes training. Roughly 2 hours.
set -euo pipefail
cd "$(dirname "$0")"

R09=data/interim/slm/runs/r1-parse-residual-smollm2-360m-round09
# The pre-resume checkpoints live OUTSIDE ~/Documents on purpose: Documents is iCloud-synced,
# and 663 MB of new files there gave fileproviderd enough upload work to stall a checkpoint
# save for 23 minutes mid-training. Keeping them out also protects them from Optimize-Mac-
# Storage eviction, which has emptied model files from this tree before.
PRERUN=/Users/dellboy/toppdblx_prerun_ckpts
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
# **The resume reset the iteration counter, so filenames lie.** Training stopped at true 2900 and
# restarted numbering from 0000100, which then began overwriting the originals. The pre-resume
# checkpoints for true 1000-2900 were copied to prerun_offset0/ before that happened; everything in
# the run dir now is offset by 2900 (true = 2900 + reported). Sweeping "1000 2000 ... 8000" against
# the run dir would have scored two different schedules under one set of names, and would never
# have found 6000-8000 at all because the resumed run only counts to 5100.
# Format: <true iter>:<dir>:<reported iter>
# Denser through true 3000-5000: val loss hit its floor (0.0040) at true 3800 and has been flat
# since, so that is where the downstream peak most likely sits and where 1000-iter spacing would
# step straight over it. Sparse above 5000, where the curve is not moving.
SWEEP="1000:prerun_offset0:1000 2000:prerun_offset0:2000 3000:.:100 3500:.:600 \
       4000:.:1100 4500:.:1600 5000:.:2100 6000:.:3100 7000:.:4100 8000:.:5100"
for ENTRY in $SWEEP; do
  TRUE="${ENTRY%%:*}"; REST="${ENTRY#*:}"; SUB="${REST%%:*}"; CKPT="${REST##*:}"
  DIR="$R09"; [ "$SUB" = "." ] || DIR="$PRERUN"
  [ -f "$DIR/$(printf '%07d' $CKPT)_adapters.safetensors" ] || { echo "skip: no true-$TRUE"; continue; }
  for G in gold_set_20260801 gold_set_20260803; do
    ./run.sh models.apply_slm --adapter-dir "$DIR" --checkpoint "$CKPT" --system-version v2 \
        --records "data/interim/$G.json" \
        --progress "data/interim/slm/apply_r09_t${TRUE}_${G}.jsonl" \
        --out "data/interim/slm_components_r09_t${TRUE}_${G}.parquet"
    echo "--- round 09 @ TRUE iter $TRUE (file $(printf '%07d' $CKPT) in $SUB) on $G"
    ./run.sh eval.gold_metrics --gold "data/interim/$G.json" \
        --slm-components "data/interim/slm_components_r09_t${TRUE}_${G}.parquet" \
        --out "data/interim/gold_r09_t${TRUE}_${G}.json" | grep -E "rules \+ model|source "
  done
done

echo
echo "Pick the checkpoint with the best F0.5 across BOTH gold sets, then set CKPT and run step 3."
echo "Round 06 stays shipped unless round 09 wins. Its current figures are 98.2% / 95.2%, F0.5 97.6."
echo
echo "=================== 3. Components shipped (edit CKPT first) ==================="
cat <<'NEXT'
  # Use the TRUE iteration the sweep reported, and the dir it came from:
  #   true 1000-2900 -> DIR=$PRERUN, CKPT = the true number
  #   true 3000-8000 -> DIR=$R09,               CKPT = true - 2900
  CKPT=<reported number for the winner>
  DIR=<$R09, or $PRERUN for true 1000-2900>
  # A NEW progress file. apply_progress.jsonl holds round 06's generations, and apply_slm
  # resumes from whatever it is given -- pointing at the old one would silently re-ship round 06.
  ./run.sh models.apply_slm --adapter-dir $DIR \
      --checkpoint $CKPT --system-version v2 \
      --progress data/interim/slm/apply_progress_r09.jsonl \
      --out data/interim/slm_components_r09.parquet
  ./run.sh assign.classify --slm-components data/interim/slm_components_r09.parquet
  ./run.sh assign.screen_match
  ./run.sh release.datasheet
NEXT
