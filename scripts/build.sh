#!/bin/sh
# Build the compiler from the committed bootstrap, with no Python, C compiler,
# assembler or linker involved.
#
#   stage1  the committed executable (bootstrap/), unpacked
#   stage2  the executable stage1 writes for today's source -- the result,
#           written as `turkey`, the name the compiler is run by
#   stage3  the executable stage2 writes for the same source  (--fixpoint only)
#   stage4  the executable stage3 writes, which must be stage3 (--fixpoint only)
#
# Stage N is written by stage N-1, as the file stageN in --out, except
# stage2, which is the file `turkey`. stage2 is today's source compiled by the
# committed compiler: correct if that one is, but its code is whatever an older
# compiler emitted. stage3 is today's source compiled by today's source, and the
# fixed point is that stage3 writes exactly the executable it is. The writer
# puts nothing of the time or the output path in the bytes, so stage3 and
# stage4 are compared byte for byte.
#
# Every stage runs from the repository root with TURKEY_LIB=lib, so each reads
# this checkout's lib/ and names its files lib/... wherever --out puts the
# stages: one under $TMPDIR has no lib/ above it to find on its own.
#
# bootstrap/ holds the committed compiler: the executable it writes for
# src/Main.gob, once per target, each under gzip -9 -n (3.6 MB each; -n so one
# gzip always compresses the same bytes to the same bytes, though GNU's and
# macOS's differ from each other), and PROVENANCE, which records the commit
# they came from and the hashes checked here before anything runs. Every
# target's executable is the same compiler written for a different platform,
# so a build on any of them starts from the same source.
# scripts/reproduce-bootstrap.sh checks that PROVENANCE's commit, built from
# the bootstrap it carries, writes exactly these bytes.
#
# The target is --target's, or else the host's, from `uname`. On a host that
# is neither -- x86-64 Linux running the arm64 Linux compiler under
# qemu-user -- it is the one $TURKEY_CC links for, if that is set.
#
# Each stage is the whole program, the runtime included -- the collector, the
# allocator and the panic state are Turkey in lib/, compiled in with
# everything else -- and loads nothing but the C library.
#
# Measured on arm64 macOS: about 5 s for each stage written; 17 s with
# --fixpoint.
#
# Usage: scripts/build.sh [--out DIR] [--target TARGET] [--fixpoint]
#                         [--stage1 BINARY] [--stage1-only]
#
#   --out DIR        where the stages go (default build/stages)
#   --target TARGET  arm64-darwin or arm64-linux (default: the host's)
#   --fixpoint       also build stage3 and stage4, and check that stage4 is
#                    stage3, byte for byte
#   --stage1 BINARY  use an existing compiler as stage1 instead of the committed
#                    one; how to bump across a change the committed compiler
#                    cannot build. Its `build -o PATH --target TARGET` must
#                    write an executable, which a compiler older than that
#                    default does not
#   --stage1-only    stop after stage1: what scripts/test.sh compiles the test
#                    harness with

set -eu

# A prefix for running a stage, split into words, empty to run it directly:
# `qemu-aarch64-static` where binfmt_misc cannot be registered. README.md says
# more; tests/toolchain.py reads the same variable.
RUN=${TURKEY_RUN:-}

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
TURKEY_LIB=lib
export TURKEY_LIB

# The compiler's entry module. Relative, and always spelled the same way: the
# executable names its files, so the spelling is in the bytes.
SOURCE=src/Main.gob
BOOTSTRAP=bootstrap

out=build/stages
target=
fixpoint=0
stage1=
only1=0
while [ $# -gt 0 ]; do
    case $1 in
        --out) out=$2; shift 2 ;;
        --target) target=$2; shift 2 ;;
        --fixpoint) fixpoint=1; shift ;;
        --stage1) stage1=$2; shift 2 ;;
        --stage1-only) only1=1; shift ;;
        *) echo "usage: $0 [--out DIR] [--target TARGET] [--fixpoint]" \
                "[--stage1 BINARY] [--stage1-only]" >&2
           exit 2 ;;
    esac
done

# The host as a target, or nothing if it is not one.
case $(uname -s)/$(uname -m) in
    Darwin/arm64) host=arm64-darwin ;;
    Linux/aarch64|Linux/arm64) host=arm64-linux ;;
    *) host= ;;
esac
if [ -z "$target" ]; then
    target=$host
fi
# What $TURKEY_CC links for: arm64-apple-darwin25.6.0 from Apple clang,
# aarch64-linux-gnu from gcc.
if [ -z "$target" ] && [ -n "${TURKEY_CC:-}" ]; then
    case $($TURKEY_CC -dumpmachine 2>/dev/null) in
        arm64-apple-darwin*|arm64-apple-macos*|aarch64-apple-darwin*)
            target=arm64-darwin ;;
        aarch64*-linux*|arm64*-linux*) target=arm64-linux ;;
    esac
fi
case $target in
    arm64-darwin|arm64-linux) ;;
    '') echo "build.sh: this host, $(uname -s) on $(uname -m), is not a target;" \
             "pass --target arm64-linux to run the arm64 Linux compiler under" \
             "qemu-user, registered with binfmt_misc or named by TURKEY_RUN" >&2
        exit 1 ;;
    *) echo "build.sh: unknown target '$target';" \
            "supported: arm64-darwin, arm64-linux" >&2
       exit 2 ;;
esac
ARTIFACT=$BOOTSTRAP/$target.gz
# The committed compiler for another target than the host's runs only under
# an emulator, which TURKEY_RUN names. Refused here rather than when stage1
# fails to start.
if [ -n "$host" ] && [ "$target" != "$host" ] && [ -z "$RUN" ] && [ -z "$stage1" ]; then
    echo "build.sh: the committed $target compiler cannot run on $host;" \
         "build for $host, or set TURKEY_RUN to an emulator for $target" >&2
    exit 1
fi

mkdir -p "$out"
case $out in /*) ;; *) out=$ROOT/$out ;; esac

started=$(date +%s)
step() {
    echo "[$(( $(date +%s) - started ))s] $*" >&2
}

# One field of PROVENANCE.
provenance() {
    sed -n "s/^$1: //p" "$BOOTSTRAP/PROVENANCE"
}

# shasum comes with perl, which a minimal Linux may not have; coreutils'
# sha256sum prints the same first field.
sha() {
    if command -v shasum > /dev/null; then
        shasum -a 256 "$1" | cut -d' ' -f1
    else
        sha256sum "$1" | cut -d' ' -f1
    fi
}

# The compiler's own source, written by $1 as the executable $2.
emit() {
    step "$(basename "$1") $SOURCE -> $(basename "$2")"
    $RUN "$1" build -o "$2-tmp" --target "$target" "$SOURCE"
    mv "$2-tmp" "$2"
}

# The same, as assembly on standard output: for saying where two stages
# differ, since an executable's first differing byte names no line.
assembly() {
    $RUN "$1" build -o - -f asm --target "$target" "$SOURCE"
}

if [ -n "$stage1" ]; then
    step "stage1 is $stage1"
    cp "$stage1" "$out/stage1"
else
    if [ ! -f "$ARTIFACT" ]; then
        echo "build.sh: there is no committed compiler for $target" >&2
        exit 1
    fi
    expected=$(provenance "$target gz-sha256")
    if [ -z "$expected" ] || [ "$(sha "$ARTIFACT")" != "$expected" ]; then
        echo "build.sh: $ARTIFACT does not match PROVENANCE's" \
             "$target gz-sha256" >&2
        exit 1
    fi
    step "unpack stage1"
    gunzip -c "$ARTIFACT" > "$out/stage1-tmp"
    if [ "$(sha "$out/stage1-tmp")" != "$(provenance "$target exe-sha256")" ]; then
        echo "build.sh: $ARTIFACT decompresses to the wrong bytes" >&2
        exit 1
    fi
    chmod +x "$out/stage1-tmp"
    mv "$out/stage1-tmp" "$out/stage1"
fi

if [ $only1 = 1 ]; then
    step "done: $out/stage1"
    exit 0
fi

emit "$out/stage1" "$out/turkey"
# Nothing here writes a file named stage2. Remove one found in --out, since it
# is a compiler this build did not make and would be run in place of `turkey`
# by anything that still names it.
rm -f "$out/stage2"

if [ $fixpoint = 1 ]; then
    emit "$out/turkey" "$out/stage3"
    emit "$out/stage3" "$out/stage4"
    step "compare stage3 stage4"
    if ! cmp -s "$out/stage3" "$out/stage4"; then
        assembly "$out/turkey" > "$out/stage3.s"
        assembly "$out/stage3" > "$out/stage4.s"
        difference=$(cmp "$out/stage3.s" "$out/stage4.s")
        echo "build.sh: no fixpoint: ${difference:-the executables differ, the assembly does not}" >&2
        line=${difference##* line }
        case $line in *[!0-9]*|'') ;; *)
            echo "  stage3: $(sed -n "${line}p" "$out/stage3.s")" >&2
            echo "  stage4: $(sed -n "${line}p" "$out/stage4.s")" >&2 ;;
        esac
        exit 1
    fi
fi

step "done: $out/turkey"
