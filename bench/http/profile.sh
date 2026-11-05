#!/bin/sh
# Profile HTTP servers under wrk on Linux, with perf and strace.
#
#   sh bench/http/profile.sh [IMPL ...]     (default: turkey go)
#
# For each server, after `sh bench/http/run.sh build`: start it with $CORES
# threads (default 2), warm it up, then
#
#   * record 10 s of call stacks with `perf record --call-graph fp` while
#     wrk drives 64 keep-alive connections, and report the functions by
#     their own samples and by their callees' too;
#   * count its system calls with `strace -c -f` over 8 s of the same load,
#     and divide by the requests wrk completed in that time.
#
# strace slows the server several times over, so only its counts per
# request mean anything, not its times. Everything goes to
# build/bench/http/profile/. Needs perf (`linux-tools`), strace, and sudo to
# let perf and strace attach.

set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
OUT=$ROOT/build/bench/http
P=$OUT/profile
CORES=${CORES:-2}
PORT=18090
mkdir -p "$P"

# Starts server IMPL on $PORT and sets $SERVER to its pid.
start() {
    case $1 in
        turkey) TURKEY_WORKERS=$CORES "$OUT/turkey-server" $PORT > "$P/server.log" 2>&1 & ;;
        go) GOMAXPROCS=$CORES "$OUT/go-server" $PORT > "$P/server.log" 2>&1 & ;;
        rust) TOKIO_WORKER_THREADS=$CORES "$OUT/rust-server" $PORT > "$P/server.log" 2>&1 & ;;
        *) echo "profile.sh: no server $1" >&2; exit 2 ;;
    esac
    SERVER=$!
    for _ in $(seq 100); do
        if curl -s -o /dev/null "http://127.0.0.1:$PORT/hello"; then return; fi
        sleep 0.1
    done
    echo "profile.sh: $1 did not start" >&2
    exit 1
}

url=http://127.0.0.1:$PORT/hello
for impl in ${*:-turkey go}; do
    echo "== $impl"
    start "$impl"
    wrk -t2 -c64 -d3s "$url" > /dev/null

    wrk -t2 -c64 -d13s "$url" > "$P/$impl.wrk-perf.txt" &
    load=$!
    sleep 1
    sudo perf record -q -F 999 --call-graph fp -o "$P/$impl.perf.data" -p $SERVER -- sleep 10
    wait $load
    sudo chown "$(id -u)" "$P/$impl.perf.data"
    perf report -i "$P/$impl.perf.data" --stdio --no-children --sort symbol --percent-limit 0.5 \
        > "$P/$impl.perf-self.txt" 2> /dev/null
    perf report -i "$P/$impl.perf.data" --stdio --children --sort symbol --percent-limit 2 -g none \
        > "$P/$impl.perf-children.txt" 2> /dev/null

    wrk -t2 -c64 -d10s "$url" > "$P/$impl.wrk-strace.txt" &
    load=$!
    sleep 1
    sudo timeout -s INT 8 strace -c -f -p $SERVER -o "$P/$impl.strace.txt" || true
    wait $load
    # wrk ran 10 s, strace watched 8 of them: count the requests in those 8.
    requests=$(awk '/requests in/ { print int($1 * 0.8) }' "$P/$impl.wrk-strace.txt")
    echo "requests while traced: about $requests" >> "$P/$impl.strace.txt"
    awk -v n="$requests" '$NF ~ /^[a-z_0-9]+$/ && $4 ~ /^[0-9]+$/ { printf "%-16s %6.2f per request\n", $NF, $4 / n }' \
        "$P/$impl.strace.txt" > "$P/$impl.syscalls-per-request.txt"

    kill $SERVER 2> /dev/null || true
    wait $SERVER 2> /dev/null || true
    grep Requests/sec "$P/$impl.wrk-perf.txt" | sed "s/^/$impl under perf: /"
done
