#!/usr/bin/env bash
# Overnight chain, 2026-08-05. Each step is resumable and none touches the release.
cd "$(dirname "$0")/../.."   # repo root
say(){ printf '%s | %s\n' "$(date '+%H:%M:%S')" "$1"; }

# 1. Fill the two "not measured" cells in the README's rounds table (~45 min).
for R in 07 08; do
  OUT=data/interim/slm/eval_round${R}_frozen_lex092.json
  if [ ! -f "$OUT" ]; then
    say "frozen benchmark, round $R"
    ./run.sh models.eval_slm --frozen \
        --adapter-dir data/interim/slm/runs/r1-parse-residual-smollm2-360m-round${R} \
        --system-version v1 --out "$OUT" >> data/interim/slm/overnight.log 2>&1 \
      && say "round $R done" || say "round $R FAILED"
  else
    say "round $R already scored, skipping"
  fi
done

# 2. Long pole: does a 32B teacher find real chemistry in what is still unnamed?
#    2,500 records, resumable via the progress file, gold excluded at build time.
say "teacher probe over 2,500 still-unnamed records (long, resumable)"
for attempt in 1 2 3; do
  [ -f data/interim/teacher_unnamed_probe.parquet ] && break
  say "teacher attempt $attempt"
  ./run.sh models.teacher_label \
      --records data/interim/unnamed_probe.json \
      --progress data/interim/slm/teacher_progress_unnamed.jsonl \
      --out data/interim/teacher_unnamed_probe.parquet \
      >> data/interim/slm/overnight.log 2>&1
done
if [ -f data/interim/teacher_unnamed_probe.parquet ]; then
  say "OVERNIGHT COMPLETE — teacher probe finished"
else
  say "OVERNIGHT FINISHED WITH TEACHER INCOMPLETE — partial results in the progress file"
fi
