#!/bin/zsh
# What the shadow stack costs, measured through boot's own LLVM path.
#
# NATIVE-BACKEND.md "Frames, calls and roots, surveyed" quotes these numbers;
# this rebuilds and reruns them. Stage2 `boot` -- `Turkey.Llvm`'s output for
# `boot/Main.gob` -- runs `boot asm` over the corpus in five variants:
#
#   calls         .ll and runtime compiled apart at -O2: enter/leave are calls
#   inline        -flto -O2: enter/leave inline (the inline shadow stack)
#   inline-nogc   inline, and collection never runs
#   tables-nogc   enter/leave stubbed and the mask stores deleted, root stores
#                 kept, no collection: what frame tables leave behind
#   none-nogc     root stores deleted too
#
# The no-GC variants are only correct because nothing collects; they exist to
# be timed, not run for real. Usage: benchmarks/shadow_stack.sh [workdir]
setopt nomultios
set -e
REPO=${0:A:h:h}
WORK=${1:-${TMPDIR:-/tmp}/turkey-shadow-stack}
mkdir -p $WORK
cd $WORK
BOOT=$(cd $REPO && python3 -c "from tests import bootc; print(bootc.binary())")
[[ -s boot.ll ]] || (cd $REPO && $BOOT llvm boot/Main.gob) | sed '1{/^; === /d;}' > boot.ll
cp $REPO/runtime/turkey_runtime.c runtime.c
cp $REPO/runtime/turkey_runtime.h turkey_runtime.h

python3 - <<'PY'
import re
src = open("runtime.c").read()
enter = ("void turkey_root_enter(void *pointer, void *values, int64_t count,\n"
         "                       const char *function_name) {\n")
leave = "void turkey_root_leave(void *pointer) {\n"
threshold = "static int64_t collection_threshold = 1024;"
assert enter in src and leave in src and threshold in src
nogc = src.replace(threshold, "static int64_t collection_threshold = INT64_MAX;")
open("runtime_nogc.c", "w").write(nogc)
stub = nogc
for head, body in ((enter, "    (void)pointer; (void)values; (void)count; (void)function_name;"),
                   (leave, "    (void)pointer;")):
    i = stub.index(head); j = stub.index("\n}\n", i)
    stub = stub[:i] + head + body + stub[j:]
open("runtime_stub.c", "w").write(stub)

lines = open("boot.ll").read().split("\n")
def strip(kinds):
    out, i, dropped = [], 0, 0
    gep = re.compile(r"\s*(%t\d+) = getelementptr inbounds (ptr, ptr %roots|i8, ptr %frame), i64 \d+$")
    while i < len(lines):
        m = gep.match(lines[i])
        if (m and ("roots" in m.group(2)) in kinds and i + 1 < len(lines)
                and re.match(r"\s*store (ptr|i64) \S+, ptr " + re.escape(m.group(1)) + r"$", lines[i + 1])):
            dropped += 1; i += 2; continue
        out.append(lines[i]); i += 1
    return "\n".join(out), dropped
text, masks = strip({False})
open("boot-nomasks.ll", "w").write(text)
text, both = strip({False, True})
open("boot-nostores.ll", "w").write(text)
print(f"{sum('@turkey_root_enter(' in l for l in lines)} enter sites, "
      f"{masks} mask stores, {both - masks} root stores")
PY

build() { local name=$1 module=$2 runtime=$3; shift 3
          [[ -x $name ]] || cc -w -std=c11 "$@" -o $name $module $runtime; }
build boot-calls        boot.ll          runtime.c      -O2
build boot-inline       boot.ll          runtime.c      -O2 -flto
build boot-inline-nogc  boot.ll          runtime_nogc.c -O2 -flto
build boot-tables-nogc  boot-nomasks.ll  runtime_stub.c -O2 -flto
build boot-none-nogc    boot-nostores.ll runtime_stub.c -O2 -flto

PROGS=($(cd $REPO && ls tests/programs/*.gob | grep -v /err_))
typeset -A best
for round in 1 2 3 4 5 6 7 8 9; do
  for v in calls inline inline-nogc tables-nogc none-nogc; do
    t0=$(perl -MTime::HiRes=time -e 'printf "%.3f", time')
    (cd $REPO && $WORK/boot-$v asm $PROGS > /dev/null)
    t1=$(perl -MTime::HiRes=time -e 'printf "%.3f", time')
    dt=$(( t1 - t0 ))
    if [[ -z ${best[$v]} ]] || (( dt < best[$v] )); then best[$v]=$dt; fi
  done
done
for v in calls inline inline-nogc tables-nogc none-nogc; do
  printf "%-12s best of 9: %.3f s\n" $v ${best[$v]}
done
