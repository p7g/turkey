"""`foreign` with a body: Turkey that C can call (SPEC-DELTAS 74, TIX-67).

A definition is the other direction of a declaration -- Turkey defines the C
symbol and C calls it -- so the rules are the declaration's, plus the one that
makes entering it free: it may appear only in a giblet module. Each rule is
checked under both compilers and the two verdicts compared, the way
`test_giblets` checks the giblet rule; the calling convention is checked by
running C against each native backend, which is the only thing that can.

The entry and the crash handler in `lib/Turkey/Entry.gob` are definitions, so
every compiled program exercises the happy path. What is here is what a
program that works cannot show: what is refused, and that a symbol reached
only from C is not dropped.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests import bootc
from tests.test_giblets import HOOK, LIB, REPO_ROOT, verdict

_SAFE = re.compile(r"[^A-Za-z0-9_]")


class Probe:
    """A module in the real `lib/Turkey/` and an entry that imports it."""

    def __init__(self, name: str, path: Path, entry: Path) -> None:
        self.name, self.path, self.entry = name, path, entry

    def __call__(self, body: str, giblet: bool = True) -> str:
        """Both compilers' verdict on the module, which is listed as a giblet
        unless the test says otherwise."""
        self.path.write_text(f"module {self.name} (f)\n\n{body}", encoding="utf-8")
        return verdict(self.entry, self.name if giblet else "")


@pytest.fixture
def probe(request, tmp_path):
    digest = hashlib.sha1(request.node.name.encode()).hexdigest()[:10]
    stem = "Probe_" + _SAFE.sub("_", request.node.originalname)[:40] + "_" + digest
    entry = tmp_path / "main.gob"
    entry.write_text(f"import Turkey.{stem} as P\nfun main() {{ }}\n",
                     encoding="utf-8")
    made = Probe(f"Turkey.{stem}", LIB / "Turkey" / f"{stem}.gob", entry)
    try:
        yield made
    finally:
        made.path.unlink(missing_ok=True)


# -- where a definition may appear -------------------------------------------


def test_a_definition_in_a_giblet_module_is_accepted(probe):
    assert probe('foreign "probe_f" fun f(x : Int) -> Int { x + 1 }\n') == ""


def test_a_definition_outside_a_giblet_module_is_refused(probe):
    """The gate is what makes a C caller's job empty: no root frame to set up
    and no collector to hand, because the body holds nothing traced."""
    message = probe('foreign "probe_f" fun f(x : Int) -> Int { x + 1 }\n',
                    giblet=False)
    assert "a foreign definition may only appear in a giblet module" in message


def test_a_program_may_not_define_a_symbol(tmp_path):
    entry = tmp_path / "main.gob"
    entry.write_text('foreign "probe_f" fun f(x : Int) -> Int { x }\n'
                     "fun main() { }\n", encoding="utf-8")
    assert "may only appear in a giblet module" in verdict(entry, "")


def test_the_body_is_held_to_the_giblet_rule(probe):
    message = probe('foreign "probe_f" fun f(x : Int) -> Int {\n'
                    '    let s = "text"\n    x\n}\n')
    assert "a string literal has type String" in message


# -- what it may say ---------------------------------------------------------


def test_only_the_seven_representable_types_cross(probe):
    message = probe('foreign "probe_f" fun f(x : Int) -> Int = x\n'
                    'foreign "probe_g" fun g(s : String) -> Int = 0\n')
    assert "'String' cannot cross a foreign boundary in" in message


def test_every_parameter_is_named(probe):
    message = probe('foreign "probe_f" fun f(Int) -> Int { 0 }\n')
    assert "has a body, so each parameter needs a name" in message


def test_seven_general_arguments_fit_and_an_eighth_does_not(probe):
    """The body is a Turkey function, and its environment takes one of the
    eight registers C would pass an argument in."""
    seven = ", ".join(f"a{i} : Int" for i in range(7))
    assert probe(f'foreign "probe_f" fun f({seven}) -> Int = a6\n') == ""
    eight = ", ".join(f"a{i} : Int" for i in range(8))
    message = probe(f'foreign "probe_f" fun f({eight}) -> Int = a7\n')
    assert "takes 8 general arguments, and a foreign definition takes at " \
           "most 7" in message


def test_a_symbol_is_defined_once(probe):
    message = probe('foreign "probe_f" fun f(x : Int) -> Int = x\n'
                    'foreign "probe_f" fun g(x : Int) -> Int = x\n')
    assert "the C symbol 'probe_f' is already claimed by" in message


# -- kept alive ----------------------------------------------------------------


def test_a_definition_nothing_calls_is_exported_under_its_symbol(probe):
    """Reached only from C, which `mono` cannot see: dropping it would leave
    the symbol undefined at link time. `main` calls nothing here, so each
    backend's output having the symbol is the whole claim."""
    assert probe('foreign "probe_f" fun f(x : Int) -> Int = x + 1\n') == ""
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), **{HOOK: probe.name})
    python = subprocess.run([sys.executable, "-m", "turkey", "llvm",
                             str(probe.entry)], cwd=REPO_ROOT, env=env,
                            capture_output=True, text=True, check=True).stdout
    assert re.search(r"^define i64 @\"?probe_f\"?\(i64", python, re.M), python
    boot = subprocess.run([str(bootc.binary()), "llvm", str(probe.entry)],
                          cwd=REPO_ROOT, env=env, capture_output=True,
                          text=True, check=True).stdout
    assert 'define i64 @"probe_f"(i64 %a1)' in boot
    native = subprocess.run([str(bootc.binary()), "native", str(probe.entry)],
                            cwd=REPO_ROOT, env=env, capture_output=True,
                            text=True, check=True).stdout
    assert '.globl "_probe_f"' in native


# -- called from C -------------------------------------------------------------

CALLER = r"""
#include <stdint.h>
#include <stdio.h>
int64_t probe_scale(int64_t, double, int64_t);
void probe_note(int64_t);
/* Before `main`: nothing of the program's has run, which is what a signal
   handler or a thread's start routine may see too. Through a pointer, so the
   call is to the address C would be handed. */
__attribute__((constructor)) static void before(void) {
    int64_t (*scale)(int64_t, double, int64_t) = probe_scale;
    void (*note)(int64_t) = probe_note;
    note(7);
    fprintf(stderr, "scale=%lld\n", (long long)scale(3, 2.5, 4));
}
"""


@pytest.mark.skipif(shutil.which("cc") is None, reason="no C compiler")
@pytest.mark.parametrize("backend", ["native", "llvm"])
def test_c_calls_a_definition_through_a_pointer(probe, tmp_path, backend):
    """The calling convention, which only running it can check: general and
    floating arguments interleaved, a `Unit` result that is C's `void`, and a
    call made before the program's own entry has run."""
    assert probe(
        "import Std.Classes\n"
        'foreign "probe_scale" fun f(a : Int, x : Float, b : Int) -> Int =\n'
        "    Prim.floatTruncate(Prim.intToFloat(a) * x) + b\n"
        'foreign "probe_note" fun note(n : Int) -> Unit { }\n') == ""
    env = dict(os.environ, **{HOOK: probe.name})
    text = subprocess.run([str(bootc.binary()), backend, str(probe.entry)],
                          cwd=REPO_ROOT, env=env, capture_output=True,
                          text=True, check=True).stdout
    source = tmp_path / ("program.s" if backend == "native" else "program.ll")
    source.write_text(text, encoding="utf-8")
    caller = tmp_path / "caller.c"
    caller.write_text(CALLER, encoding="utf-8")
    runtime = tmp_path / "runtime.o"
    subprocess.run(["cc", "-std=c11", "-O1", "-c", "-o", str(runtime),
                    str(REPO_ROOT / "runtime" / "turkey_runtime.c")], check=True)
    binary = tmp_path / "program"
    subprocess.run(["cc", "-O1", "-Wno-override-module", "-o", str(binary),
                    str(source), str(runtime), str(caller)], check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "scale=11" in result.stderr
