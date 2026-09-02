"""The Python backend: source shape, control flow, and evaluator parity."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from turkey.builtins import initial_values
from turkey.cli import main as cli_main
from turkey.driver import check, run
from turkey.errors import TurkeyPanic
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


def test_python_command_prints_compilable_source_without_running_it(tmp_path, capsys):
    program = tmp_path / "program.tl"
    program.write_text("fun main() { print(12345) }", encoding="utf-8")

    assert cli_main(["python", str(program)]) == 0
    captured = capsys.readouterr()
    compile(captured.out, str(program), "exec")
    assert captured.err == ""
    assert "def __turkey_run():" in captured.out


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


def test_array_length_uses_the_length_protocol_in_both_backends():
    src = """
fun main() {
    let a = Array.new(3)
    print(len(a))
    Array.push(a, 10)
    Array.push(a, 20)
    print(len(a))
}
"""
    checked = check(src)
    assert compiled(src) == interpreted(checked) == "0\n2\n"


def test_user_defined_index_and_length_instances_work_together():
    src = """
type Box = Box { values : Array Int }

instance Index Box {
    type Key = Int
    type Value = Int
    fun get(box, i) = box.values[i]
    fun set(box, i, x) { box.values[i] = x }
}

instance Length Box {
    fun len(box) = len(box.values)
}

fun main() {
    let box = Box { values = [1, 2] }
    box[1] = 7
    print(len(box))
    print(box[1])
}
"""
    checked = check(src)
    assert compiled(src) == interpreted(checked) == "2\n7\n"


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


def test_run_keeps_the_python_api_default_for_capture_compatibility(monkeypatch, capsys):
    called = False

    def fake_execute(program, decls, main, filename):
        nonlocal called
        called = True

    monkeypatch.setattr("turkey.driver.pygen.execute", fake_execute)
    run("fun main() { print(1) }")
    assert called
    assert capsys.readouterr().out == ""


def test_run_accepts_the_native_backend_explicitly(monkeypatch, capsys):
    called = False

    def fake_execute(program, decls, main, filename):
        nonlocal called
        called = True

    monkeypatch.setattr("turkey.driver.llvmgen.execute", fake_execute)
    run("fun main() { print(1) }", backend="llvm")
    assert called
    assert capsys.readouterr().out == ""


def test_optimized_panic_frames_agree_between_backends():
    src = """fun descend(n : Int) -> Int {
    if n == 0 { return error("boom") }
    descend(n - 1)
}
fun main() { print(descend(2)) }
"""
    checked = check(src, "trace.tl")

    with pytest.raises(TurkeyPanic) as compiled_panic:
        execute(checked.opt, checked.decls, checked.main, "trace.tl")
    with pytest.raises(TurkeyPanic) as interpreted_panic:
        Evaluator(checked.decls, initial_values()).run(
            checked.opt, checked.main)

    expected = """panic: boom
  at descend (trace.tl:2:24)
  at descend (trace.tl:3:5)
  at descend (trace.tl:3:5)
  at main (trace.tl:5:20)"""
    assert compiled_panic.value.render("trace.tl") == expected
    assert interpreted_panic.value.render("trace.tl") == expected


def test_a_panic_stack_does_not_invent_inlined_frames():
    src = """fun boom(n : Int) -> Int = error("bad")
fun middle(n : Int) -> Int = boom(n)
fun main() { print(middle(3)) }
"""
    checked = check(src, "inline.tl")
    with pytest.raises(TurkeyPanic) as panic:
        execute(checked.opt, checked.decls, checked.main, "inline.tl")
    assert panic.value.render("inline.tl") == (
        "panic: bad\n  at main (inline.tl:3:20)"
    )
