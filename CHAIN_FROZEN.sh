#!/usr/bin/env bash
# Waits for the true-1000 frozen eval, then scores the SHIPPED checkpoint (true 4500).
# Polls the output file, never `until ! pgrep -f`, which matches its own command line.
cd "$(dirname "$0")"
T1000=data/interim/slm/eval_round09_t1000_frozen_lex092.json
T4500=data/interim/slm/eval_round09_t4500_frozen_lex092.json
echo "$(date '+%H:%M:%S') | waiting for true-1000"
for _ in $(seq 1 180); do [ -f "$T1000" ] && break; sleep 20; done
[ -f "$T1000" ] && echo "$(date '+%H:%M:%S') | true-1000 done" || echo "$(date '+%H:%M:%S') | true-1000 did NOT produce output"
echo "$(date '+%H:%M:%S') | scoring shipped checkpoint true 4500"
./run.sh models.eval_slm --frozen \
    --adapter-dir data/interim/slm/runs/r1-parse-residual-smollm2-360m-round09 \
    --checkpoint 1600 --system-version v2 --out "$T4500" >> data/interim/slm/eval_t4500.log 2>&1
if [ -f "$T4500" ]; then echo "$(date '+%H:%M:%S') | true-4500 COMPLETE"; else echo "$(date '+%H:%M:%S') | true-4500 FAILED"; fi
