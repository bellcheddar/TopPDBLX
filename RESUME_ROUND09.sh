#!/usr/bin/env bash
# Resume round 09 after the 2026-08-04 reboot. Stopped cleanly at iteration 2,900 of 8,000.
set -euo pipefail
cd "$(dirname "$0")"

OFFSET=2900                 # iterations already done, from checkpoint 0002900
REMAINING=$((8000-OFFSET))  # 5,100 to go
R09=data/interim/slm/runs/r1-parse-residual-smollm2-360m-round09

# **A resume restarts the iteration counter and the learning-rate schedule.** mlx-lm reloads the
# adapter weights and nothing else, confirmed on chem_sage R3 and chatPDB. So `--iters` must be
# the REMAINING budget, not the original total, and the reported iteration will read 1..5100
# while the true one is 2901..8000. The cosine also restarts: it will warm up over 50 steps and
# decay to 1e-5 across 5,100 rather than continuing the original curve. That is a real difference
# from an uninterrupted run and belongs in the round table when this is written up.
echo "resuming from iteration $OFFSET; $REMAINING to go; reported iters will be offset by $OFFSET"

pgrep -f "mlx_lm.lora" >/dev/null && { echo "training already running"; exit 1; }

echo "== indexing should still be off after the reboot: =="
mdutil -a -s 2>/dev/null | head -2
echo "   if it says 'Indexing enabled', run: sudo mdutil -a -i off"

bash scripts/preflight.sh

nohup ./scripts/spotlight_reaper.sh 40 >> data/interim/slm/reaper.log 2>&1 &
echo "reaper started (pid $!)"

./run.sh models.train_slm \
  --run-name    r1-parse-residual-smollm2-360m-round09 \
  --adapter-dir "$R09" \
  --resume \
  --iter-offset "$OFFSET" \
  --num-layers 32 --lora-rank 16 --lora-dropout 0.05 \
  --batch-size 16 --max-seq-length 1024 \
  --iters "$REMAINING" --learning-rate 1e-4 --end-learning-rate 1e-5 --warmup 50 \
  --steps-per-report 10 --steps-per-eval 50
