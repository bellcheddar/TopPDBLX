#!/usr/bin/env bash
# Apple's background metadata/media analysers compete with a long GPU run and launchd respawns
# each one seconds after a kill. This holds their duty cycle down for the length of a run rather
# than stopping them; the durable fix is `sudo mdutil -a -i off` plus pausing Photos analysis, in
# a real Terminal, because sudo has no TTY here.
#
# Deliberately narrow. Only background *analysers*, only above a CPU threshold, and never
# fileproviderd -- signalling that freezes every I/O under Documents (iCloud) and hangs the shell.
THRESH=${1:-40}
PATTERN='Metadata\.framework|mds_stores|MediaAnalysis|photoanalysisd|photolibraryd|AppleNeuralEngine|mdworker'
while true; do
  ps -eo pid,pcpu,args | \
    awk -v t="$THRESH" -v p="$PATTERN" \
        '$2+0 > t && $0 ~ p && $0 !~ /fileproviderd/ && $0 !~ /awk/ {print $1, $2}' | \
  while read -r pid cpu; do
    kill "$pid" 2>/dev/null && printf '%s | reaped %s at %s%% cpu\n' "$(date '+%H:%M:%S')" "$pid" "$cpu"
  done
  sleep 20
done
