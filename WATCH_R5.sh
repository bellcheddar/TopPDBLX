#!/usr/bin/env bash
cd "$(dirname "$0")"
LOG=data/interim/r5.log
say(){ printf '%s | %s\n' "$(date '+%H:%M:%S')" "$1"; }
last=""; changed=$(date +%s); beat=$(date +%s); STALL=3600
while true; do
  sleep 60; now=$(date +%s)
  if grep -q "roadmap criteria" "$LOG" 2>/dev/null; then
    say "round 05: COMPLETE — $(grep -h 'best MCC' "$LOG" | tail -1 | xargs)"; break; fi
  if grep -qE "Traceback|Killed|out of memory|RuntimeError" "$LOG" 2>/dev/null; then
    say "round 05: FAILED — $(grep -hE 'Traceback|Killed|out of memory|RuntimeError' "$LOG" | tail -1 | cut -c1-140)"; break; fi
  if ! pgrep -f "[t]rain_boundary" >/dev/null; then
    say "round 05: process gone without a verdict"; break; fi
  cur=$(tr '\r' '\n' < "$LOG" 2>/dev/null | grep -oE "[0-9]+/[0-9]+ \[" | tail -1)
  [ "$cur" != "$last" ] && { last=$cur; changed=$now; }
  if [ $((now-beat)) -ge 1800 ]; then
    say "round 05: alive ${cur:-starting}"
    tr '\r' '\n' < "$LOG" | grep -E "^  epoch [0-9]+: loss" | tail -1 | sed 's/^/           /'
    tr '\r' '\n' < "$LOG" | grep -E "coverage@k" | tail -1 | sed 's/^/           /'
    beat=$now; fi
  if [ $((now-changed)) -ge $STALL ]; then say "round 05: NO PROGRESS FOR 60 MIN at ${cur:-?}"; changed=$now; fi
done
