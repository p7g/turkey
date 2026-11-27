#!/bin/sh
# Time compilers from different commits on one fixed input, so that a slower
# compiler is told apart from a bigger input.
#
# An input is a whole tree, src/ and lib/, because lib/ holds the runtime and
# the Prelude every program is compiled with. A compiler accepts only a lib/
# close to its own: the runtime's interface with the compiler and the syntax
# both move. So no one input spans a long range of commits. Compare adjacent
# commits on an input both accept, and chain the ratios across the range.
#
#   build COMMIT DIR
#       Check COMMIT out into DIR/tree (a detached worktree of this
#       repository), run its scripts/build.sh --fixpoint there and copy its
#       stage3 to DIR/stage3. stage3 is timed rather than stage2 because stage2
#       is COMMIT's source compiled by the committed bootstrap's code
#       generation, not by COMMIT's own.
#
#   input COMMIT DIR
#       Write COMMIT's src/ and lib/ to DIR, as an input for `time`.
#
#   time ROUNDS INPUT NAME=COMPILER...
#       Run every COMPILER on INPUT/src/Main.gob, from INPUT with
#       TURKEY_LIB=lib, ROUNDS times, interleaving the compilers within each
#       round. Prints one tab-separated row per run:
#
#           round name status wall_s user_s sys_s footprint_bytes instructions sha
#
#       where the times, the peak memory footprint and the retired
#       instructions are /usr/bin/time -l's (macOS), and sha is the output's
#       SHA-1, so a run that wrote different bytes from another round shows.
#       Each compiler is asked for an executable, or an object where it cannot
#       write one; status is `reject` where it cannot compile even an empty
#       program against INPUT's lib/.
#
#   summary FILE
#       Print per-name medians and ranges of the rows `time` wrote to FILE,
#       and whether every run exited 0 and wrote the same bytes.
#
# Run it under `heavy`: other sessions share the machine, and these numbers
# mean nothing while another heavy command runs.

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)

# Print the flags with which COMPILER writes a whole program from INPUT: none
# where it writes an executable by default, `-f exe` where it needs asking, and
# `-f obj` where it writes no executable at all, so the run leaves out the
# system linker. Found by compiling an empty program with each in turn; fails
# if none works, which is how a compiler rejects INPUT's lib/.
output_flags() {
    printf 'fun main() {}\n' >"$3/probe.gob"
    for f in "" "-f exe" "-f obj"; do
        # shellcheck disable=SC2086
        if (cd "$2" && TURKEY_LIB=lib "$1" build $f -o "$3/probe" "$3/probe.gob") \
            >/dev/null 2>&1; then
            echo "$f"
            return
        fi
    done
    return 1
}

cmd=${1:-}
[ $# -gt 0 ] && shift

case $cmd in
build)
    commit=$1
    dir=$2
    mkdir -p "$dir"
    dir=$(cd "$dir" && pwd)
    if [ ! -d "$dir/tree" ]; then
        git -C "$ROOT" worktree add -q --detach "$dir/tree" "$commit"
    fi
    # A fixpoint build occasionally fails for reasons outside the commit
    # (another session's load, a flaky link); retry it a few times.
    tries=0
    until (cd "$dir/tree" && sh scripts/build.sh --fixpoint --out "$dir/stages") \
        >"$dir/build.log" 2>&1; do
        tries=$((tries + 1))
        if [ $tries -ge 3 ]; then
            tail -20 "$dir/build.log" >&2
            exit 1
        fi
    done
    cp "$dir/stages/stage3" "$dir/stage3"
    git -C "$dir/tree" rev-parse HEAD >"$dir/commit"
    ;;
input)
    commit=$1
    dir=$2
    mkdir -p "$dir"
    git -C "$ROOT" archive "$commit" src lib | tar -x -C "$dir"
    ;;
time)
    rounds=$1
    input=$(cd "$2" && pwd)
    shift 2
    out=$(mktemp -d)
    r=1
    while [ $r -le "$rounds" ]; do
        for pair in "$@"; do
            name=${pair%%=*}
            compiler=${pair#*=}
            rm -f "$out/exe" "$out/time"
            if ! flags=$(output_flags "$compiler" "$input" "$out"); then
                printf '%s\t%s\treject\t-\t-\t-\t-\t-\t-\n' $r "$name"
                continue
            fi
            # shellcheck disable=SC2086
            if (cd "$input" && /usr/bin/time -l env TURKEY_LIB=lib \
                "$compiler" build $flags -o "$out/exe" src/Main.gob) \
                >"$out/log" 2>"$out/time"; then
                status=0
            else
                status=$?
            fi
            sha=-
            [ -f "$out/exe" ] && sha=$(shasum "$out/exe" | cut -c1-12)
            awk -v r=$r -v n="$name" -v s=$status -v h="$sha" '
                / real / { wall = $1; user = $3; sys = $5 }
                /peak memory footprint/ { fp = $1 }
                /instructions retired/ { ins = $1 }
                END { printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n",
                      r, n, s, wall, user, sys, fp, ins, h }' "$out/time"
            if [ $status -ne 0 ]; then
                grep -v -e ' real ' -e '^ *[0-9]' "$out/time" | head -5 >&2 || true
            fi
        done
        r=$((r + 1))
    done
    rm -rf "$out"
    ;;
summary)
    file=$1
    awk -F'\t' '
        { n = $2; k = ++count[n]; wall[n, k] = $4; user[n, k] = $5
          fp[n, k] = $7; ins[n, k] = $8
          if ($3 != 0) bad[n] = 1
          if (!(n in sha)) { sha[n] = $9; order[++names] = n }
          else if (sha[n] != $9) differ[n] = 1 }
        function med(a, n, c,   i, j, t, v) {
            for (i = 1; i <= c; i++) v[i] = a[n, i] + 0
            for (i = 2; i <= c; i++)
                for (j = i; j > 1 && v[j - 1] > v[j]; j--) {
                    t = v[j]; v[j] = v[j - 1]; v[j - 1] = t }
            lo = v[1]; hi = v[c]
            return c % 2 ? v[(c + 1) / 2] : (v[c / 2] + v[c / 2 + 1]) / 2
        }
        END {
            printf "%-14s %5s %16s %16s %18s %14s  %s\n", "name", "runs",
                "wall s (range)", "user s (range)", "footprint MB (range)",
                "Ginstr (range)", "check"
            for (i = 1; i <= names; i++) {
                n = order[i]; c = count[n]
                w = med(wall, n, c); wl = lo; wh = hi
                u = med(user, n, c); ul = lo; uh = hi
                f = med(fp, n, c) / 1e6; fl = lo / 1e6; fh = hi / 1e6
                g = med(ins, n, c) / 1e9; gl = lo / 1e9; gh = hi / 1e9
                printf "%-14s %5d %5.2f (%4.2f-%4.2f) %5.2f (%5.2f-%5.2f) %5.0f (%4.0f-%4.0f) %6.1f (%5.1f-%5.1f)  %s\n",
                    n, c, w, wl, wh, u, ul, uh, f, fl, fh, g, gl, gh,
                    bad[n] ? "FAILED" : (differ[n] ? "OUTPUT DIFFERS" : "ok")
            }
        }' "$file"
    ;;
*)
    sed -n '2,39p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
