#!/bin/sh
# Replace the committed bootstrap with today's compiler.
#
# A policy step, not a build step. Bump only when the source needs something the
# committed compiler cannot build -- a new language feature, or a changed
# runtime ABI. Land the feature, bump, and only then use the feature in the
# compiler; a bump for any other reason is 7 MB of git history for nothing.
#
# The bump is its own commit, containing only bootstrap/, so that a bad one is
# fixed by reverting it. This runs the fixed point first and refuses a dirty
# tree, because PROVENANCE has to name the commit the assembly was built from.
#
# There is one artifact per target, arm64-darwin.s.gz and arm64-linux.s.gz, and
# a bump writes both from the fixed-point compiler, whichever target that was
# built for: the compiler emits either platform's assembly from any host. They
# move together so that a build on either platform starts from the same
# commit's compiler, and so that PROVENANCE names one commit, not two.
#
# --stage1 is how to bump across a change the committed compiler cannot build:
# start from a compiler built before the change. The first bump was made that
# way, from the last compiler the Python implementation built.
#
# Usage: scripts/bump-bootstrap.sh [--stage1 BINARY] [--target TARGET]
#
#   --stage1 BINARY  start from this compiler instead of the committed one
#   --target TARGET  what to build the fixed point for (both are passed through
#                    to build.sh; the artifacts are for every target regardless)

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

RUN=${TURKEY_RUN:-}

BOOTSTRAP=bootstrap
TARGETS="arm64-darwin arm64-linux"
OUT=build/bump

sha() {
    if command -v shasum > /dev/null; then
        shasum -a 256 "$1" | cut -d' ' -f1
    else
        sha256sum "$1" | cut -d' ' -f1
    fi
}

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "bump-bootstrap.sh: the tree has uncommitted changes;" \
         "PROVENANCE must name the commit the bootstrap was built from" >&2
    exit 1
fi

sh scripts/build.sh --out "$OUT" --fixpoint "$@"

mkdir -p "$BOOTSTRAP/runtime"
cp runtime/turkey_runtime.c runtime/turkey_runtime.h "$BOOTSTRAP/runtime/"

cat > "$OUT/PROVENANCE" <<EOF
commit: $(git rev-parse HEAD)
date: $(date -u +%Y-%m-%d)
command: native --target TARGET src/Main.gob
cc: $(${TURKEY_CC:-cc} --version | head -n 1)
EOF
for target in $TARGETS; do
    echo "emit $target" >&2
    $RUN "$OUT/stage3" native --target "$target" src/Main.gob > "$OUT/$target.s"
    # -n makes one gzip compress the same text to the same bytes, but GNU
    # gzip and macOS's disagree. An artifact whose text is unchanged is kept,
    # so a bump from the other platform does not rewrite 3.5 MB of it.
    if [ ! -f "$BOOTSTRAP/$target.s.gz" ] ||
            ! gunzip -c "$BOOTSTRAP/$target.s.gz" | cmp -s - "$OUT/$target.s"; then
        gzip -9 -n -c "$OUT/$target.s" > "$BOOTSTRAP/$target.s.gz"
    fi
    cat >> "$OUT/PROVENANCE" <<EOF
$target asm-sha256: $(sha "$OUT/$target.s")
$target gz-sha256: $(sha "$BOOTSTRAP/$target.s.gz")
EOF
done
mv "$OUT/PROVENANCE" "$BOOTSTRAP/PROVENANCE"

echo
echo "bootstrap/ now holds $(git rev-parse --short HEAD)'s compiler. Commit it alone:"
echo
echo "    git add $BOOTSTRAP && git commit -m 'bootstrap: bump to $(git rev-parse --short HEAD)'"
