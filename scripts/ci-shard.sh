#!/bin/sh
# Print the pytest arguments for one shard of the suite, for CI.
#
#   python3 -m pytest -q -m '' $(sh scripts/ci-shard.sh SHARD)
#
# CI splits the suite across jobs because a free runner has four cores and the
# suite wants far more; `.github/workflows/linux.yml` runs one job per shard.
#
# Shards are by what the tests exercise, not by count, and the grouping keeps
# tests that share a disk-cached corpus-wide run together where it can, since
# a shard on its own runner pays for every such run it touches:
#
#   bootstrap  the fixed point, and the emitter over the compiler's source,
#              which test_emit and test_bootstrap share
#   native     the corpus built by the arm64 backend and run under GC stress
#   assembler  the encoder and the object writer, checked against `as`
#   passes     instruction selection, SSA, the machine checks, the runtime,
#              the inference corpus and diagnostics
#   system     I/O, processes, tasks and the poller
#   language   every other file, so a new test file lands somewhere without
#              anyone listing it
#
# Measured on arm64 macOS, cold, in worker-seconds: native about 550,
# language 500, assembler 360, system 350, passes 220, and bootstrap 150 plus
# the emission it waits on. A four-core runner takes nearly twice as long per
# worker-second. `native` and `bootstrap` set the wall time: each holds a
# serial chain (one `boot` run over the corpus, then the slowest program under
# GC stress; stage2 compiling the compiler, then stage3 over the corpus) that
# more cores do not shorten.

set -eu

BOOTSTRAP="test_bootstrap test_emit"
NATIVE="test_arm64_native"
ASSEMBLER="test_encode test_object"
PASSES="test_select test_machine_check test_ssa_lower test_ssa test_sccp
        test_mono test_heap_verifier test_runtime_gc test_allocators
        test_alloc_sites test_infer_corpus test_errors test_entry
        test_system test_target"
SYSTEM="test_process test_poller test_poller_kernel test_parallel_compile
        test_parallel test_filesystem test_file test_fd test_fd_poll
        test_io_floor test_streams test_stdout test_tasks test_coroutines
        test_sync test_time_zones"

files() {
    for name in $1; do
        printf 'tests/%s.py\n' "$name"
    done
}

case ${1:-} in
    bootstrap) files "$BOOTSTRAP" ;;
    native)    files "$NATIVE" ;;
    assembler) files "$ASSEMBLER" ;;
    passes)    files "$PASSES" ;;
    system)    files "$SYSTEM" ;;
    language)
        echo tests
        files "$BOOTSTRAP $NATIVE $ASSEMBLER $PASSES $SYSTEM" | sed 's/^/--ignore=/'
        ;;
    *)
        echo "usage: $0 bootstrap|native|assembler|passes|system|language" >&2
        exit 2
        ;;
esac
