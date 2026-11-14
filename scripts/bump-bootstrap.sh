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
# tree, because PROVENANCE has to name the commit the compiler was built from.
#
# There is one artifact per target, arm64-darwin.gz and arm64-linux.gz: the
# executable the fixed-point compiler writes for that target, under
# gzip -9 -n (3.6 MB each). A bump writes both, whichever target the fixed
# point was built for, since the compiler writes either platform's executable
# from any host. They move together so that a build on either platform starts
# from the same commit's compiler, and so that PROVENANCE names one commit,
# not two.
#
# The executables are deterministic: the same compiler writes the same bytes
# for a target on any host, whatever the output is called. So anyone can check
# a bump by checking out PROVENANCE's commit, building it from the bootstrap
# that commit carries, and running PROVENANCE's command for each target.
#
# --stage1 is how to bump across a change the committed compiler cannot build:
# start from a compiler built before the change. It must write executables
# (`build -f exe`), which a compiler older than the executable writer cannot.
#
# Usage: scripts/bump-bootstrap.sh [--stage1 BINARY] [--target TARGET]
#
#   --stage1 BINARY  start from this compiler instead of the committed one
#   --target TARGET  what to build the fixed point for (both go to the fixed
#                    point's build, and the check of the committed compiler
#                    gets only --target; the artifacts are for every target
#                    regardless)

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

# From here on bootstrap/ is being rewritten, so any failure says how to put
# it back.
trap 'echo "bump-bootstrap.sh: failed; restore bootstrap/ with git checkout -- bootstrap" >&2' EXIT

cat > "$OUT/PROVENANCE" <<EOF
commit: $(git rev-parse HEAD)
date: $(date -u +%Y-%m-%d)
command: build -o OUTPUT --target TARGET src/Main.gob
EOF
for target in $TARGETS; do
    echo "write $target" >&2
    TURKEY_LIB=lib $RUN "$OUT/stage3" build -o "$OUT/$target" --target "$target" src/Main.gob
    # -n makes one gzip compress the same bytes to the same bytes, but GNU
    # gzip and macOS's disagree. An artifact whose executable is unchanged is
    # kept, so a bump from the other platform does not rewrite 3.6 MB of it.
    if [ ! -f "$BOOTSTRAP/$target.gz" ] ||
            ! gunzip -c "$BOOTSTRAP/$target.gz" | cmp -s - "$OUT/$target"; then
        gzip -9 -n -c "$OUT/$target" > "$BOOTSTRAP/$target.gz"
    fi
    cat >> "$OUT/PROVENANCE" <<EOF
$target exe-sha256: $(sha "$OUT/$target")
$target gz-sha256: $(sha "$BOOTSTRAP/$target.gz")
EOF
done
mv "$OUT/PROVENANCE" "$BOOTSTRAP/PROVENANCE"

# Unpack stage1 from the host's executable just committed, as build.sh will,
# and check that it writes every target's executable as the fixed point did:
# its own, which is stage3's bytes, and the other target's, which nothing here
# runs. Only --target is passed on, so that this stage1 is the committed one.
target=
previous=
for argument in "$@"; do
    if [ "$previous" = --target ]; then
        target=$argument
    fi
    previous=$argument
done
sh scripts/build.sh --out "$OUT/committed" --stage1-only ${target:+--target "$target"}
for other in $TARGETS; do
    TURKEY_LIB=lib $RUN "$OUT/committed/stage1" build -o "$OUT/committed/$other" \
        --target "$other" src/Main.gob
    if ! cmp -s "$OUT/committed/$other" "$OUT/$other"; then
        echo "bump-bootstrap.sh: the committed compiler writes $other's executable" \
             "differently from the fixed point" >&2
        exit 1
    fi
done
trap - EXIT

echo
echo "bootstrap/ now holds $(git rev-parse --short HEAD)'s compiler. The host's executable"
echo "ran and writes every target's as the fixed point did; nothing here ran the"
echo "other's. Commit it alone:"
echo
echo "    git add $BOOTSTRAP && git commit -m 'bootstrap: bump to $(git rev-parse --short HEAD)'"
