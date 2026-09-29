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
    names = [f"Probe_early_{suffix}", f"Probe_zdependency_{suffix}"]
    paths = [bootc.REPO_ROOT / "lib" / Path(n.replace(".", "/") + ".gob")
             for n in names]
    entry = tmp_path / "main.gob"
    env = dict(os.environ, TURKEY_TEST_GIBLETS=",".join(names),
               TURKEY_TEST_FOREIGN=",".join(names))

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
        'import Turkey.Libc as C\n'
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
    probe = tmp_path / "probe.c"
    probe.write_text('''
#include <stdint.h>
#include <stdlib.h>
#include "turkey_exports.h"
static int steps;
int64_t probe_step(int64_t n) {
    /* In order, and before the program has allocated anything. */
    if (n != ++steps || turkey_heap_objects() != 0 || turkey_collection_count() != 0)
        abort();
    return n == 1 ? 40 : 2;
}
''')
    binary = tmp_path / "program"
    subprocess.run([*toolchain.cc(), "-O1", "-I", str(bootc.PROBE_INCLUDE),
                    *toolchain.clang_only("-Wno-override-module"), str(generated),
                    str(probe), *toolchain.libraries(), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    for stress in ["0", "1"]:
        run = subprocess.run(toolchain.command(binary), capture_output=True, text=True,
                             env=dict(env, TURKEY_GC_STRESS=stress))
        assert run.returncode == 0, run.stderr
        assert run.stdout == "initialized\n43\n"


@pytest.mark.parametrize("backend", toolchain.BACKENDS)
def test_c_can_run_early_initialization(modules, tmp_path, backend):
    """`turkey_giblets_initialize` is the early initializer under a C name, so
    that C which runs a program's Turkey code without running the program sets
    up giblet state exactly as `turkey_entry` does."""
    entry, env = modules(
        'foreign "probe_step" fun step() -> Int\n'
        'var runs : Int = step()\n'
        'foreign "probe_read" fun read() -> Int = runs\n',
        'let base : Int = 0')
    result = compile_source(entry, env, backend)
    assert result.returncode == 0, result.stderr
    # The probe supplies C's main, and never runs the program's.
    main = toolchain.c_symbol("main")
    text = result.stdout.replace(f'"{main}"', '"_unused_probe_main"')
    text = text.replace("@main(", "@unused_probe_main(")
    generated = tmp_path / ("program.s" if backend == "native" else "program.ll")
    generated.write_text(text)
    probe = tmp_path / "probe.c"
    probe.write_text('''
#include <stdint.h>
#include <stdio.h>
#include "turkey_exports.h"
static int64_t steps;
int64_t probe_step(void) { return ++steps; }
int64_t probe_read(void);
int main(void) {
    printf("%lld ", (long long)probe_read());
    turkey_giblets_initialize();
    printf("%lld ", (long long)probe_read());
    turkey_giblets_initialize();
    printf("%lld\\n", (long long)probe_read());
    return 0;
}
''')
    binary = tmp_path / "program"
    subprocess.run([*toolchain.cc(), "-O1", "-I", str(bootc.PROBE_INCLUDE),
                    *toolchain.clang_only("-Wno-override-module"), str(generated),
                    str(probe), *toolchain.libraries(),
                    "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    run = subprocess.run(toolchain.command(binary), capture_output=True, text=True,
                         env=env)
    assert run.returncode == 0, run.stderr
    # Zero before, as untraced storage starts; each call runs the initializer.
    assert run.stdout == "0 1 2\n"


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
    ('import Turkey.Heap as H\n'
     'let value : Prim.Ptr = H.allocate(32, 0, 0, Prim.frameAddress())\nfun read() -> Int = 0',
     'let base : Int = 0', 'may not call the collector'),
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
        'import Turkey.Process as R\n'
        'let failed : Int = fail()\n'
        'fun fail() -> Int { R.panic(Prim.cString("early failure")); 0 }\n'
        'fun read() -> Int = failed', 'let base : Int = 0')
    result = compile_source(entry, env, backend)
    assert result.returncode == 0, result.stderr
    generated = tmp_path / ("program.s" if backend == "native" else "program.ll")
    generated.write_text(result.stdout)
    probe = tmp_path / "probe.c"
    probe.write_text('#include <stdint.h>\n#include <stdio.h>\n#include "turkey_exports.h"\n'
                     '__attribute__((destructor)) static void report(void) '
                     '{ fprintf(stderr, "objects %lld\\n", (long long)turkey_heap_objects()); }\n')
    binary = tmp_path / "program"
    subprocess.run([*toolchain.cc(), "-O1", "-I", str(bootc.PROBE_INCLUDE),
                    *toolchain.clang_only("-Wno-override-module"), str(generated),
                    str(probe), *toolchain.libraries(), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    run = subprocess.run(toolchain.command(binary), capture_output=True, text=True, env=env)
    assert run.returncode == 1, run.stderr
    assert run.stdout == ""
    assert run.stderr.startswith("panic: early failure\n")
    # Nothing was allocated: the entry stopped before interning a literal.
    assert run.stderr.endswith("objects 0\n")
