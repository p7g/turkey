"""The Python backend: source shape, control flow, and evaluator parity."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from turkey.builtins import initial_values
from turkey.driver import check, run
from turkey.eval import Evaluator
from turkey.pygen import execute, generate


PROGRAMS = Path(__file__).parent / "programs"


def compiled(src: str) -> str:
    checked = check(src)
    out = io.StringIO()
    with redirect_stdout(out):
        execute(checked.opt, checked.decls, checked.main)
    return out.getvalue()


def interpreted(checked) -> str:
    out = io.StringIO()
    with redirect_stdout(out):
        Evaluator(checked.decls, initial_values()).run(checked.opt, checked.main)
    return out.getvalue()


def test_generated_source_is_internal_python_not_a_serialized_evaluator():
    checked = check("fun main() { print(1 + 2) }")
    source = generate(checked.opt, checked.decls, checked.main)
    compile(source, "<generated>", "exec")
    assert "def __turkey_run():" in source
    assert "Evaluator" not in source
    assert "REnv" not in source
    assert "Closure" not in source
    assert "JumpSignal" not in source


def test_values_closures_shadowing_mutation_and_patterns_compile_together():
    src = """
type Box = Box { value : Int }
var counter = 0
fun bump(n : Int) -> Int { counter = counter + n; counter }
fun main() {
    let values = [1, 2]
    let make = Some
    let captured = 10
    let add = fun(n : Int) -> Int = captured + n
    let captured = 99
    let box = Box { value = add(values[1]) }
    match make(box.value) {
        Some(n) -> print(bump(n))
        None -> print(0)
    }
}
"""
    checked = check(src)
    assert compiled(src) == interpreted(checked) == "12\n"


def test_array_field_writes_share_the_checked_runtime_operations():
    src = """
fun main() {
    let a = Array.new(1)
    a.capacity = 3
    a.length = 2
    print(a.capacity)
    print(a.length)
}
"""
    checked = check(src)
    assert compiled(src) == interpreted(checked) == "3\n2\n"


def test_operands_stay_left_to_right_across_a_control_flow_operand():
    src = """
var log = ""
fun mark(s : String, n : Int) -> Int { log = log + s; n }
fun take(a : Int, b : Int, c : Int) -> Unit { }
fun main() {
    take(mark("a", 1), if True { mark("b", 2) } else { 0 }, mark("c", 3))
    print(log)
}
"""
    checked = check(src)
    assert compiled(src) == interpreted(checked) == "abc\n"


def test_a_jump_to_an_outer_join_crosses_an_inner_join_without_an_exception():
    src = """
fun answer() -> Int {
    loop {
        loop { return 7 }
    }
}
fun main() { print(answer()) }
"""
    checked = check(src)
    source = generate(checked.opt, checked.decls, checked.main)
    assert "while True:" in source and "JumpSignal" not in source
    assert compiled(src) == interpreted(checked) == "7\n"


def test_a_recursive_join_is_stack_safe_in_generated_python():
    src = """
fun count(n : Int) -> Int {
    var i = 0
    while i < n { i = i + 1 }
    i
}
fun main() { print(count(50000)) }
"""
    assert compiled(src) == "50000\n"


def test_a_closure_snapshots_each_recursive_join_iteration():
    src = """
fun main() {
    let fs = [] : Array (fun() -> Int)
    var i = 0
    while i < 3 {
        let n = i
        Array.push(fs, fun() -> Int = n)
        i = i + 1
    }
    print(fs[0]())
    print(fs[1]())
    print(fs[2]())
}
"""
    checked = check(src)
    assert compiled(src) == interpreted(checked) == "0\n1\n2\n"


SUCCESSFUL = sorted(
    p for p in PROGRAMS.glob("*.tl") if not p.name.startswith("err_"))
SUCCESSFUL += sorted(
    p / "Main.tl" for p in PROGRAMS.iterdir()
    if p.is_dir() and not p.name.startswith("err_") and (p / "Main.tl").is_file()
)


@pytest.mark.parametrize("program", SUCCESSFUL,
                         ids=lambda p: p.parent.name if p.name == "Main.tl" else p.stem)
def test_generated_python_agrees_with_the_evaluator(program: Path):
    src = program.read_text(encoding="utf-8")
    checked = check(src, str(program), [program.parent.resolve()])
    expected = interpreted(checked)
    out = io.StringIO()
    with redirect_stdout(out):
        execute(checked.opt, checked.decls, checked.main, str(program))
    assert out.getvalue() == expected


def test_run_uses_the_generated_backend(monkeypatch, capsys):
    called = False

    def fake_execute(program, decls, main, filename):
        nonlocal called
        called = True

    monkeypatch.setattr("turkey.driver.pygen.execute", fake_execute)
    run("fun main() { print(1) }")
    assert called
    assert capsys.readouterr().out == ""
