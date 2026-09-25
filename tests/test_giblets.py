"""Giblets: modules whose code does not allocate or hold a traced value across
a call.

Every rule is run through `boot`, and its verdict -- accepted, or the
diagnostic -- is the test.

A giblet module whose code is wrong on purpose cannot be in the compiler's
list, so the tests name theirs through `TURKEY_TEST_GIBLETS`, which the
compiler reads. The module itself has to come from the shipped `lib/` -- that
is one of the rules -- so, as in `test_foreign`, a probe is written there and
removed afterwards, under a name carrying the test's own so the parallel suite
cannot collide.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "lib"
HOOK = "TURKEY_TEST_GIBLETS"


def _run(entry: Path, giblets: str, command: str) -> tuple[int, str]:
    env = dict(os.environ, **{HOOK: giblets})
    result = subprocess.run(
        toolchain.command(bootc.binary(), command, str(entry)),
        cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    return result.returncode, result.stderr


def _message(stderr: str) -> str:
    """The diagnostic without its file, which is a temporary path or a library
    one and is not what these tests are about."""
    return re.sub(r"^\S*?(\d+:\d+: )", r"\1", stderr.strip())


def verdict(entry: Path, giblets: str, command: str = "core") -> str:
    """What `boot` says: `""` for a program it accepts, the diagnostic
    otherwise. `core` stops before the giblets check; `ssa` runs it."""
    code, stderr = _run(entry, giblets, command)
    assert code == 0 or stderr, "boot failed and said nothing"
    return "" if code == 0 else _message(stderr)


# -- where a giblet module may come from ---------------------------------------


def test_a_giblet_module_must_come_from_the_library(tmp_path):
    """The same gate `foreign` has: a program cannot opt its own module in,
    and could not stop the compiler holding it to the rule either."""
    (tmp_path / "Mine.gob").write_text("module Mine (f)\nfun f() -> Int = 0\n",
                                       encoding="utf-8")
    entry = tmp_path / "main.gob"
    entry.write_text("import Mine as M\nfun main() { }\n", encoding="utf-8")
    message = verdict(entry, "Mine")
    assert "'Mine' is a giblet module, and a giblet module must come from " \
           "the standard library" in message


# -- the lowered code ----------------------------------------------------------
#
# What a giblet may do is decided on the Low IR, after optimization: nothing
# may allocate, directly or through a direct callee, and nothing traced may be
# held across a call. Each probe is called from `main`, since specialization
# keeps only what is reachable.


@pytest.fixture
def lowered(request, tmp_path):
    """A giblet probe and an ordinary one beside it, lowered by `boot`."""
    digest = hashlib.sha1(request.node.name.encode()).hexdigest()[:10]
    giblet = LIB / "Turkey" / f"Probe_giblet_{digest}.gob"
    helper = LIB / "Turkey" / f"Probe_helper_{digest}.gob"
    entry = tmp_path / "main.gob"
    # Called from `main`, since specialization keeps only what is reachable.
    entry.write_text(f"import Turkey.Probe_giblet_{digest} as P\n"
                     f"fun main() {{ print(Int.toString(P.f(3))) }}\n",
                     encoding="utf-8")

    def lower(giblet_body: str, helper_body: str,
              exports: str = "h") -> tuple[int, str, str]:
        helper.write_text(f"module Turkey.Probe_helper_{digest} ({exports})\n\n"
                          + helper_body, encoding="utf-8")
        giblet.write_text(f"module Turkey.Probe_giblet_{digest} (f)\n\n"
                          f"import Turkey.Probe_helper_{digest} as H\n"
                          + giblet_body, encoding="utf-8")
        env = dict(os.environ, **{HOOK: f"Turkey.Probe_giblet_{digest}"})
        result = subprocess.run(
            toolchain.command(bootc.binary(), "ssa", str(entry)),
            cwd=REPO_ROOT, env=env, capture_output=True, text=True)
        return result.returncode, result.stdout, result.stderr

    try:
        yield lower
    finally:
        giblet.unlink(missing_ok=True)
        helper.unlink(missing_ok=True)


def test_the_code_the_collector_will_be_written_in_is_accepted(lowered):
    """A `var`, a loop, a `Bool`, a newtype over `Prim.Ptr`, a method of a
    known instance, a call to an ordinary function, and a panic's message."""
    code, _, stderr = lowered(
        "import Std.Classes\n"
        "import Data.Bool.Type (Bool(..))\n"
        "import Unsafe.Ptr as Ptr\n"
        "fun g(p : Ptr.Ptr, q : Ptr.Ptr, n : Int) -> Bool {\n"
        "    var i = 0\n"
        "    var seen = False\n"
        "    while i < n {\n"
        "        if Ptr.load(p, i) == Prim.byteFromInt(0) { seen = True }\n"
        "        i = i + 1\n"
        "    }\n"
        "    if n < 0 { Prim.error(\"negative\") }\n"
        "    seen && p == q\n"
        "}\n"
        "fun f(n : Int) -> Int =\n"
        "    if g(Ptr.null(), Ptr.null(), n) { H.h(n) } else { 0 }\n",
        "fun h(n : Int) -> Int = n\n")
    assert code == 0, stderr


def test_a_value_of_traced_type_that_is_never_made_is_accepted(lowered):
    """What is judged is the code that runs. `OS` is laid out as a traced
    pointer, but `Target.os` is a bare constructor that the optimizer puts in
    place and then decides the `match` on."""
    code, _, stderr = lowered(
        "import Target (OS(..))\n"
        "import Target as Target\n"
        "fun f(n : Int) -> Int = match Target.os {\n"
        "    Darwin -> n\n"
        "    Linux -> n + 1\n"
        "}\n",
        "fun h(n : Int) -> Int = n\n")
    assert code == 0, stderr


def test_an_instance_declared_in_a_giblet_and_called_directly_is_accepted(
        lowered):
    """A method of a known instance is a direct call once specialized."""
    code, _, stderr = lowered(
        "import Std.Classes\n"
        "import Data.Bool.Type (Bool(..))\n"
        "type Handle = Handle(Int)\n"
        "instance Eq Handle {\n"
        "    fun eq(a : Handle, b : Handle) -> Bool = True\n"
        "}\n"
        "fun f(n : Int) -> Int = if Handle(n) == Handle(n) { 1 } else { 0 }\n",
        "fun h(n : Int) -> Int = n\n")
    assert code == 0, stderr


# Each helper is recursive, so it is a loop breaker and is never inlined: what
# the giblet makes to pass to it survives to the Low IR.
MADE = (
    "type P = P { x : Int, y : Int }\n"                                   # 3
    "fun tuple(t : (Int, Int), k : Int) -> Int =\n"                        # 4
    "    if k == 0 { match t { (a, _) -> a } } else { tuple(t, k - 1) }\n"                     # 5
    "fun array(t : Array Int, k : Int) -> Int =\n"                         # 6
    "    if k == 0 { len(t) } else { array(t, k - 1) }\n"                  # 7
    "fun record(t : P, k : Int) -> Int =\n"                                # 8
    "    if k == 0 { t.x } else { record(t, k - 1) }\n"                    # 9
    "fun closure(g : fun(Int) -> Int, k : Int) -> Int =\n"                 # 10
    "    if k == 0 { 0 } else { closure(g, k - 1) }\n"                     # 11
    "fun pick(k : Int) -> fun(Int) -> Int =\n"                             # 12
    "    if k == 0 { fun(x : Int) = x } else { pick(k - 1) }\n"            # 13
    "fun h(n : Int) -> Int = if n <= 0 { 0 } else { h(n - 1) }\n"          # 14
)


@pytest.mark.parametrize("body, function, reason", [
    ("fun f(n : Int) -> Int = H.tuple((n, n), n)\n",
     "f", "builds a tuple"),
    ("fun f(n : Int) -> Int = H.array([n, n], n)\n",
     "f", "builds an array"),
    ("fun f(n : Int) -> Int = H.record(H.P { x = n, y = n }, n)\n",
     "f", "builds a record, P"),
    # The closure's captured environment is the object it makes.
    ("fun f(n : Int) -> Int = H.closure(fun(x : Int) = x + n, n)\n",
     "f", "builds an object -- a constructor, tuple, record or dictionary"),
    ("fun f(n : Int) -> Int = H.pick(n)(n)\n",
     "f", "calls through a closure or a dictionary"),
    # Polymorphic recursion through a newtype, which is erased: the types grow
    # past the specialization cap without anything being built, and the
    # generic body boxes `k` to pass it as `b`.
    ("type W a = W(a)\n"
     "fun g(x : a, y : b, k : Int) -> Int =\n"
     "    if k == 0 { 0 } else { g(W(x), k, k - 1) }\n"
     "fun f(n : Int) -> Int = g(n, n, n)\n",
     "g@Int,Int", "boxes a value to pass it where the type is not known"),
    # A method of an instance the giblet declares, called through the
    # dictionary an existential carries.
    ("import Std.Classes\n"
     "import Data.Bool.Type (Bool(..))\n"
     "type Handle = Handle(Int)\n"
     "instance Eq Handle {\n"
     "    fun eq(a : Handle, b : Handle) -> Bool = True\n"
     "}\n"
     "type Hidden = Hidden[Eq a](a)\n"
     "fun same(h : Hidden, k : Int) -> Bool =\n"
     "    if k == 0 { match h { Hidden(x) -> x == x } } else { same(h, k - 1) }\n"
     "fun f(n : Int) -> Int = if same(Hidden(Handle(n)), n) { 1 } else { 0 }\n",
     "same", "calls through a closure or a dictionary"),
])
def test_what_a_giblet_makes_is_refused(lowered, body, function, reason):
    code, _, stderr = lowered(body, MADE,
                              exports="P(..), tuple, array, record, closure, "
                                      "pick, h")
    assert code != 0
    assert re.search(rf"giblets: {re.escape(function)} is in a giblet module "
                     rf"and may allocate: .*{re.escape(reason)} "
                     rf"\([^)]*Probe_giblet_\w+\.gob:\d+:\d+\)", stderr), stderr


def test_a_traced_value_held_across_a_call_is_refused(lowered):
    """Nothing allocates, but `s` is live across `H.h`, so the collector would
    have to find it in a frame a giblet does not have."""
    code, _, stderr = lowered(
        "fun g(s : String, n : Int) -> Int = if n < 0 { g(s, n + 1) } else {\n"
        "    let m = H.h(n)\n"
        "    String.byteLength(s) + m\n"
        "}\n"
        "fun f(n : Int) -> Int = g(\"text\", n)\n",
        "fun h(n : Int) -> Int = if n <= 0 { 0 } else { h(n - 1) }\n")
    assert code != 0
    assert "giblets: g is in a giblet module and holds a traced value across " \
           "a call" in stderr, stderr



def test_a_call_that_allocates_is_refused_with_the_path_to_it(lowered):
    """The allocation is in `h`, which is not a giblet; the giblet is refused
    for calling it."""
    code, _, stderr = lowered(
        "fun f(n : Int) -> Int = H.h(n)\n",
        # Recursive, so it is a loop breaker and is never inlined: the call
        # survives to the Low IR, and the diagnostic has a path to print.
        "fun h(n : Int) -> Int = if n == 0 { len([n, n]) } else { h(n - 1) }\n")
    assert code != 0
    assert "giblets: f is in a giblet module and may allocate: @f calls @h " \
           "(" in stderr, stderr
    assert re.search(r"@f calls @h \([^)]*Probe_giblet_\w+\.gob:4:\d+\), which "
                     r"builds an array \([^)]*Probe_helper_\w+\.gob:3:\d+\)",
                     stderr), stderr


# The allocations nobody writes: each helper names nothing traced, and each
# lowers to an allocation its source does not spell. Recursive, so each stays a call; the line is where the
# construct the diagnostic should point at is written.
HIDDEN = (
    "type Hidden = Hidden[Show a](a)\n"                                   # 3
    "fun poly(x : a, k : Int) -> Int =\n"                                 # 4
    "    if k == 0 { 0 } else { poly((x, x), k - 1) }\n"                  # 5
    "fun showAll[Show a](x : a, k : Int) -> Int =\n"                      # 6
    "    if k == 0 { String.byteLength(show(x)) }\n"                      # 7
    "    else { showAll((x, x), k - 1) }\n"                               # 8
    "fun nests(n : Int) -> Int = if n == 0 { poly(n, 3) } else { nests(n - 1) }\n"   # 9
    "fun packs(n : Int) -> Int = if n == 0 {\n"                           # 10
    "    match Hidden(n) { Hidden(x) -> String.byteLength(show(x)) }\n"   # 11
    "} else { packs(n - 1) }\n"                                           # 12
    "fun dicts(n : Int) -> Int = if n == 0 { showAll(n, 3) } else { dicts(n - 1) }\n"  # 13
)


@pytest.mark.parametrize("helper, reason, line", [
    # Polymorphic recursion is past any specialization cap: the tuple the
    # generic body builds is reached through the call.
    ("nests", "builds a tuple", 9),
    # An existential packing is an object with the layout codes in front.
    ("packs", "packs an existential, Hidden", 11),
    # A class-polymorphic function past the cap: its dictionary's pairs.
    ("dicts", "builds a tuple", 13),
])
def test_an_allocation_nobody_wrote_is_named_where_it_came_from(
        lowered, helper, reason, line):
    code, _, stderr = lowered(f"fun f(n : Int) -> Int = H.{helper}(n)\n",
                              HIDDEN, exports="nests, packs, dicts")
    assert code != 0
    assert f"@f calls @{helper} (" in stderr, stderr
    assert re.search(rf"which {re.escape(reason)} \([^)]*Probe_helper_\w+\.gob:"
                     rf"{line}:\d+\)", stderr), stderr


def test_a_giblet_calling_a_giblet_holds_no_root(lowered):
    """The null environment a direct call passes is traced; it is not a root
    (`Turkey.Roots.root`), or every giblet that called anything would need a
    frame the collector must walk."""
    code, stdout, stderr = lowered(
        "fun g(n : Int) -> Int = n\n"
        "fun f(n : Int) -> Int = g(n) + H.h(n)\n",
        "fun h(n : Int) -> Int = n\n")
    assert code == 0, stderr
    assert "Probe_giblet_" in stdout


def test_the_first_giblet_module_lowers_clean():
    stdout = bootc.boot("ssa", "tests/programs/giblets_memory.gob")
    assert "fun @Turkey.Memory#fill(" in stdout


def test_a_giblet_function_is_emitted_with_no_root_frame():
    """What the collector needs of a giblet: nothing to find in it. The check
    after lowering refuses any giblet `Turkey.Roots` would give a slot, and
    both native emitters read their root frames from `Turkey.Roots`, so this
    is the LLVM emitter's half of that stated where it is visible -- no
    `turkey_root_enter` in any `Turkey.Memory` function. Dropping the frame
    record itself is TIX-55's leaf frames."""
    llvm = bootc.boot("llvm", "tests/programs/giblets_memory.gob")
    bodies = re.findall(r'^define [^\n]*@"Turkey\.Memory#[^"]*"\(.*?^}',
                        llvm, re.M | re.S)
    assert len(bodies) >= 3
    for body in bodies:
        assert "turkey_root_enter" not in body, body.splitlines()[0]
