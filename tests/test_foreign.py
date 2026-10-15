"""`foreign`: the rules a declaration has to obey.

The happy path is a conformance program -- `tests/programs/foreign.gob` calls
declared symbols and its recorded output is the only thing that checks a
calling convention. What is here is the other half: every rule the declaration
form states, each one written against a source small enough that the answer is
the whole test.

The rules exist because a foreign signature is an assertion nothing can check.
Rust's RFC 3484 puts it plainly -- the compiler "cannot itself verify these
assertions", so it is the declarer's responsibility -- and each check below is
one of the few things a compiler *can* still say about such a declaration:
where it may be written, which types it may name, and how many arguments it may
take before the ABI this backend implements runs out.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain
from tests.lang import check, program, run, types
from tests.lang import CompileError

_SAFE = re.compile(r"[^A-Za-z0-9_]")

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "lib"


def fails(src: str | Path, modules: dict[str, str] | None = None) -> str:
    with pytest.raises(CompileError) as caught:
        check(src, modules)
    return caught.value.rendered


@pytest.fixture
def probe(request, monkeypatch):
    """Write a throwaway module into the real `lib/`, let it declare foreign
    symbols through `TURKEY_TEST_FOREIGN`, and remove it.

    The gate is not "a directory named lib" -- it is *this* `lib`, the shipped
    one, which is where `Prim.` may be spelled too. A fixture that wrote into
    `tmp_path` would be testing nothing, so this writes where the rule points
    and cleans up. The name carries the test's own, so the parallel suite
    cannot have two of these at once.
    """
    stem = "Probe_" + _SAFE.sub("_", request.node.name)[:60]
    path = LIB / f"{stem}.gob"
    monkeypatch.setenv("TURKEY_TEST_FOREIGN", stem)

    def write(body: str) -> str:
        path.write_text(body.replace("PROBE", stem), encoding="utf-8")
        return f"import {stem} as P\nfun main() {{ }}\n"

    try:
        yield write
    finally:
        path.unlink(missing_ok=True)


# -- where a declaration may appear ------------------------------------------


def test_a_program_may_not_declare_a_foreign_symbol():
    """The containment is the whole of what the compiler offers here."""
    message = fails('foreign "getenv" fun getenv(Prim.Ptr) -> Prim.Ptr\n'
                    "fun main() { }\n")
    assert "a foreign declaration may only appear in" in message


def test_naming_your_own_module_after_a_listed_one_does_not_let_you_in(tmp_path):
    """Checked against where the file came from, not against what it says it
    is called. A program that could opt in by writing a module header would
    have no gate at all. `import Prelude ()`, because a module beside the
    entry shadows the library's own `Turkey.Libc`, which the Prelude
    reaches."""
    message = fails("import Turkey.Libc as E\nfun main() { }\n", {
        "Turkey/Libc.gob": "module Turkey.Libc (f)\n"
                           "import Prelude ()\n"
                           'foreign "system" fun f(Prim.Ptr) -> Int\n'})
    assert "a foreign declaration may only appear in" in message


def test_a_directory_named_lib_is_not_the_library(tmp_path):
    """The first search root is the entry file's own directory, so "under some
    lib/" would be the same hole as trusting the module's name."""
    root = tmp_path / "lib"
    (root / "Turkey").mkdir(parents=True)
    (root / "Turkey" / "Libc.gob").write_text(
        "module Turkey.Libc (f)\n"
        "import Prelude ()\n"
        'foreign "strlen" fun f(Prim.Ptr) -> Int\n',
        encoding="utf-8")
    (root / "Main.gob").write_text("import Turkey.Libc as P\nfun main() { }\n",
                                   encoding="utf-8")
    message = fails(root / "Main.gob")
    assert "a foreign declaration may only appear in" in message


# -- what it may say ---------------------------------------------------------


def test_a_declaration_binds_the_name_at_the_type_it_states(probe):
    entry = probe((
        "module PROBE (call)\n"
        'foreign "strlen" fun strlen(s : Prim.Ptr) -> Int\n'
        "fun call(p : Prim.Ptr) -> Int = strlen(p)\n"))
    assert types(entry)["main"] == "fun() -> Unit"


@pytest.mark.parametrize(
    "written", ["String", "Array Int", "a", "fun(Int) -> Int", "Option Int"])
def test_only_the_seven_representable_types_cross(probe, written):
    """`String` is the one worth naming: it is a heap array of bytes with no
    terminator, and every C function that wants a string wants a
    `char *`. The copy is the caller's (`Turkey.Ptr.toCString`)."""
    entry = probe((
        "module PROBE (f)\n"
        f'foreign "probe" fun f(x : {written}) -> Int\n'))
    message = fails(entry)
    assert "cannot cross a foreign boundary" in message


def test_a_result_type_is_checked_too(probe):
    entry = probe((
        "module PROBE (f)\n"
        'foreign "probe" fun f(x : Int) -> String\n'))
    assert "cannot cross a foreign boundary" in fails(entry)


def test_unit_is_how_a_void_function_is_written(probe):
    entry = probe((
        "module PROBE (f)\n"
        'foreign "free" fun f(p : Prim.Ptr) -> Unit\n'))
    check(entry)


# -- how many arguments ------------------------------------------------------


def test_eight_general_arguments_fit(probe):
    """x0-x7. `mmap` is the widest thing the runtime sequence needs and takes
    six, so the limit costs nothing today."""
    params = ", ".join(f"a{i} : Int" for i in range(8))
    entry = probe((
        "module PROBE (f)\n"
        f'foreign "probe" fun f({params}) -> Int\n'))
    check(entry)


def test_a_ninth_general_argument_is_rejected_at_the_declaration(probe):
    """Rejected here rather than stopped in the selector. `Turkey.Select` says
    its outgoing stack slots are its own convention and not one a C function
    would read; a runtime entry point that overflowed was a compiler bug, and a
    declaration is written by someone, so it gets an error that says which
    register file filled up."""
    params = ", ".join(f"a{i} : Int" for i in range(9))
    entry = probe((
        "module PROBE (f)\n"
        f'foreign "probe" fun f({params}) -> Int\n'))
    message = fails(entry)
    assert "9 general arguments" in message
    assert "x0-x7" in message


def test_the_two_register_files_are_counted_apart(probe):
    """Eight integers and eight doubles is sixteen arguments and fits: they
    travel in x0-x7 and d0-d7, which is why one limit would be wrong."""
    params = ", ".join(
        [f"a{i} : Int" for i in range(8)] + [f"b{i} : Float" for i in range(8)])
    entry = probe((
        "module PROBE (f)\n"
        f'foreign "probe" fun f({params}) -> Int\n'))
    check(entry)


def test_a_ninth_float_is_rejected_and_says_so(probe):
    params = ", ".join(f"a{i} : Float" for i in range(9))
    entry = probe((
        "module PROBE (f)\n"
        f'foreign "probe" fun f({params}) -> Int\n'))
    message = fails(entry)
    assert "9 floating-point arguments" in message
    assert "d0-d7" in message


# -- the namespace -----------------------------------------------------------


def test_a_foreign_name_collides_with_a_function_of_the_same_name(probe):
    entry = probe((
        "module PROBE (f)\n"
        'foreign "probe" fun f(x : Int) -> Int\n'
        "fun f(x : Int) -> Int = x\n"))
    assert "already defined" in fails(entry)


def test_declaring_the_same_name_twice_is_an_error(probe):
    entry = probe((
        "module PROBE (f)\n"
        'foreign "probe" fun f(x : Int) -> Int\n'
        'foreign "other" fun f(x : Int) -> Int\n'))
    assert "declared more than once" in fails(entry)


def test_two_names_may_share_one_symbol(probe):
    """Nothing about a C symbol is owned by the declaration, so two of them
    may name it. The Turkey names are what collide, not the symbols."""
    entry = probe((
        "module PROBE (f, g)\n"
        'foreign "strlen" fun f(p : Prim.Ptr) -> Int\n'
        'foreign "strlen" fun g(p : Prim.Ptr) -> Int\n'))
    check(entry)


# -- the shape of the declaration itself -------------------------------------


def test_a_return_type_is_required(probe):
    """"The signature is stated in full" is the only property that makes an
    unverifiable declaration worth trusting, so there is no defaulting."""
    entry = probe((
        "module PROBE (f)\n"
        'foreign "probe" fun f(x : Int)\n'))
    message = fails(entry)
    assert "must state a return type" in message
    assert "-> Unit" in message


def test_the_c_symbol_is_required(probe):
    entry = probe((
        "module PROBE (f)\n"
        "foreign fun f(x : Int) -> Int\n"))
    assert "expected the C symbol as a string" in fails(entry)


def test_parameters_may_be_named_or_bare(probe):
    """Naming is allowed here and not in a class method's signature because
    the ambiguity that forbids it there -- a bare identifier is both a
    parameter name and a type variable -- is settled only by the presence of a
    body, and every parameter of a foreign signature states its type, so
    `x : T` is always a name."""
    entry = probe((
        "module PROBE (f)\n"
        'foreign "probe" fun f(fd : Int, Prim.Ptr, count : Int) -> Int\n'))
    check(entry)


# -- variadic functions ------------------------------------------------------

# `open` with a mode and `fcntl` with an argument, which C declares variadic:
# the mode and the argument follow the `...`. Darwin passes every variadic
# argument on the stack and arm64 Linux in the registers, so a call that put
# them where a named argument goes would work on one target and read garbage
# on the other; the run is what checks that this target's call is the one its
# libc reads.
VARIADIC = """\
module PROBE (create, descriptorFlags, setDescriptorFlags, close)

import Target (OS(..))
import Target as Target
import Turkey.Ptr as Ptr

foreign "open" fun openCreate(path : Prim.Ptr, flags : Int, ... mode : Int) -> Int
foreign "fcntl" fun fcntl(fd : Int, command : Int) -> Int
foreign "fcntl" fun fcntlWith(fd : Int, command : Int, ... argument : Int) -> Int
foreign "close" fun close(fd : Int) -> Int

-- `O_WRONLY | O_CREAT | O_EXCL`.
fun createFlags() -> Int = match Target.os {
    Darwin -> 0x0001 + 0x0200 + 0x0800
    Linux -> 0x0001 + 0x0040 + 0x0080
}

fun create(path : String, mode : Int) -> Int {
    let name = Ptr.toCString(path)
    let fd = openCreate(Ptr.raw(name), createFlags(), mode)
    Ptr.free(name)
    fd
}

-- `F_GETFD` and `F_SETFD`, the same on both.
fun descriptorFlags(fd : Int) -> Int = fcntl(fd, 1)
fun setDescriptorFlags(fd : Int, flags : Int) -> Int = fcntlWith(fd, 2, flags)
"""

VARIADIC_MAIN = """\
fun main() {
    let fd = P.create("made", 0o640)
    print(fd >= 0)
    print(P.descriptorFlags(fd))
    -- `FD_CLOEXEC`.
    print(P.setDescriptorFlags(fd, 1))
    print(P.descriptorFlags(fd))
    print(P.close(fd))
}
"""


def test_a_variadic_declaration_passes_its_trailing_arguments(probe, tmp_path):
    """The mode reaches `open`, which the file's permissions show, and the
    flag reaches `fcntl`, which reading it back shows."""
    entry = probe(VARIADIC).replace("fun main() { }\n", VARIADIC_MAIN)
    result = run(entry, cwd=tmp_path)
    assert result.code == 0, result.stderr
    assert result.stdout.split() == ["True", "0", "0", "1", "0"]
    mask = os.umask(0)
    os.umask(mask)
    assert stat.S_IMODE((tmp_path / "made").stat().st_mode) == 0o640 & ~mask


@pytest.mark.parametrize(("target", "on_stack"), [
    ("arm64-darwin", True), ("arm64-linux", False)])
def test_darwin_puts_variadic_arguments_on_the_stack(probe, target, on_stack):
    """Darwin gives each variadic argument an 8-byte stack slot and no
    register, whatever registers are left; Linux passes it as a named one."""
    entry = program(probe(VARIADIC).replace("fun main() { }\n", VARIADIC_MAIN))
    result = subprocess.run(
        toolchain.command(bootc.binary(), *bootc.argv("select"), "--target",
                          target, entry.name),
        cwd=entry.parent, env=dict(os.environ, TURKEY_LIB=str(LIB)),
        capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    stored = "[outgoing 0]" in result.stdout.decode()
    assert stored == on_stack


def test_a_variadic_marker_needs_an_argument_after_it(probe):
    """A declaration fixes the type of every argument it passes, so a `...`
    with nothing after it would declare no call at all."""
    entry = probe((
        "module PROBE (f)\n"
        'foreign "open" fun f(path : Prim.Ptr, flags : Int, ...) -> Int\n'))
    assert "must be followed by the variadic arguments" in fails(entry)


def test_a_parameter_list_has_one_variadic_marker(probe):
    entry = probe((
        "module PROBE (f)\n"
        'foreign "probe" fun f(a : Int, ... b : Int, ... c : Int) -> Int\n'))
    assert "one '...'" in fails(entry)


def test_a_foreign_definition_cannot_be_variadic():
    """A Turkey body has no way to read C's variadic arguments."""
    message = fails('foreign "probe" fun f(a : Int, ... b : Int) -> Int = a\n'
                    "fun main() { }\n")
    assert "cannot be variadic" in message


def test_variadic_arguments_count_against_the_registers(probe):
    """Darwin would pass the ninth on the stack, but Linux passes variadic
    arguments as named ones, and a declaration is checked the same for
    both."""
    params = ", ".join(f"a{i} : Int" for i in range(7))
    entry = probe((
        "module PROBE (f)\n"
        f'foreign "probe" fun f({params}, ... b : Int, c : Int) -> Int\n'))
    assert "9 general arguments" in fails(entry)
