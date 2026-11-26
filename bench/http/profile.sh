#!/bin/sh
# Profile HTTP servers under wrk on Linux, with perf and strace.
#
#   sh bench/http/profile.sh [IMPL ...]     (default: turkey go)
#
# For each server, after `sh bench/http/run.sh build`: start it with $CORES
# threads (default 2), warm it up, then
#
#   * record 10 s of call stacks with `perf record --call-graph fp` while
#     wrk, with $LOAD_THREADS threads (default 2), drives 64 keep-alive
#     connections, and report the functions by their own samples and by
#     their callees' too. perf samples on the `cpu-clock` timer rather than
#     a hardware counter, which a virtual machine may not expose;
#   * count its system calls with `perf stat` on every `syscalls:sys_enter_*`
#     tracepoint over 8 s of the same load, and divide by the requests wrk
#     completed in that time.
#
# The counts come from tracepoints rather than strace because strace slows
# the server several times over, and a slow server changes what it calls: a
# read that would have waited finds its data already there, so the waits,
# and the polling calls that make them, drop out of the count. The
# tracepoints cost the server a few percent. Everything goes to
# build/bench/http/profile/. Needs perf (`linux-tools`) and sudo to let perf
# attach.

set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
OUT=$ROOT/build/bench/http
P=$OUT/profile
CORES=${CORES:-2}
LOAD_THREADS=${LOAD_THREADS:-2}
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
    wrk -t"$LOAD_THREADS" -c64 -d3s "$url" > /dev/null

    wrk -t"$LOAD_THREADS" -c64 -d13s "$url" > "$P/$impl.wrk-perf.txt" &
    load=$!
    sleep 1
    sudo perf record -q -e cpu-clock -F 999 --call-graph fp -o "$P/$impl.perf.data" -p $SERVER -- sleep 10
    wait $load
    sudo chown "$(id -u)" "$P/$impl.perf.data"
    perf report -i "$P/$impl.perf.data" --stdio --no-children --sort symbol --percent-limit 0.5 \
        > "$P/$impl.perf-self.txt" 2> /dev/null
    perf report -i "$P/$impl.perf.data" --stdio --children --sort symbol --percent-limit 2 -g none \
        > "$P/$impl.perf-children.txt" 2> /dev/null

    wrk -t"$LOAD_THREADS" -c64 -d10s "$url" > "$P/$impl.wrk-syscalls.txt" &
    load=$!
    sleep 1
    sudo perf stat -x, -e 'syscalls:sys_enter_*' -p $SERVER -o "$P/$impl.syscalls.csv" -- sleep 8
    sudo chown "$(id -u)" "$P/$impl.syscalls.csv"
    wait $load
    # wrk ran 10 s and perf watched 8 s of the steady middle: take those 8 s
    # at wrk's average rate.
    requests=$(awk '/Requests\/sec/ { print int($2 * 8) }' "$P/$impl.wrk-syscalls.txt")
    awk -F, -v n="$requests" '$1 ~ /^[0-9]+$/ && $1 > 0 {
            sub(/^syscalls:sys_enter_/, "", $3)
            total += $1
            printf "%-16s %6.2f per request\n", $3, $1 / n
        }
        END { printf "%-16s %6.2f per request, of about %d requests\n", "total", total / n, n }' \
        "$P/$impl.syscalls.csv" | sort -k2 -rn > "$P/$impl.syscalls-per-request.txt"

    kill $SERVER 2> /dev/null || true
    wait $SERVER 2> /dev/null || true
    grep Requests/sec "$P/$impl.wrk-perf.txt" | sed "s/^/$impl under perf: /"
done
