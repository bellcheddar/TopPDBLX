#!/usr/bin/env bash
# Round 09 launch. Run this once the blank-record teacher job has finished.
# Plan and reasoning: NEXT.md, section "2026-08-03: round 09 plan".
set -euo pipefail
cd "$(dirname "$0")"

echo "== 1. the teacher job must be finished =="
pgrep -f "toppdblx.*teacher_label" >/dev/null && { echo "still running; wait"; exit 1; }

echo "== 2. convert the blank-record labels =="
[ -f data/interim/teacher_components_blank.parquet ] || \
  ./run.sh models.teacher_label \
    --records data/interim/teacher_targets_blank.json \
    --progress data/interim/slm/teacher_progress_blank.jsonl \
    --out data/interim/teacher_components_blank.parquet

echo "== 3. rebuild training data: rules + BOTH teacher sources, gold held out =="
./run.sh models.build_slm_dataset --system-version v2 \
  --teacher-components data/interim/teacher_components_2k.parquet \
                       data/interim/teacher_components_blank.parquet \
  --exclude-gold data/interim/gold_set_20260801.json data/interim/gold_set_20260803.json \
  --oversample-teacher 4

echo "== 4. gate: no gold record may appear in train or valid =="
PYTHONPATH=src .venv/bin/python - <<'PY'
import json, sys
gold = {}
for g in ('gold_set_20260801', 'gold_set_20260803'):
    for r in json.load(open(f'data/interim/{g}.json'))['records']:
        gold[' '.join((r.get('text') or '').split()).lower()] = r['pdb_id']
bad = 0
for name in ('train', 'valid'):
    hits = {gold[u] for line in open(f'data/interim/slm/{name}.jsonl')
            for u in [' '.join(json.loads(line)["messages"][1]["content"].split()).lower()]
            if u in gold}
    print(f"   {name}: {len(hits)} gold records leaked")
    bad += len(hits)
sys.exit(1 if bad else 0)
PY

echo "== 5. preflight =="
bash scripts/preflight.sh

echo "== 6. train: 32 layers, NOT the default 16 =="
./run.sh models.train_slm \
  --run-name    r1-parse-residual-smollm2-360m-round09 \
  --adapter-dir data/interim/slm/runs/r1-parse-residual-smollm2-360m-round09 \
  --num-layers 32 --lora-rank 16 --lora-dropout 0.05 \
  --batch-size 16 --max-seq-length 1024 \
  --iters 8000 --learning-rate 1e-4 --end-learning-rate 1e-5 --warmup 50 \
  --steps-per-report 10 --steps-per-eval 50

echo
echo "Next, in the morning: sweep checkpoints on BOTH gold sets and serve with v2."
echo "  ./run.sh models.apply_slm --checkpoint N --system-version v2 --records <gold>"
echo "  ./run.sh eval.gold_metrics --gold <gold> --slm-components ..."
echo "Round 06 stays shipped unless round 09 wins on F0.5."
