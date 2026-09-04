"""The bootstrap compiler, diffed against the implementation it is porting.

`boot/` is a Turkey compiler written in Turkey (plan.txt item 9), whose
modules are `Turkey.*`. Each of its
milestones is checked the same way: run it over every Turkey file in the repo
and compare its output, byte for byte, against the Python implementation's.

The corpus is the whole repository -- the conformance programs, the standard
library, and `boot/` itself. Its own source is the point rather than a
flourish: a compiler that cannot read itself cannot be bootstrapped, and the
library is by some way the largest Turkey program there is.

Cost is why `boot` is handed the entire corpus in one invocation. Starting it
means compiling it, which is about two seconds; per-file that would be minutes,
and once per suite it is once. The Python side needs no subprocess at all,
being an ordinary function call.

The M21 milestone is the slow one, and knowingly: it loads a whole module graph
per entry program, so the library is parsed once per program on both sides. The
Python side does that in under three seconds and `boot` takes minutes, because
`boot` is a Turkey program running on generated Python. That ratio is the thing
M26 removes rather than a reason to shrink the corpus.
"""

from __future__ import annotations

import atexit
import functools
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from turkey.astdump import dump as dump_ast
from turkey.driver import (check, declared, desugared, registered,
                           show_binding_groups, show_classes, show_declarations)
from turkey.errors import short
from turkey.lexer import tokenize
from turkey.parser import parse
from turkey.core import show_program
from turkey.types import show_scheme

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOT_MAIN = REPO_ROOT / "boot" / "Main.tl"


def _corpus() -> list[Path]:
    """Every Turkey file in the repository, in a stable order."""
    found = sorted(
        set(REPO_ROOT.glob("tests/programs/**/*.tl"))
        | set(REPO_ROOT.glob("lib/**/*.tl"))
        | set(REPO_ROOT.glob("boot/**/*.tl"))
    )
    assert found, "no Turkey source found to compare against"
    return found


CORPUS = _corpus()


def _entries() -> list[Path]:
    """The corpus files that can be an *entry* module.

    A library module resolves its own imports against its own directory, so
    `lib/Data/Array.tl` alone is not a program and neither implementation can
    load it as one. Every library module is still covered by the milestone
    below, as an import of every program that is here.
    """
    found = sorted(
        p for p in REPO_ROOT.glob("tests/programs/**/*.tl")
        if "err_" not in str(p))
    found.append(BOOT_MAIN)
    return found


ENTRIES = _entries()


def _sample() -> list[Path]:
    """A few entries, for the stages whose diff is redundant across programs.

    `decls`, `deps` and `classes` each dump tables built from the whole module
    graph, so thirty-two programs dump the library thirty-two times. One
    invocation of `boot` per stage is minutes; the milestone check below runs at
    full breadth and these run on a sample chosen for what it covers -- classes
    with superclasses and defaults, associated families, records and dictionary
    passing, `?`, and the compiler itself.
    """
    names = ["adt", "classes", "families", "dicts", "question", "records"]
    found = [REPO_ROOT / "tests" / "programs" / f"{n}.tl" for n in names]
    missing = [p for p in found if not p.is_file()]
    assert not missing, f"sample programs are gone: {missing}"
    return [*found, BOOT_MAIN]


SAMPLE = _sample()


@functools.lru_cache(maxsize=1)
def _binary() -> Path:
    """`boot`, compiled to an executable once for the whole module.

    Every stage below runs the same program at different arguments. Compiling
    it takes over two minutes and running it takes hundredths of a second, so
    ten fixtures each spawning `turkey run` paid the compile ten times over to
    do the work once -- about half an hour of a test suite that is otherwise
    six minutes.

    `turkey build` is the answer rather than compiling in-process and calling
    it ten times, for two reasons: a subprocess per stage keeps a crash in
    `boot` from taking the test session with it, which matters while `boot`
    still has one; and a real executable is what self-hosting needs anyway.
    """
    directory = Path(tempfile.mkdtemp(prefix="turkey-boot-"))
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    output = directory / "boot"
    result = subprocess.run(
        [sys.executable, "-m", "turkey", "build", str(BOOT_MAIN),
         "-o", str(output)],
        cwd=REPO_ROOT,
        env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"building boot failed\n{result.stdout}\n{result.stderr}")
    return output


def _boot(*args: str) -> str:
    # Bytes, decoded here rather than by `text=True`. Universal-newline
    # translation would rewrite a `\r` inside a *string literal* in the output
    # as a `\n`, which is a difference the reference side never had -- and the
    # dumps are compared line by line, so it lands as a mismatch several
    # thousand lines from anything that is actually wrong.
    result = subprocess.run(
        [str(_binary()), *args],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    out = result.stdout.decode("utf-8")
    err = result.stderr.decode("utf-8")
    assert result.returncode == 0, (
        f"boot exited {result.returncode}\n{out}\n{err}")
    return out


def _python_tokens(paths: list[Path]) -> str:
    out: list[str] = []
    for path in paths:
        for token in tokenize(path.read_text(encoding="utf-8"), str(path)):
            out.append(token.canonical())
            out.append("\n")
    return "".join(out)


def _python_ast(paths: list[Path]) -> str:
    # `known` is empty, matching what `turkey ast` passes: design.md 7's
    # alias-vs-data question is decided by a pre-pass over the one file.
    return "".join(
        dump_ast(parse(p.read_text(encoding="utf-8"))) for p in paths)


def _first_difference(actual: str, expected: str, what: str) -> None:
    if actual == expected:
        return
    actual_lines = actual.split("\n")
    expected_lines = expected.split("\n")
    for i, (got, want) in enumerate(zip(actual_lines, expected_lines)):
        if got != want:
            context = "\n".join(expected_lines[max(0, i - 3):i])
            pytest.fail(
                f"{what}: first difference at line {i + 1}:\n{context}\n"
                f"  python: {want!r}\n  boot:   {got!r}")
    pytest.fail(
        f"{what}: dumps differ in length: python {len(expected_lines)} lines, "
        f"boot {len(actual_lines)}")


@pytest.fixture(scope="module")
def boot_tokens() -> str:
    return _boot("tokens", *(str(p) for p in CORPUS))


@pytest.fixture(scope="module")
def boot_ast() -> str:
    return _boot("ast", *(str(p) for p in CORPUS))


def _relative(paths: list[Path]) -> list[str]:
    return [str(p.relative_to(REPO_ROOT)) for p in paths]


@pytest.fixture(scope="module")
def boot_decls() -> str:
    return _boot("decls", *_relative(SAMPLE))


@pytest.fixture(scope="module")
def boot_deps() -> str:
    return _boot("deps", *_relative(SAMPLE))


@pytest.fixture(scope="module")
def boot_classes() -> str:
    return _boot("classes", *_relative(SAMPLE))


@pytest.fixture(scope="module")
def boot_types() -> tuple[str, str]:
    result = subprocess.run(
        [sys.executable, "-m", "turkey", "run", str(BOOT_MAIN), "--",
         "types", *_relative(ENTRIES)],
        cwd=REPO_ROOT,
        env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
        capture_output=True,
    )
    out = result.stdout.decode("utf-8")
    err = result.stderr.decode("utf-8")
    assert result.returncode == 0, (
        f"boot exited {result.returncode}\n{out}\n{err}")
    return out, err


@pytest.fixture(scope="module")
def boot_core() -> str:
    return _boot("core", *_relative(ENTRIES))


@pytest.fixture(scope="module")
def boot_mono() -> str:
    return _boot("mono", *_relative(SAMPLE))


@pytest.fixture(scope="module")
def boot_opt() -> str:
    return _boot("opt", *_relative(ENTRIES))


@pytest.fixture(scope="module")
def boot_desugar() -> str:
    return _boot("desugar", *(str(p.relative_to(REPO_ROOT)) for p in ENTRIES))


def _python_desugar(paths: list[Path]) -> str:
    out: list[str] = []
    for path in paths:
        relative = str(path.relative_to(REPO_ROOT))
        src = path.read_text(encoding="utf-8")
        for module in desugared(src, relative, [path.parent]):
            out.append(f"module {module.name}\n")
            out.append(dump_ast(module.program))
    return "".join(out)


def test_the_corpus_is_the_whole_repository() -> None:
    # A shrinking corpus would silently weaken every test below it.
    names = {p.name for p in CORPUS}
    assert "Main.tl" in names, "boot's own source must be in the corpus"
    assert "String.tl" in names, "the standard library must be in the corpus"
    assert len(CORPUS) > 60


def test_boot_lexes_the_corpus_exactly_as_python_does(boot_tokens: str) -> None:
    """M19: the two lexers agree on every token of every file.

    Both sides print the canonical dump of `turkey/lexer.py`, which is
    deliberately not Python's `repr`: the float spelling is PRIMITIVES.md 3.3's
    and the escapes are design.md 2.1's, so neither implementation can be right
    only by being the host.
    """
    _first_difference(boot_tokens, _python_tokens(CORPUS), "tokens")


def test_boot_parses_the_corpus_exactly_as_python_does(boot_ast: str) -> None:
    """M20: the two parsers build the same tree, spans included.

    Spans are in the dump on purpose. A parser that builds the right shape from
    the wrong token reports every later error in the wrong place, and this is
    the only stage where that is cheap to catch.

    It also covers the two speculations Python does by catching a `ParseError`
    and `boot` does by scanning tokens -- whether a method is a signature, and
    whether a `for` is the `in` form. The standard library is full of class
    signatures and of both loop forms, so agreement over the corpus is what
    says the two readings coincide.
    """
    _first_difference(boot_ast, _python_ast(CORPUS), "ast")


def test_boot_resolves_and_desugars_the_corpus_exactly_as_python_does(
        boot_desugar: str) -> None:
    """M21: module loading, name resolution and the `?`/`do` lowering.

    Every module of every program, not just the entry one: what resolution does
    to a module depends on what it imported, so a dump of the entry alone would
    check the interesting half of the stage against nothing. Between them these
    programs pull in the whole of `lib/`, and `boot/Main.tl` pulls in the whole
    of `boot/`.

    The tree is the M20 dump, so what this adds over that milestone is exactly
    what the two passes changed: every name is internal, every bracket is an
    `Index` call, and every `?` is a `bind` -- including the lifted loops, whose
    generated `%loop`/`%fell`/`%k` binders are in the dump and therefore pin the
    two implementations to the same *numbering*, not merely the same shape.
    """
    _first_difference(boot_desugar, _python_desugar(ENTRIES), "desugar")


def test_the_desugar_corpus_exercises_the_hard_cases() -> None:
    """The milestone is only worth its runtime if the shapes are in it."""
    dumped = _python_desugar(ENTRIES)
    assert "%k1" in dumped, "no `?` was lowered to a bind"
    assert "%loop" in dumped, "no loop containing a `?` was lifted"
    assert "%fell" in dumped, "no flow-mode sequencing point"
    assert "%seq" in dumped, "no `for ... in` was expanded to its cursor form"
    assert "Prelude#Fall" in dumped, "no control transfer became a value"
    assert "\n  eindex" not in dumped, "a bracket survived desugaring"


def test_boot_builds_the_same_declaration_table(boot_decls: str) -> None:
    """M22a: every type constructor's inferred kind and every value
    constructor's generalized scheme, for the whole program's table."""
    expected = "".join(
        show_declarations(declared(
            p.read_text(encoding="utf-8"), str(p.relative_to(REPO_ROOT)),
            [p.parent])[0])
        for p in SAMPLE)
    _first_difference(boot_decls, expected, "decls")


def test_boot_orders_binding_groups_the_same_way(boot_deps: str) -> None:
    """M22a: the strongly-connected components, dependencies first.

    The order groups come out in is the order bindings are checked and then
    evaluated, so it has to be a property of the program rather than of the
    host's iteration order -- which is what a second implementation agreeing
    about it demonstrates.
    """
    expected = "".join(
        show_binding_groups(desugared(
            p.read_text(encoding="utf-8"), str(p.relative_to(REPO_ROOT)),
            [p.parent]))
        for p in SAMPLE)
    _first_difference(boot_deps, expected, "deps")


def test_boot_builds_the_same_class_table(boot_classes: str) -> None:
    """M22b: classes, their kinds, superclasses, families and method schemes,
    and every instance's head, context, family bindings and home module."""
    expected = "".join(
        show_classes(*registered(
            p.read_text(encoding="utf-8"), str(p.relative_to(REPO_ROOT)),
            [p.parent]))
        for p in SAMPLE)
    _first_difference(boot_classes, expected, "classes")


def test_boot_infers_the_same_types(boot_types: tuple[str, str]) -> None:
    """M22, the milestone: the whole front end, end to end.

    Every entry program in the corpus, at full breadth -- so the library is
    inferred thirty-two times over and `boot/Main.tl` puts the compiler itself
    through it. What is compared is the entry module's schemes and the warnings
    exhaustiveness produced, which between them cover unification, ranks and
    generalization, the value restriction, class entailment and superclass
    simplification, associated-family reduction, numeric defaulting, and the
    context a scheme ends up carrying.

    A disagreement anywhere else in inference does not go unnoticed either: a
    program is only reported at all if it type-checks, so one implementation
    accepting what the other rejects fails here as an exit status.
    """
    out, err = boot_types
    expected_out: list[str] = []
    expected_err: list[str] = []
    for path in ENTRIES:
        relative = str(path.relative_to(REPO_ROOT))
        checked = check(path.read_text(encoding="utf-8"), relative, [path.parent])
        for warning in checked.warnings:
            expected_err.append(f"{relative}:{short(warning)}\n")
        for name, scheme in checked.signatures:
            expected_out.append(f"{name} : {show_scheme(scheme)}\n")
    _first_difference(out, "".join(expected_out), "types")
    _first_difference(err, "".join(expected_err), "warnings")


def test_the_types_corpus_exercises_the_hard_cases() -> None:
    """Again, the milestone is only worth its runtime if the shapes are in it."""
    seen = "".join(
        f"{n} : {show_scheme(s)}\n"
        for p in ENTRIES
        for n, s in check(p.read_text(encoding="utf-8"),
                          str(p.relative_to(REPO_ROOT)), [p.parent]).signatures)
    assert "[" in seen, "no scheme carried a context"
    assert "OneOf" in seen, "no numeric literal set survived into a scheme"
    assert "Container.Elem" in seen, "no associated family reached a signature"
    assert "HasField" in seen, "no field demand travelled in a scheme"
    assert "~" in seen, "no family equality was carried"
    assert "fun(a) -> a" in seen, "nothing was generalized"


def test_boot_elaborates_to_the_same_core(boot_core: str) -> None:
    """M23: the typed Core, and the checker that makes it evidence.

    Every entry program, at full breadth. What is compared is the entry
    module's elaboration -- dictionaries as records, instances as bindings,
    evidence as ordinary terms, polymorphism as type abstraction and
    application, and the four loop forms and three control transfers gone,
    replaced by join points and jumps.

    The dump is only half the check. `boot core` runs `Turkey.Coretc` on what
    it lowered, unconditionally, so a term that does not typecheck fails here
    as an exit status rather than as a diff -- which matters because the
    printer does not show a node's type, and a wrong one is invisible in the
    text. That is not hypothetical: it is how the port's `get`/`set` collision
    was found, with every golden still matching.
    """
    parts = []
    for path in ENTRIES:
        checked = check(path.read_text(encoding="utf-8"),
                        str(path.relative_to(REPO_ROOT)), [path.parent])
        parts.append(show_program(checked.core, checked.module))
    expected = "".join(parts)
    _first_difference(boot_core, expected, "core")


def test_boot_specializes_the_same_way(boot_mono: str) -> None:
    """M24: monomorphization, and the checker run again on its output.

    On the sample rather than at full breadth, because the milestone check is
    the one below: `opt` runs this pass first and then two more on top of it,
    so a specialization that differed would have to survive three passes to
    escape both. What this adds is the smaller failure -- a diff at the stage
    it happened in rather than at the stage after it.
    """
    parts = []
    for path in SAMPLE:
        checked = check(path.read_text(encoding="utf-8"),
                        str(path.relative_to(REPO_ROOT)), [path.parent])
        parts.append(show_program(checked.mono, checked.module))
    _first_difference(boot_mono, "".join(parts), "mono")


def test_boot_optimizes_the_same_way(boot_opt: str) -> None:
    """M24: inlining, join discovery and the local reductions.

    Every entry program, at full breadth, and the last stage before a backend.
    Two details are ports rather than approximations because a golden depends
    on each: the specialization budget is shared across mono's two rounds, and
    the loop breaker chosen within a strongly-connected component is the
    lexicographically first name in it.

    The composition is load-bearing and is reproduced exactly on both sides --
    `reduce`, then `discover`, then `reduce` -- because the middle pass is the
    representation boundary: the first reduction exposes local continuations
    for discovery, and discovery exposes constructor-valued jumps for join
    specialization.

    As with `core`, the dump is only half of it. `boot opt` runs
    `Turkey.Coretc` on what it produced, which is where join discovery and the
    checker are made to agree about what a tail position is: calling one a tail
    where the rule does not is a jump with nowhere to go, and that is an exit
    status rather than a diff.
    """
    parts = []
    for path in ENTRIES:
        checked = check(path.read_text(encoding="utf-8"),
                        str(path.relative_to(REPO_ROOT)), [path.parent])
        parts.append(show_program(checked.opt, checked.module))
    _first_difference(boot_opt, "".join(parts), "opt")


def test_boot_reports_a_missing_file(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(_binary()), "tokens", str(tmp_path / "absent.tl")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "cannot read" in result.stderr
