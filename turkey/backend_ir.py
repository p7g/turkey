"""Checked machine-oriented control-flow IR for native backends.

Core deliberately retains expression structure.  This IR is the boundary at
which evaluation order and control flow become explicit, without exposing
llvmlite objects to semantic lowering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Layout(Enum):
    UNIT = "unit"
    I1 = "i1"
    I8 = "i8"
    I32 = "i32"
    I64 = "i64"
    F64 = "f64"
    PTR = "ptr"
    BOXED = "boxed"


@dataclass(frozen=True)
class Value:
    name: str
    layout: Layout


@dataclass(frozen=True)
class Constant:
    layout: Layout
    value: object


Operand = Value | Constant


@dataclass(frozen=True)
class Instruction:
    op: str
    args: tuple[Operand | str, ...] = ()
    result: Value | None = None


@dataclass(frozen=True)
class Return:
    value: Operand


@dataclass(frozen=True)
class Jump:
    target: str
    args: tuple[Operand, ...] = ()


@dataclass(frozen=True)
class Branch:
    condition: Operand
    yes: str
    no: str


@dataclass(frozen=True)
class Panic:
    message: str


Terminator = Return | Jump | Branch | Panic


@dataclass
class Block:
    name: str
    params: list[Value] = field(default_factory=list)
    instructions: list[Instruction] = field(default_factory=list)
    terminator: Terminator | None = None


@dataclass
class Function:
    name: str
    params: list[Value]
    result: Layout
    blocks: list[Block]
    entry: str = "entry"
    # Mutable compiler temporaries.  LLVM emission lowers these to entry-block
    # allocas, so values remain available after a control-flow edge without
    # weakening the IR's block-local SSA rule.
    slots: list[Value] = field(default_factory=list)


@dataclass
class Module:
    functions: list[Function]
    entry: str
    globals: list[Value] = field(default_factory=list)


class CheckError(ValueError):
    pass


def check(module: Module) -> None:
    """Reject malformed backend IR before LLVM sees it."""
    functions: set[str] = set()
    for function in module.functions:
        if function.name in functions:
            raise CheckError(f"duplicate function {function.name}")
        functions.add(function.name)
        _check_function(function)
    if module.entry not in functions:
        raise CheckError(f"missing entry function {module.entry}")


def _check_function(function: Function) -> None:
    blocks = {block.name: block for block in function.blocks}
    if len(blocks) != len(function.blocks):
        raise CheckError(f"duplicate block in {function.name}")
    if function.entry not in blocks:
        raise CheckError(f"missing entry block in {function.name}")

    defined = {value.name for value in function.params}
    if len(defined) != len(function.params):
        raise CheckError(f"duplicate parameter in {function.name}")
    slot_names = {slot.name for slot in function.slots}
    if len(slot_names) != len(function.slots):
        raise CheckError(f"duplicate slot in {function.name}")
    for block in function.blocks:
        if block.terminator is None:
            raise CheckError(f"unterminated block {function.name}:{block.name}")
        local = defined | {value.name for value in block.params}
        for instruction in block.instructions:
            for arg in instruction.args:
                if isinstance(arg, Value) and arg.name not in local:
                    raise CheckError(
                        f"undefined value %{arg.name} in {function.name}:{block.name}")
            if instruction.result is not None:
                if instruction.result.name in local:
                    raise CheckError(
                        f"duplicate value %{instruction.result.name} in {function.name}")
                local.add(instruction.result.name)
        term = block.terminator
        operands: tuple[Operand, ...] = ()
        if isinstance(term, Return):
            operands = (term.value,)
            if _layout(term.value) is not function.result:
                raise CheckError(f"wrong return layout in {function.name}:{block.name}")
        elif isinstance(term, Jump):
            if term.target not in blocks:
                raise CheckError(f"unknown target {term.target} in {function.name}")
            target = blocks[term.target]
            if len(term.args) != len(target.params):
                raise CheckError(f"wrong jump arity to {term.target} in {function.name}")
            if any(_layout(arg) is not param.layout
                   for arg, param in zip(term.args, target.params)):
                actual = ", ".join(_layout(arg).value for arg in term.args)
                expected = ", ".join(param.layout.value for param in target.params)
                raise CheckError(
                    f"wrong jump layout to {term.target} in {function.name}: "
                    f"got ({actual}), expected ({expected})")
            operands = term.args
        elif isinstance(term, Branch):
            if term.yes not in blocks or term.no not in blocks:
                raise CheckError(f"unknown branch target in {function.name}:{block.name}")
            if _layout(term.condition) is not Layout.I1:
                raise CheckError(f"non-boolean branch in {function.name}:{block.name}")
            operands = (term.condition,)
        for operand in operands:
            if isinstance(operand, Value) and operand.name not in local:
                raise CheckError(
                    f"undefined value %{operand.name} in {function.name}:{block.name}")


def _layout(operand: Operand) -> Layout:
    return operand.layout


def format_module(module: Module) -> str:
    """Stable human-readable form used by focused lowering tests."""
    lines = [f"entry @{module.entry}"]
    lines.extend(f"global ${value.name}:{value.layout.value}"
                 for value in module.globals)
    for function in module.functions:
        params = ", ".join(_value(p) for p in function.params)
        lines.append(f"fun @{function.name}({params}) -> {function.result.value} {{")
        for slot in function.slots:
            lines.append(f"  slot ${slot.name}:{slot.layout.value}")
        for block in function.blocks:
            suffix = "(" + ", ".join(_value(p) for p in block.params) + ")" if block.params else ""
            lines.append(f"  {block.name}{suffix}:")
            for inst in block.instructions:
                head = f"{_value(inst.result)} = " if inst.result else ""
                args = ", ".join(_operand(arg) for arg in inst.args)
                lines.append(f"    {head}{inst.op}{(' ' + args) if args else ''}")
            lines.append(f"    {_term(block.terminator)}")
        lines.append("}")
    return "\n".join(lines) + "\n"


def _value(value: Value | None) -> str:
    assert value is not None
    return f"%{value.name}:{value.layout.value}"


def _operand(value: Operand | str) -> str:
    if isinstance(value, Value):
        return _value(value)
    if isinstance(value, Constant):
        return f"{value.layout.value} {value.value!r}"
    return f"@{value}"


def _term(term: Terminator | None) -> str:
    assert term is not None
    if isinstance(term, Return):
        return f"return {_operand(term.value)}"
    if isinstance(term, Jump):
        return f"jump {term.target}({', '.join(_operand(a) for a in term.args)})"
    if isinstance(term, Branch):
        return f"branch {_operand(term.condition)}, {term.yes}, {term.no}"
    return f"panic {term.message!r}"


__all__ = [
    "Block", "Branch", "CheckError", "Constant", "Function", "Instruction",
    "Jump", "Layout", "Module", "Operand", "Panic", "Return", "Value",
    "check", "format_module",
]
