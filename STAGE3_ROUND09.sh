#!/usr/bin/env bash
# Stage 3: re-generate the corpus with the SHIPPED round 09 checkpoint (true 4,500 = reported 1,600).
# Chained behind CHAIN_FROZEN.sh by waiting on its output file, not on a pid.
cd "$(dirname "$0")"
R09=data/interim/slm/runs/r1-parse-residual-smollm2-360m-round09
CKPT=1600                                   # true 4,500; offset 2,900 from the resume
T4500=data/interim/slm/eval_round09_t4500_frozen_lex092.json
PROG=data/interim/slm/apply_progress_r09.jsonl
OUT=data/interim/slm_components_r09.parquet
say(){ printf '%s | %s\n' "$(date '+%H:%M:%S')" "$1"; }

say "stage 3 queued, waiting for the true-4500 frozen eval"
for _ in $(seq 1 240); do [ -f "$T4500" ] && break; sleep 20; done
[ -f "$T4500" ] || say "true-4500 eval never produced output — proceeding anyway, it does not gate the corpus"

# **A NEW progress file.** apply_progress.jsonl holds round 06's generations and apply_slm resumes
# from whatever it is given, so reusing it would silently re-ship round 06.
say "apply_slm over the residual with round 09 @ true 4,500 (~4.7 h, resumable)"
for attempt in 1 2 3 4 5; do
  [ -f "$OUT" ] && break
  say "apply_slm attempt $attempt"
  ./run.sh models.apply_slm --adapter-dir "$R09" --checkpoint "$CKPT" --system-version v2 \
      --progress "$PROG" --out "$OUT" >> data/interim/slm/stage3.log 2>&1
done
if [ ! -f "$OUT" ]; then say "apply_slm FAILED after 5 attempts — stopping before the release steps"; exit 1; fi
say "apply_slm complete: $(wc -l < "$PROG" | tr -d ' ') records generated"

for step in "assign.classify --slm-components $OUT" "assign.screen_match" "release.datasheet"; do
  say "running ${step%% *}"
  # shellcheck disable=SC2086
  if ! ./run.sh $step >> data/interim/slm/stage3.log 2>&1; then
    say "${step%% *} FAILED — see data/interim/slm/stage3.log"; exit 1
  fi
done
say "STAGE 3 COMPLETE — corpus re-generated with round 09"
