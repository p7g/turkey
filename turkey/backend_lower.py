"""Lower optimized Core into :mod:`turkey.backend_ir`.

This first native slice covers the scalar language and real CFG joins.  Every
unsupported heap or closure form is diagnosed here, before LLVM construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import fields as data_fields

from . import ast, backend_ir as bir
from .core import (
    CApp, CArray, CAssign, CBind, CCon, CDeref, CExpr, CField, CIf, CIndex,
    CJoin, CJump, CLam, CLet, CLetRec, CLit, CMatch, CPrim, CProgram, CProject,
    CRecord, CRef, CTuple, CTyApp, CTyLam, CUnit, CVar, is_ref, names_of,
)
from .builtins import PRIM_NAMES
from .errors import Unsupported
from .prelude import BOOL_FALSE, BOOL_TRUE
from .types import (
    BOTTOM, BYTE, CHAR, FLOAT, INT, STRING, UNIT, TCon, TFun, TTuple, Type,
    TVar, instantiate, prune, spine, unify,
)


def layout_of(ty: Type) -> bir.Layout:
    ty = prune(ty)
    if ty is BOTTOM:
        return bir.Layout.UNIT
    if isinstance(ty, TCon):
        if ty.name == INT.name:
            return bir.Layout.I64
        if ty.name == BYTE.name:
            return bir.Layout.I8
        if ty.name == CHAR.name:
            return bir.Layout.I32
        if ty.name == FLOAT.name:
            return bir.Layout.F64
        if ty.name == STRING.name:
            return bir.Layout.PTR
        if ty.name == UNIT.name:
            return bir.Layout.UNIT
        if ty.name == "Data.Bool.Type#Bool":
            return bir.Layout.I1
    if is_ref(ty) or isinstance(ty, TFun):
        return bir.Layout.PTR
    return bir.Layout.PTR


def mangle(name: str) -> str:
    pieces = []
    for byte in name.encode("utf-8"):
        char = chr(byte)
        pieces.append(char if char.isalnum() or char == "_" else f"_{byte:02x}_")
    return "turkey_" + "".join(pieces)


@dataclass(frozen=True)
class _Destination:
    block: bir.Block | None


class _FunctionLowerer:
    def __init__(self, bind: CBind, lam: CLam,
                 functions: dict[str, tuple[str, TFun]], decls,
                 tags: dict[str, int], record_fields: dict[str, list[str]]) -> None:
        self.bind = bind
        self.lam = lam
        self.functions = functions
        self.decls = decls
        self.tags = tags
        self.record_fields = record_fields
        self.count = 0
        self.blocks: list[bir.Block] = []
        self.slots: list[bir.Value] = []
        self.slot_names: set[str] = set()
        self.entry = self.new_block("entry")
        self.params = [bir.Value(self.fresh(p.name), layout_of(p.ty)) for p in lam.params]
        self.env: dict[str, bir.Value] = {}
        for core_param, param in zip(lam.params, self.params):
            slot = self.new_slot(core_param.name, param.layout)
            self.entry.instructions.append(bir.Instruction("slot_store", (slot.name, param)))
            self.env[core_param.name] = slot

    def fresh(self, hint: str) -> str:
        clean = "".join(c if c.isalnum() else "_" for c in hint).strip("_") or "v"
        value = f"v{self.count}_{clean[:20]}"
        self.count += 1
        return value

    def new_slot(self, hint: str, layout: bir.Layout) -> bir.Value:
        slot = bir.Value(self.fresh("slot_" + hint), layout)
        assert slot.name not in self.slot_names
        self.slot_names.add(slot.name)
        self.slots.append(slot)
        return slot

    def new_block(self, hint: str, result: bir.Layout | None = None) -> bir.Block:
        name = self.fresh("block_" + hint)
        params = [] if result is None else [bir.Value(self.fresh("arg"), result)]
        block = bir.Block(name, params)
        self.blocks.append(block)
        return block

    def emit(self, block: bir.Block, op: str,
             args: tuple[bir.Operand | str, ...], layout: bir.Layout) -> bir.Value:
        result = bir.Value(self.fresh(op), layout)
        block.instructions.append(bir.Instruction(op, args, result))
        return result

    def finish(self) -> bir.Function:
        self.lower(self.lam.body, dict(self.env), {}, self.entry, _Destination(None))
        function_type = prune(self.bind.ty)
        assert isinstance(function_type, TFun)
        return bir.Function(
            mangle(self.bind.name), self.params, layout_of(function_type.ret),
            self.blocks, self.entry.name, self.slots,
        )

    def transfer(self, block: bir.Block, dest: _Destination,
                 value: bir.Operand) -> None:
        if dest.block is None:
            block.terminator = bir.Return(value)
        else:
            block.terminator = bir.Jump(dest.block.name, (value,))

    def lower(self, expr: CExpr | None, env: dict[str, bir.Value],
              joins: dict[str, tuple[bir.Block, list[bir.Value]]],
              block: bir.Block, dest: _Destination) -> None:
        assert expr is not None
        if isinstance(expr, CLet):
            after = self.new_block("let", layout_of(expr.value.ty))
            slot = self.new_slot(expr.name, layout_of(expr.bound))
            after.instructions.append(
                bir.Instruction("slot_store", (slot.name, after.params[0])))
            inner = dict(env)
            inner[expr.name] = slot
            self.lower(expr.body, inner, joins, after, dest)
            self.lower(expr.value, env, joins, block, _Destination(after))
            return
        if isinstance(expr, CIf):
            def branch(at: bir.Block, values: list[bir.Operand]) -> None:
                yes, no = self.new_block("then"), self.new_block("else")
                at.terminator = bir.Branch(values[0], yes.name, no.name)
                self.lower(expr.then, env, joins, yes, dest)
                other = expr.otherwise or CUnit(UNIT, expr.span)
                self.lower(other, env, joins, no, dest)
            self.lower_values([expr.cond], env, joins, block, branch)
            return
        if isinstance(expr, CMatch):
            after_scrutinee = self.new_block("match_value", layout_of(expr.scrutinee.ty))
            scrutinee_slot = self.new_slot("scrutinee", layout_of(expr.scrutinee.ty))
            after_scrutinee.instructions.append(
                bir.Instruction("slot_store", (scrutinee_slot.name,
                                                after_scrutinee.params[0])))
            failed = self.new_block("match_failed")
            failed.terminator = bir.Panic("no match arm applies")
            test = after_scrutinee
            for index, alt in enumerate(expr.alts):
                value = self.emit(test, "slot_load", (scrutinee_slot.name,),
                                  scrutinee_slot.layout)
                success = self.new_block("match_arm")
                failure = (failed if index + 1 == len(expr.alts)
                           else self.new_block("match_next"))
                inner = dict(env)
                self.lower_pattern(alt.pat, value, expr.scrutinee.ty,
                                   inner, test, success, failure)
                self.lower(alt.body, inner, joins, success, dest)
                test = failure
            self.lower(expr.scrutinee, env, joins, block,
                       _Destination(after_scrutinee))
            return
        if isinstance(expr, CJoin):
            target = self.new_block("join")
            param_slots = [self.new_slot(p.name, layout_of(p.ty)) for p in expr.params]
            target.params = [bir.Value(self.fresh("join_arg"), slot.layout)
                             for slot in param_slots]
            for slot, value in zip(param_slots, target.params):
                target.instructions.append(
                    bir.Instruction("slot_store", (slot.name, value)))
            body_env = dict(env)
            body_env.update(zip((p.name for p in expr.params), param_slots))
            body_joins = dict(joins)
            if expr.recursive:
                body_joins[expr.name] = (target, param_slots)
            rest_joins = dict(joins)
            rest_joins[expr.name] = (target, param_slots)
            self.lower(expr.body, body_env, body_joins, target, dest)
            self.lower(expr.rest, env, rest_joins, block, dest)
            return
        if isinstance(expr, CJump):
            if expr.name not in joins:
                raise Unsupported(f"LLVM jump target '{expr.name}' is not in scope", expr.span)
            target, slots = joins[expr.name]
            def jump(at: bir.Block, values: list[bir.Operand]) -> None:
                if len(values) != len(slots):
                    raise Unsupported("LLVM jump arity mismatch", expr.span)
                # Values were materialized in distinct slots by lower_values,
                # so stores on the target edge are simultaneous.
                at.terminator = bir.Jump(target.name, tuple(values))
            self.lower_values(expr.args, env, joins, block, jump)
            return
        if isinstance(expr, (CLetRec,)):
            raise Unsupported("LLVM backend does not yet support local recursive closures", expr.span)

        self.lower_value(expr, env, joins, block,
                         lambda at, value: self.transfer(at, dest, value))

    def lower_values(self, exprs: list[CExpr | None], env: dict[str, bir.Value],
                     joins: dict[str, tuple[bir.Block, list[bir.Value]]],
                     block: bir.Block, done) -> None:
        slots: list[bir.Value] = []

        def one(index: int, at: bir.Block) -> None:
            if index == len(exprs):
                values = [self.emit(at, "slot_load", (slot.name,), slot.layout)
                          for slot in slots]
                done(at, values)
                return
            expr = exprs[index]
            assert expr is not None
            after = self.new_block("operand", layout_of(expr.ty))
            slot = self.new_slot("operand", layout_of(expr.ty))
            slots.append(slot)
            after.instructions.append(
                bir.Instruction("slot_store", (slot.name, after.params[0])))
            one(index + 1, after)
            self.lower(expr, env, joins, at, _Destination(after))

        one(0, block)

    def lower_value(self, expr: CExpr, env: dict[str, bir.Value],
                    joins: dict[str, tuple[bir.Block, list[bir.Value]]],
                    block: bir.Block, done) -> None:
        if isinstance(expr, CLit):
            layout = layout_of(expr.ty)
            if expr.kind == "Int" and not (-(1 << 63) <= int(expr.value) < (1 << 63)):
                raise Unsupported("Int literal is outside the signed 64-bit range", expr.span)
            if expr.kind == "Char":
                value = ord(expr.value)
                if 0xD800 <= value <= 0xDFFF or value > 0x10FFFF:
                    raise Unsupported("Char literal is not a Unicode scalar", expr.span)
                done(block, bir.Constant(layout, value))
                return
            if expr.kind == "String":
                done(block, self.emit(block, "string_const", (str(expr.value),), bir.Layout.PTR))
                return
            done(block, bir.Constant(layout, expr.value))
            return
        if isinstance(expr, CUnit):
            done(block, bir.Constant(bir.Layout.UNIT, 0))
            return
        if isinstance(expr, CVar):
            if expr.name not in env:
                raise Unsupported(f"LLVM backend cannot use top-level value '{expr.name}'", expr.span)
            slot = env[expr.name]
            done(block, self.emit(block, "slot_load", (slot.name,), slot.layout))
            return
        if isinstance(expr, CCon):
            if expr.name == BOOL_FALSE:
                done(block, bir.Constant(bir.Layout.I1, False))
                return
            if expr.name == BOOL_TRUE:
                done(block, bir.Constant(bir.Layout.I1, True))
                return
            info = self.decls.constructors.get(expr.name)
            if info is not None and info.arity == 0:
                done(block, self.emit(block, "object_new",
                                      ("1", str(self.tags[expr.name]), "0", "0"),
                                      bir.Layout.PTR))
                return
            raise Unsupported(f"LLVM constructor '{expr.name}' must be saturated", expr.span)
        if isinstance(expr, (CTyLam, CTyApp)):
            inner = expr.body if isinstance(expr, CTyLam) else expr.fn
            self.lower_value(inner, env, joins, block, done)
            return
        if isinstance(expr, CRef):
            self.lower_values([expr.value], env, joins, block,
                              lambda at, xs: done(at, self.emit(
                                  at, "cell_new", (xs[0],), bir.Layout.PTR)))
            return
        if isinstance(expr, CDeref):
            self.lower_values([expr.target], env, joins, block,
                              lambda at, xs: done(at, self.emit(
                                  at, "cell_load", (xs[0],), layout_of(expr.ty))))
            return
        if isinstance(expr, CAssign):
            if isinstance(expr.target, CField):
                index = self.field_index(expr.target.target.ty, expr.target.name)
                def set_field(at: bir.Block, xs: list[bir.Operand]) -> None:
                    at.instructions.append(bir.Instruction(
                        "object_set", (xs[1], str(index), xs[0])))
                    done(at, bir.Constant(bir.Layout.UNIT, 0))
                self.lower_values([expr.value, expr.target.target], env, joins,
                                  block, set_field)
                return
            if isinstance(expr.target, CIndex):
                def set_index(at: bir.Block, xs: list[bir.Operand]) -> None:
                    at.instructions.append(bir.Instruction(
                        "array_set", (xs[1], xs[2], xs[0])))
                    done(at, bir.Constant(bir.Layout.UNIT, 0))
                self.lower_values([expr.value, expr.target.target, expr.target.index],
                                  env, joins, block, set_index)
                return
            if not is_ref(expr.target.ty):
                raise Unsupported("LLVM assignment target is not addressable", expr.span)
            def assigned(at: bir.Block, xs: list[bir.Operand]) -> None:
                at.instructions.append(bir.Instruction("cell_store", (xs[1], xs[0])))
                done(at, bir.Constant(bir.Layout.UNIT, 0))
            # Core assignment evaluates the value before its target.
            self.lower_values([expr.value, expr.target], env, joins, block, assigned)
            return
        if isinstance(expr, (CTuple, CArray, CRecord)):
            items = (expr.elems if isinstance(expr, (CTuple, CArray))
                     else [value for _, value in expr.fields])
            def aggregate(at: bir.Block, values: list[bir.Operand]) -> None:
                if isinstance(expr, CArray):
                    pointer_bits = 1 if values and _pointer_layout(values[0].layout) else 0
                    made = self.emit(at, "array_new",
                                     (str(len(values)), str(pointer_bits)), bir.Layout.PTR)
                    for index, value in enumerate(values):
                        at.instructions.append(bir.Instruction(
                            "array_set", (made, str(index), value)))
                else:
                    if isinstance(expr, CRecord):
                        fields = self.record_fields.get(expr.con, [name for name, _ in expr.fields])
                        by_name = dict(zip((name for name, _ in expr.fields), values))
                        values = [by_name[name] for name in fields]
                        tag, kind = self.tags.get(expr.con, -1), 1
                    else:
                        tag, kind = -1, 0
                    bitmap = sum(1 << i for i, value in enumerate(values)
                                 if _pointer_layout(value.layout))
                    made = self.emit(at, "object_new",
                                     (str(kind), str(tag), str(len(values)), str(bitmap)),
                                     bir.Layout.PTR)
                    for index, value in enumerate(values):
                        at.instructions.append(bir.Instruction(
                            "object_set", (made, str(index), value)))
                done(at, made)
            self.lower_values(items, env, joins, block, aggregate)
            return
        if isinstance(expr, (CField, CProject, CIndex)):
            targets = ([expr.target, expr.index] if isinstance(expr, CIndex)
                       else [expr.target])
            def project(at: bir.Block, values: list[bir.Operand]) -> None:
                if isinstance(expr, CIndex):
                    made = self.emit(at, "array_get", tuple(values), layout_of(expr.ty))
                else:
                    index = expr.index if isinstance(expr, CProject) else self.field_index(
                        expr.target.ty, expr.name)
                    made = self.emit(at, "object_get", (values[0], str(index)),
                                     layout_of(expr.ty))
                done(at, made)
            self.lower_values(targets, env, joins, block, project)
            return
        if isinstance(expr, CApp):
            fn = _erase_types(expr.fn)
            # Function expression precedes arguments. Direct names and
            # primitives have no runtime evaluation, but this still sequences
            # every actual argument left-to-right.
            if isinstance(fn, CPrim) or (
                    isinstance(fn, CVar) and fn.name in PRIM_NAMES):
                primitive = fn.name
                if primitive == "Prim.error":
                    def panic(at: bir.Block, _values: list[bir.Operand]) -> None:
                        at.terminator = bir.Panic("error")
                    self.lower_values(expr.args, env, joins, block, panic)
                    return
                self.lower_values(expr.args, env, joins, block,
                                  lambda at, xs: done(at, self.emit(
                                      at, "prim." + primitive.removeprefix("Prim."),
                                      tuple(xs), layout_of(expr.ty))))
                return
            if isinstance(fn, CVar) and fn.name in self.functions:
                symbol, _ = self.functions[fn.name]
                self.lower_values(expr.args, env, joins, block,
                                  lambda at, xs: done(at, self.emit(
                                      at, "call", (symbol, *xs), layout_of(expr.ty))))
                return
            if isinstance(fn, CCon):
                info = self.decls.constructors[fn.name]
                def construct(at: bir.Block, values: list[bir.Operand]) -> None:
                    bitmap = sum(1 << i for i, value in enumerate(values)
                                 if _pointer_layout(value.layout))
                    made = self.emit(at, "object_new",
                                     ("1", str(self.tags[fn.name]), str(info.arity),
                                      str(bitmap)), bir.Layout.PTR)
                    for index, value in enumerate(values):
                        at.instructions.append(bir.Instruction(
                            "object_set", (made, str(index), value)))
                    done(at, made)
                self.lower_values(expr.args, env, joins, block, construct)
                return
            raise Unsupported("LLVM closure calls are not implemented", expr.span)
        if isinstance(expr, CPrim):
            raise Unsupported("LLVM primitive values must be called directly", expr.span)
        if isinstance(expr, CLam):
            raise Unsupported("LLVM closures are not implemented", expr.span)
        # Control-shaped operands re-enter the general continuation lowering.
        if isinstance(expr, (CLet, CIf, CJoin, CJump, CLetRec)):
            after = self.new_block("value", layout_of(expr.ty))
            done(after, after.params[0])
            self.lower(expr, env, joins, block, _Destination(after))
            return
        raise Unsupported(f"LLVM backend does not support {type(expr).__name__}", expr.span)

    def field_index(self, ty: Type, name: str) -> int:
        head, _ = spine(ty)
        if isinstance(head, TCon):
            if head.name in self.record_fields:
                return self.record_fields[head.name].index(name)
            info = self.decls.tycons.get(head.name)
            if info is not None:
                for con in info.variants:
                    if con.field_names and name in con.field_names:
                        return con.field_names.index(name)
        raise Unsupported(f"LLVM backend has no layout for field '{name}'")

    def lower_pattern(self, pat, value: bir.Operand, ty: Type,
                      env: dict[str, bir.Value],
                      block: bir.Block, success: bir.Block,
                      failure: bir.Block) -> None:
        if isinstance(pat, ast.PAnnot):
            self.lower_pattern(pat.pat, value, ty, env, block, success, failure)
            return
        if isinstance(pat, ast.PWild):
            block.terminator = bir.Jump(success.name)
            return
        if isinstance(pat, ast.PVar):
            slot = self.new_slot(pat.name, layout_of(ty))
            env[pat.name] = slot
            block.instructions.append(bir.Instruction("slot_store", (slot.name, value)))
            block.terminator = bir.Jump(success.name)
            return
        if isinstance(pat, ast.PLit):
            literal = (self.emit(block, "string_const", (str(pat.value),), bir.Layout.PTR)
                       if pat.kind == "String" else bir.Constant(value.layout, pat.value))
            op = "string_eq" if pat.kind == "String" else (
                "float_eq" if value.layout is bir.Layout.F64 else "scalar_eq")
            condition = self.emit(block, op, (value, literal), bir.Layout.I1)
            block.terminator = bir.Branch(condition, success.name, failure.name)
            return
        if isinstance(pat, ast.PCon) and pat.name in (BOOL_FALSE, BOOL_TRUE):
            wanted = bir.Constant(bir.Layout.I1, pat.name == BOOL_TRUE)
            condition = self.emit(block, "scalar_eq", (value, wanted), bir.Layout.I1)
            block.terminator = bir.Branch(condition, success.name, failure.name)
            return
        if isinstance(pat, (ast.PCon, ast.PRecord)):
            held = self.new_slot("pattern_value", value.layout)
            block.instructions.append(bir.Instruction("slot_store", (held.name, value)))
            tag = self.emit(block, "object_tag", (value,), bir.Layout.I32)
            wanted = self.tags[pat.name]
            condition = self.emit(block, "scalar_eq",
                                  (tag, bir.Constant(bir.Layout.I32, wanted)), bir.Layout.I1)
            contents = self.new_block("pattern_contents")
            block.terminator = bir.Branch(condition, contents.name, failure.name)
            if isinstance(pat, ast.PCon):
                pieces = list(enumerate(pat.args))
            else:
                fields = self.record_fields[pat.name]
                pieces = [(fields.index(name), sub) for name, sub in pat.fields]
            info = self.decls.constructors[pat.name]
            con_type = instantiate(info.scheme, lambda: TVar(1))
            assert isinstance(con_type, TFun)
            unify(con_type.ret, ty)
            at = contents
            for index, (field, sub) in enumerate(pieces):
                field_ty = con_type.params[field]
                field_layout = layout_of(field_ty)
                object_value = self.emit(at, "slot_load", (held.name,), held.layout)
                loaded = self.emit(at, "object_get", (object_value, str(field)), field_layout)
                following = success if index + 1 == len(pieces) else self.new_block("pattern_field")
                self.lower_pattern(sub, loaded, field_ty, env, at, following, failure)
                at = following
            if not pieces:
                contents.terminator = bir.Jump(success.name)
            return
        if isinstance(pat, ast.PTuple):
            tuple_ty = prune(ty)
            assert isinstance(tuple_ty, TTuple)
            held = self.new_slot("tuple_value", value.layout)
            block.instructions.append(bir.Instruction("slot_store", (held.name, value)))
            at = block
            for index, (sub, field_ty) in enumerate(zip(pat.elems, tuple_ty.elems)):
                tuple_value = self.emit(at, "slot_load", (held.name,), held.layout)
                loaded = self.emit(at, "object_get", (tuple_value, str(index)),
                                   layout_of(field_ty))
                following = success if index + 1 == len(pat.elems) else self.new_block("tuple_field")
                self.lower_pattern(sub, loaded, field_ty, env, at, following, failure)
                at = following
            if not pat.elems:
                block.terminator = bir.Jump(success.name)
            return
        raise Unsupported(f"LLVM pattern {type(pat).__name__} is not implemented")


def _erase_types(expr: CExpr | None) -> CExpr | None:
    while isinstance(expr, (CTyApp, CTyLam)):
        expr = expr.fn if isinstance(expr, CTyApp) else expr.body
    return expr


def _pointer_layout(layout: bir.Layout) -> bool:
    return layout in (bir.Layout.PTR, bir.Layout.BOXED)


def _record_layouts(program: CProgram, decls) -> dict[str, list[str]]:
    layouts = {
        con.name: list(con.field_names)
        for con in decls.constructors.values() if con.field_names is not None
    }

    def walk(value) -> None:
        if isinstance(value, CRecord):
            layouts.setdefault(value.con, [name for name, _ in value.fields])
        if isinstance(value, (CExpr, CBind)):
            for item in data_fields(value):
                walk(getattr(value, item.name))
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(program.dicts)
    walk(program.binds)
    return layouts


def lower(program: CProgram, decls, main: str = "main") -> bir.Module:
    record_fields = _record_layouts(program, decls)
    tag_names = sorted(set(decls.constructors) | set(record_fields))
    tags = {name: index for index, name in enumerate(tag_names)}
    functions: dict[str, tuple[str, TFun]] = {}
    candidates: list[tuple[CBind, CLam]] = []
    for bind in program.dicts + program.binds:
        value = _erase_types(bind.value)
        ty = prune(bind.ty)
        if isinstance(value, CLam) and isinstance(ty, TFun):
            functions[bind.name] = (mangle(bind.name), ty)
            candidates.append((bind, value))
    if main not in functions:
        raise Unsupported(f"LLVM entry '{main}' is not a function")
    by_name = {bind.name: (bind, lam) for bind, lam in candidates}
    reachable: set[str] = set()
    pending = [main]
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        reachable.add(name)
        bind, _ = by_name[name]
        pending.extend(sorted((names_of(bind.value) & by_name.keys()) - reachable))
    chosen = [pair for pair in candidates if pair[0].name in reachable]
    module = bir.Module(
        [_FunctionLowerer(bind, lam, functions, decls, tags,
                          record_fields).finish() for bind, lam in chosen],
        functions[main][0],
    )
    bir.check(module)
    return module


__all__ = ["layout_of", "lower", "mangle"]
