#!/bin/sh
# Compare two versions of the compiler on the self-compile: what each
# allocates, by site, and what each costs to run.
#
#   sh scripts/compare.sh [--runs N] [--out DIR] BASE NEW
#
# BASE and NEW are each a directory holding a checkout, used as it is,
# uncommitted changes included, or a git revision, checked out into a
# temporary worktree under DIR. From each side:
#
#   stage2    the side's source compiled by its committed bootstrap
#             (scripts/build.sh)
#   counted   the side's source compiled by its stage2 with
#             TURKEY_ALLOC_SITES=1, and --gc-stats where the side has it: a
#             compiler that counts its allocations per site and reports its
#             collections
#   stage3    the side's source compiled by its stage2: the compiler whose
#             time is measured, built by a compiler with the side's own
#             optimizations
#
# Both sides compile the same input: BASE's src/ and lib/, copied before
# anything runs. The compiler reads lib/ relative to its working directory, so
# the input is both what is compiled and where each run happens. Comparing
# each side compiling its own source would change the workload with the code.
#
# Builds and the counted runs go in parallel; the timed runs go one at a time,
# alternating base and new, since they are the measurement. Instructions
# retired is the number that barely moves between runs; wall time and cycles
# move with the machine's load. Instructions, cycles and peak footprint come
# from macOS's `time -l`; on Linux only wall time and max RSS are reported.
#
# Measured on arm64 macOS: about 10 minutes with the default two runs, most of
# it the two builds and the four compiles of the compiler.
#
#   --runs N    timed runs per side (default 2)
#   --out DIR   where everything goes (default build/compare); emptied first

set -eu

CC=${TURKEY_CC:-cc}
RUN=${TURKEY_RUN:-}

CALLER=$(pwd)
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

SOURCE=src/Main.gob

runs=2
out=build/compare
while [ $# -gt 0 ]; do
    case $1 in
        --runs) runs=$2; shift 2 ;;
        --out) out=$2; shift 2 ;;
        -*) echo "usage: $0 [--runs N] [--out DIR] BASE NEW" >&2; exit 2 ;;
        *) break ;;
    esac
done
if [ $# -ne 2 ]; then
    echo "usage: $0 [--runs N] [--out DIR] BASE NEW" >&2
    exit 2
fi
case $out in /*) ;; *) out=$ROOT/$out ;; esac
case $out in "$ROOT"|"$ROOT/"|/) echo "compare.sh: --out $out would be emptied" >&2; exit 2 ;; esac

case $($CC -dumpmachine 2>/dev/null) in
    *linux*) LIBS=-lm ;;
    *) LIBS= ;;
esac
case $(uname) in
    Darwin) TIME="/usr/bin/time -l" ;;
    *) TIME="/usr/bin/time -v" ;;
esac

started=$(date +%s)
step() {
    echo "[$(( $(date +%s) - started ))s] $*" >&2
}

# Worktrees this made for revisions, removed on exit however it exits.
cleanup() {
    for side in base new; do
        if [ -d "$out/$side-tree" ]; then
            git -C "$ROOT" worktree remove --force "$out/$side-tree" || true
        fi
    done
    git -C "$ROOT" worktree prune
}

# The checkout for one side: $2 itself if it is a directory (relative to where
# this was run from), else revision $2 checked out detached at $out/$1-tree.
checkout() {
    if (cd "$CALLER" && [ -d "$2" ]); then
        (cd "$CALLER" && cd "$2" && pwd)
    else
        git -C "$ROOT" rev-parse --verify --quiet "$2^{commit}" > /dev/null || {
            echo "compare.sh: '$2' is neither a directory nor a revision" >&2
            exit 2
        }
        git -C "$ROOT" worktree add --detach --quiet "$out/$1-tree" "$2" >&2
        echo "$out/$1-tree"
    fi
}

# `--gc-stats` if the tree's compiler takes it. A tree from before it read
# TURKEY_GC_STATS at run time instead, which the counted run also sets.
stats_flag() {
    if grep -q -- '--gc-stats' "$1/src/Turkey/Build.gob" 2> /dev/null; then
        echo --gc-stats
    fi
}

# Wait for background jobs $@ (pids); if any failed, show the end of its log.
# Each job's log is at the path in the variable log_<pid>.
await() {
    failed=0
    for pid in "$@"; do
        if ! wait "$pid"; then
            eval "log=\$log_$pid"
            echo "compare.sh: failed; the end of $log:" >&2
            tail -20 "$log" >&2
            failed=1
        fi
    done
    [ $failed = 0 ] || exit 1
}

cleanup
trap cleanup EXIT
rm -rf "$out"
mkdir -p "$out/base" "$out/new"

base=$(checkout base "$1")
new=$(checkout new "$2")
step "base: $1 ($base)"
step "new: $2 ($new)"

mkdir -p "$out/input"
cp -R "$base/src" "$base/lib" "$out/input/"

# 1. stage2 on each side, from that side's committed bootstrap.
step "build stage2 (both sides)"
pids=
for side in base new; do
    eval "tree=\$$side"
    sh "$tree/scripts/build.sh" --out "$out/$side/stages" \
        > "$out/$side/build.log" 2>&1 &
    eval "log_$!=\"$out/$side/build.log\""
    pids="$pids $!"
done
await $pids

# 2. The counted compiler and stage3 on each side, from its own source.
step "compile the counted compiler and stage3 (both sides)"
pids=
for side in base new; do
    eval "tree=\$$side"
    d=$out/$side
    (cd "$tree" && TURKEY_ALLOC_SITES=1 $RUN "$d/stages/stage2" native \
        $(stats_flag "$tree") "$SOURCE" > "$d/counted.s" 2> "$d/sites.txt" \
        && $CC -o "$d/counted" "$d/counted.s" $LIBS) > "$d/counted.log" 2>&1 &
    eval "log_$!=\"$d/counted.log\""
    pids="$pids $!"
    (cd "$tree" && $RUN "$d/stages/stage2" native "$SOURCE" > "$d/stage3.s" \
        && $CC -o "$d/stage3" "$d/stage3.s" $LIBS) > "$d/stage3.log" 2>&1 &
    eval "log_$!=\"$d/stage3.log\""
    pids="$pids $!"
done
await $pids

# 3. The counted runs, which are also where the collector's statistics come
# from: counting puts every allocation on the slow path, so the timed runs
# leave it out. Each writes turkey-alloc-sites.bin in its working directory, so
# each runs in its own copy of the input.
step "count allocations (both sides)"
pids=
for side in base new; do
    d=$out/$side
    cp -R "$out/input" "$d/input"
    (cd "$d/input" && TURKEY_GC_STATS=1 $RUN "$d/counted" native "$SOURCE" \
        > /dev/null 2> "$d/gc.txt" \
        && mv turkey-alloc-sites.bin "$d/sites.bin") > "$d/count.log" 2>&1 &
    eval "log_$!=\"$d/count.log\""
    pids="$pids $!"
done
await $pids

# 4. The timed runs, alternating.
i=1
while [ $i -le "$runs" ]; do
    for side in base new; do
        step "time $side, run $i of $runs"
        (cd "$out/input" && $TIME $RUN "$out/$side/stage3" \
            native "$SOURCE" > /dev/null 2> "$out/$side/run$i.txt") || {
            echo "compare.sh: the $side run failed; see $out/$side/run$i.txt" >&2
            exit 1
        }
    done
    i=$((i + 1))
done

step "report"
python3 - "$out" "$runs" << 'EOF' | tee "$out/report.txt"
import collections, re, struct, sys

out, runs = sys.argv[1], int(sys.argv[2])
sides = ('base', 'new')

def number(pattern, text):
    m = re.search(pattern, text)
    return float(m.group(1).replace(',', '')) if m else None

def clock(text):
    # GNU time's h:mm:ss or m:ss.
    m = re.search(r'Elapsed \(wall clock\) time.*: ([\d:.]+)', text)
    if not m:
        return None
    seconds = 0.0
    for part in m.group(1).split(':'):
        seconds = seconds * 60 + float(part)
    return seconds

def measure(text):
    rss = number(r'(\d+)\s+maximum resident set size', text)
    if rss is None:
        kb = number(r'Maximum resident set size \(kbytes\): (\d+)', text)
        rss = kb * 1024 if kb is not None else None
    wall = number(r'([\d.]+) real', text)
    return {
        'wall (s)': wall if wall is not None else clock(text),
        'instructions': number(r'(\d+)\s+instructions retired', text),
        'cycles': number(r'(\d+)\s+cycles elapsed', text),
        'peak footprint': number(r'(\d+)\s+peak memory footprint', text),
        'max RSS': rss,
        'allocations': number(r'\[gc\] collections \d+, allocations (\d+)', text),
        'collections': number(r'\[gc\] collections (\d+)', text),
        'objects traced': number(r'objects traced (\d+)', text),
        'collect time (s)': number(r'collect time ([\d.]+) s', text),
        'peak heap regions': number(r'\[gc\] region bytes \d+, peak (\d+)', text),
    }

# The collector's numbers are the counted run's, the same for every timed run.
measured = {s: [measure(open(f'{out}/{s}/run{i}.txt').read()
                        + open(f'{out}/{s}/gc.txt').read())
                for i in range(1, runs + 1)] for s in sides}

def show(key, v):
    if v is None:
        return '-'
    if key in ('peak footprint', 'max RSS', 'peak heap regions'):
        return f'{v / 1e9:.3f} GB'
    if key in ('instructions', 'cycles'):
        return f'{v / 1e9:.2f} G'
    if key.endswith('(s)'):
        return f'{v:.2f}'
    return f'{v:,.0f}'

print(f'\n{"":20} {"base":>30} {"new":>30} {"change":>8}')
for key in measured['base'][0]:
    b = [r[key] for r in measured['base'] if r[key] is not None]
    n = [r[key] for r in measured['new'] if r[key] is not None]
    if not b or not n:
        continue
    change = (sum(n) / len(n)) / (sum(b) / len(b)) - 1
    cells = [' / '.join(show(key, v) for v in xs) for xs in (b, n)]
    print(f'{key:20} {cells[0]:>30} {cells[1]:>30} {100 * change:+7.2f}%')

# Allocations by function and by site reason, joined across the two sides by
# name: site numbers are not comparable between two builds.
def counted(side):
    sites = {}
    for line in open(f'{out}/{side}/sites.txt', errors='replace'):
        if line.startswith('site\t'):
            parts = line.rstrip('\n').split('\t')
            sites[int(parts[1])] = parts[2:5]
    data = open(f'{out}/{side}/sites.bin', 'rb').read()
    n = struct.unpack_from('<q', data, 0)[0]
    counts = struct.unpack_from(f'<{n}q', data, 8)
    by_function, by_reason = collections.Counter(), collections.Counter()
    for sid in range(n):
        fn, op, reason = sites[sid]
        if op in ('object.new', 'array.new', 'cell.new', 'closure.new'):
            by_function[fn] += counts[sid]
            by_reason[f'{op}: {reason}'] += counts[sid]
    return by_function, by_reason

(bf, br), (nf, nr) = counted('base'), counted('new')
total = sum(bf.values())
print(f'\ncounted allocations: base {total:,}, new {sum(nf.values()):,}')
for title, b, n in (('by function', bf, nf), ('by reason', br, nr)):
    deltas = sorted(set(b) | set(n), key=lambda k: -abs(n[k] - b[k]))[:15]
    deltas = [k for k in deltas if n[k] != b[k]]
    if not deltas:
        continue
    print(f'\nlargest changes {title}:')
    for k in deltas:
        d = n[k] - b[k]
        print(f'  {d:>+14,} {100 * d / max(total, 1):+6.2f}%  '
              f'{b[k]:>13,} -> {n[k]:<13,} {k}')
EOF
step "done: $out/report.txt"
