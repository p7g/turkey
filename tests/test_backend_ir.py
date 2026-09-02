"""The native backend's checked control-flow boundary."""

import pytest

from turkey.backend_ir import (
    Block, Branch, CheckError, Constant, Function, Jump, Layout, Module, Return,
    Value, check, format_module,
)


def test_a_well_formed_loop_has_a_deterministic_rendering():
    n = Value("n", Layout.I64)
    again = Block("again", [n], terminator=Jump("again", (n,)))
    module = Module([Function("main", [], Layout.I64, [
        Block("entry", terminator=Jump("again", (Constant(Layout.I64, 1),))),
        again,
    ])], "main")
    check(module)
    assert format_module(module) == """entry @main
fun @main() -> i64 {
  entry:
    jump again(i64 1)
  again(%n:i64):
    jump again(%n:i64)
}
"""


def test_checker_rejects_layout_mismatch_on_an_edge():
    module = Module([Function("main", [], Layout.I64, [
        Block("entry", terminator=Jump("done", (Constant(Layout.F64, 1.0),))),
        Block("done", [Value("n", Layout.I64)],
              terminator=Return(Value("n", Layout.I64))),
    ])], "main")
    with pytest.raises(CheckError, match="wrong jump layout"):
        check(module)


def test_checker_rejects_non_boolean_branches():
    module = Module([Function("main", [], Layout.I64, [
        Block("entry", terminator=Branch(Constant(Layout.I64, 1), "yes", "no")),
        Block("yes", terminator=Return(Constant(Layout.I64, 1))),
        Block("no", terminator=Return(Constant(Layout.I64, 0))),
    ])], "main")
    with pytest.raises(CheckError, match="non-boolean branch"):
        check(module)
