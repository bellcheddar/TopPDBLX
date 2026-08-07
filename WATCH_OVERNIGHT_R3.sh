#!/usr/bin/env bash
cd "$(dirname "$0")"
LOG=data/interim/overnight_r3.log
say(){ printf '%s | %s\n' "$(date '+%H:%M:%S')" "$1"; }
last=""; changed=$(date +%s); beat=$(date +%s); STALL=3600
while true; do
  sleep 60; now=$(date +%s)
  if grep -q "OVERNIGHT COMPLETE" "$LOG" 2>/dev/null; then say "overnight: COMPLETE"; break; fi
  if ! pgrep -f "[O]VERNIGHT_R3.sh" >/dev/null; then
    say "overnight: chain exited without completing — check the logs"; break; fi
  if grep -qE "Traceback|Killed|out of memory" data/interim/r3.log data/interim/r4.log 2>/dev/null; then
    say "overnight: ERROR — $(grep -hE 'Traceback|Killed|out of memory' data/interim/r3.log data/interim/r4.log | tail -1 | cut -c1-120)"; break; fi
  cur=$(cat data/interim/r3.log data/interim/r4.log 2>/dev/null | tr '\r' '\n' | grep -oE "[0-9]+/2699 \[" | tail -1)
  [ "$cur" != "$last" ] && { last=$cur; changed=$now; }
  if [ $((now-beat)) -ge 1800 ]; then
    say "overnight: alive ${cur:-starting}"
    cat data/interim/r3.log data/interim/r4.log 2>/dev/null | tr '\r' '\n' | grep -E "coverage@k" | tail -1 | sed 's/^/           /'
    beat=$now
  fi
  if [ $((now-changed)) -ge $STALL ]; then say "overnight: NO PROGRESS FOR 60 MIN at ${cur:-?}"; changed=$now; fi
done
