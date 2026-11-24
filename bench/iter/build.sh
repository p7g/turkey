#!/bin/sh
# Builds every benchmark twice: build/bi/NAME, and build/bi/NAME.gc with --gc-stats.
# Usage: sh bench/iter/build.sh COMPILER
set -u
cc=${1:-build/s2/turkey}
mkdir -p build/bi
for f in bench/iter/*.gob; do
	n=$(basename "$f" .gob)
	TURKEY_LIB=lib "$cc" build -o "build/bi/$n" "$f" || echo "FAIL $n"
	TURKEY_LIB=lib "$cc" build --gc-stats -o "build/bi/$n.gc" "$f" || echo "FAIL $n.gc"
done
