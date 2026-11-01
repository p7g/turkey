#!/bin/sh
# Run the test harness, tests/harness/, with the arguments given:
#
#   scripts/test.sh [-j N] [-v] [--shard I/N] [--timeout-scale F] [PATH[::CASE]...]
#   scripts/test.sh --selftest
#
# Builds two programs first, each cached by a fingerprint of its inputs under
# $TMPDIR/turkey-test/ and reused while that fingerprint holds:
#
#   boot     the compiler under test, scripts/build.sh's stage2, or the binary
#            $TURKEY_BOOT names, used as it is with nothing built
#   harness  tests/harness/Main.gob, compiled by stage1
#
# **The harness is compiled by stage1, the committed compiler in bootstrap/,
# and not by the boot it tests.** A working tree whose compiler miscompiles
# would otherwise be reported on by a harness it miscompiled. The harness still
# links the working tree's lib/, so a broken lib/ fails the harness's build
# loudly rather than giving a wrong answer.
#
# The constraint that follows: the harness, and every library module it
# imports, may use only what the committed compiler supports. A harness or
# library change that compiles under stage2 but not under stage1 is that
# case; it waits for scripts/bump-bootstrap.sh, or is written another way.
#
# $TURKEY_CC and $TURKEY_RUN are read as scripts/build.sh reads them.

set -eu

CC=${TURKEY_CC:-cc}
RUN=${TURKEY_RUN:-}

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

tmp=${TMPDIR:-/tmp}
CACHE=${tmp%/}/turkey-test

# shasum comes with perl, which a minimal Linux may not have; coreutils'
# sha256sum prints the same lines.
if command -v shasum > /dev/null; then
    SHA="shasum -a 256"
else
    SHA=sha256sum
fi

# A digest of the files named on standard input, by name and contents, in a
# stable order, with the C compiler's identity: the same source linked by two
# compilers is two different binaries, and this cache is shared by every
# session on the machine.
fingerprint() {
    {
        LC_ALL=C sort | xargs $SHA
        printf '%s\n' "$CC"
        $CC --version 2>&1
    } | $SHA | cut -c1-16
}

# The library, without a test's throwaway `Probe_` modules, which are not an
# input to anything built here.
library() {
    find lib -name '*.gob' ! -name 'Probe_*'
}

bootstrap_files() {
    find bootstrap -type f
    echo scripts/build.sh
}

# Build with scripts/build.sh into a staging directory and move ARTIFACT to
# DEST: a concurrent build of the same fingerprint makes the same bytes, and
# the move is atomic, so nobody sees half a binary.
build() {
    artifact=$1 dest=$2
    shift 2
    staging=$(dirname "$dest").$$
    rm -rf "$staging"
    sh scripts/build.sh --out "$staging" "$@" >&2
    mkdir -p "$(dirname "$dest")"
    mv "$staging/$artifact" "$dest"
    rm -rf "$staging"
}

mkdir -p "$CACHE"

stage1_key=$(bootstrap_files | fingerprint)
stage1=$CACHE/stage1/$stage1_key/stage1
if [ ! -x "$stage1" ]; then
    echo "test.sh: building stage1 ($stage1_key)" >&2
    build stage1 "$stage1" --stage1-only
fi
touch "$(dirname "$stage1")"

if [ -n "${TURKEY_BOOT:-}" ]; then
    boot=$TURKEY_BOOT
else
    boot_key=$({ bootstrap_files; library; find src -name '*.gob'; } | fingerprint)
    boot=$CACHE/boot/$boot_key/boot
    if [ ! -x "$boot" ]; then
        echo "test.sh: building boot ($boot_key)" >&2
        build stage2 "$boot"
    fi
    touch "$(dirname "$boot")"
fi
case $boot in /*) ;; *) boot=$ROOT/$boot ;; esac

# glibc keeps the maths library out of libc, and lib/ calls fmod and floor.
case $($CC -dumpmachine) in
    *linux*) LIBS=-lm ;;
    *) LIBS= ;;
esac

harness_key=$({ bootstrap_files; library; find tests/harness -maxdepth 1 -name '*.gob'; } | fingerprint)
harness=$CACHE/harness/$harness_key/harness
if [ ! -x "$harness" ]; then
    echo "test.sh: building the harness ($harness_key)" >&2
    staging=$CACHE/harness/$harness_key.$$
    mkdir -p "$staging"
    (cd tests/harness &&
     TURKEY_LIB=$ROOT/lib $RUN "$stage1" build -o "$staging/harness.o" -f obj Main.gob)
    $CC -o "$staging/harness" "$staging/harness.o" $LIBS
    mkdir -p "$(dirname "$harness")"
    mv "$staging/harness" "$harness"
    rm -rf "$staging"
fi
touch "$(dirname "$harness")"

# The harness runs a task per case in flight, and as many cases as it has
# workers unless -j says otherwise; each case's own processes are given
# TURKEY_WORKERS=1 by the harness, so a setting here is not passed on.
unset TURKEY_WORKERS
exec $RUN "$harness" --root "$ROOT" --boot "$boot" --cache "$CACHE" "$@"
