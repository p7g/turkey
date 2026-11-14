#!/bin/sh
# Time the HTTP/1 codec's head parser in tests/programs/http_codec/, stage 1
# and stage 2 together and stage 2 alone, and count what it allocates.
#
#   sh scripts/bench-http-codec.sh [--runs N] [--out DIR]
#
# Inputs, under DIR/inputs:
#
#   http-requests.txt, bigger.txt   rust-bakery/parser_benchmarks' HTTP heads
#                                   (55 and 275), fetched once and checked
#                                   against the hashes below
#   kittyhell.txt                   that benchmark's one large request
#   long-cookie.txt                 a request with a 4 KiB Cookie value
#
# The compiler is $TURKEY_BOOT, by default build/stages/stage3: today's source
# compiled by today's source, which `sh scripts/build.sh --fixpoint` leaves.
# A plain stage2 runs the bootstrap's code generator, so its timings measure
# the wrong compiler.
#
# For each input, hyperfine times `Bench time FILE ROUNDS` (stage 1 reading
# 64 KiB at a time from memory, then stage 2) and `Bench parse FILE ROUNDS`
# (stage 2 alone), with ROUNDS chosen so that one run takes a few tenths of a
# second; the time per head is the mean wall time over ROUNDS times the heads
# in the file. Startup and reading the file are in it, and are under a
# millisecond. Then a build with TURKEY_ALLOC_SITES=1 runs 1 and 11 rounds of
# bigger.txt, and the difference over 10 rounds of 275 heads is the
# allocations per head.
#
# Before timing, every head of every input must be accepted, and 20000
# mutations of heads and of chunked bodies must pass the fuzz in Fuzz.gob.
#
#   --runs N    hyperfine runs per measurement (default 10)
#   --out DIR   where everything goes (default build/bench-http-codec)

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

BOOT=${TURKEY_BOOT:-build/stages/stage3}
runs=10
out=build/bench-http-codec
while [ $# -gt 0 ]; do
    case $1 in
        --runs) runs=$2; shift 2 ;;
        --out) out=$2; shift 2 ;;
        *) echo "usage: $0 [--runs N] [--out DIR]" >&2; exit 2 ;;
    esac
done
case $BOOT in /*) ;; *) BOOT=$ROOT/$BOOT ;; esac
case $out in /*) ;; *) out=$ROOT/$out ;; esac
if [ ! -x "$BOOT" ]; then
    echo "bench-http-codec.sh: no compiler at $BOOT; run sh scripts/build.sh --fixpoint" >&2
    exit 2
fi
command -v hyperfine > /dev/null || { echo "bench-http-codec.sh: needs hyperfine" >&2; exit 2; }

mkdir -p "$out/inputs"
inputs=$out/inputs

upstream=https://raw.githubusercontent.com/rust-bakery/parser_benchmarks/master/http
for pair in \
    "http-requests.txt 423fb29299647d8045492f4ea7d4d36307c31408989e83a64b8594a4d2d7cbb7" \
    "bigger.txt 0d453dcc2fb1da7ac7e3fc2d4a036c5ad4d6b08e6d40ff799fcbee81f69ad7fa"
do
    set -- $pair
    if [ ! -f "$inputs/$1" ]; then
        curl -sSfL -o "$inputs/$1.part" "$upstream/$1"
        mv "$inputs/$1.part" "$inputs/$1"
    fi
    echo "$2  $inputs/$1" | shasum -a 256 -c - > /dev/null
done

printf '%s\r\n' \
    'GET /wp-content/uploads/2010/03/hello-kitty-darth-vader-pink.jpg HTTP/1.1' \
    'Host: www.kittyhell.com' \
    'User-Agent: Mozilla/5.0 (Macintosh; U; Intel Mac OS X 10.6; ja-JP-mac; rv:1.9.2.3) Gecko/20100401 Firefox/3.6.3 Pathtraq/0.9' \
    'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8' \
    'Accept-Language: ja,en-us;q=0.7,en;q=0.3' \
    'Accept-Encoding: gzip,deflate' \
    'Accept-Charset: Shift_JIS,utf-8;q=0.7,*;q=0.7' \
    'Keep-Alive: 115' \
    'Connection: keep-alive' \
    'Cookie: wp_ozh_wsa_visits=2; wp_ozh_wsa_visit_lasttime=xxxxxxxxxx; __utma=xxxxxxxxx.xxxxxxxxxx.xxxxxxxxxx.xxxxxxxxxx.xxxxxxxxxx.x; __utmz=xxxxxxxxx.xxxxxxxxxx.x.x.utmccn=(referral)|utmcsr=reader.livedoor.com|utmcct=/reader/|utmcmd=referral' \
    '' > "$inputs/kittyhell.txt"

chunk=Zm9vYmFyYmF6cXV4MDEyMzQ1Njc4OWFiY2RlZmdoaWprbG1ub3BxcnN0dXZ3eHl6
value=
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 \
         33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 61 62 63 64
do
    value=$value$chunk
done
printf '%s\r\n' \
    'GET /account HTTP/1.1' \
    'Host: example.com' \
    'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10.8; rv:15.0) Gecko/20100101 Firefox/15.0.1' \
    'Accept: */*' \
    "Cookie: session=$value" \
    '' > "$inputs/long-cookie.txt"

source=tests/programs/http_codec/Bench.gob
dir=$(dirname "$source")
(cd "$dir" && TURKEY_LIB="$ROOT/lib" "$BOOT" build -o "$out/bench" Bench.gob)
(cd "$dir" && TURKEY_LIB="$ROOT/lib" TURKEY_ALLOC_SITES=1 "$BOOT" build -o "$out/counted" Bench.gob \
    2> "$out/sites.txt")

for f in http-requests bigger kittyhell long-cookie; do
    "$out/bench" check "$inputs/$f.txt"
done
"$out/bench" fuzz 20000 1

results=$out/results.txt
: > "$results"
for pair in "http-requests 55 4000" "bigger 275 800" "kittyhell 1 200000" "long-cookie 1 100000"; do
    set -- $pair
    name=$1; heads=$2; rounds=$3
    for mode in time parse; do
        hyperfine -N --warmup 2 --runs "$runs" --export-json "$out/$name-$mode.json" \
            "$out/bench $mode $inputs/$name.txt $rounds" > /dev/null
        mean=$(sed -n 's/^ *"mean": \([0-9.e-]*\),$/\1/p' "$out/$name-$mode.json" | head -1)
        stddev=$(sed -n 's/^ *"stddev": \([0-9.e-]*\),$/\1/p' "$out/$name-$mode.json" | head -1)
        label=$([ "$mode" = time ] && echo "stage 1+2" || echo "stage 2")
        awk -v n="$name" -v p="$label" -v m="$mean" -v s="$stddev" -v h="$heads" -v r="$rounds" \
            'BEGIN { printf "%-14s %-10s %9.1f ns/head  +- %.1f\n", n, p, m * 1e9 / (h * r), s * 1e9 / (h * r) }' \
            | tee -a "$results"
    done
done

(
    cd "$out"
    for mode in time parse; do
        for r in 1 11; do
            ./counted "$mode" "inputs/bigger.txt" "$r" > /dev/null
            mv turkey-alloc-sites.bin "alloc-$mode-$r.bin"
        done
        before=$(python3 "$ROOT/scripts/alloc_sites.py" sites.txt "alloc-$mode-1.bin" --top 1 | head -1 \
                 | sed 's/.*direct allocations \([0-9,]*\);.*/\1/' | tr -d ,)
        after=$(python3 "$ROOT/scripts/alloc_sites.py" sites.txt "alloc-$mode-11.bin" --top 1 | head -1 \
                | sed 's/.*direct allocations \([0-9,]*\);.*/\1/' | tr -d ,)
        python3 "$ROOT/scripts/alloc_sites.py" sites.txt "alloc-$mode-11.bin" --top 25 > "alloc-$mode.txt"
        awk -v p="$mode" -v a="$before" -v b="$after" \
            'BEGIN { printf "allocations    %-10s %9.1f per head\n", p, (b - a) / 2750 }'
    done
) | tee -a "$results"
