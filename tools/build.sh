#!/bin/sh
# Build the compiler from the committed bootstrap, with no Python involved.
#
#   stage1  the committed assembly (bootstrap/), linked with bootstrap/runtime
#   stage2  stage1 compiling today's source, linked with runtime/ -- the result
#   stage3  stage2 compiling the same source             (--fixpoint only)
#   stage4  stage3's output, which must equal stage3's   (--fixpoint only)
#
# Stage N is built by stage N-1. stage2 is today's source compiled by the
# committed compiler: correct if that one is, but its code is whatever an older
# compiler emitted. stage3 is today's source compiled by today's source, and the
# fixed point is that stage3 emits exactly the assembly it was built from.
#
# The compiler reads lib/ relative to the working directory, so every stage runs
# from the repository root.
#
# bootstrap/ holds the committed compiler: the whole-program arm64 assembly for
# boot/Main.gob under gzip -9 -n (3.5 MB; -n so the same text always compresses
# to the same bytes), a copy of the C runtime it was emitted against, and
# PROVENANCE, which records the commit it came from and the hashes checked here
# before anything is built. To reproduce it from source, check out that commit
# and build it with the bootstrap that commit itself carries.
#
# The runtime copy is what makes an ABI change possible: a new compiler is built
# by the old one, which only links against the old runtime. So stage1 links
# against bootstrap/runtime and stage2 onward against runtime/. As the runtime
# moves into Turkey it shrinks; when nothing is left in C, delete the copy and
# the step that builds it.
#
# Measured on arm64 macOS, Apple clang 17: 128 s for stage2, 376 s with
# --fixpoint, of which each self-compile is about 105 s and each link 10 s.
#
# Usage: tools/build.sh [--out DIR] [--fixpoint] [--stage1 BINARY]
#
#   --out DIR        where the stages go (default build/stages)
#   --fixpoint       also build stage3 and check stage3's output equals stage2's
#   --stage1 BINARY  use an existing compiler as stage1 instead of the committed
#                    assembly; how the first bump was made, and how to bump
#                    across a change the committed compiler cannot build

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

# The compiler's entry module. Relative, and always spelled the same way: it is
# printed in the first line of the assembly, so the spelling is in the bytes.
SOURCE=boot/Main.gob
BOOTSTRAP=bootstrap
ARTIFACT=$BOOTSTRAP/arm64-darwin.s.gz

out=build/stages
fixpoint=0
stage1=
while [ $# -gt 0 ]; do
    case $1 in
        --out) out=$2; shift 2 ;;
        --fixpoint) fixpoint=1; shift ;;
        --stage1) stage1=$2; shift 2 ;;
        *) echo "usage: $0 [--out DIR] [--fixpoint] [--stage1 BINARY]" >&2
           exit 2 ;;
    esac
done

if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
    echo "build.sh: the bootstrap is arm64 macOS assembly;" \
         "this host is $(uname -s) $(uname -m)" >&2
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

sha() {
    shasum -a 256 "$1" | cut -d' ' -f1
}

# The compiler's own source, compiled by $1, into $2.
emit() {
    step "$(basename "$1") $SOURCE -> $(basename "$2")"
    "$1" native "$SOURCE" > "$2.tmp"
    mv "$2.tmp" "$2"
}

link() {
    step "link $(basename "$2")"
    cc -o "$2.tmp" "$1" "$3"
    mv "$2.tmp" "$2"
}

step "runtime"
cc -std=c11 -O1 -c -o "$out/runtime.o" runtime/turkey_runtime.c

if [ -n "$stage1" ]; then
    step "stage1 is $stage1"
    cp "$stage1" "$out/stage1"
else
    expected=$(provenance gz-sha256)
    if [ "$(sha "$ARTIFACT")" != "$expected" ]; then
        echo "build.sh: $ARTIFACT does not match PROVENANCE's gz-sha256" >&2
        exit 1
    fi
    step "bootstrap runtime"
    cc -std=c11 -O1 -c -o "$out/runtime0.o" "$BOOTSTRAP/runtime/turkey_runtime.c"
    gunzip -c "$ARTIFACT" > "$out/stage1.s"
    if [ "$(sha "$out/stage1.s")" != "$(provenance asm-sha256)" ]; then
        echo "build.sh: $ARTIFACT decompresses to the wrong bytes" >&2
        exit 1
    fi
    link "$out/stage1.s" "$out/stage1" "$out/runtime0.o"
    rm "$out/stage1.s"
fi

emit "$out/stage1" "$out/stage2.s"
link "$out/stage2.s" "$out/stage2" "$out/runtime.o"

if [ $fixpoint = 1 ]; then
    emit "$out/stage2" "$out/stage3.s"
    link "$out/stage3.s" "$out/stage3" "$out/runtime.o"
    emit "$out/stage3" "$out/stage4.s"
    step "compare stage3.s stage4.s"
    if ! difference=$(cmp "$out/stage3.s" "$out/stage4.s"); then
        echo "build.sh: no fixpoint: $difference" >&2
        line=${difference##* line }
        case $line in *[!0-9]*|'') ;; *)
            echo "  stage3: $(sed -n "${line}p" "$out/stage3.s")" >&2
            echo "  stage4: $(sed -n "${line}p" "$out/stage4.s")" >&2 ;;
        esac
        exit 1
    fi
fi

step "done: $out/stage2"
