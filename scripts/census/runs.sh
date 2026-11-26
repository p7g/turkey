#!/bin/sh
# Usage: runs.sh S MODE...  where MODE is native-stats, wasm-stats, native-plain, wasm-plain
set -u
S=$1; shift
cd /Users/patrickgingras/code/turkey-lite/.claude/worktrees/tix-724-throw-1e33d8
B=$S/b; L=$S/logs; W=$HOME/.wasmtime/bin/wasmtime
export TURKEY_LIB=lib
for mode in "$@"; do
for sched in default target; do
  if [ $sched = target ]; then T=2500000000; else T=0; fi
  case $mode in
  native-stats)
    TURKEY_GC_TARGET_BYTES=$T /usr/bin/time -l $B/stats build -o $S/out/n src/Main.gob > $L/$mode-$sched.log 2>&1 ;;
  native-plain)
    for r in 1 2 3; do
    TURKEY_GC_TIMELOG=1 TURKEY_GC_TARGET_BYTES=$T /usr/bin/time -l $B/plain build -o $S/out/n src/Main.gob > $L/$mode-$sched-$r.log 2>&1
    done ;;
  wasm-stats)
    /usr/bin/time -l $W run --dir lib --dir src --dir $S/out::out --env TURKEY_LIB=lib --env TURKEY_GC_TARGET_BYTES=$T $B/stats.wasm build --target arm64-darwin -o out/w src/Main.gob > $L/$mode-$sched.log 2>&1 ;;
  wasm-plain)
    /usr/bin/time -l $W run --dir lib --dir src --dir $S/out::out --env TURKEY_LIB=lib --env TURKEY_GC_TIMELOG=1 --env TURKEY_GC_TARGET_BYTES=$T $B/plain.wasm build --target arm64-darwin -o out/w src/Main.gob > $L/$mode-$sched.log 2>&1 ;;
  esac
  echo "$mode $sched exit $?"
done
done
