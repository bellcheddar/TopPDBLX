#!/usr/bin/env bash
# Unattended chain: wait for the teacher, score rounds 06/07/08, then train round 09.
# Started 2026-08-03. Log: data/interim/slm/tonight.log
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root
say() { printf '\n=== %s  %s ===\n' "$(date '+%H:%M:%S')" "$*"; }

# --- 1. wait for the teacher --------------------------------------------------------------
# **Polls for the output file, never for the process.** `until ! pgrep -f "..."` matches its own
# command line and waits forever; that cost this project over two hours on one occasion and 75
# minutes on another. `teacher_label` writes its parquet once, at the end, inside the manifest
# block, so the file appearing is an unambiguous completion signal.
say "waiting for the teacher to finish"
DEADLINE=$(( $(date +%s) + 6*3600 ))
until [ -f data/interim/teacher_components_blank.parquet ]; do
  if [ "$(date +%s)" -gt "$DEADLINE" ]; then
    echo "timed out after 6h waiting for teacher_components_blank.parquet"; exit 1
  fi
  sleep 60
done
say "teacher finished: $(wc -l < data/interim/slm/teacher_progress_blank.jsonl) records"

# --- 2. identification and grounding, all rounds against TODAY's lexicon -------------------
# The frozen benchmark fixes the records but identification asks whether an emitted name is IN
# the lexicon, and that has moved 502 -> 499 reagents with 92 ids retired. Round 06's published
# 88.99% and a figure measured now are not the same measurement, so every round is re-scored
# here, today, against one dictionary.
#
# --limit 500 caps the *validation* half, which only measures fidelity to the rules and is not in
# the round table. The 2,000 frozen records are always scored in full.
#
# v1 for all three: prompt v2 did not exist when any of them was trained.
for R in 06 07 08; do
  say "eval round $R (frozen, lexicon 0.9.2, prompt v1)"
  ./run.sh models.eval_slm --frozen --limit 500 --system-version v1 \
      --adapter-dir "data/interim/slm/runs/r1-parse-residual-smollm2-360m-round${R}" \
      --out "data/interim/slm/eval_round${R}_frozen_lex092.json"
done

say "identification and grounding, comparable across rounds"
PYTHONPATH=src .venv/bin/python - <<'PY'
import json, pathlib
for r in ("06", "07", "08"):
    p = pathlib.Path(f"data/interim/slm/eval_round{r}_frozen_lex092.json")
    if not p.exists():
        print(f"  round {r}: missing"); continue
    d = json.load(p.open())
    ident = d.get("identification_pct") or d.get("residual_identification_pct")
    ground = d.get("grounding_pct") or d.get("residual_grounding_pct")
    print(f"  round {r}: identification {ident}  grounding {ground}")
PY

# --- 3. train round 09 --------------------------------------------------------------------
say "launching round 09"
./RUN_ROUND09.sh
say "done"
