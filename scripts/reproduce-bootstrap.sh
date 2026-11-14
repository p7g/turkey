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

# Every artifact has a record, and there is at least one, so that a
# PROVENANCE without its hashes cannot pass by checking nothing.
targets=$(sed -n 's/^\([^ ]*\) exe-sha256: .*/\1/p' bootstrap/PROVENANCE)
if [ -z "$targets" ]; then
    echo "reproduce-bootstrap.sh: PROVENANCE records no executable" >&2
    exit 1
fi
for artifact in bootstrap/*.gz; do
    case " $(echo $targets) " in
        *" $(basename "$artifact" .gz) "*) ;;
        *) echo "reproduce-bootstrap.sh: PROVENANCE has no record of $artifact" >&2
           exit 1 ;;
    esac
done

# The commit has to be in this checkout's history, not merely somewhere on a
# server: a squash or rebase merge of the bump's branch rewrites the commit
# PROVENANCE names, and its record then points at nothing on main.
if ! git cat-file -e "$commit^{commit}" 2> /dev/null ||
        ! git merge-base --is-ancestor "$commit" HEAD; then
    echo "reproduce-bootstrap.sh: PROVENANCE's commit $commit is not in this" \
         "branch's history; a squash or rebase merge of the bump is the" \
         "likely cause, and the bump must be made again from the merged commit" >&2
    exit 1
fi

tree=$out/tree
mkdir -p "$out"
# A tree removed by hand is still registered until pruned, and would refuse
# the add below.
git worktree prune
if [ -e "$tree" ]; then
    git worktree remove --force "$tree"
fi
git worktree add --detach "$tree" "$commit" > /dev/null
trap 'git worktree remove --force "$tree"' EXIT

echo "build $commit" >&2
sh "$tree/scripts/build.sh" --out "$out/stages" --fixpoint

status=0
for target in $targets; do
    artifact=bootstrap/$target.gz
    written=$out/$target
    # PROVENANCE's command, split into words as the bump ran it, with the two
    # placeholders filled in word by word, so that --out may hold any
    # character.
    set --
    for word in $command; do
        case $word in
            OUTPUT) word=$written ;;
            TARGET) word=$target ;;
        esac
        set -- "$@" "$word"
    done
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
