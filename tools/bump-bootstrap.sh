#!/bin/sh
# Replace the committed bootstrap with today's compiler.
#
# A policy step, not a build step: bump only when the source needs something
# the committed compiler cannot build, or the runtime's ABI changed. The bump is
# its own commit, containing only bootstrap/, and a bad one is reverted.
# BOOTSTRAP.md has the policy.
#
# Usage: tools/bump-bootstrap.sh [--stage1 BINARY]
#
#   --stage1 BINARY  start from this compiler instead of the committed one
#                    (passed through to build.sh)

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

BOOTSTRAP=bootstrap
ARTIFACT=$BOOTSTRAP/arm64-darwin.s.gz
OUT=build/bump

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "bump-bootstrap.sh: the tree has uncommitted changes;" \
         "PROVENANCE must name the commit the bootstrap was built from" >&2
    exit 1
fi

sh tools/build.sh --out "$OUT" --fixpoint "$@"

mkdir -p "$BOOTSTRAP/runtime"
gzip -9 -n -c "$OUT/stage3.s" > "$ARTIFACT"
cp runtime/turkey_runtime.c runtime/turkey_runtime.h "$BOOTSTRAP/runtime/"

cat > "$BOOTSTRAP/PROVENANCE" <<EOF
commit: $(git rev-parse HEAD)
date: $(date -u +%Y-%m-%d)
target: arm64-apple-darwin
command: native boot/Main.gob
asm-sha256: $(shasum -a 256 "$OUT/stage3.s" | cut -d' ' -f1)
gz-sha256: $(shasum -a 256 "$ARTIFACT" | cut -d' ' -f1)
cc: $(cc --version | head -n 1)
EOF

echo
echo "bootstrap/ now holds $(git rev-parse --short HEAD)'s compiler. Commit it alone:"
echo
echo "    git add $BOOTSTRAP && git commit -m 'bootstrap: bump to $(git rev-parse --short HEAD)'"
