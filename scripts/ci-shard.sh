#!/bin/sh
# Print the pytest arguments for one shard of the suite, for CI.
#
#   python3 -m pytest -q -m '' $(sh scripts/ci-shard.sh SHARD)
#
# CI splits the suite across jobs because a free runner has four cores and the
# suite wants far more; `.github/workflows/linux.yml` runs one job per shard.
#
# Shards are by what the tests exercise, not by count, and the grouping keeps
# tests that share a disk-cached corpus-wide run together, since a shard on
# its own runner pays for every such run it touches:
#
#   self      the compiler compiling itself: the fixed point, and the arm64
#             emitter over the compiler's source and the corpus, which
#             test_emit, test_bootstrap and test_arm64_native share
#   backend   instruction selection, SSA and the other backends
#   corpus    the recorded programs, the reference's examples, and diagnostics
#   language  every other file, so a new test file lands somewhere without
#             anyone listing it
#
# Measured on arm64 macOS with four workers, cold, each shard is 700-1200
# worker-seconds; `self` is the largest and holds the longest serial chain
# (stage2 compiling the compiler, then stage3 compiling it again).

set -eu

SELF="test_bootstrap test_emit test_arm64_native"
BACKEND="test_select test_native test_ssa_lower test_ssa test_sccp test_mono
         test_heap_verifier test_runtime_gc test_allocators test_alloc_sites"
CORPUS="test_reference test_programs test_infer_corpus test_errors test_entry
        test_system test_target"

files() {
    for name in $1; do
        printf 'tests/%s.py\n' "$name"
    done
}

case ${1:-} in
    self)     files "$SELF" ;;
    backend)  files "$BACKEND" ;;
    corpus)   files "$CORPUS" ;;
    language)
        echo tests
        files "$SELF $BACKEND $CORPUS" | sed 's/^/--ignore=/'
        ;;
    *)
        echo "usage: $0 self|backend|corpus|language" >&2
        exit 2
        ;;
esac
