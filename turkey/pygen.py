"""Compile checked Core to Python source and execute it.

The generated source is deliberately an internal artifact.  It uses Python
locals and functions for Core locals and lambdas, and a small basic-block
dispatcher for Core join points.  Consequently a recursive ``CJoin`` is a
Python loop rather than evaluator recursion or an exception.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import ast
from .builtins import PRIM_NAMES, initial_primitives
from .core import (
    CApp, CArray, CAssign, CCon, CDeref, CExpr, CField, CIf, CIndex,
    CJoin, CJump, CLam, CLet, CLetRec, CLit, CMatch, CPrim, CProgram,
    CRecord, CRef, CTuple, CTyApp, CTyLam, CUnit, CVar,
)
from .decls import DeclTable
from .types import TCon, spine
from .values import (
    UNIT, ArrayObj, Cell, ConValue, RecordObj, get_field, set_field, truth,
)
from .errors import TurkeyPanic


@dataclass(frozen=True)
class _Name:
    py: str
    # A recursive/local-top-level name must be captured through Python's cell;
    # ordinary locals are snapshotted in lambda defaults.  The latter preserves
    # Core's fresh environment for each trip around a recursive join.
    late: bool = False


@dataclass(frozen=True)
class _Dest:
    block: int | None
    names: tuple[str, ...] = ()


@dataclass
class _Block:
    lines: list[str] = field(default_factory=list)
    term: object | None = None


@dataclass(frozen=True)
class _Return:
    value: str


@dataclass(frozen=True)
class _Goto:
    block: int
    assignments: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class _Branch:
    cond: str
    yes: int
    no: int


@dataclass(frozen=True)
class _MatchArm:
    cond: str
    assignments: tuple[tuple[str, str], ...]
    block: int


@dataclass(frozen=True)
class _Match:
    value: str
    arms: tuple[_MatchArm, ...]


_SAFE = re.compile(r"[^A-Za-z0-9_]")


class _Generator:
    def __init__(self, decls: DeclTable) -> None:
        self.decls = decls
        self.count = 0
        self.constructors: dict[str, str] = {}
        self.primitives: dict[str, str] = {}

    def fresh(self, hint: str = "v") -> str:
        clean = _SAFE.sub("_", hint).strip("_") or "v"
        out = f"_t{self.count}_{clean[:24]}"
        self.count += 1
        return out

    def function(self, name: str, params: list[str], env: dict[str, _Name],
                 body: CExpr) -> tuple[str, str]:
        py_name = self.fresh(name)
        captures: list[str] = []
        seen: set[str] = set(params)
        for binding in env.values():
            if not binding.late and binding.py not in seen:
                seen.add(binding.py)
                captures.append(binding.py)
        signature = params + [f"{name}={name}" for name in captures]
        compiler = _Function(self, py_name, signature)
        compiler.compile(body, env, {}, compiler.entry, _Dest(None))
        return py_name, compiler.render()


class _Function:
    def __init__(self, gen: _Generator, name: str,
                 signature: list[str] | None = None) -> None:
        self.gen = gen
        self.name = name
        self.signature = signature or []
        self.blocks: list[_Block] = []
        self.preamble: list[str] = []
        self.entry = self.new_block()

    def new_block(self) -> int:
        self.blocks.append(_Block())
        return len(self.blocks) - 1

    def line(self, block: int, text: str) -> None:
        self.blocks[block].lines.append(text)

    def end(self, block: int, term) -> None:
        assert self.blocks[block].term is None, f"block {block} already ended"
        self.blocks[block].term = term

    def transfer(self, block: int, dest: _Dest, values: list[str]) -> None:
        if dest.block is None:
            assert len(values) == 1
            self.end(block, _Return(values[0]))
            return
        assert len(dest.names) == len(values)
        self.end(block, _Goto(dest.block, tuple(zip(dest.names, values))))

    # -- expressions -----------------------------------------------------

    def compile(self, e: CExpr, env: dict[str, _Name],
                joins: dict[str, _Dest], block: int, dest: _Dest) -> None:
        if self.can_value(e):
            self.transfer(block, dest, [self.value(e, env, joins, block)])
            return

        if isinstance(e, CLet):
            name = self.gen.fresh(e.name)
            inner = dict(env)
            inner[e.name] = _Name(name)
            if self.can_value(e.value):
                value = self.value(e.value, env, joins, block)
                self.line(block, f"{name} = {value}")
                self.compile(e.body, inner, joins, block, dest)
            else:
                after = self.new_block()
                self.compile(e.body, inner, joins, after, dest)
                self.compile(e.value, env, joins, block, _Dest(after, (name,)))
            return

        if isinstance(e, CLetRec):
            inner = dict(env)
            for bind in e.binds:
                inner[bind.name] = _Name(self.gen.fresh(bind.name), True)

            def finish(i: int, at: int) -> None:
                if i == len(e.binds):
                    self.compile(e.body, inner, joins, at, dest)
                    return
                bind = e.binds[i]
                target = inner[bind.name].py
                if self.can_value(bind.value):
                    made = self.value(bind.value, inner, joins, at)
                    self.line(at, f"{target} = {made}")
                    finish(i + 1, at)
                else:
                    following = self.new_block()
                    finish(i + 1, following)
                    self.compile(bind.value, inner, joins, at,
                                 _Dest(following, (target,)))

            finish(0, block)
            return

        if isinstance(e, CIf):
            def branch(at: int, values: list[str]) -> None:
                yes, no = self.new_block(), self.new_block()
                self.end(at, _Branch(values[0], yes, no))
                self.compile(e.then, env, joins, yes, dest)
                other = e.otherwise if e.otherwise is not None else CUnit(e.ty, e.span)
                self.compile(other, env, joins, no, dest)

            self.values([e.cond], env, joins, block, branch)
            return

        if isinstance(e, CMatch):
            def decide(at: int, values: list[str]) -> None:
                held = self.gen.fresh("scrutinee")
                self.line(at, f"{held} = {values[0]}")
                arms: list[_MatchArm] = []
                for alt in e.alts:
                    target = self.new_block()
                    inner = dict(env)
                    cond, assignments = self.pattern(alt.pat, held, inner)
                    arms.append(_MatchArm(cond, tuple(assignments), target))
                    self.compile(alt.body, inner, joins, target, dest)
                self.end(at, _Match(held, tuple(arms)))

            self.values([e.scrutinee], env, joins, block, decide)
            return

        if isinstance(e, CJoin):
            params = [self.gen.fresh(p.name) for p in e.params]
            target = self.new_block()
            jump_dest = _Dest(target, tuple(params))
            body_env = dict(env)
            for param, py in zip(e.params, params):
                body_env[param.name] = _Name(py)
            body_joins = dict(joins)
            if e.recursive:
                body_joins[e.name] = jump_dest
            rest_joins = dict(joins)
            rest_joins[e.name] = jump_dest
            self.compile(e.body, body_env, body_joins, target, dest)
            self.compile(e.rest, env, rest_joins, block, dest)
            return

        if isinstance(e, CJump):
            target = joins[e.name]

            def jump(at: int, values: list[str]) -> None:
                # Even atoms get temporaries: jump parameters are simultaneous,
                # while their arguments are strict and left-to-right.
                temps = []
                for value in values:
                    temp = self.gen.fresh("jump")
                    self.line(at, f"{temp} = {value}")
                    temps.append(temp)
                self.transfer(at, target, temps)

            self.values(e.args, env, joins, block, jump)
            return

        # A value-shaped node containing a control-shaped operand.
        self.composite(e, env, joins, block, dest)

    def can_value(self, e: CExpr | None) -> bool:
        if e is None:
            return True
        if isinstance(e, (CLit, CUnit, CVar, CCon, CPrim, CLam)):
            return True
        if isinstance(e, (CTyLam, CTyApp)):
            return self.can_value(e.body if isinstance(e, CTyLam) else e.fn)
        if isinstance(e, (CTuple, CArray)):
            return all(self.can_value(x) for x in e.elems)
        if isinstance(e, CRecord):
            return all(self.can_value(x) for _, x in e.fields)
        if isinstance(e, CField):
            return self.can_value(e.target)
        if isinstance(e, CIndex):
            return self.can_value(e.target) and self.can_value(e.index)
        if isinstance(e, CApp):
            return self.can_value(e.fn) and all(self.can_value(x) for x in e.args)
        if isinstance(e, CRef):
            return self.can_value(e.value)
        if isinstance(e, CDeref):
            return self.can_value(e.target)
        if isinstance(e, CAssign):
            target = e.target
            if isinstance(target, CIndex):
                return (self.can_value(e.value) and self.can_value(target.target)
                        and self.can_value(target.index))
            if isinstance(target, CField):
                return self.can_value(e.value) and self.can_value(target.target)
            return self.can_value(e.value) and self.can_value(target)
        return False

    def value(self, e: CExpr, env: dict[str, _Name],
              joins: dict[str, _Dest], block: int) -> str:
        assert self.can_value(e), type(e).__name__
        if isinstance(e, CLit):
            return repr(e.value)
        if isinstance(e, CUnit):
            return "_UNIT"
        if isinstance(e, CVar):
            return env[e.name].py
        if isinstance(e, CCon):
            return self.gen.constructors[e.name]
        if isinstance(e, CPrim):
            return self.gen.primitives[e.name]
        if isinstance(e, CTuple):
            values = []
            for item in e.elems:
                held = self.gen.fresh("tuple")
                self.line(block, f"{held} = {self.value(item, env, joins, block)}")
                values.append(held)
            if len(values) == 1:
                return f"({values[0]},)"
            return "(" + ", ".join(values) + ")"
        if isinstance(e, CArray):
            arr = self.gen.fresh("array")
            self.line(block, f"{arr} = _ArrayObj({len(e.elems)})")
            for item in e.elems:
                value = self.gen.fresh("item")
                self.line(block, f"{value} = {self.value(item, env, joins, block)}")
                self.line(block, f"{arr}.push({value})")
            return arr
        if isinstance(e, CRecord):
            made: list[tuple[str, str]] = []
            for label, item in e.fields:
                value = self.gen.fresh(label)
                self.line(block, f"{value} = {self.value(item, env, joins, block)}")
                made.append((label, value))
            info = self.gen.decls.constructors.get(e.con)
            if info is None:
                fields = ", ".join(f"{label!r}: {value}" for label, value in made)
                return f"_RecordObj({e.con!r}, {{{fields}}})"
            order = info.field_names or []
            by_name = dict(made)
            values = ", ".join(by_name[name] for name in order)
            if len(order) == 1:
                values += ","
            if self.gen.decls.tycons[info.tycon].is_mutable_record:
                fields = ", ".join(f"{name!r}: {by_name[name]}" for name in order)
                return f"_RecordObj({e.con!r}, {{{fields}}})"
            return f"_ConValue({e.con!r}, ({values}), {order!r})"
        if isinstance(e, CField):
            target = self.gen.fresh("target")
            self.line(block, f"{target} = {self.value(e.target, env, joins, block)}")
            kind = self.field_kind(e.target)
            if kind == "array":
                return f"{target}.{e.name}"
            if kind == "record":
                return f"{target}.fields[{e.name!r}]"
            return f"_get_field({target}, {e.name!r})"
        if isinstance(e, CIndex):
            target = self.gen.fresh("target")
            index = self.gen.fresh("index")
            self.line(block, f"{target} = {self.value(e.target, env, joins, block)}")
            self.line(block, f"{index} = {self.value(e.index, env, joins, block)}")
            return f"{target}.get({index})"
        if isinstance(e, CLam):
            inner = dict(env)
            params = []
            for param in e.params:
                py = self.gen.fresh(param.name)
                params.append(py)
                inner[param.name] = _Name(py)
            name, source = self.gen.function(e.name, params, inner, e.body)
            self.line(block, source)
            return name
        if isinstance(e, CApp):
            fn = self.gen.fresh("callable")
            self.line(block, f"{fn} = {self.value(e.fn, env, joins, block)}")
            args = []
            for arg in e.args:
                held = self.gen.fresh("argument")
                self.line(block, f"{held} = {self.value(arg, env, joins, block)}")
                args.append(held)
            return f"{fn}({', '.join(args)})"
        if isinstance(e, CTyLam):
            return self.value(e.body, env, joins, block)
        if isinstance(e, CTyApp):
            return self.value(e.fn, env, joins, block)
        if isinstance(e, CRef):
            held = self.gen.fresh("referent")
            self.line(block, f"{held} = {self.value(e.value, env, joins, block)}")
            return f"_Cell({held})"
        if isinstance(e, CDeref):
            held = self.gen.fresh("cell")
            self.line(block, f"{held} = {self.value(e.target, env, joins, block)}")
            return f"{held}.value"
        if isinstance(e, CAssign):
            value = self.gen.fresh("assigned")
            self.line(block, f"{value} = {self.value(e.value, env, joins, block)}")
            target = e.target
            if isinstance(target, CIndex):
                obj = self.gen.fresh("target")
                index = self.gen.fresh("index")
                self.line(block, f"{obj} = {self.value(target.target, env, joins, block)}")
                self.line(block, f"{index} = {self.value(target.index, env, joins, block)}")
                self.line(block, f"{obj}.set({index}, {value})")
            elif isinstance(target, CField):
                obj = self.gen.fresh("target")
                self.line(block, f"{obj} = {self.value(target.target, env, joins, block)}")
                kind = self.field_kind(target.target)
                if kind == "array":
                    method = "set_length" if target.name == "length" else "set_capacity"
                    self.line(block, f"{obj}.{method}({value})")
                elif kind == "record":
                    self.line(block, f"{obj}.fields[{target.name!r}] = {value}")
                else:
                    self.line(block, f"_set_field({obj}, {target.name!r}, {value})")
            else:
                obj = self.gen.fresh("cell")
                self.line(block, f"{obj} = {self.value(target, env, joins, block)}")
                self.line(block, f"{obj}.value = {value}")
            return "_UNIT"
        raise AssertionError(f"no Python value rule for {type(e).__name__}")

    def composite(self, e: CExpr, env: dict[str, _Name],
                  joins: dict[str, _Dest], block: int, dest: _Dest) -> None:
        def finish(at: int, values: list[str]) -> None:
            if isinstance(e, CTuple):
                value = "(" + ", ".join(values) + ("," if len(values) == 1 else "") + ")"
            elif isinstance(e, CArray):
                arr = self.gen.fresh("array")
                self.line(at, f"{arr} = _ArrayObj({len(values)})")
                for item in values:
                    self.line(at, f"{arr}.push({item})")
                value = arr
            elif isinstance(e, CRecord):
                value = self.record_from_values(e, values)
            elif isinstance(e, CField):
                kind = self.field_kind(e.target)
                if kind == "array":
                    value = f"{values[0]}.{e.name}"
                elif kind == "record":
                    value = f"{values[0]}.fields[{e.name!r}]"
                else:
                    value = f"_get_field({values[0]}, {e.name!r})"
            elif isinstance(e, CIndex):
                value = f"{values[0]}.get({values[1]})"
            elif isinstance(e, CApp):
                value = f"{values[0]}({', '.join(values[1:])})"
            elif isinstance(e, CRef):
                value = f"_Cell({values[0]})"
            elif isinstance(e, CDeref):
                value = f"{values[0]}.value"
            elif isinstance(e, (CTyLam, CTyApp)):
                value = values[0]
            elif isinstance(e, CAssign):
                value = self.assignment_from_values(e, values, at)
            else:
                raise AssertionError(f"no composite rule for {type(e).__name__}")
            self.transfer(at, dest, [value])

        if isinstance(e, (CTuple, CArray)):
            operands = e.elems
        elif isinstance(e, CRecord):
            operands = [x for _, x in e.fields]
        elif isinstance(e, CField):
            operands = [e.target]
        elif isinstance(e, CIndex):
            operands = [e.target, e.index]
        elif isinstance(e, CApp):
            operands = [e.fn, *e.args]
        elif isinstance(e, (CRef, CDeref)):
            operands = [e.value if isinstance(e, CRef) else e.target]
        elif isinstance(e, (CTyLam, CTyApp)):
            operands = [e.body if isinstance(e, CTyLam) else e.fn]
        elif isinstance(e, CAssign):
            target = e.target
            if isinstance(target, CIndex):
                operands = [e.value, target.target, target.index]
            elif isinstance(target, CField):
                operands = [e.value, target.target]
            else:
                operands = [e.value, target]
        else:
            raise AssertionError(f"cannot decompose {type(e).__name__}")
        self.values(operands, env, joins, block, finish)

    def values(self, exprs: list[CExpr], env: dict[str, _Name],
               joins: dict[str, _Dest], block: int, done) -> None:
        def go(i: int, at: int, values: list[str]) -> None:
            if i == len(exprs):
                done(at, values)
                return
            expr = exprs[i]
            if self.can_value(expr):
                temp = self.gen.fresh("value")
                self.line(at, f"{temp} = {self.value(expr, env, joins, at)}")
                go(i + 1, at, values + [temp])
                return
            temp = self.gen.fresh("value")
            following = self.new_block()
            go(i + 1, following, values + [temp])
            self.compile(expr, env, joins, at, _Dest(following, (temp,)))

        go(0, block, [])

    def record_from_values(self, e: CRecord, values: list[str]) -> str:
        made = list(zip((name for name, _ in e.fields), values))
        info = self.gen.decls.constructors.get(e.con)
        if info is None:
            fields = ", ".join(f"{name!r}: {value}" for name, value in made)
            return f"_RecordObj({e.con!r}, {{{fields}}})"
        order = info.field_names or []
        by_name = dict(made)
        if self.gen.decls.tycons[info.tycon].is_mutable_record:
            fields = ", ".join(f"{name!r}: {by_name[name]}" for name in order)
            return f"_RecordObj({e.con!r}, {{{fields}}})"
        args = ", ".join(by_name[name] for name in order)
        if len(order) == 1:
            args += ","
        return f"_ConValue({e.con!r}, ({args}), {order!r})"

    def assignment_from_values(self, e: CAssign, values: list[str], block: int) -> str:
        value, target = values[0], e.target
        if isinstance(target, CIndex):
            self.line(block, f"{values[1]}.set({values[2]}, {value})")
        elif isinstance(target, CField):
            kind = self.field_kind(target.target)
            if kind == "array":
                method = "set_length" if target.name == "length" else "set_capacity"
                self.line(block, f"{values[1]}.{method}({value})")
            elif kind == "record":
                self.line(block, f"{values[1]}.fields[{target.name!r}] = {value}")
            else:
                self.line(block, f"_set_field({values[1]}, {target.name!r}, {value})")
        else:
            self.line(block, f"{values[1]}.value = {value}")
        return "_UNIT"

    # -- patterns and rendering -----------------------------------------

    def pattern(self, pat, value: str, env: dict[str, _Name]) -> tuple[str, list[tuple[str, str]]]:
        if isinstance(pat, ast.PWild):
            return "True", []
        if isinstance(pat, ast.PVar):
            py = self.gen.fresh(pat.name)
            env[pat.name] = _Name(py)
            return "True", [(py, value)]
        if isinstance(pat, ast.PAnnot):
            return self.pattern(pat.pat, value, env)
        if isinstance(pat, ast.PLit):
            return f"({value} == {pat.value!r} and type({value}) is type({pat.value!r}))", []
        if isinstance(pat, ast.PTuple):
            conds = [f"isinstance({value}, tuple)", f"len({value}) == {len(pat.elems)}"]
            assignments: list[tuple[str, str]] = []
            for i, sub in enumerate(pat.elems):
                cond, made = self.pattern(sub, f"{value}[{i}]", env)
                conds.append(cond)
                assignments.extend(made)
            return "(" + " and ".join(conds) + ")", assignments
        if isinstance(pat, (ast.PCon, ast.PRecord)):
            info = self.gen.decls.constructors[pat.name]
            mutable = self.gen.decls.tycons[info.tycon].is_mutable_record
            conds = [f"isinstance({value}, (_ConValue, _RecordObj))",
                     f"{value}.con == {pat.name!r}"]
            assignments: list[tuple[str, str]] = []
            if isinstance(pat, ast.PCon):
                fields = info.field_names or []
                pieces = list(enumerate(pat.args))
                access = lambda i: (f"{value}.fields[{fields[i]!r}]" if mutable
                                    else f"{value}.args[{i}]")
            else:
                pieces = [(name, sub) for name, sub in pat.fields]
                if mutable:
                    access = lambda name: f"{value}.fields[{name!r}]"
                else:
                    fields = info.field_names or []
                    access = lambda name: f"{value}.args[{fields.index(name)}]"
            for key, sub in pieces:
                cond, made = self.pattern(sub, access(key), env)
                conds.append(cond)
                assignments.extend(made)
            return "(" + " and ".join(conds) + ")", assignments
        raise AssertionError(f"no Python pattern rule for {type(pat).__name__}")

    def field_kind(self, e: CExpr) -> str:
        head, _ = spine(e.ty)
        if not isinstance(head, TCon):
            return "dynamic"
        if head.name == "Array":
            return "array"
        if head.name.startswith("%Dict.") or head.name in self.gen.decls.tycons:
            return "record"
        return "dynamic"

    def render(self, initial: int | None = None) -> str:
        for i, block in enumerate(self.blocks):
            assert block.term is not None, f"unterminated block {i} in {self.name}"
        out = [f"def {self.name}({', '.join(self.signature)}):"]
        for line in self.preamble:
            out.extend(_indent(line, 1))
        out.append(f"    _pc = {self.entry if initial is None else initial}")
        out.append("    while True:")
        for i, block in enumerate(self.blocks):
            word = "if" if i == 0 else "elif"
            out.append(f"        {word} _pc == {i}:")
            for line in block.lines:
                out.extend(_indent(line, 3))
            out.extend(self.render_term(block.term, 3))
        out.append("        else:")
        out.append("            raise AssertionError(f'bad generated block {_pc}')")
        return "\n".join(out)

    def render_term(self, term, indent: int) -> list[str]:
        pad = "    " * indent
        if isinstance(term, _Return):
            return [f"{pad}return {term.value}"]
        if isinstance(term, _Goto):
            out = []
            if term.assignments:
                left = ", ".join(name for name, _ in term.assignments)
                right = ", ".join(value for _, value in term.assignments)
                out.append(f"{pad}{left} = {right}")
            out.extend((f"{pad}_pc = {term.block}", f"{pad}continue"))
            return out
        if isinstance(term, _Branch):
            return [
                f"{pad}if _truth({term.cond}):",
                f"{pad}    _pc = {term.yes}",
                f"{pad}else:",
                f"{pad}    _pc = {term.no}",
                f"{pad}continue",
            ]
        if isinstance(term, _Match):
            out: list[str] = []
            if not term.arms:
                return [
                    f'{pad}raise _TurkeyPanic('
                    f'f"no match arm applies to {{{term.value}!r}}")'
                ]
            for i, arm in enumerate(term.arms):
                out.append(f"{pad}{'if' if i == 0 else 'elif'} {arm.cond}:")
                if arm.assignments:
                    left = ", ".join(name for name, _ in arm.assignments)
                    right = ", ".join(value for _, value in arm.assignments)
                    out.append(f"{pad}    {left} = {right}")
                out.append(f"{pad}    _pc = {arm.block}")
            out.append(f"{pad}else:")
            out.append(
                f'{pad}    raise _TurkeyPanic('
                f'f"no match arm applies to {{{term.value}!r}}")')
            out.append(f"{pad}continue")
            return out
        raise AssertionError(f"no renderer for {type(term).__name__}")


def _indent(text: str, levels: int) -> list[str]:
    pad = "    " * levels
    return [pad + line if line else "" for line in text.splitlines()]


def generate(program: CProgram, decls: DeclTable, main: str = "main") -> str:
    """Return deterministic Python source for a checked Core program."""
    gen = _Generator(decls)
    runner = _Function(gen, "__turkey_run")

    # Names are allocated before any body is compiled, just as Core's checker
    # puts every top-level name in scope before checking any definition.
    env: dict[str, _Name] = {}
    for bind in program.dicts + program.binds:
        env.setdefault(bind.name, _Name(gen.fresh(bind.name), True))

    for name in sorted(PRIM_NAMES):
        py = gen.fresh(name)
        gen.primitives[name] = py
        env[name] = _Name(py, True)
        runner.preamble.append(f"{py} = _PRIMS[{name!r}]")

    for name, info in decls.constructors.items():
        py = gen.fresh(name)
        gen.constructors[name] = py
        env[name] = _Name(py, True)
        if info.arity == 0:
            runner.preamble.append(f"{py} = _ConValue({name!r}, ())")
            continue
        args = [gen.fresh(field or "arg") for field in (info.field_names or [""] * info.arity)]
        if decls.tycons[info.tycon].is_mutable_record:
            fields = ", ".join(
                f"{field!r}: {arg}" for field, arg in zip(info.field_names or [], args))
            body = f"return _RecordObj({name!r}, {{{fields}}})"
        else:
            packed = ", ".join(args) + ("," if len(args) == 1 else "")
            body = f"return _ConValue({name!r}, ({packed}), {info.field_names!r})"
        runner.preamble.append(
            f"def {py}({', '.join(args)}):\n    {body}")

    final = runner.new_block()
    if main in env:
        runner.line(final, f"{env[main].py}()")
    runner.end(final, _Return("None"))

    actions: list[tuple] = []
    records: list[tuple[str, CRecord]] = []
    for bind in program.dicts:
        if isinstance(bind.value, CRecord):
            actions.append(("placeholder", env[bind.name].py, bind.value.con))
            records.append((env[bind.name].py, bind.value))
        else:
            actions.append(("bind", env[bind.name].py, bind.value))
    for record, node in records:
        for label, value in node.fields:
            actions.append(("field", record, label, value))
    for bind in program.binds:
        actions.append(("bind", env[bind.name].py, bind.value))

    following = final
    for action in reversed(actions):
        entry = runner.new_block()
        kind = action[0]
        if kind == "placeholder":
            _, target, con = action
            runner.line(entry, f"{target} = _RecordObj({con!r}, {{}})")
            runner.end(entry, _Goto(following))
        elif kind == "bind":
            _, target, value = action
            runner.compile(value, env, {}, entry, _Dest(following, (target,)))
        else:
            _, record, label, value = action
            held = gen.fresh(label)
            assigned = runner.new_block()
            runner.line(assigned, f"{record}.fields[{label!r}] = {held}")
            runner.end(assigned, _Goto(following))
            runner.compile(value, env, {}, entry, _Dest(assigned, (held,)))
        following = entry

    # The constructor/primitive preamble runs once; the first real block is
    # the first top-level action, not block zero (which is otherwise unused).
    runner.end(runner.entry, _Goto(following))
    return runner.render() + "\n"


def execute(program: CProgram, decls: DeclTable, main: str = "main",
            filename: str = "<input>") -> None:
    """Compile generated source in memory and execute its runner."""
    source = generate(program, decls, main)
    namespace = _runtime_namespace()
    exec(compile(source, filename, "exec"), namespace)
    namespace["__turkey_run"]()


def _runtime_namespace() -> dict[str, object]:
    """Fresh globals for one generated module (also used by the benchmark)."""
    return {
        "_UNIT": UNIT,
        "_ArrayObj": ArrayObj,
        "_Cell": Cell,
        "_ConValue": ConValue,
        "_RecordObj": RecordObj,
        "_get_field": get_field,
        "_set_field": set_field,
        "_truth": truth,
        "_TurkeyPanic": TurkeyPanic,
        "_PRIMS": initial_primitives(),
    }


__all__ = ["execute", "generate"]
