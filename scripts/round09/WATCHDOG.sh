#!/usr/bin/env bash
# Supervises tonight end to end: teacher -> evals -> training.
# Detects a stalled stage and restarts it. Every line on stdout is an event.
cd "$(dirname "$0")/../.."   # repo root
emit(){ printf '%s | %s\n' "$(date '+%H:%M:%S')" "$*"; }

RUNS=data/interim/slm/runs
R09=$RUNS/r1-parse-residual-smollm2-360m-round09
TEACH_PAT="toppdblx.*teacher_label"
TEACH_START='nohup ./run.sh models.teacher_label --records data/interim/teacher_targets_blank.json --progress data/interim/slm/teacher_progress_blank.jsonl --out data/interim/teacher_components_blank.parquet >> data/interim/slm/teacher_blank.log 2>&1 &'

# **Stall thresholds are set above the worst observed batch, not above the typical one.**
# This run has produced single batches of 1,332 s while healthy. Restarting a slow-but-advancing
# job throws away work and achieves nothing, so the threshold asks "has it produced ANYTHING in
# this window", where the window is several times the worst legitimate gap.
supervise() {                       # name  progress_cmd  pattern  start_cmd  stall_s  done_cmd
  local name=$1 progress=$2 pattern=$3 start=$4 stall=$5 done_cmd=$6
  local last="" changed=$(date +%s) restarts=0 beat=$(date +%s)
  while true; do
    if eval "$done_cmd" 2>/dev/null; then emit "$name: COMPLETE"; return 0; fi
    local now; now=$(date +%s)
    if ! pgrep -f "$pattern" >/dev/null 2>&1; then
      restarts=$((restarts+1))
      if [ "$restarts" -gt 5 ]; then emit "$name: DIED and 5 restarts exhausted — giving up"; return 1; fi
      emit "$name: process gone, restarting (attempt $restarts)"
      eval "$start"; sleep 90; changed=$(date +%s); last=""; continue
    fi
    local cur; cur=$(eval "$progress" 2>/dev/null | tr -d ' ')
    if [ -n "$cur" ] && [ "$cur" != "$last" ]; then
      last=$cur; changed=$now
    elif [ $((now-changed)) -gt "$stall" ]; then
      restarts=$((restarts+1))
      emit "$name: STALLED at '$cur' for $(( (now-changed)/60 ))m — killing and restarting (attempt $restarts)"
      pkill -f "$pattern" 2>/dev/null; sleep 25
      pgrep -f "$pattern" >/dev/null && { pkill -9 -f "$pattern"; sleep 10; }
      eval "$start"; sleep 90; changed=$(date +%s); last=""
    fi
    if [ $((now-beat)) -ge 1800 ]; then emit "$name: alive, progress '$cur'"; beat=$now; fi
    sleep 60
  done
}

emit "watchdog started"

# --- stage 1: the teacher. 40-minute stall window; worst healthy batch seen is 22 minutes. ---
supervise "teacher" 'wc -l < data/interim/slm/teacher_progress_blank.jsonl' \
          "$TEACH_PAT" "$TEACH_START" 2400 '[ -f data/interim/teacher_components_blank.parquet ]' \
  || { emit "FATAL: teacher could not be kept alive"; exit 1; }

# --- stage 2: evals. Short enough to just run; a failure is reported, not retried. ---
for R in 06 07 08; do
  emit "eval round $R starting"
  if ./run.sh models.eval_slm --frozen --limit 500 --system-version v1 \
        --adapter-dir "$RUNS/r1-parse-residual-smollm2-360m-round${R}" \
        --out "data/interim/slm/eval_round${R}_frozen_lex092.json" \
        >> data/interim/slm/evals.log 2>&1; then
    emit "eval round $R done"
  else
    emit "eval round $R FAILED — see data/interim/slm/evals.log, continuing"
  fi
done

# --- stage 3: training, behind the gates in RUN_ROUND09.sh ---
emit "launching RUN_ROUND09.sh (re-parse, rebuild, gold gate, token gate, preflight, train)"
nohup ./RUN_ROUND09.sh >> data/interim/slm/round09.log 2>&1 &
sleep 120
# Checkpoints land every 100 iterations, roughly every six minutes at this scale, so a 45-minute
# window without a new one means it is genuinely stuck rather than merely slow.
supervise "training" "ls $R09/*.safetensors 2>/dev/null | wc -l" \
          "mlx_lm.lora|RUN_ROUND09" \
          'nohup ./RUN_ROUND09.sh >> data/interim/slm/round09.log 2>&1 &' \
          2700 "[ -f $R09/adapters.safetensors ]" \
  || { emit "FATAL: training could not be kept alive"; exit 1; }

emit "ALL DONE — teacher, evals and round 09 training complete"
