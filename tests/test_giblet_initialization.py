"""Giblet state exists before the first managed allocation on both backends."""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain


@pytest.fixture
def modules(request, tmp_path):
    suffix = hashlib.sha1(request.node.nodeid.encode()).hexdigest()[:10]
    names = [f"Unsafe.Probe_early_{suffix}", f"Unsafe.Probe_zdependency_{suffix}"]
    paths = [bootc.REPO_ROOT / "lib" / Path(n.replace(".", "/") + ".gob")
             for n in names]
    entry = tmp_path / "main.gob"
    env = dict(os.environ, TURKEY_TEST_GIBLETS=",".join(names))

    def write(body, dependency="", main="print(P.read())", exports="read",
              dependency_giblet=True):
        paths[1].write_text(f"module {names[1]} (base)\n{dependency}\n")
        paths[0].write_text(f"module {names[0]} ({exports})\n"
                            f"import {names[1]} as D\n{body}\n")
        entry.write_text(f"import {names[0]} as P\nfun main() {{ {main} }}\n")
        actual_env = dict(env)
        if not dependency_giblet:
            actual_env["TURKEY_TEST_GIBLETS"] = names[0]
        return entry, actual_env

    try:
        yield write
    finally:
        for path in paths:
            path.unlink(missing_ok=True)


def compile_source(entry, env, command):
    return subprocess.run(toolchain.command(bootc.binary(), command, str(entry)),
                          cwd=bootc.REPO_ROOT, env=env,
                          capture_output=True, text=True)


@pytest.mark.parametrize("backend", toolchain.BACKENDS)
def test_globals_precede_every_managed_allocation(modules, tmp_path, backend):
    entry, env = modules(
        'import Unsafe.Libc as C\n'
        'foreign "probe_step" fun step(Int) -> Int\n'
        'let answer : Int = D.base + step(2)\n'
        'var state : Prim.Ptr = C.malloc(8)\n'
        'let _ = Prim.storeI64(state, 0, answer)\n'
        'foreign "probe_ready" fun ready() -> Int = Prim.loadI64(state, 0)\n'
        'fun read() -> Int {\n'
        '    let before = ready()\n'
        '    let old = state\n'
        '    state = C.malloc(8)\n'
        '    Prim.storeI64(state, 0, before + 1)\n'
        '    C.free(old)\n'
        '    let after = ready()\n'
        '    C.free(state)\n'
        '    state = Prim.ptrNull()\n'
        '    after\n'
        '}\n',
        'foreign "probe_step" fun step(Int) -> Int\n'
        'var base : Int = step(1)\n',
        main='print("initialized"); print(P.read())')
    result = compile_source(entry, env, backend)
    assert result.returncode == 0, result.stderr
    generated = tmp_path / ("program.s" if backend == "native" else "program.ll")
    generated.write_text(result.stdout)
    runtime = tmp_path / "runtime.o"
    subprocess.run([*toolchain.cc(), "-std=c11", "-O1", "-c", str(bootc.RUNTIME),
                    "-Dturkey_heap_allocate=probe_allocate", "-o", str(runtime)],
                   check=True, capture_output=True, text=True)
    probe = tmp_path / "probe.c"
    probe.write_text('''
#include <stdint.h>
#include <stdlib.h>
extern void *probe_allocate(uint64_t, int64_t, void *);
extern int64_t probe_ready(void);
static int steps, allocated;
int64_t probe_step(int64_t n) {
    if (allocated || n != ++steps) abort();
    return n == 1 ? 40 : 2;
}
void *turkey_heap_allocate(uint64_t size, int64_t kind, void *frame) {
    if (!allocated && (steps != 2 || probe_ready() != 42)) abort();
    allocated = 1;
    return probe_allocate(size, kind, frame);
}
''')
    binary = tmp_path / "program"
    subprocess.run([*toolchain.cc(), "-O1",
                    *toolchain.clang_only("-Wno-override-module"), str(generated),
                    str(runtime), str(probe), *toolchain.libraries(), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    for stress in ["0", "1"]:
        run = subprocess.run(toolchain.command(binary), capture_output=True, text=True,
                             env=dict(env, TURKEY_GC_STRESS=stress))
        assert run.returncode == 0, run.stderr
        assert run.stdout == "initialized\n43\n"


@pytest.mark.parametrize("body, dependency, message", [
    ('let value : Int = Prim.error("early")\nfun read() -> Int = value',
     'let base : Int = 0', 'panics with a String'),
    ('let value : Int = helper(2)\n'
     'fun helper(n : Int) -> Int = if n == 0 { Prim.error("early") } '
     'else { helper(n - 1) }\nfun read() -> Int = value',
     'let base : Int = 0', 'panics with a String'),
    ('let value : Int = helper(2)\n'
     'foreign "probe_early_helper" fun helper(n : Int) -> Int = '
     'if n == 0 { Prim.error("early") } else { helper(n - 1) }\n'
     'fun read() -> Int = value',
     'let base : Int = 0', 'panics with a String'),
    ('import Unsafe.Runtime as R\n'
     'let value : Prim.Ptr = R.heapAllocate(32, 0, Prim.frameAddress())\nfun read() -> Int = 0',
     'let base : Int = 0', 'may not call turkey_heap_allocate'),
    ('let (a, b) : (Int, Int) = (1, 2)\nfun read() -> Int = a + b',
     'let base : Int = 0', 'builds a tuple'),
    ('var value : String = "traced"\nfun read() -> Int = 0',
     'let base : Int = 0', 'uses the string literal "traced"'),
    ('var value : Option Int = None\nfun read() -> Int = 0',
     'let base : Int = 0', 'accesses the later global %nullary'),
])
def test_unsafe_initializers_are_refused(modules, body, dependency, message):
    entry, env = modules(body, dependency)
    result = compile_source(entry, env, "ssa")
    assert result.returncode != 0
    assert message in result.stderr, result.stderr


@pytest.mark.parametrize("dependency, message", [
    ('fun base(n : Int) -> Int = if n == 0 { len([n, n]) } '
     'else { base(n - 1) }', 'may allocate'),
    ('foreign "getpid" fun pid() -> Int\nlet later : Int = pid()\n'
     'fun base(n : Int) -> Int = if n == 0 { later } '
     'else { base(n - 1) }', 'accesses the later global'),
    ('fun base(n : Int) -> Int = if n == 0 { Prim.error("early") } '
     'else { base(n - 1) }', 'uses a string literal before interning'),
])
def test_early_helpers_cannot_reach_the_late_runtime(modules, dependency, message):
    entry, env = modules('let value : Int = D.base(2)\n'
                         'fun read() -> Int = value', dependency,
                         dependency_giblet=False)
    result = compile_source(entry, env, "ssa")
    assert result.returncode != 0
    assert message in result.stderr, result.stderr


@pytest.mark.parametrize("backend", toolchain.BACKENDS)
def test_early_raw_panic_stops_before_allocation(modules, tmp_path, backend):
    entry, env = modules(
        'import Unsafe.Runtime as R\n'
        'let failed : Int = fail()\n'
        'fun fail() -> Int { R.panic(Prim.cString("early failure")); 0 }\n'
        'fun read() -> Int = failed', 'let base : Int = 0')
    result = compile_source(entry, env, backend)
    assert result.returncode == 0, result.stderr
    generated = tmp_path / ("program.s" if backend == "native" else "program.ll")
    generated.write_text(result.stdout)
    runtime = tmp_path / "runtime.o"
    subprocess.run([*toolchain.cc(), "-std=c11", "-O1", "-c", str(bootc.RUNTIME),
                    "-Dturkey_heap_allocate=unused_allocate", "-o", str(runtime)],
                   check=True, capture_output=True, text=True)
    probe = tmp_path / "probe.c"
    probe.write_text('#include <stdint.h>\n#include <stdlib.h>\n'
                     'void *turkey_heap_allocate(uint64_t size, int64_t kind, void *frame) '
                     '{ abort(); }\n')
    binary = tmp_path / "program"
    subprocess.run([*toolchain.cc(), "-O1",
                    *toolchain.clang_only("-Wno-override-module"), str(generated),
                    str(runtime), str(probe), *toolchain.libraries(), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    run = subprocess.run(toolchain.command(binary), capture_output=True, text=True, env=env)
    assert run.returncode == 1, run.stderr
    assert run.stdout == ""
    assert run.stderr.startswith("panic: early failure\n")
