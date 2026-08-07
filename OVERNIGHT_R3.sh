#!/usr/bin/env bash
# Two boundary experiments back to back, so the machine is not idle overnight.
#   03: soft targets gated to proteins with >=10 deposited constructs, hard labels elsewhere
#   04: no soft targets at all, round 01's recipe run for 6 epochs instead of 3
# Both score against the hard label on the same validation split, so they are directly
# comparable to each other and to round 01 (MCC 0.694, coverage@3 40.3%).
cd "$(dirname "$0")"
say(){ printf '%s | %s\n' "$(date '+%H:%M:%S')" "$1"; }

say "round 03: soft targets gated at >=10 constructs, 6 epochs"
./run.sh model.train_boundary --epochs 6 --token-budget 6144 \
    --soft-targets --soft-min-constructs 10 \
    --out-dir data/interim/boundary_model_r3 >> data/interim/r3.log 2>&1
say "round 03 done: $(tr '\r' '\n' < data/interim/r3.log | grep 'best MCC' | tail -1 | xargs)"

say "round 04: binary targets, 6 epochs (round 01 recipe, twice the schedule)"
./run.sh model.train_boundary --epochs 6 --token-budget 6144 \
    --out-dir data/interim/boundary_model_r4 >> data/interim/r4.log 2>&1
say "round 04 done: $(tr '\r' '\n' < data/interim/r4.log | grep 'best MCC' | tail -1 | xargs)"
say "OVERNIGHT COMPLETE"
