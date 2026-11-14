#!/bin/sh
# Build the compiler from the committed bootstrap, with no Python involved.
#
#   stage1  the committed assembly (bootstrap/), linked
#   stage2  the executable stage1 writes for today's source -- the result
#   stage3  the executable stage2 writes for the same source  (--fixpoint only)
#   stage4  the executable stage3 writes, which must be stage3 (--fixpoint only)
#
# Stage N is written by stage N-1. stage2 is today's source compiled by the
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
# bootstrap/ holds the committed compiler: the whole-program assembly for
# src/Main.gob once per target, each under gzip -9 -n (6.5 MB each; -n so one
# gzip always compresses the same text to the same bytes, though GNU's and
# macOS's differ from each other), and PROVENANCE, which records the commit
# they came from and the hashes checked here before anything is built. Every
# target's assembly is the same compiler emitting for a different platform, so
# a build on any of them starts from the same source. To reproduce it, check out that commit and
# build it with the bootstrap that commit itself carries.
#
# The target is the one the C compiler links for, read from `$CC -dumpmachine`,
# unless --target names it; every stage is written for it and stage1 is linked
# by $CC, so the two must agree.
# Nothing here asks what the host is: under qemu-user an x86-64 Linux machine
# builds and runs the arm64 Linux compiler, with $TURKEY_CC a cross compiler.
#
# Each stage is the whole program, the runtime included -- the collector, the
# allocator and the panic state are Turkey in lib/, compiled in with
# everything else -- so stage1 is linked from its assembly alone, against
# nothing but the C library, and every later stage loads only the C library.
#
# Measured on arm64 macOS, Apple clang 17: 9 s to link stage1, then about 5 s
# for each later stage; 25 s with --fixpoint.
#
# Usage: scripts/build.sh [--out DIR] [--target TARGET] [--fixpoint]
#                         [--stage1 BINARY] [--stage1-only]
#
#   --out DIR        where the stages go (default build/stages)
#   --target TARGET  arm64-darwin or arm64-linux (default: what $CC links for)
#   --fixpoint       also build stage3 and stage4, and check that stage4 is
#                    stage3, byte for byte
#   --stage1 BINARY  use an existing compiler as stage1 instead of the committed
#                    assembly; how to bump across a change the committed
#                    compiler cannot build. It must accept
#                    `build -o PATH -f exe --target TARGET`, which a compiler
#                    older than the executable writer does not
#   --stage1-only    stop after stage1: what scripts/test.sh compiles the test
#                    harness with

set -eu

# The C compiler and linker, and a prefix for running what it links, both split
# into words: $TURKEY_CC is `cc` when unset, and an empty $TURKEY_RUN runs a
# stage directly. README.md says what they are for; tests/toolchain.py reads
# the same two.
CC=${TURKEY_CC:-cc}
RUN=${TURKEY_RUN:-}

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
TURKEY_LIB=lib
export TURKEY_LIB

# The compiler's entry module. Relative, and always spelled the same way: it is
# printed in the first line of the assembly, so the spelling is in the bytes.
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

# What $CC links for, as a target, or nothing if the triple is not one this
# knows: arm64-apple-darwin25.6.0 from Apple clang, aarch64-linux-gnu from gcc.
# --target is for a compiler whose triple is spelled some other way.
machine=$($CC -dumpmachine 2>/dev/null) || {
    echo "build.sh: cannot run the C compiler '$CC'" >&2
    exit 1
}
case $machine in
    arm64-apple-darwin*|arm64-apple-macos*|aarch64-apple-darwin*)
        linked=arm64-darwin ;;
    aarch64*-linux*|arm64*-linux*) linked=arm64-linux ;;
    *) linked= ;;
esac
if [ -z "$target" ]; then
    target=$linked
fi
case $target in
    arm64-darwin|arm64-linux) ;;
    '') echo "build.sh: '$CC' links for $machine, which is not a target;" \
             "set TURKEY_CC to an arm64 macOS or Linux C compiler," \
             "or pass --target" >&2
        exit 1 ;;
    *) echo "build.sh: unknown target '$target';" \
            "supported: arm64-darwin, arm64-linux" >&2
       exit 2 ;;
esac
if [ -n "$linked" ] && [ "$target" != "$linked" ]; then
    echo "build.sh: the target is $target but '$CC' links for $machine" >&2
    exit 1
fi
ARTIFACT=$BOOTSTRAP/$target.s.gz

# glibc keeps the maths library out of libc, and lib/ calls fmod and floor;
# Darwin's libSystem carries it, so there is nothing to add there.
case $target in
    arm64-linux) LIBS=-lm ;;
    *) LIBS= ;;
esac

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
    $RUN "$1" build -o "$2-tmp" -f exe --target "$target" "$SOURCE"
    mv "$2-tmp" "$2"
}

# The same, as assembly on standard output: for saying where two stages
# differ, since an executable's first differing byte names no line.
assembly() {
    $RUN "$1" build -o - -f asm --target "$target" "$SOURCE"
}

link() {
    step "link $(basename "$2")"
    $CC -o "$2.tmp" "$1" $LIBS
    mv "$2.tmp" "$2"
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
    gunzip -c "$ARTIFACT" > "$out/stage1.s"
    if [ "$(sha "$out/stage1.s")" != "$(provenance "$target asm-sha256")" ]; then
        echo "build.sh: $ARTIFACT decompresses to the wrong bytes" >&2
        exit 1
    fi
    link "$out/stage1.s" "$out/stage1"
    rm "$out/stage1.s"
fi

if [ $only1 = 1 ]; then
    step "done: $out/stage1"
    exit 0
fi

emit "$out/stage1" "$out/stage2"

if [ $fixpoint = 1 ]; then
    emit "$out/stage2" "$out/stage3"
    emit "$out/stage3" "$out/stage4"
    step "compare stage3 stage4"
    if ! cmp -s "$out/stage3" "$out/stage4"; then
        assembly "$out/stage2" > "$out/stage3.s"
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

step "done: $out/stage2"
