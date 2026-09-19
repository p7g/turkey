"""Giblets: modules whose code holds no traced value (TIX-63, SPEC-DELTAS 73).

Every rule is run through *both* compilers and the two verdicts compared. The
check lives on Core, which `test_boot` already compares byte for byte, but that
compares the input to the check, not the check: a rule fixed on one side only
is exactly the failure FINDINGS 43 describes, and this is the test that notices
it.

A giblet module whose code is wrong on purpose cannot be in the compiler's
list, so the tests name theirs through `TURKEY_TEST_GIBLETS`, which both
compilers read. The module itself has to come from the shipped `lib/` -- that
is one of the rules -- so, as in `test_foreign`, a probe is written there and
removed afterwards, under a name carrying the test's own so the parallel suite
cannot collide.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests import bootc

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "lib"
HOOK = "TURKEY_TEST_GIBLETS"
_SAFE = re.compile(r"[^A-Za-z0-9_]")


def _run(host: str, entry: Path, giblets: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    env[HOOK] = giblets
    command = ([sys.executable, "-m", "turkey", "core", str(entry)]
               if host == "python" else [str(bootc.binary()), "core", str(entry)])
    result = subprocess.run(command, cwd=REPO_ROOT, env=env,
                            capture_output=True, text=True)
    return result.returncode, result.stderr


def _message(stderr: str) -> str:
    """The diagnostic without its file. The two compilers name a library file
    differently -- `Turkey/X.gob` and `lib/Turkey/X.gob` -- which is theirs to
    disagree on and not this check's."""
    return re.sub(r"^\S*?(\d+:\d+: )", r"\1", stderr.strip())


def verdict(entry: Path, giblets: str) -> str:
    """What both compilers say, which must be the same thing: `""` for a
    program they accept, the diagnostic otherwise."""
    answers = {}
    for host in ("python", "boot"):
        code, stderr = _run(host, entry, giblets)
        answers[host] = "" if code == 0 else _message(stderr)
        assert code == 0 or stderr, f"{host} failed and said nothing"
    assert answers["python"] == answers["boot"], answers
    return answers["python"]


@pytest.fixture
def probe(request, tmp_path):
    """Write a module into the real `lib/Turkey/` and an entry that imports it;
    answer a function that checks the pair under both compilers."""
    # The parametrized cases share a prefix longer than any sensible file
    # name, so the tail that tells them apart is kept as a digest.
    digest = hashlib.sha1(request.node.name.encode()).hexdigest()[:10]
    stem = "Probe_" + _SAFE.sub("_", request.node.originalname)[:40] + "_" + digest
    path = LIB / "Turkey" / f"{stem}.gob"
    name = f"Turkey.{stem}"
    entry = tmp_path / "main.gob"
    entry.write_text(f"import {name} as P\nfun main() {{ }}\n", encoding="utf-8")

    def check(body: str) -> str:
        path.write_text(f"module {name} (f)\n\n{body}", encoding="utf-8")
        return verdict(entry, name)

    try:
        yield check
    finally:
        path.unlink(missing_ok=True)


# -- what a giblet may say -----------------------------------------------------


def test_the_code_the_collector_will_be_written_in_is_accepted(probe):
    """Everything untraced, and every exemption the rule makes, in one body:
    a `var`, a loop, a `Bool`, a newtype over `Prim.Ptr`, a method of a known
    instance, a call to an ordinary function, and a panic's message."""
    assert probe(
        "import Std.Classes\n"
        "import Data.Bool.Type (Bool(..))\n"
        "import Unsafe.Ptr as Ptr\n"
        "fun f(p : Ptr.Ptr, q : Ptr.Ptr, n : Int) -> Bool {\n"
        "    var i = 0\n"
        "    var seen = False\n"
        "    while i < n {\n"
        "        if Ptr.load(p, i) == Prim.byteFromInt(0) { seen = True }\n"
        "        i = i + 1\n"
        "    }\n"
        "    if n < 0 { Prim.error(\"negative\") }\n"
        "    seen && p == q\n"
        "}\n") == ""


def test_the_first_giblet_module_is_accepted(tmp_path):
    entry = tmp_path / "main.gob"
    entry.write_text("import Turkey.Memory as M\nfun main() { }\n",
                     encoding="utf-8")
    assert verdict(entry, "") == ""


@pytest.mark.parametrize("body, what", [
    ('fun f() -> Int {\n    let s = "text"\n    0\n}\n',
     "a string literal has type String"),
    ("import Data.Option.Type (Option(..))\n"
     "fun f() -> Int {\n    let x = Some(1)\n    0\n}\n",
     "has type Option"),
    ("fun f() -> Int {\n    let t = (1, 2)\n    0\n}\n",
     "a tuple has type ("),
    ("fun f() -> Int {\n    let xs = [1, 2]\n    0\n}\n",
     "has type Array Int"),
    ("fun f() -> Int {\n    let g = fun(x : Int) = x\n    0\n}\n",
     "has type fun(Int) -> Int"),
    ("fun g(x : Int) -> Int = x\n"
     "fun f() -> Int {\n    let h = g\n    0\n}\n",
     "'g' has type fun(Int) -> Int"),
    ("fun f(x : a) -> Int = 0\n",
     "the parameter 'x' has type a"),
    ("fun f(s : String) -> Int = 0\n",
     "the parameter 's' has type String"),
    ("fun f() -> Int {\n    fun g(x : Int) -> Int = x\n    g(1)\n}\n",
     "the local function 'g' has type fun(Int) -> Int"),
])
def test_a_traced_value_is_refused_where_it_is_made(probe, body, what):
    message = probe(body)
    assert what in message, message
    assert "is a giblet module, whose code holds untraced values only" in message


def test_a_giblet_module_may_not_declare_an_instance(probe):
    message = probe(
        "import Std.Classes\n"
        "type Handle = Handle(Int)\n"
        "instance Eq Handle {\n"
        "    fun eq(a : Handle, b : Handle) -> Bool = True\n"
        "}\n"
        "fun f() -> Int = 0\n")
    assert "may not declare an instance" in message


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


# -- the list ----------------------------------------------------------------


def test_both_compilers_keep_the_same_list():
    from turkey.giblets import GIBLET_MODULES
    source = (REPO_ROOT / "boot" / "Turkey" / "Giblets.gob").read_text(
        encoding="utf-8")
    found = re.search(r"^let giblets = \[(.*?)\]", source, re.M | re.S)
    assert found is not None
    assert set(re.findall(r'"([^"]+)"', found.group(1))) == set(GIBLET_MODULES)
