#!/bin/sh
set -eu
cd /Users/patrickgingras/code/turkey-lite/.claude/worktrees/tix-724-throw-1e33d8
O=$1
sh scripts/build.sh --out $O
export TURKEY_LIB=lib
$O/turkey build -o $O/plain src/Main.gob
$O/turkey build --gc-stats -o $O/stats src/Main.gob
$O/plain build --target wasm32-wasi -o $O/plain.wasm src/Main.gob
$O/plain build --gc-stats --target wasm32-wasi -o $O/stats.wasm src/Main.gob
ls -la $O
