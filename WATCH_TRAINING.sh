#!/usr/bin/env bash
# Supervises round 09 training only. Replaces the watchdog that exited on a false completion.
cd "$(dirname "$0")"
emit(){ printf '%s | %s\n' "$(date '+%H:%M:%S')" "$*"; }
LOG=data/interim/slm/round09.log
# **The resumed run reports 1..5100, not 2901..8000.** mlx-lm reloads weights only, so its
# iteration counter restarts; --iter-offset is for our reporting, not for what it prints.
TARGET=5100
OFFSET=2900

# **The done-condition must not be satisfiable mid-run.** The previous check was
# `[ -f adapters.safetensors ]`, and mlx_lm writes that file at the FIRST checkpoint — so it
# fired at iteration 100 of 8,000, the supervisor declared victory and exited, and an 8-hour
# job ran unwatched. The completion signal is the final iteration appearing in the log.
done_check(){ grep -q "Iter ${TARGET}:" "$LOG" 2>/dev/null; }
# Progress is the highest iteration reached, which only ever increases.
progress(){ grep -oE "^Iter [0-9]+:" "$LOG" 2>/dev/null | grep -oE "[0-9]+" | sort -n | tail -1; }

last=""; changed=$(date +%s); beat=$changed; restarts=0
emit "training supervisor started at reported iteration $(progress) (true $((OFFSET+$(progress 2>/dev/null || echo 0))))"
while true; do
  if done_check; then emit "training: reached iteration $TARGET — COMPLETE"; break; fi
  now=$(date +%s)
  if ! pgrep -f "RUN_ROUND09|mlx_lm.lora" >/dev/null 2>&1; then
    if done_check; then emit "training: COMPLETE"; break; fi
    restarts=$((restarts+1))
    [ "$restarts" -gt 3 ] && { emit "training: died and 3 restarts exhausted — STOPPING"; exit 1; }
    emit "training: process gone at iteration $(progress) without finishing — restarting ($restarts)"
    nohup ./RUN_ROUND09.sh >> "$LOG" 2>&1 &
    sleep 180; changed=$(date +%s); last=""; continue
  fi
  cur=$(progress)
  if [ -n "$cur" ] && [ "$cur" != "$last" ]; then last=$cur; changed=$now
  elif [ $((now-changed)) -gt 2700 ]; then
    restarts=$((restarts+1))
    [ "$restarts" -gt 3 ] && { emit "training: stalled and 3 restarts exhausted — STOPPING"; exit 1; }
    emit "training: STALLED at iteration $cur for $(( (now-changed)/60 ))m — restarting ($restarts)"
    pkill -f "mlx_lm.lora"; pkill -f "RUN_ROUND09"; sleep 30
    nohup ./RUN_ROUND09.sh >> "$LOG" 2>&1 &
    sleep 180; changed=$(date +%s); last=""
  fi
  if [ $((now-beat)) -ge 1800 ]; then
    emit "training: alive at ${cur:-?}/$TARGET reported, true $((OFFSET+${cur:-0}))/8000"; beat=$now
  fi
  sleep 60
done
