"""Programs built for `wasm32-wasi`, run under `wasmtime`.

`turkey build --target wasm32-wasi` writes a module through `Turkey.Wasm.Select`
and `Turkey.Wasm.Program`; `wasmtime run` runs it over WASI. Each program here
prints what its native build prints, which is written out rather than read
back from a native run. Operands come from the command line, so the optimizer
cannot fold the arithmetic under test away.

A program that allocates runs twice: as it is, and built with `--gc-verify`
under `TURKEY_GC_STRESS=1`, which collects at every allocation and checks the
heap at every collection, so a root the shadow frames miss is freed while it is
still live and the check, or the output, says so.

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
def _module(src: str, *flags: str) -> Path:
    """`src` built as a wasm module with the build flags `flags`, once per
    run."""
    entry = lang.program(src)
    out = Path(tempfile.mkdtemp(prefix="turkey-wasm-")) / "main.wasm"
    result = subprocess.run(
        toolchain.command(bootc.binary(), "build", "--target", "wasm32-wasi", *flags,
                          "-o", str(out), entry.name),
        cwd=entry.parent, env=dict(os.environ, TURKEY_LIB=str(lang.LIB)),
        capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    return out


def _run(src: str, *args: str, stress: bool = False, build: tuple[str, ...] = (),
         engine: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    """`src` run under wasmtime with `args`, built with the flags `build` and
    run with wasmtime's flags `engine`; with `stress`, also built with the
    heap verifier and collecting at every allocation."""
    if stress:
        build = (*build, "--gc-verify")
        engine = (*engine, "--env", "TURKEY_GC_STRESS=1")
    return subprocess.run([toolchain.needs_wasmtime(), "run", *engine,
                           str(_module(src, *build)), *args],
                          capture_output=True, text=True, timeout=120)


# Run as it is and under GC stress with the heap verifier.
both = pytest.mark.parametrize("stress", [False, True], ids=["plain", "stressed"])


@both
def test_hello(stress):
    result = _run('fun main() { print("hello") }\n', stress=stress)
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


@both
def test_arithmetic(stress):
    result = _run(ARITHMETIC, "1000000007", "-37", stress=stress)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\n") == [
        "999999970", "1000000044", "-37000000259", "-27027028", "-29", "-1000000007",
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
    ("-5", "/", "3", "-2"),
    ("5", "/", "-3", "-2"),
    ("-5", "/", "-3", "1"),
    ("-5", "%", "3", "1"),
    ("5", "%", "-3", "-1"),
    ("-5", "%", "-3", "-2"),
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


@both
def test_loops_calls_and_switches(stress):
    result = _run(LOOPS, stress=stress)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "111\n6765\n1650\nzero\none\ntwo\nthree\nzero\n"


# Ten million calls deep, which runs only if a call in tail position, mutual
# recursion and a closure's included, takes no stack.
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


@both
def test_closures(stress):
    result = _run(CLOSURES, stress=stress)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "18\n6\n21\n2\n"


# A closure that calls itself through a variable, so each call is through the
# closure's code, ten million deep.
INDIRECT_TAIL_CALLS = """\
import System.Env as Env

fun main() {
    let n = (len(Env.args()) + 1) * 10_000_000
    var go : fun(Int) -> Int = fun(k) = k
    go = fun(k) = if k == 0 { 0 } else { go(k - 1) }
    print(go(n))
}
"""


def test_indirect_tail_calls_run_in_constant_stack():
    result = _run(INDIRECT_TAIL_CALLS)
    assert (result.returncode, result.stdout) == (0, "0\n"), result.stderr


# A closure in the frame of a function that tail-calls itself a million
# times: the frame is released before each tail call, or the shadow stack
# runs out.
FRAME_TAIL_CALLS = """\
import System.Env as Env

fun applyTo(f : fun(Int) -> Int, x : Int, k : Int) -> Int =
    if k == 0 { f(x) } else { applyTo(f, x, k - 1) }

fun spin(n : Int, acc : Int, k : Int) -> Int =
    if n == 0 { acc } else { spin(n - 1, applyTo(fun(x) = x + 3, acc, k), k) }

fun main() {
    let k = len(Env.args())
    print(spin((k + 1) * 1_000_000, 0, k))
}
"""


@both
def test_a_frame_is_released_before_a_tail_call(stress):
    result = _run(FRAME_TAIL_CALLS, stress=stress)
    assert (result.returncode, result.stdout) == (0, "3000000\n"), result.stderr


# A function that returns a pair returns it in two results, here through a
# tail call too.
MULTIPLE_RESULTS = """\
import System.Env as Env

fun bounds(xs : Array Int, i : Int, low : Int, high : Int) -> (Int, Int) =
    if i == len(xs) { (low, high) }
    else { bounds(xs, i + 1, if xs[i] < low { xs[i] } else { low },
                  if xs[i] > high { xs[i] } else { high }) }

fun main() {
    let k = len(Env.args())
    let xs = [3 + k, -7, 12, 0, 5 * k]
    let (low, high) = bounds(xs, 0, xs[0], xs[0])
    print(low)
    print(high)
}
"""


@both
def test_multiple_results(stress):
    result = _run(MULTIPLE_RESULTS, stress=stress)
    assert (result.returncode, result.stdout) == (0, "-7\n12\n"), result.stderr


FLOATS = """\
import System.Env as Env

fun main() {
    let k = Float.fromInt(len(Env.args()) + 2)
    let nan = Float.nan()
    print(k * 2.5 > 4.9)
    print(k / 4.0 == 0.5)
    print(k - 3.0 < 0.0)
    print(nan != nan)
    print(nan == nan)
    print(nan < 1.0 || nan >= 1.0)
    print(Float.isNaN(nan))
    print(Float.bits(-0.0))
    print(Float.truncate(k * 1000.75))
    print(Float.truncate(nan))
    print(Float.truncate(k * 1.0e300))
    print(-k)
}
"""


@both
def test_floats(stress):
    result = _run(FLOATS, stress=stress)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ("True\nTrue\nTrue\nTrue\nFalse\nFalse\nTrue\n"
                             "-9223372036854775808\nSome(2001)\nNone\nNone\n-2.0\n")


# Bytes and code points, which are narrower than a word in memory.
NARROW = """\
import System.Env as Env

fun main() {
    let text = "héllo, wörld" + Int.toString(len(Env.args()))
    let bytes = String.toBytes(text)
    var sum = 0
    for b in bytes { sum = sum + Byte.toInt(b) }
    print(len(bytes))
    print(sum)
    print(Byte.toInt(bytes[1]))
    var codes = 0
    for c in String.codePoints(text) { codes = codes + Char.toInt(c) }
    print(codes)
}
"""


@both
def test_narrow_values(stress):
    result = _run(NARROW, stress=stress)
    assert (result.returncode, result.stdout) == (0, "15\n1737\n195\n1475\n"), result.stderr


BOUNDS = """\
import System.Env as Env

fun main() {
    let xs = [1, 2, 3]
    print(xs[len(Env.args())])
    print(xs[len(Env.args()) + 5])
    print("unreached")
}
"""


@both
def test_an_index_out_of_bounds_panics(stress):
    result = _run(BOUNDS, stress=stress)
    assert (result.returncode, result.stdout) == (1, "1\n")
    assert result.stderr.splitlines()[0] == (
        "panic: array index out of bounds: read at index 5, length 3")


# The panic is raised in a callee, and every caller leaves.
CALLEE_PANIC = """\
fun check(n : Int) -> Int {
    if n > 2 { error("too big: " + Int.toString(n)) }
    n * 10
}

fun main() {
    for var i = 0; i < 5; i = i + 1 { print(check(i)) }
    print("unreached")
}
"""


@both
def test_a_panic_in_a_callee_leaves_every_caller(stress):
    result = _run(CALLEE_PANIC, stress=stress)
    assert (result.returncode, result.stdout) == (1, "0\n10\n20\n")
    assert result.stderr.splitlines()[0] == "panic: too big: 3"


# `exit` returns any type, and here one the runtime's own `exit` does not:
# its result is never read.
EXIT = """\
import System.Env as Env

fun pick(k : Int) -> String = if k > 0 { "yes" } else { Env.exit(3) }

fun main() {
    print(pick(len(Env.args())))
    print(pick(0))
    print("unreached")
}
"""


@both
def test_exit_where_a_value_is_wanted(stress):
    result = _run(EXIT, "x", stress=stress)
    assert (result.returncode, result.stdout) == (3, "yes\n"), result.stderr


# -- roots and traces -----------------------------------------------------------------
#
# Each allocates far past the first collection's threshold, 131072 bytes, so
# an object only a shadow frame holds is freed if the frame does not name it.

# An object made before ten thousand others, which only the frame keeps.
KEEP = """\
import System.Env as Env

type Box = Box { name : String }

fun make(n : Int) -> Box = Box { name = Int.toString(n) }

fun main() {
    let n = match Int.parse(Env.args()[0]) {
        Some(n) -> n
        None -> 0
    }
    let keep = make(42)
    var last = make(0)
    for var i = 0; i < n; i = i + 1 { last = make(i) }
    print(keep.name)
    print(last.name)
}
"""


@both
def test_a_value_held_across_allocations_survives(stress):
    result = _run(KEEP, "200" if stress else "10000", stress=stress)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ("42\n199\n" if stress else "42\n9999\n")


# Ping-pong through closures: each `pong` is a fresh closure, held only by
# `ping`'s frame across the allocation of a string, then tail-called.
PING_PONG = """\
import System.Env as Env

fun ping(n : Int, pong : fun(Int) -> Int) -> Int =
    if n == 0 { 0 } else {
        let label = Int.toString(n)
        pong(n - String.byteLength(label) + String.byteLength(label) - 1)
    }

fun pongFor(k : Int) -> fun(Int) -> Int = fun(n) = ping(n, pongFor(k + n % 2))

fun main() {
    let n = match Int.parse(Env.args()[0]) {
        Some(n) -> n
        None -> 0
    }
    print(ping(n, pongFor(0)))
}
"""


@both
def test_closures_ping_pong_deep(stress):
    result = _run(PING_PONG, "20000" if stress else "10000000", stress=stress)
    assert (result.returncode, result.stdout) == (0, "0\n"), result.stderr


# A million-node list, built and then walked, each by a tail-recursive loop.
LIST = """\
import System.Env as Env

type Chain = Link(Int, Chain) | End

fun build(n : Int, acc : Chain) -> Chain = if n == 0 { acc } else { build(n - 1, Link(n, acc)) }

fun total(xs : Chain, acc : Int) -> Int = match xs {
    End -> acc
    Link(x, rest) -> total(rest, acc + x)
}

fun main() {
    let n = match Int.parse(Env.args()[0]) {
        Some(n) -> n
        None -> 0
    }
    print(total(build(n, End), 0))
}
"""


@both
def test_a_long_list(stress):
    result = _run(LIST, "2000" if stress else "1000000", stress=stress)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ("2001000\n" if stress else "500000500000\n")


# A tail-recursive loop whose lambda captures variables and is passed on, so
# it escapes: every iteration allocates a closure and its environment, 46 MB
# over a million, which the collector frees as it goes.
CAPTURING_LOOP = """\
import System.Env as Env

fun spin(n : Int, acc : Int, step : Int, last : fun(Int) -> Int) -> Int =
    if n == 0 { last(acc) } else {
        let add = fun(x) = x + step + n
        spin(n - 1, add(acc) - n, step, add)
    }

fun main() {
    let n = match Int.parse(Env.args()[0]) {
        Some(n) -> n
        None -> 0
    }
    print(spin(n, 0, len(Env.args()), fun(x) = x))
}
"""


@both
def test_a_capturing_lambda_in_a_loop(stress):
    result = _run(CAPTURING_LOOP, "2000" if stress else "1000000", stress=stress)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ("2002\n" if stress else "1000002\n")


def test_a_capturing_lambda_in_a_loop_allocates_and_collects():
    """What the test above claims: past 16 MB allocated and many collections,
    which escape analysis would quietly take away by keeping `add` in the
    frame."""
    result = _run(CAPTURING_LOOP, "1000000", build=("--gc-stats",))
    assert result.returncode == 0, result.stderr
    stats = next(line for line in result.stderr.splitlines()
                 if line.startswith("[gc] collections "))
    words = stats.replace(",", "").split()
    collections, allocated = int(words[2]), int(words[6])
    assert allocated > 16 << 20, stats
    assert collections > 100, stats


# Non-tail recursion, so that thousands of shadow frames are live at once, each
# holding a string across the recursive call.
DOUBLE = """\
import System.Env as Env

type Chain = Link(Int, Chain) | End

fun build(n : Int, acc : Chain) -> Chain = if n == 0 { acc } else { build(n - 1, Link(n, acc)) }

fun double(xs : Chain) -> Chain = match xs {
    End -> End
    Link(x, rest) -> {
        let s = Int.toString(x)
        let r = double(rest)
        Link(x * 2 + String.byteLength(s) - String.byteLength(s), r)
    }
}

fun total(xs : Chain, acc : Int) -> Int = match xs {
    End -> acc
    Link(x, rest) -> total(rest, acc + x)
}

fun main() {
    let n = match Int.parse(Env.args()[0]) {
        Some(n) -> n
        None -> 0
    }
    print(total(double(build(n, End)), 0))
}
"""


@both
def test_many_frames_hold_roots_at_once(stress):
    # Five thousand deep fits wasmtime's default stack, which runs out near
    # 6500 frames of this, before the shadow stack does.
    result = _run(DOUBLE, "300" if stress else "5000", stress=stress)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ("90300\n" if stress else "25005000\n")


def test_running_out_of_shadow_stack_panics():
    # A 256 MB engine stack, so that the shadow stack runs out first.
    result = _run(DOUBLE, "400000", engine=("-W", "max-wasm-stack=268435456"))
    assert result.returncode == 1
    lines = result.stderr.splitlines()
    assert lines[0] == "panic: stack overflow"
    assert lines[1].startswith("  at double (Main.gob:")
    assert lines[-1] == "  at main (Main.gob:26:17)"


# A closure kept in the frame, whose environment holds a string, called in a
# loop that allocates: the environment's traced field is a root.
FRAME_CLOSURE = """\
import System.Env as Env

fun main() {
    let n = match Int.parse(Env.args()[0]) {
        Some(n) -> n
        None -> 0
    }
    let s = Int.toString(n * 7)
    let g = fun(x : Int) -> Int = x + String.byteLength(s)
    var t = 0
    for var i = 0; i < n; i = i + 1 {
        t = t + g(i) + String.byteLength(Int.toString(i)) - String.byteLength(Int.toString(i))
    }
    print(t)
    print(s)
}
"""


@both
def test_a_frame_closure_keeps_its_environment(stress):
    result = _run(FRAME_CLOSURE, "300" if stress else "100000", stress=stress)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ("46050\n2100\n" if stress else "5000550000\n700000\n")


# The reference's own example (`docs/ref/runtime-errors.md`), whose trace is
# the one a native build prints.
TRACE = """\
fun lastScore(scores : Array Int) -> Int {
    if len(scores) == 0 { error("there are no scores") }
    scores[len(scores) - 1]
}

fun main() {
    print("checking")
    print(lastScore([]))
}
"""


@both
def test_a_panic_prints_its_trace(stress):
    result = _run(TRACE, stress=stress)
    assert (result.returncode, result.stdout) == (1, "checking\n")
    assert result.stderr == ("panic: there are no scores\n"
                             "  at error (Control/Panic.gob:8:36)\n"
                             "  at lastScore (Main.gob:2:27)\n"
                             "  at main (Main.gob:8:11)\n")


# A panic eighty calls deep: the trace keeps the innermost 48 frames and the
# outermost 16, and reads as arm64's does, line for line.
DEEP_TRACE = """\
fun down(n : Int) -> Int = if n == 0 { error("bottom") } else { 1 + down(n - 1) }

fun main() { print(down(80)) }
"""


def test_a_deep_trace_is_shortened():
    result = _run(DEEP_TRACE)
    assert result.returncode == 1
    lines = result.stderr.splitlines()
    assert lines[0] == "panic: bottom"
    # The innermost frame is `down`'s, with `error` inlined into it.
    assert lines[1:3] == ["  at error (Control/Panic.gob:8:36)", "  at down (Main.gob:1:40)"]
    assert lines[3:50] == ["  at down (Main.gob:1:69)"] * 47
    # One of them is the entry's, which has no position and prints nothing.
    assert lines[50] == "  ... 19 more frames"
    assert lines[51:65] == ["  at down (Main.gob:1:69)"] * 14
    assert lines[65:] == ["  at main (Main.gob:3:20)"]


# -- the build ------------------------------------------------------------------------


def test_the_compiler_is_a_valid_module(tmp_path):
    """`src/Main.gob` built for wasm passes the engine's validation, which
    checks every function's types, including the ones no test runs."""
    wasmtime = toolchain.needs_wasmtime()
    out = tmp_path / "turkey.wasm"
    built = subprocess.run(
        toolchain.command(bootc.binary(), "build", "--target", "wasm32-wasi", "-o", str(out),
                          str(bootc.REPO_ROOT / "src" / "Main.gob")),
        cwd=bootc.REPO_ROOT, env=dict(os.environ, TURKEY_LIB=str(lang.LIB)),
        capture_output=True, text=True, timeout=600)
    assert built.returncode == 0, built.stderr
    validated = subprocess.run([wasmtime, "compile", "-o",
                                str(tmp_path / "turkey.cwasm"), str(out)],
                               capture_output=True, text=True, timeout=600)
    assert validated.returncode == 0, validated.stderr


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
