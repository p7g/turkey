import pytest
from pathlib import Path

from turkey.driver import check
from turkey.cli import main as cli_main
from turkey.errors import TurkeyPanic
from turkey.llvmgen import execute, generate


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


@pytest.mark.parametrize("name", ["records", "fields"])
def test_native_aggregates_and_patterns_match_conformance_output(name, capfd):
    program = Path(__file__).parent / "programs" / f"{name}.tl"
    checked = check(program.read_text(encoding="utf-8"), str(program),
                    [program.parent.resolve()])
    execute(checked.opt, checked.decls, checked.main, str(program))
    expected = (program.with_suffix(".expected")
                .read_text(encoding="utf-8"))
    assert capfd.readouterr().out == expected
