#!/bin/sh
# Benchmark Turkey's HTTP server and client against Go net/http, Rust
# hyper/reqwest, Node http, nginx and curl, and for context Python (ASGI on
# uvicorn, WSGI on gunicorn), Ruby (Rack on Puma) and Java (the JDK's
# HttpServer on virtual threads), on this machine.
#
#   sh bench/http/run.sh [build] [SCENARIO ...]
#
# `build` builds every program into build/bench/http and stops. With no
# scenario, every scenario runs:
#
#   keepalive   GET /hello over keep-alive, at each of $KEEPALIVE_CONNS
#               connections (default 1, 64 and 256)
#   close       GET /hello, a new connection per request, 64 at once, driven
#               by go/connload (its comment says why wrk cannot)
#   download    GET /1m, a 1 MiB response, 64 connections
#   upload      POST /upload of 1 MiB, read and discarded, 64 connections
#   stream      GET /stream, 1 MiB chunked, 64 connections
#   tls         GET /hello over TLS: keep-alive by wrk (tls_keepalive), and
#               a handshake per request by connload (tls_handshake)
#   rate        GET /hello over 64 keep-alive connections at each of $RATES
#               requests per second (default 5000 and 40000), driven by oha
#               with its correction for coordinated omission: every server
#               does the same work, so its CPU time and RSS compare directly
#   client      each client against nginx: GET /hello, 1 and 64 at once
#   idle        server RSS holding 10,000 idle keep-alive connections
#
# Every configuration runs once for each core count in $CORES (default
# "2 8"), and every server gets that many: TURKEY_WORKERS, GOMAXPROCS,
# TOKIO_WORKER_THREADS, Java's -XX:ActiveProcessorCount, and as many
# processes for the servers whose language runs one thread at a time --
# Node's cluster workers, uvicorn's and gunicorn's workers, Puma's -- and
# nginx's. gunicorn's and Puma's workers have 16 threads each, for requests
# waiting on I/O. Two counts far apart show how each server scales, which
# one count cannot: a server can lead at 2 cores and fall behind at 8. wrk drives the
# servers with $LOAD_THREADS threads (default 4), connload with
# GOMAXPROCS=$CONNLOAD_PROCS (default 8), since a connection costs it more.
# macOS cannot pin a thread to a core, so the split is by count only, on a
# machine with more cores than the two together; the script warns when the
# largest count in $CORES and $LOAD_THREADS add up to more than it has.
#
# Each configuration runs $REPS times (default 3) for $SECONDS_PER_RUN
# seconds (default 10), each after a fresh start of the server and a
# 2-second warm-up that is not recorded. Before each, the machine must be
# quiet: at least $IDLE_PCT percent idle (default 85) over one second of
# `top`, checked every 60 seconds for at most 30 minutes, after which the run
# goes ahead marked noisy. Peak RSS is the largest sum, over the server's
# processes, of `ps` samples taken every half second during the run. CPU
# time is the server's user and system time, over all its processes,
# during the measured run alone.
#
# Results go to build/bench/http/results.tsv, one line per run; summarize.py
# prints the median and the range of each configuration.
#
# Runs on macOS and on Linux; .github/workflows/http-bench.yml runs it on an
# arm64 Linux runner. Needs: go, cargo, node, the Turkey compiler at
# $TURKEY_BOOT (default build/stages/stage3, from `sh scripts/build.sh
# --fixpoint`: a stage2 runs the bootstrap's older code generator), OpenSSL
# 3 (Homebrew's openssl@3 on a Mac), and wrk, oha, nginx and curl on PATH; on
# Linux, GNU time too. The context servers need python3 with uvicorn,
# uvloop, httptools and gunicorn, ruby with puma, and a JDK (21 or later);
# $SERVERS lists the servers to run, all of them by default.

set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
HERE=$ROOT/bench/http
OUT=$ROOT/build/bench/http
BOOT=${TURKEY_BOOT:-build/stages/stage3}
CORES=${CORES:-"2 8"}
LOAD_THREADS=${LOAD_THREADS:-4}
CONNLOAD_PROCS=${CONNLOAD_PROCS:-8}
REPS=${REPS:-3}
SECONDS_PER_RUN=${SECONDS_PER_RUN:-10}
IDLE_PCT=${IDLE_PCT:-85}
RATES=${RATES:-"5000 40000"}
KEEPALIVE_CONNS=${KEEPALIVE_CONNS:-"1 64 256"}
PORT=18080
CC=${TURKEY_CC:-cc}
RESULTS=$OUT/results.tsv
servers=${SERVERS:-"turkey go rust node nginx asgi wsgi ruby java"}
OS=$(uname -s)
# The system's OpenSSL on Linux. On a Mac, Homebrew's, since the system's
# libcrypto ends any process that loads it.
if [ "$OS" = Darwin ]; then
    ossl=$(brew --prefix openssl@3)
    LIBS="-L$ossl/lib -lssl -lcrypto"
    OPENSSL_BIN=$ossl/bin/openssl
else
    LIBS="-lssl -lcrypto -lm"
    OPENSSL_BIN=openssl
fi
mkdir -p "$OUT"

build() {
    echo "building into $OUT"
    for p in Server Client; do
        "$BOOT" build -o "$OUT/turkey-$p.o" "bench/http/turkey/$p.gob"
        $CC -o "$OUT/turkey-$(echo $p | tr A-Z a-z)" "$OUT/turkey-$p.o" $LIBS
    done
    for p in server client holdconns connload; do
        (cd "$HERE/go" && go build -o "$OUT/go-$p" "./$p")
    done
    CARGO_TARGET_DIR=$OUT/rust cargo build --release --quiet --manifest-path "$HERE/rust/Cargo.toml"
    cp "$OUT/rust/release/server" "$OUT/rust-server"
    cp "$OUT/rust/release/client" "$OUT/rust-client"
    if [ ! -f "$OUT/cert.pem" ]; then
        "$OPENSSL_BIN" req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
            -keyout "$OUT/key.pem" -out "$OUT/cert.pem" -days 365 -subj /CN=localhost \
            -addext subjectAltName=DNS:localhost,IP:127.0.0.1 2> /dev/null
    fi
    if [ ! -f "$OUT/keystore.p12" ]; then
        "$OPENSSL_BIN" pkcs12 -export -in "$OUT/cert.pem" -inkey "$OUT/key.pem" \
            -out "$OUT/keystore.p12" -passout pass:bench -name localhost
    fi
    case " $servers " in
        *" java "*) javac -d "$OUT/java" "$HERE/java/Server.java" ;;
    esac
    head -c 1048576 /dev/zero | tr '\0' x > "$OUT/1m"
}

# Prints the percentage of the CPUs left idle over one second.
idle() {
    if [ "$OS" = Darwin ]; then
        top -l 2 -s 1 -n 0 | awk '/^CPU usage/ { i = $7 } END { sub("%", "", i); print int(i) }'
    else
        vmstat 1 2 | awk 'END { print $15 }'
    fi
}

# Prints "quiet" or "noisy": whether the machine was at least $IDLE_PCT
# percent idle over a second, waiting up to 30 minutes for it.
quiet() {
    tries=0
    while :; do
        i=$(idle)
        if [ "$i" -ge "$IDLE_PCT" ]; then echo quiet; return; fi
        tries=$((tries + 1))
        if [ $tries -ge 30 ]; then echo noisy; return; fi
        echo "  machine busy ($i% idle); waiting" >&2
        sleep 60
    done
}

# Runs a command, writing its standard output to $OUT/client.out and setting
# $REAL to its wall time in seconds and $RSS to its peak RSS in KiB.
timed() {
    if [ "$OS" = Darwin ]; then
        /usr/bin/time -l "$@" > "$OUT/client.out" 2> "$OUT/client.time"
        REAL=$(awk '/ real / { print $1 }' "$OUT/client.time")
        RSS=$(awk '/maximum resident set size/ { print int($1 / 1024) }' "$OUT/client.time")
    else
        /usr/bin/time -f '%e %M' -o "$OUT/client.time" "$@" > "$OUT/client.out"
        REAL=$(awk '{ print $1 }' "$OUT/client.time")
        RSS=$(awk '{ print $2 }' "$OUT/client.time")
    fi
}

# Starts server IMPL on $PORT, plain or `tls`, with $cores cores, setting
# $SERVER to its pid.
# Refuses if anything already listens there: the readiness check below
# would take that process for the new server, and every run would measure
# it.
start() {
    impl=$1
    mode=${2:-plain}
    if curl -sk -o /dev/null "http://127.0.0.1:$PORT/hello" || curl -sk -o /dev/null "https://127.0.0.1:$PORT/hello"; then
        echo "port $PORT is already in use; stop whatever listens there" >&2
        exit 1
    fi
    tlsargs=""
    if [ "$mode" = tls ]; then tlsargs="$OUT/cert.pem $OUT/key.pem"; fi
    case $impl in
        turkey) TURKEY_WORKERS=$cores "$OUT/turkey-server" $PORT $tlsargs > "$OUT/server.log" 2>&1 & ;;
        go) GOMAXPROCS=$cores "$OUT/go-server" $PORT $tlsargs > "$OUT/server.log" 2>&1 & ;;
        rust) TOKIO_WORKER_THREADS=$cores "$OUT/rust-server" $PORT $tlsargs > "$OUT/server.log" 2>&1 & ;;
        node) BENCH_WORKERS=$cores node "$HERE/node/server.js" $PORT $tlsargs > "$OUT/server.log" 2>&1 & ;;
        asgi)
            set -- python3 -m uvicorn --app-dir "$HERE/python" asgi:app --host 127.0.0.1 --port $PORT \
                --workers $cores --loop uvloop --http httptools --no-access-log --log-level warning \
                --timeout-keep-alive 60
            if [ "$mode" = tls ]; then set -- "$@" --ssl-certfile "$OUT/cert.pem" --ssl-keyfile "$OUT/key.pem"; fi
            "$@" > "$OUT/server.log" 2>&1 &
            ;;
        wsgi)
            set -- python3 -m gunicorn --chdir "$HERE/python" wsgi:app -b 127.0.0.1:$PORT --workers $cores \
                --worker-class gthread --threads 16 --keep-alive 60 --worker-connections 5000 --log-level warning
            if [ "$mode" = tls ]; then set -- "$@" --certfile "$OUT/cert.pem" --keyfile "$OUT/key.pem"; fi
            "$@" > "$OUT/server.log" 2>&1 &
            ;;
        ruby)
            bind=tcp://127.0.0.1:$PORT
            if [ "$mode" = tls ]; then bind="ssl://127.0.0.1:$PORT?cert=$OUT/cert.pem&key=$OUT/key.pem&verify_mode=none"; fi
            puma -w $cores -t 16:16 -b "$bind" --silent "$HERE/ruby/config.ru" > "$OUT/server.log" 2>&1 &
            ;;
        java)
            ks=""
            if [ "$mode" = tls ]; then ks=$OUT/keystore.p12; fi
            java -XX:ActiveProcessorCount=$cores -Dsun.net.httpserver.maxIdleConnections=20000 \
                -cp "$OUT/java" Server $PORT $ks > "$OUT/server.log" 2>&1 &
            ;;
        nginx)
            mkdir -p "$OUT/nginx"
            cp "$OUT/1m" "$OUT/nginx/1m"
            if [ "$mode" = tls ]; then
                t=ssl
                c="ssl_certificate $OUT/cert.pem; ssl_certificate_key $OUT/key.pem;"
            else
                t=""
                c=""
            fi
            sed -e "s|WORKERS|$cores|" -e "s|PORT|$PORT|" -e "s|ROOT|$OUT/nginx|g" \
                -e "s| TLS;| $t;|" -e "s|TLSCONFIG|$c|" "$HERE/nginx/nginx.conf" > "$OUT/nginx/nginx.conf"
            nginx -e stderr -p "$OUT/nginx" -c "$OUT/nginx/nginx.conf" > "$OUT/server.log" 2>&1 &
            ;;
    esac
    SERVER=$!
    scheme=http
    if [ "$mode" = tls ]; then scheme=https; fi
    for _ in $(seq 100); do
        if curl -sk -o /dev/null "$scheme://127.0.0.1:$PORT/hello"; then return; fi
        sleep 0.1
    done
    echo "server $impl did not start:" >&2
    cat "$OUT/server.log" >&2
    exit 1
}

# Succeeds if any of the processes named is running. An exited process the
# shell has not reaped yet is a zombie, which `kill -0` would count.
running() {
    ps -o stat= -p "$(echo "$@" | tr ' ' ',')" 2> /dev/null | grep -qv '^Z'
}

# Stops the server and waits for every one of its processes to end, for at
# most five seconds before killing what is left.
stop() {
    all=$(pids)
    kill $all 2> /dev/null || true
    for _ in $(seq 50); do
        if ! running $all; then break; fi
        sleep 0.1
    done
    kill -9 $all 2> /dev/null || true
    wait $SERVER 2> /dev/null || true
}

# Prints the server's pid and its children's: Node's cluster workers,
# nginx's worker processes.
pids() {
    echo $SERVER $(pgrep -P $SERVER || true)
}

# Prints the server's resident set in KiB, summed over its processes.
rss() {
    ps -o rss= -p "$(pids | tr ' ' ',')" | awk '{ s += $1 } END { print s }'
}

# Prints the CPU time, user and system, in seconds, that the server's
# processes have used so far. On Linux from /proc, since `ps` there gives
# whole seconds.
cputime() {
    if [ "$OS" = Darwin ]; then
        # [[dd-]hh:]mm:ss.ss
        ps -o time= -p "$(pids | tr ' ' ',')" | awk '{
            n = split($1, f, ":"); t = 0
            for (i = 1; i <= n; i++) t = t * 60 + f[i]
            s += t
        } END { printf "%.2f\n", s }'
    else
        hz=$(getconf CLK_TCK)
        for p in $(pids); do cat "/proc/$p/stat" 2> /dev/null; done |
            awk -v hz="$hz" '{ sub(/^.*\) /, ""); s += $12 + $13 } END { printf "%.2f\n", s / hz }'
    fi
}

# Samples rss() every half second until $OUT/sampled exists, then writes the
# peak to it. Run it in the background beside the load. It stops on a file
# rather than when the load's process ends, because a shell may not reap
# that process while it waits for this one -- dash does not -- and
# `kill -0` succeeds on an unreaped process.
sample() {
    max=0
    while [ ! -e "$OUT/sampled" ]; do
        r=$(rss)
        if [ "$r" -gt "$max" ]; then max=$r; fi
        sleep 0.5
    done
    echo $max > "$OUT/sampled"
}

# Runs wrk against the server: SCENARIO IMPL CONNS MODE URL [wrk options],
# with $SCRIPT_ARGS passed to wrk.lua; or connload in its place when the
# options are `connload`, or oha at a fixed rate when they are `oha RATE`.
# Records one line per core count in $CORES and repetition.
measure() {
    scenario=$1 impl=$2 conns=$3 mode=$4 url=$5
    shift 5
    threads=$LOAD_THREADS
    if [ "$conns" -lt "$threads" ]; then threads=$conns; fi
    for cores in $CORES; do
        for rep in $(seq "$REPS"); do
            state=$(quiet)
            start "$impl" "$mode"
            rm -f "$OUT/sampled"
            if [ "${1:-}" = connload ]; then
                GOMAXPROCS=$CONNLOAD_PROCS "$OUT/go-connload" "$url" "$conns" 2 > /dev/null 2>&1 || true
                sample &
                sampler=$!
                cpu0=$(cputime)
                GOMAXPROCS=$CONNLOAD_PROCS "$OUT/go-connload" "$url" "$conns" "$SECONDS_PER_RUN" > "$OUT/wrk.out" 2>&1 || true
            elif [ "${1:-}" = oha ]; then
                oha --no-tui --output-format quiet -c "$conns" -z 2s -q "$2" "$url" > /dev/null 2>&1 || true
                sample &
                sampler=$!
                cpu0=$(cputime)
                oha --no-tui --output-format json -c "$conns" -z "${SECONDS_PER_RUN}s" -q "$2" --latency-correction "$url" \
                    > "$OUT/oha.json" 2> /dev/null || true
                python3 "$HERE/oha.py" "$OUT/oha.json" > "$OUT/wrk.out" 2>&1 || true
            else
                wrk -t"$threads" -c"$conns" -d2s -s "$HERE/wrk.lua" "$@" "$url" ${SCRIPT_ARGS:-} > /dev/null 2>&1 || true
                sample &
                sampler=$!
                cpu0=$(cputime)
                wrk -t"$threads" -c"$conns" -d"${SECONDS_PER_RUN}s" -s "$HERE/wrk.lua" "$@" "$url" ${SCRIPT_ARGS:-} > "$OUT/wrk.out" 2>&1 || true
            fi
            cpu=$(awk -v a="$cpu0" -v b="$(cputime)" 'BEGIN { printf "%.2f", b - a }')
            touch "$OUT/sampled"
            wait $sampler || true
            top=$(cat "$OUT/sampled")
            stop
            line=$(grep '^rps=' "$OUT/wrk.out" || { cat "$OUT/wrk.out" >&2; echo "rps=0"; })
            line="$line cpu_s=$cpu secs=$SECONDS_PER_RUN cores=$cores"
            echo "  $scenario $impl c=$conns cores=$cores #$rep: $line rss_kb=$top $state"
            printf '%s\t%s\t%s\t%s\t%s\trss_kb=%s\t%s\n' "$scenario" "$impl" "$conns" "$rep" "$line" "$top" "$state" >> "$RESULTS"
        done
    done
}

u=http://127.0.0.1:$PORT
s=https://127.0.0.1:$PORT

scenario_keepalive() {
    for conns in $KEEPALIVE_CONNS; do
        for impl in $servers; do measure keepalive $impl $conns plain $u/hello; done
    done
}

scenario_rate() {
    for rate in $RATES; do
        for impl in $servers; do measure "rate-$rate" $impl 64 plain $u/hello oha "$rate"; done
    done
}

scenario_close() {
    for impl in $servers; do measure close $impl 64 plain $u/hello connload; done
}

scenario_download() {
    for impl in $servers; do measure download $impl 64 plain $u/1m; done
}

scenario_upload() {
    SCRIPT_ARGS="-- upload"
    for impl in $servers; do
        if [ $impl != nginx ]; then measure upload $impl 64 plain $u/upload; fi
    done
    SCRIPT_ARGS=""
}

scenario_stream() {
    for impl in $servers; do
        if [ $impl != nginx ]; then measure stream $impl 64 plain $u/stream; fi
    done
}

scenario_tls() {
    scenario_tls_keepalive
    scenario_tls_handshake
}

scenario_tls_keepalive() {
    for impl in $servers; do measure tls-keepalive $impl 64 tls $s/hello; done
}

scenario_tls_handshake() {
    for impl in $servers; do measure tls-handshake $impl 64 tls $s/hello connload; done
}

# Each client sends REQUESTS GETs of /hello to nginx, from 1 and from 64
# tasks, with $cores threads of its own for each count in $CORES; nginx has
# $cores workers too.
scenario_client() {
    for cores in $CORES; do
        start nginx plain
        url=$u/hello
        for conc in 1 64; do
            n=20000
            if [ "$conc" -gt 1 ]; then n=200000; fi
            for impl in turkey go rust curl; do
                for rep in $(seq "$REPS"); do
                    state=$(quiet)
                    case $impl in
                        turkey) cmd="env TURKEY_WORKERS=$cores $OUT/turkey-client $url $n $conc" ;;
                        go) cmd="env GOMAXPROCS=$cores $OUT/go-client $url $n $conc" ;;
                        rust) cmd="env TOKIO_WORKER_THREADS=$cores $OUT/rust-client $url $n $conc" ;;
                        curl)
                            awk -v n=$n -v url=$url 'BEGIN { for (i = 0; i < n; i++) print "url = " url "\noutput = /dev/null" }' > "$OUT/curl.cfg"
                            if [ "$conc" -gt 1 ]; then
                                cmd="curl -s --parallel --parallel-max $conc -K $OUT/curl.cfg"
                            else
                                cmd="curl -s -K $OUT/curl.cfg"
                            fi
                            ;;
                    esac
                    timed $cmd
                    rss=$RSS
                    line=$(grep '^rps=' "$OUT/client.out" || awk -v n=$n -v r="$REAL" 'BEGIN { printf "rps=%.0f\n", n / r }')
                    line="$line cores=$cores"
                    echo "  client $impl conc=$conc cores=$cores #$rep: $line rss_kb=$rss $state"
                    printf '%s\t%s\t%s\t%s\t%s\trss_kb=%s\t%s\n' client $impl "$conc" "$rep" "$line" "$rss" "$state" >> "$RESULTS"
                done
            done
        done
        stop
    done
}

# The server's RSS after one request, and with 10,000 idle keep-alive
# connections open, each of which has had one response.
scenario_idle() {
    for cores in $CORES; do
        for impl in $servers; do
            for rep in $(seq "$REPS"); do
                state=$(quiet)
                start "$impl" plain
                before=$(rss)
                "$OUT/go-holdconns" 127.0.0.1:$PORT 10000 10 > "$OUT/hold.out" 2>&1 &
                hold=$!
                for _ in $(seq 600); do
                    if grep -q ready "$OUT/hold.out"; then break; fi
                    sleep 0.1
                done
                sleep 2
                held=$(rss)
                wait $hold || true
                stop
                ok=$(grep -c ready "$OUT/hold.out" || true)
                echo "  idle $impl cores=$cores #$rep: before_kb=$before held_kb=$held ready=$ok $state"
                printf '%s\t%s\t%s\t%s\tbefore_kb=%s held_kb=%s ready=%s cores=%s\t\t%s\n' idle $impl 10000 "$rep" "$before" "$held" "$ok" "$cores" "$state" >> "$RESULTS"
            done
        done
    done
}

# Writes the machine and every tool's version to $OUT/versions.txt.
versions() {
    {
        uname -srm
        if [ "$OS" = Darwin ]; then
            sysctl -n machdep.cpu.brand_string
            echo "$(sysctl -n hw.ncpu) CPUs, $(sysctl -n hw.perflevel0.physicalcpu 2> /dev/null || echo ?) performance cores"
        else
            lscpu | grep -E 'Model name|^CPU\(s\)'
        fi
        echo "Turkey $(git rev-parse --short HEAD), $BOOT"
        $CC --version | head -1
        "$OPENSSL_BIN" version
        go version
        rustc --version
        grep -A1 -E '^name = "(hyper|tokio|reqwest|rustls)"$' "$HERE/rust/Cargo.lock" | grep version | paste -sd ' ' -
        echo "node $(node --version)"
        nginx -v 2>&1
        wrk -v 2>&1 | head -1
        oha --version 2>&1
        curl --version | head -1
        python3 -c 'import sys, uvicorn, uvloop, httptools, gunicorn; print("python", sys.version.split()[0], "uvicorn", uvicorn.__version__, "uvloop", uvloop.__version__, "httptools", httptools.__version__, "gunicorn", gunicorn.__version__)' 2>&1
        ruby --version 2>&1
        puma --version 2>&1
        java -version 2>&1 | head -1
    } > "$OUT/versions.txt" 2>&1 || true
}

if [ "${1:-}" = build ]; then
    build
    exit
fi
build
versions
if [ "$OS" = Darwin ]; then ncpu=$(sysctl -n hw.ncpu); else ncpu=$(nproc); fi
most=$(echo $CORES | tr ' ' '\n' | sort -n | tail -1)
if [ $((most + LOAD_THREADS)) -gt "$ncpu" ]; then
    echo "warning: $most server cores and $LOAD_THREADS load threads share $ncpu CPUs; the load generator will slow the server" >&2
fi
all="keepalive close download upload stream tls rate client idle"
for sc in ${*:-$all}; do
    echo "== $sc"
    scenario_$sc
done
