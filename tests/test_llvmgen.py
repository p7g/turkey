import pytest
from pathlib import Path

from turkey.driver import check
from turkey.cli import main as cli_main
from turkey.errors import TurkeyPanic
from turkey.llvmgen import compile, execute, generate


def native(src: str) -> None:
    checked = check(src)
    execute(checked.opt, checked.decls, checked.main)


def test_generated_llvm_is_verified_and_contains_native_arithmetic():
    checked = check("fun main() { print(1 + 2) }")
    text = generate(checked.opt, checked.decls, checked.main)
    assert "llvm.sadd.with.overflow.i64" in text
    assert "turkey_int_to_string" in text


def test_native_scalar_program_prints(capfd):
    native("fun main() { print(40 + 2) }")
    assert capfd.readouterr().out == "42\n"


def test_native_recursive_join_is_stack_safe(capfd):
    native("""
fun main() {
    var i = 0
    while i < 100000 { i = i + 1 }
    print(i)
}
""")
    assert capfd.readouterr().out == "100000\n"


def test_native_checked_integer_overflow_panics():
    with pytest.raises(TurkeyPanic, match="integer overflow in \\+"):
        native("fun main() { print(9223372036854775807 + 1) }")


def test_native_integer_division_remainder_and_shifts(capfd):
    native("""
fun main() {
    print(-7 / 2)
    print(-7 % 3)
    print(Int.shl(1, 10))
    print(Int.shr(-8, 1))
}
""")
    assert capfd.readouterr().out == "-3\n-1\n1024\n-4\n"


def test_native_invalid_shift_panics():
    with pytest.raises(TurkeyPanic, match="shift amount"):
        native("fun main() { print(Int.shl(1, 64)) }")


def test_native_float_division_is_ieee(capfd):
    native("fun main() { print(1.0 / 0.0); print(0.0 / 0.0); print(-0.0) }")
    assert capfd.readouterr().out == "Infinity\nNaN\n-0.0\n"


def test_native_closures_snapshot_values_and_share_cells(capfd):
    native("""
fun main() {
    let fs = [] : Array (fun() -> Int)
    var i = 0
    while i < 3 {
        let n = i
        Array.push(fs, fun() -> Int = n)
        i = i + 1
    }
    var total = 0
    let bump = fun() -> Int = { total = total + 1; total }
    print(fs[0]()); print(fs[1]()); print(fs[2]())
    print(bump()); print(bump())
}
""")
    assert capfd.readouterr().out == "0\n1\n2\n1\n2\n"


def test_native_recursive_local_closure_uses_two_phase_environment(capfd):
    native("""
fun main() {
    fun fib(n : Int) -> Int =
        if n < 2 { n } else { fib(n - 1) + fib(n - 2) }
    print(fib(10))
}
""")
    assert capfd.readouterr().out == "55\n"


def test_exact_roots_survive_collection_on_every_allocation(monkeypatch, capfd):
    monkeypatch.setenv("TURKEY_GC_STRESS", "1")
    checked = check("""
fun main() {
    let prefix = "value="
    let values = [40, 41, 42]
    let show = fun(i : Int) -> String = prefix + Int.toString(values[i])
    print(show(2))
}
""")
    module = compile(checked.opt, checked.decls, checked.main)
    module.execute()
    assert capfd.readouterr().out == "value=42\n"
    assert module.runtime.turkey_heap_objects() == 0


def test_pointer_arrays_are_traced_under_gc_stress(monkeypatch, capfd):
    monkeypatch.setenv("TURKEY_GC_STRESS", "1")
    native("""
fun main() {
    let values = [] : Array (Option Int)
    Array.push(values, Some(1))
    Array.push(values, None)
    print(values)
}
""")
    assert capfd.readouterr().out == "[Some(1), None]\n"


def test_array_byte_uses_one_byte_elements(capfd):
    checked = check("""
fun main() {
    let values = [Byte.maxValue(), Byte.truncate(300)]
    print(values)
}
""")
    text = generate(checked.opt, checked.decls, checked.main)
    calls = [line for line in text.splitlines() if "call i8* @turkey_array_new" in line]
    assert any("i32 1, i32 0" in line for line in calls)
    execute(checked.opt, checked.decls, checked.main)
    assert capfd.readouterr().out == "[255, 44]\n"


def test_llvm_command_prints_verified_ir(tmp_path, capsys):
    program = tmp_path / "program.tl"
    program.write_text("fun main() { print(3) }", encoding="utf-8")
    assert cli_main(["llvm", str(program)]) == 0
    assert "define i8 @turkey_Main_23_main" in capsys.readouterr().out


def test_run_accepts_opt_in_llvm_backend(tmp_path, capfd):
    program = tmp_path / "program.tl"
    program.write_text("fun main() { print(7) }", encoding="utf-8")
    assert cli_main(["run", "--backend", "llvm", str(program)]) == 0
    assert capfd.readouterr().out == "7\n"


@pytest.mark.parametrize("name", [
    "records", "fields", "tuple_binding", "iter", "dicts",
    "question_control", "operators",
])
def test_native_supported_programs_match_conformance_output(name, capfd):
    program = Path(__file__).parent / "programs" / f"{name}.tl"
    checked = check(program.read_text(encoding="utf-8"), str(program),
                    [program.parent.resolve()])
    execute(checked.opt, checked.decls, checked.main, str(program))
    expected = (program.with_suffix(".expected")
                .read_text(encoding="utf-8"))
    assert capfd.readouterr().out == expected
