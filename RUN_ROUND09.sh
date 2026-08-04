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

echo "== 2b. re-parse: lexicon 0.8.0 de-fragmented PEG MME, so targets must be rebuilt =="
./run.sh parse.run_parser
./run.sh assign.classify --slm-components data/interim/slm_components.parquet

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

echo "== 4b. gate: nothing may exceed the training sequence length =="
# A training example longer than --max-seq-length is silently TRUNCATED, which teaches the model
# to emit unterminated JSON. The docstring in train_slm.py sized this from *characters*; the real
# question is tokens. Measured 2026-08-03 the longest example was 993 tokens against a 1024 cap:
# safe, but 3% of headroom, and tonight's rebuild adds rows that were not in that measurement.
PYTHONPATH=src .venv/bin/python - <<'PY'
import json, sys
from transformers import AutoTokenizer
CAP = 1024
tok = AutoTokenizer.from_pretrained("HuggingFaceTB/SmolLM2-360M-Instruct")
worst = 0
for line in open("data/interim/slm/train.jsonl"):
    messages = json.loads(line)["messages"]
    if len(json.dumps(messages)) < 1800:      # cheap filter; only long rows can threaten the cap
        continue
    # **Render to text, then encode.** `apply_chat_template(tokenize=True)` returns a
    # BatchEncoding, which is NOT a dict subclass in transformers 5.x, so `len()` on it counts
    # its two keys and every example measures "2 tokens". The gate passed trivially and
    # validated nothing -- worse than having no gate, because it reported success.
    n = len(tok(tok.apply_chat_template(messages, tokenize=False))["input_ids"])
    worst = max(worst, n)
print(f"   longest training example: {worst} tokens against a cap of {CAP}")
if worst > CAP:
    print("   ABORT: examples would be truncated mid-answer")
    sys.exit(1)
if worst > CAP * 0.97:
    print("   WARNING: under 3% headroom")
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
echo "Next: ./SCORE_ROUND09.sh   -- identification, grounding, the gold sweep and components"
echo
echo "Then, in the morning: sweep checkpoints on BOTH gold sets and serve with v2."
echo "  ./run.sh models.apply_slm --checkpoint N --system-version v2 --records <gold>"
echo "  ./run.sh eval.gold_metrics --gold <gold> --slm-components ..."
echo "Round 06 stays shipped unless round 09 wins on F0.5."
