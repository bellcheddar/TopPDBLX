#!/usr/bin/env bash
# Supervises SCORE_ROUND09.sh. Same two lessons as WATCH_TRAINING.sh:
#   - the done-condition must not be satisfiable mid-run
#   - silence is not success, so failure signatures are watched as well as progress
LOG=data/interim/slm/score_round09.log
emit(){ printf '%s | %s\n' "$(date '+%H:%M:%S')" "$1"; }
stall=2700          # iCloud can block a write for 34 min; above the worst healthy gap
last_size=0; last_change=$(date +%s); beat=$(date +%s)
while true; do
  sleep 20
  now=$(date +%s)
  # finished, either way
  if ! pgrep -f "[S]CORE_ROUND09.sh" >/dev/null; then
    if grep -qE "Traceback|Error|error:|No such file|SystemExit|Killed" "$LOG" 2>/dev/null; then
      emit "scoring: EXITED WITH ERRORS — $(grep -E 'Traceback|Error|error:|No such file|SystemExit' "$LOG" | tail -1 | cut -c1-160)"
    else
      emit "scoring: COMPLETE"
    fi
    break
  fi
  size=$(wc -c < "$LOG" 2>/dev/null || echo 0)
  if [ "$size" != "$last_size" ]; then last_size=$size; last_change=$now; fi
  # surface each scored result as it lands
  if [ $((now-beat)) -ge 900 ]; then
    cur=$(grep -c "^--- round 09 @ TRUE iter" "$LOG" 2>/dev/null || echo 0)
    emit "scoring: alive, $cur/20 gold evaluations done"
    beat=$now
  fi
  if [ $((now-last_change)) -ge $stall ]; then
    emit "scoring: NO OUTPUT FOR $((stall/60)) MIN — likely blocked, intervene"
    last_change=$now
  fi
done
