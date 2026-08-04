#!/usr/bin/env bash
# Supervises stage 3. Progress signal is the line count of the resumable progress file, which
# changes while healthy; never CPU%, which reads ~0 for a GPU-bound MLX process.
cd "$(dirname "$0")"
PROG=data/interim/slm/apply_progress_r09.jsonl
OUT=data/interim/slm_components_r09.parquet
LOG=data/interim/slm/stage3_chain.log
STALL=2700          # 45 min: above the 34-min iCloud write block seen earlier today
say(){ printf '%s | %s\n' "$(date '+%H:%M:%S')" "$1"; }
last=-1; changed=$(date +%s); beat=$(date +%s)
while true; do
  sleep 30; now=$(date +%s)
  if grep -q "STAGE 3 COMPLETE" "$LOG" 2>/dev/null; then say "stage 3: COMPLETE"; break; fi
  if grep -qE "FAILED" "$LOG" 2>/dev/null; then say "stage 3: FAILED — $(grep FAILED "$LOG" | tail -1)"; break; fi
  if ! pgrep -f "[S]TAGE3_ROUND09.sh" >/dev/null; then say "stage 3: script exited without a completion line — check stage3.log"; break; fi
  cur=$(wc -l < "$PROG" 2>/dev/null | tr -d ' '); cur=${cur:-0}
  if [ "$cur" != "$last" ]; then last=$cur; changed=$now; fi
  if [ $((now-beat)) -ge 1800 ]; then say "stage 3: alive, $cur records generated"; beat=$now; fi
  if [ $((now-changed)) -ge $STALL ] && [ ! -f "$OUT" ]; then
    say "stage 3: STALLED at $cur records for $((STALL/60)) min — terminating apply_slm so it resumes"
    pkill -TERM -f "[a]pply_slm"; changed=$now
  fi
done
