#!/usr/bin/env bash
# Supervises R3 boundary training. Progress signal is the batch counter in the log, which moves
# while healthy; never CPU%, which reads low for an MPS-bound process that is working.
cd "$(dirname "$0")"
LOG=data/interim/boundary_train.log
STALL=1800
say(){ printf '%s | %s\n' "$(date '+%H:%M:%S')" "$1"; }
last=""; changed=$(date +%s); beat=$(date +%s)
while true; do
  sleep 30; now=$(date +%s)
  if grep -q "roadmap criteria" "$LOG" 2>/dev/null; then
    say "training: COMPLETE — $(grep -h 'best MCC' "$LOG" | tail -1 | xargs)"; break; fi
  if grep -qE "Traceback|Error|Killed|out of memory" "$LOG" 2>/dev/null; then
    say "training: FAILED — $(grep -E 'Traceback|Error|Killed' "$LOG" | tail -1 | cut -c1-140)"; break; fi
  if ! pgrep -f "[t]rain_boundary" >/dev/null; then
    say "training: process gone without a verdict — check the log"; break; fi
  cur=$(tr '\r' '\n' < "$LOG" 2>/dev/null | grep -oE "[0-9]+/[0-9]+ \[" | tail -1)
  [ "$cur" != "$last" ] && { last=$cur; changed=$now; }
  if [ $((now-beat)) -ge 1800 ]; then
    ep=$(tr '\r' '\n' < "$LOG" | grep -oE "epoch [0-9]+:" | tail -1)
    say "training: alive, ${ep:-starting} ${cur:-?}"; beat=$now
    tr '\r' '\n' < "$LOG" | grep -E "^  epoch [0-9]+: loss" | tail -1 | sed 's/^/           /'
  fi
  if [ $((now-changed)) -ge $STALL ]; then
    say "training: NO PROGRESS FOR $((STALL/60)) MIN at ${cur:-?} — intervene"; changed=$now; fi
done
