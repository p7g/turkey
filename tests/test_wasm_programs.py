"""Programs built for `wasm32-wasi`, run under `wasmtime`.

`turkey build --target wasm32-wasi` writes a module through `Turkey.Wasm.Select`
and `Turkey.Wasm.Program`; `wasmtime run` runs it over WASI. Each program here
prints what its native build prints, which is written out rather than read
back from a native run. Operands come from the command line, so the optimizer
cannot fold the arithmetic under test away.

These skip when `wasmtime` is not installed, unless `$TURKEY_REQUIRE_WASMTIME`
says it must be.
"""

from __future__ import annotations

import functools
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests import bootc, lang, toolchain


@functools.lru_cache(maxsize=None)
def _module(src: str) -> Path:
    """`src` built as a wasm module, once per run."""
    entry = lang.program(src)
    out = Path(tempfile.mkdtemp(prefix="turkey-wasm-")) / "main.wasm"
    result = subprocess.run(
        toolchain.command(bootc.binary(), "build", "--target", "wasm32-wasi",
                          "-o", str(out), entry.name),
        cwd=entry.parent, env=dict(os.environ, TURKEY_LIB=str(lang.LIB)),
        capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    return out


def _run(src: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([toolchain.needs_wasmtime(), "run", str(_module(src)), *args],
                          capture_output=True, text=True, timeout=120)


def test_hello():
    result = _run('fun main() { print("hello") }\n')
    assert (result.returncode, result.stdout) == (0, "hello\n"), result.stderr


# -- arithmetic -----------------------------------------------------------------------

ARITHMETIC = """\
import System.Env as Env

fun arg(k : Int) -> Int = match Int.parse(Env.args()[k]) {
    Some(n) -> n
    None -> 0
}

fun main() {
    let a = arg(0)
    let b = arg(1)
    print(a + b)
    print(a - b)
    print(a * b)
    print(a / b)
    print(a % b)
    print(-a)
    print(Int.mulWrapping(a, 4611686018427387904))
    print(Int.addWrapping(a, 9223372036854775807))
    print(a & b)
    print(a | b)
    print(a ^ b)
    print(a << 3)
    print(-a >> 1)
    print(a < b)
    print(a >= b)
    print(a == b)
}
"""


def test_arithmetic():
    result = _run(ARITHMETIC, "1000000007", "-37")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\n") == [
        "999999970", "1000000044", "-37000000259", "-27027027", "8", "-1000000007",
        "-4611686018427387904", "-9223372035854775802", "1000000003", "-33", "-1000000036",
        "8000000056", "-500000004", "False", "True", "False", "",
    ]


# One operation on two operands, both from the command line.
OPERATION = """\
import System.Env as Env

fun arg(k : Int) -> Int = match Int.parse(Env.args()[k]) {
    Some(n) -> n
    None -> 0
}

fun main() {
    let a = arg(0)
    let b = arg(1)
    print(match Env.args()[2] {
        "+" -> a + b
        "-" -> a - b
        "*" -> a * b
        "/" -> a / b
        "%" -> a % b
        _ -> -a
    })
}
"""

MIN = "-9223372036854775808"
MAX = "9223372036854775807"


@pytest.mark.parametrize("a, op, b, answer", [
    ("9223372036854775806", "+", "1", MAX),
    ("-9223372036854775807", "-", "1", MIN),
    ("3037000499", "*", "3037000499", "9223372030926249001"),
    ("-4611686018427387904", "*", "2", MIN),
    ("-4294967296", "*", "2147483648", MIN),
    (MIN, "*", "1", MIN),
    ("-3", "*", "-3037000499", "9111001497"),
    ("-1", "*", "-1", "1"),
    (MIN, "%", "-1", "0"),
    ("-5", "%", "3", "-2"),
    ("-9223372036854775807", "neg", "0", MAX),
])
def test_an_operation_in_range_answers(a, op, b, answer):
    result = _run(OPERATION, a, b, op)
    assert (result.returncode, result.stdout) == (0, answer + "\n"), result.stderr


@pytest.mark.parametrize("a, op, b, message", [
    (MAX, "+", "1", "integer overflow in +"),
    ("-9223372036854775807", "-", "2", "integer overflow in -"),
    ("3037000500", "*", "3037000500", "integer overflow in *"),
    ("4611686018427387904", "*", "2", "integer overflow in *"),
    ("4294967296", "*", "4294967296", "integer overflow in *"),
    (MIN, "*", "-1", "integer overflow in *"),
    ("7", "/", "0", "division by zero"),
    (MIN, "/", "-1", "integer overflow in /"),
    ("7", "%", "0", "remainder by zero"),
    (MIN, "neg", "0", "integer overflow in unary -"),
])
def test_an_operation_out_of_range_panics(a, op, b, message):
    result = _run(OPERATION, a, b, op)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.splitlines()[0] == "panic: " + message


# -- control flow and calls -----------------------------------------------------------

LOOPS = """\
import System.Env as Env

fun collatz(n : Int) -> Int {
    var steps = 0
    var x = n
    while x != 1 {
        x = if x % 2 == 0 { x / 2 } else { 3 * x + 1 }
        steps = steps + 1
    }
    steps
}

fun fib(n : Int) -> Int = if n < 2 { n } else { fib(n - 1) + fib(n - 2) }

fun classify(n : Int) -> String = match n % 4 {
    0 -> "zero"
    1 -> "one"
    2 -> "two"
    _ -> "three"
}

fun main() {
    let n = len(Env.args())
    print(collatz(27))
    print(fib(20 + n))
    var total = 0
    for var i = 0; i < 100; i = i + 1 {
        match i % 3 {
            0 -> { total = total + i }
            1 -> { total = total - 1 }
            _ -> {}
        }
    }
    print(total)
    for var i = 0; i < 5; i = i + 1 { print(classify(i + n)) }
}
"""


def test_loops_calls_and_switches():
    result = _run(LOOPS)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "111\n6765\n1650\nzero\none\ntwo\nthree\nzero\n"


# Ten million calls deep, which only a tail call runs in constant stack:
# `docs/ref/declarations.md` promises it, mutual recursion included.
TAIL_CALLS = """\
import System.Env as Env

fun isEven(n : Int) -> Bool = if n == 0 { True } else { isOdd(n - 1) }
fun isOdd(n : Int) -> Bool = if n == 0 { False } else { isEven(n - 1) }

fun sumTo(n : Int, acc : Int) -> Int = if n == 0 { acc } else { sumTo(n - 1, acc + n) }

fun countdown(n : Int, step : fun(Int) -> Int) -> Int =
    if n <= 0 { n } else { countdown(step(n), step) }

fun main() {
    let n = (len(Env.args()) + 1) * 10_000_000
    print(isEven(n))
    print(isOdd(n + 1))
    print(sumTo(n, 0))
    print(countdown(n, fun(x) = x - 1))
}
"""


def test_tail_calls_run_in_constant_stack():
    result = _run(TAIL_CALLS)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "True\nTrue\n50000005000000\n0\n"


CLOSURES = """\
import System.Env as Env

fun twice(f : fun(Int) -> Int, x : Int) -> Int = f(f(x))

fun adder(n : Int) -> fun(Int) -> Int = fun(x) = x + n

fun main() {
    let k = len(Env.args()) + 1
    print(twice(fun(x) = x * 3, k + 1))
    let add5 = adder(5)
    print(add5(k))
    print(twice(adder(k * 10), 1))
    var counter = 0
    let bump = fun() { counter = counter + k }
    bump()
    bump()
    print(counter)
}
"""


def test_closures():
    result = _run(CLOSURES)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "18\n6\n21\n2\n"


# -- the build ------------------------------------------------------------------------


def test_the_module_is_named_for_the_entry(tmp_path):
    (tmp_path / "hello.gob").write_text('fun main() { print("hi") }\n', encoding="utf-8")
    result = subprocess.run(
        toolchain.command(bootc.binary(), "build", "--target", "wasm32-wasi", "hello.gob"),
        cwd=tmp_path, env=dict(os.environ, TURKEY_LIB=str(lang.LIB)),
        capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "hello.wasm").read_bytes()[:8] == bytes.fromhex("0061736d01000000")


def test_assembly_is_refused(tmp_path):
    (tmp_path / "hello.gob").write_text('fun main() { print("hi") }\n', encoding="utf-8")
    result = subprocess.run(
        toolchain.command(bootc.binary(), "build", "--target", "wasm32-wasi", "-S", "hello.gob"),
        cwd=tmp_path, env=dict(os.environ, TURKEY_LIB=str(lang.LIB)),
        capture_output=True, text=True, timeout=600)
    assert result.returncode != 0
    assert "'wasm32-wasi' has no assembly to write; build without -S" in result.stderr
