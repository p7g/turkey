#!/bin/sh
# Check that the committed bootstrap is what its PROVENANCE says it is: the
# compiler of PROVENANCE's commit, built from the bootstrap that commit
# carries, writes exactly the committed executables.
#
#   sh scripts/reproduce-bootstrap.sh [--out DIR]
#
# Checks out the commit in a temporary worktree, builds its fixed point with
# its own scripts/build.sh, and runs PROVENANCE's command once per committed
# target, with OUTPUT and TARGET filled in, from that worktree's root with
# TURKEY_LIB=lib, as scripts/bump-bootstrap.sh ran it. Each result must equal
# the gunzipped artifact byte for byte. The executable writer puts nothing of
# the host, the time or the output's name in the bytes, so this holds on any
# host that can run the commit's build: CI runs it on arm64 Linux, which
# writes the Mach-O as a Mac does.
#
# The commit's build needs whatever its own bootstrap needed: a C compiler,
# for a commit whose bootstrap was assembly. Needs git and the commit itself,
# so a shallow clone has to fetch it first.
#
#   --out DIR   where the worktree and the stages go (default build/reproduce)

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
RUN=${TURKEY_RUN:-}

out=build/reproduce
while [ $# -gt 0 ]; do
    case $1 in
        --out) out=$2; shift 2 ;;
        *) echo "usage: $0 [--out DIR]" >&2; exit 2 ;;
    esac
done
case $out in /*) ;; *) out=$ROOT/$out ;; esac

provenance() {
    sed -n "s/^$1: //p" bootstrap/PROVENANCE
}

sha() {
    if command -v shasum > /dev/null; then
        shasum -a 256 "$1" | cut -d' ' -f1
    else
        sha256sum "$1" | cut -d' ' -f1
    fi
}

commit=$(provenance commit)
command=$(provenance command)
if [ -z "$commit" ] || [ -z "$command" ]; then
    echo "reproduce-bootstrap.sh: PROVENANCE names no commit or no command" >&2
    exit 1
fi

tree=$out/tree
mkdir -p "$out"
if [ -e "$tree" ]; then
    git worktree remove --force "$tree"
fi
git worktree add --detach "$tree" "$commit" > /dev/null
trap 'git worktree remove --force "$tree"' EXIT

echo "build $commit" >&2
sh "$tree/scripts/build.sh" --out "$out/stages" --fixpoint

status=0
for target in $(sed -n 's/^\([^ ]*\) exe-sha256: .*/\1/p' bootstrap/PROVENANCE); do
    artifact=bootstrap/$target.gz
    written=$out/$target
    # PROVENANCE's command, split into words as the bump ran it.
    set -- $(echo "$command" | sed "s|OUTPUT|$written|; s|TARGET|$target|")
    (cd "$tree" && TURKEY_LIB=lib $RUN "$out/stages/stage3" "$@")
    if gunzip -c "$artifact" | cmp -s - "$written"; then
        echo "$target: reproduced, $(sha "$written")" >&2
    else
        echo "reproduce-bootstrap.sh: $commit writes $target as $(sha "$written")," \
             "and $artifact holds $(provenance "$target exe-sha256")" >&2
        status=1
    fi
done
exit $status
