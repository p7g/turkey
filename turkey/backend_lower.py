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
    BOTTOM, BYTE, CHAR, FLOAT, INT, STRING, UNIT, TApp, TCon, TFam, TFun,
    TTuple, Type, TVar, instantiate, prune, spine, unify,
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
    if isinstance(ty, (TVar, TFam)):
        return bir.Layout.BOXED
    if isinstance(ty, (TApp, TTuple)):
        return bir.Layout.PTR
    return bir.Layout.PTR


def _expr_layout(expr: CExpr) -> bir.Layout:
    if isinstance(expr, CLit):
        resolved = prune(expr.ty)
        if not isinstance(resolved, TVar):
            return layout_of(resolved)
        return {
            "Int": bir.Layout.I64, "Byte": bir.Layout.I8,
            "Char": bir.Layout.I32, "Float": bir.Layout.F64,
            "String": bir.Layout.PTR,
        }.get(expr.kind, layout_of(expr.ty))
    return layout_of(expr.ty)


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
                 tags: dict[str, int], record_fields: dict[str, list[str]],
                 lifted: list[bir.Function], lift_counter: list[int],
                 globals_: dict[str, bir.Value],
                 captures: list[tuple[str, bir.Layout]] | None = None,
                 output_name: str | None = None) -> None:
        self.bind = bind
        self.lam = lam
        self.functions = functions
        self.decls = decls
        self.tags = tags
        self.record_fields = record_fields
        self.lifted = lifted
        self.lift_counter = lift_counter
        self.globals = globals_
        self.closure_abi = captures is not None
        self.captures = captures or []
        self.output_name = output_name or mangle(bind.name)
        function_type = prune(bind.ty)
        assert isinstance(function_type, TFun)
        self.result_layout = (bir.Layout.BOXED if self.closure_abi
                              else layout_of(function_type.ret))
        self.count = 0
        self.blocks: list[bir.Block] = []
        self.slots: list[bir.Value] = []
        self.slot_names: set[str] = set()
        self.entry = self.new_block("entry")
        parameter_hints = {
            param.name: hint
            for param in lam.params
            if (hint := _free_variable_type(lam.body, param.name)) is not None
        }
        self.params = ([bir.Value(self.fresh("environment"), bir.Layout.PTR)]
                       if self.closure_abi else [])
        internal_layouts = [layout_of(parameter_hints.get(p.name, p.ty))
                            for p in lam.params]
        self.params += [bir.Value(
            self.fresh(p.name),
            bir.Layout.BOXED if self.closure_abi else layout,
        ) for p, layout in zip(lam.params, internal_layouts)]
        self.env: dict[str, bir.Value] = {}
        if self.closure_abi:
            environment = self.params[0]
            for index, (name, layout) in enumerate(self.captures):
                slot = self.new_slot(name, layout)
                loaded = self.emit(self.entry, "object_get",
                                   (environment, str(index)), layout)
                self.entry.instructions.append(
                    bir.Instruction("slot_store", (slot.name, loaded)))
                self.env[name] = slot
        visible_params = self.params[1:] if self.closure_abi else self.params
        for core_param, param, layout in zip(lam.params, visible_params,
                                             internal_layouts):
            slot = self.new_slot(core_param.name, layout)
            value = self.coerce(self.entry, param, layout)
            self.entry.instructions.append(bir.Instruction("slot_store", (slot.name, value)))
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
             args: tuple[bir.Operand | str, ...], layout: bir.Layout,
             frame: bir.Frame | None = None) -> bir.Value:
        result = bir.Value(self.fresh(op), layout)
        block.instructions.append(bir.Instruction(op, args, result, frame))
        return result

    def frame(self, span) -> bir.Frame:
        return bir.Frame(
            self.lam.name, None if span is None else span.file,
            0 if span is None else span.line,
            0 if span is None else span.col,
        )

    def match_failure(self, expr: CMatch) -> str:
        head, _ = spine(expr.scrutinee.ty)
        if isinstance(head, TCon) and head.name in self.decls.tycons:
            variants = self.decls.tycons[head.name].variants
            handled: set[str] = set()
            for alt in expr.alts:
                pattern = alt.pat
                while isinstance(pattern, ast.PAnnot):
                    pattern = pattern.pat
                if isinstance(pattern, (ast.PCon, ast.PRecord)):
                    handled.add(pattern.name)
            missing = [variant.name for variant in variants
                       if variant.name not in handled]
            if len(missing) == 1:
                return "no match arm applies to " + missing[0].rpartition("#")[2]
        return "no match arm applies"

    def coerce(self, block: bir.Block, value: bir.Operand,
               target: bir.Layout) -> bir.Operand:
        source = value.layout
        if source is target:
            return value
        pointer_source = source in (bir.Layout.PTR, bir.Layout.BOXED)
        pointer_target = target in (bir.Layout.PTR, bir.Layout.BOXED)
        if pointer_source and pointer_target:
            return self.emit(block, "relabel", (value,), target)
        if target is bir.Layout.BOXED:
            if source is bir.Layout.UNIT:
                return bir.Constant(bir.Layout.BOXED, 0)
            return self.emit(block, "box", (value,), bir.Layout.BOXED)
        if source is bir.Layout.BOXED:
            if target is bir.Layout.UNIT:
                return bir.Constant(bir.Layout.UNIT, 0)
            return self.emit(block, "unbox", (value,), target)
        raise Unsupported(f"LLVM cannot convert {source.value} to {target.value}")

    def finish(self) -> bir.Function:
        self.lower(self.lam.body, dict(self.env), {}, self.entry, _Destination(None))
        function_type = prune(self.bind.ty)
        assert isinstance(function_type, TFun)
        by_name = {block.name: block for block in self.blocks}
        reachable: set[str] = set()
        pending = [self.entry.name]
        while pending:
            name = pending.pop()
            if name in reachable:
                continue
            reachable.add(name)
            term = by_name[name].terminator
            if isinstance(term, bir.Jump):
                pending.append(term.target)
            elif isinstance(term, bir.Branch):
                pending.extend((term.yes, term.no))
        return bir.Function(
            self.output_name, self.params, self.result_layout,
            [block for block in self.blocks if block.name in reachable],
            self.entry.name, self.slots,
        )

    def finish_initializers(self, program: CProgram,
                            runtime_binds: list[CBind]) -> bir.Function:
        final = self.new_block("initialized")
        final.terminator = bir.Return(bir.Constant(bir.Layout.UNIT, 0))
        global_roots = {
            name: self.new_slot("global_" + name, value.layout)
            for name, value in self.globals.items()
            if _pointer_layout(value.layout)
        }
        actions: list[tuple] = []
        deferred: list[tuple[str, CRecord]] = []
        runtime_names = {bind.name for bind in runtime_binds}
        for bind in program.dicts:
            if bind.name not in runtime_names:
                continue
            value = _erase_types(bind.value)
            if isinstance(value, CRecord):
                actions.append(("placeholder", bind.name, value))
                deferred.append((bind.name, value))
            else:
                actions.append(("bind", bind.name, bind.value))
        for name, record in deferred:
            for index, (_, value) in enumerate(record.fields):
                actions.append(("field", name, index, value))
        for bind in program.binds:
            if bind.name in runtime_names:
                actions.append(("bind", bind.name, bind.value))

        following = final
        for action in reversed(actions):
            entry = self.new_block("initialize")
            kind = action[0]
            if kind == "placeholder":
                _, name, record = action
                fields = self.record_fields.get(record.con,
                                                [field for field, _ in record.fields])
                by_name = dict(record.fields)
                metadata = _layout_metadata(
                    _expr_layout(by_name[field]) for field in fields)
                made = self.emit(entry, "object_new", (
                    "1", str(self.tags.get(record.con, -1)), str(len(fields)),
                    str(metadata),
                ), bir.Layout.PTR)
                entry.instructions.append(bir.Instruction(
                    "global_store", (self.globals[name].name, made)))
                entry.instructions.append(bir.Instruction(
                    "slot_store", (global_roots[name].name, made)))
                entry.terminator = bir.Jump(following.name)
            elif kind == "bind":
                _, name, value = action
                after = self.new_block("store_global", _expr_layout(value))
                after.instructions.append(bir.Instruction(
                    "global_store", (self.globals[name].name, after.params[0])))
                if name in global_roots:
                    after.instructions.append(bir.Instruction(
                        "slot_store", (global_roots[name].name, after.params[0])))
                after.terminator = bir.Jump(following.name)
                self.lower(value, {}, {}, entry, _Destination(after))
            else:
                _, name, index, value = action
                after = self.new_block("store_field", _expr_layout(value))
                record = self.emit(after, "global_load",
                                   (self.globals[name].name,), bir.Layout.PTR)
                after.instructions.append(bir.Instruction(
                    "object_set", (record, str(index), after.params[0])))
                after.terminator = bir.Jump(following.name)
                self.lower(value, {}, {}, entry, _Destination(after))
            following = entry
        self.entry.terminator = bir.Jump(following.name)
        # Reuse finish's reachability pruning without compiling the dummy body.
        function_type = prune(self.bind.ty)
        assert isinstance(function_type, TFun)
        by_name = {block.name: block for block in self.blocks}
        reachable: set[str] = set()
        pending = [self.entry.name]
        while pending:
            name = pending.pop()
            if name in reachable:
                continue
            reachable.add(name)
            term = by_name[name].terminator
            if isinstance(term, bir.Jump):
                pending.append(term.target)
            elif isinstance(term, bir.Branch):
                pending.extend((term.yes, term.no))
        return bir.Function(
            self.output_name, [], bir.Layout.UNIT,
            [block for block in self.blocks if block.name in reachable],
            self.entry.name, self.slots,
        )

    def transfer(self, block: bir.Block, dest: _Destination,
                 value: bir.Operand) -> None:
        if dest.block is None:
            block.terminator = bir.Return(self.coerce(block, value, self.result_layout))
        else:
            target = dest.block.params[0].layout
            block.terminator = bir.Jump(
                dest.block.name, (self.coerce(block, value, target),))

    def lower(self, expr: CExpr | None, env: dict[str, bir.Value],
              joins: dict[str, tuple[bir.Block, list[bir.Value]]],
              block: bir.Block, dest: _Destination) -> None:
        assert expr is not None
        if isinstance(expr, CLet):
            value_layout = _expr_layout(expr.value)
            after = self.new_block("let", value_layout)
            slot = self.new_slot(expr.name, value_layout)
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
            failed.terminator = bir.Panic(
                self.match_failure(expr), self.frame(expr.span))
            test = after_scrutinee
            for index, alt in enumerate(expr.alts):
                value = self.emit(test, "slot_load", (scrutinee_slot.name,),
                                  scrutinee_slot.layout)
                success = self.new_block("match_arm")
                failure = (failed if index + 1 == len(expr.alts)
                           else self.new_block("match_next"))
                inner = dict(env)
                hints = {
                    name: hint
                    for name in _pattern_names(alt.pat)
                    if (hint := _free_variable_type(alt.body, name)) is not None
                }
                self.lower_pattern(alt.pat, value, expr.scrutinee.ty,
                                   inner, test, success, failure, hints)
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
        if isinstance(expr, CLetRec):
            inner = dict(env)
            closure_slots: list[bir.Value] = []
            lambdas: list[tuple[CBind, CLam, str, list[tuple[str, bir.Value]]]] = []
            for bind in expr.binds:
                value = _erase_types(bind.value)
                if not isinstance(value, CLam):
                    raise Unsupported("LLVM recursive non-function binding", bind.span)
                slot = self.new_slot(bind.name, bir.Layout.PTR)
                inner[bind.name] = slot
                closure_slots.append(slot)
            # Every shell has an address before recursive captures are read.
            for bind, slot in zip(expr.binds, closure_slots):
                value = _erase_types(bind.value)
                assert isinstance(value, CLam)
                symbol, captures = self.lift(value, inner)
                bitmap = sum(1 << i for i, (_, captured) in enumerate(captures)
                             if _pointer_layout(captured.layout))
                closure = self.emit(block, "closure_new",
                                    (symbol, str(len(captures)), str(bitmap)),
                                    bir.Layout.PTR)
                block.instructions.append(
                    bir.Instruction("slot_store", (slot.name, closure)))
                lambdas.append((bind, value, symbol, captures))
            for closure_slot, (_, _, _, captures) in zip(closure_slots, lambdas):
                closure = self.emit(block, "slot_load", (closure_slot.name,),
                                    bir.Layout.PTR)
                for index, (_, captured) in enumerate(captures):
                    value = self.emit(block, "slot_load", (captured.name,), captured.layout)
                    block.instructions.append(bir.Instruction(
                        "closure_capture", (closure, str(index), value)))
            self.lower(expr.body, inner, joins, block, dest)
            return

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
            after = self.new_block("operand", _expr_layout(expr))
            slot = self.new_slot("operand", _expr_layout(expr))
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
            layout = _expr_layout(expr)
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
                if expr.name in self.functions:
                    done(block, self.emit(block, "function_closure",
                                          (self.functions[expr.name][0],),
                                          bir.Layout.PTR))
                    return
                if expr.name in self.globals:
                    global_ = self.globals[expr.name]
                    loaded = self.emit(block, "global_load",
                                       (global_.name,), global_.layout)
                    done(block, self.coerce(block, loaded, _expr_layout(expr)))
                    return
                raise Unsupported(f"LLVM backend cannot use top-level value '{expr.name}'", expr.span)
            slot = env[expr.name]
            loaded = self.emit(block, "slot_load", (slot.name,), slot.layout)
            done(block, self.coerce(block, loaded, _expr_layout(expr)))
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
                    value = self.coerce(at, xs[0], layout_of(expr.target.ty))
                    at.instructions.append(bir.Instruction(
                        "array_set", (xs[1], xs[2], value)))
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
                    _, type_args = spine(expr.ty)
                    element_layout = (layout_of(type_args[0]) if type_args else
                                      values[0].layout if values else bir.Layout.BOXED)
                    made = self.emit(at, "array_new",
                                     (str(len(values)), str(_layout_width(element_layout)),
                                      element_layout.value), bir.Layout.PTR)
                    for index, value in enumerate(values):
                        value = self.coerce(at, value, element_layout)
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
                    metadata = _layout_metadata(value.layout for value in values)
                    made = self.emit(at, "object_new",
                                     (str(kind), str(tag), str(len(values)),
                                      str(metadata)),
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
                    made = self.emit(at, "array_get", tuple(values),
                                     layout_of(expr.ty), self.frame(expr.span))
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
                    def panic(at: bir.Block, values: list[bir.Operand]) -> None:
                        at.terminator = bir.Panic(values[0], self.frame(expr.span))
                    self.lower_values(expr.args, env, joins, block, panic)
                    return
                if primitive == "Prim.stringToBytes":
                    def to_bytes(at: bir.Block, values: list[bir.Operand]) -> None:
                        string = values[0]
                        raw = self.emit(at, "prim.stringToByteStorage", (string,),
                                        bir.Layout.PTR, self.frame(expr.span))
                        length = self.emit(at, "prim.stringByteLength", (string,),
                                           bir.Layout.I64)
                        storage_fields = self.record_fields["Data.Array#ArrayStorage"]
                        storage_values = {"storage": raw, "length": length}
                        ordered = [storage_values[name] for name in storage_fields]
                        storage = self.emit(at, "object_new", (
                            "1", str(self.tags["Data.Array#ArrayStorage"]),
                            str(len(ordered)), str(_layout_metadata(
                                item.layout for item in ordered)),
                        ), bir.Layout.PTR)
                        for index, item in enumerate(ordered):
                            at.instructions.append(bir.Instruction(
                                "object_set", (storage, str(index), item)))
                        outer = self.emit(at, "object_new", (
                            "1", str(self.tags["Data.Array#Array"]), "1",
                            str(_layout_metadata([bir.Layout.PTR])),
                        ), bir.Layout.PTR)
                        at.instructions.append(bir.Instruction(
                            "object_set", (outer, "0", storage)))
                        done(at, outer)
                    self.lower_values(expr.args, env, joins, block, to_bytes)
                    return
                operation = "prim." + primitive.removeprefix("Prim.")
                array_element_layout = None
                if primitive in ("Prim.arrayNew", "Prim.arrayNewUninit"):
                    _, type_args = spine(expr.ty)
                    element_layout = layout_of(type_args[0]) if type_args else bir.Layout.BOXED
                    array_element_layout = element_layout
                    operation += "." + element_layout.value
                elif primitive == "Prim.arrayGet":
                    operation += "." + layout_of(expr.ty).value
                elif primitive == "Prim.arraySet":
                    operation += "." + _expr_layout(expr.args[2]).value
                def primitive_call(at: bir.Block, values: list[bir.Operand]) -> None:
                    if array_element_layout is not None and len(values) == 2:
                        values[1] = self.coerce(at, values[1], array_element_layout)
                    done(at, self.emit(
                        at, operation, tuple(values), layout_of(expr.ty),
                        self.frame(expr.span)))
                self.lower_values(expr.args, env, joins, block, primitive_call)
                return
            if isinstance(fn, CVar) and fn.name in self.functions:
                symbol, function_type = self.functions[fn.name]
                def direct_call(at: bir.Block, values: list[bir.Operand]) -> None:
                    arguments = [self.coerce(at, value, layout_of(expected))
                                 for value, expected in zip(values,
                                                            function_type.params)]
                    called = self.emit(at, "call", (symbol, *arguments),
                                       layout_of(function_type.ret), self.frame(expr.span))
                    done(at, self.coerce(at, called, layout_of(expr.ty)))
                self.lower_values(expr.args, env, joins, block, direct_call)
                return
            if isinstance(fn, CCon):
                info = self.decls.constructors[fn.name]
                def construct(at: bir.Block, values: list[bir.Operand]) -> None:
                    con_type = instantiate(info.scheme, lambda: TVar(1))
                    assert isinstance(con_type, TFun)
                    unify(con_type.ret, expr.ty)
                    values = [self.coerce(at, value, layout_of(expected))
                              for value, expected in zip(values, con_type.params)]
                    metadata = _layout_metadata(value.layout for value in values)
                    made = self.emit(at, "object_new",
                                     ("1", str(self.tags[fn.name]), str(info.arity),
                                      str(metadata)), bir.Layout.PTR)
                    for index, value in enumerate(values):
                        at.instructions.append(bir.Instruction(
                            "object_set", (made, str(index), value)))
                    done(at, made)
                self.lower_values(expr.args, env, joins, block, construct)
                return
            def closure_call(at: bir.Block, values: list[bir.Operand]) -> None:
                arguments = [values[0], *(self.coerce(at, value, bir.Layout.BOXED)
                                           for value in values[1:])]
                called = self.emit(at, "closure_call", tuple(arguments),
                                   bir.Layout.BOXED, self.frame(expr.span))
                done(at, self.coerce(at, called, layout_of(expr.ty)))
            self.lower_values([expr.fn, *expr.args], env, joins, block,
                              closure_call)
            return
        if isinstance(expr, CPrim):
            raise Unsupported("LLVM primitive values must be called directly", expr.span)
        if isinstance(expr, CLam):
            symbol, captures = self.lift(expr, env)
            bitmap = sum(1 << i for i, (_, captured) in enumerate(captures)
                         if _pointer_layout(captured.layout))
            closure = self.emit(block, "closure_new",
                                (symbol, str(len(captures)), str(bitmap)), bir.Layout.PTR)
            for index, (_, captured) in enumerate(captures):
                value = self.emit(block, "slot_load", (captured.name,), captured.layout)
                block.instructions.append(bir.Instruction(
                    "closure_capture", (closure, str(index), value)))
            done(block, closure)
            return
        # Control-shaped operands re-enter the general continuation lowering.
        if isinstance(expr, (CLet, CIf, CJoin, CJump, CLetRec)):
            after = self.new_block("value", layout_of(expr.ty))
            done(after, after.params[0])
            self.lower(expr, env, joins, block, _Destination(after))
            return
        raise Unsupported(f"LLVM backend does not support {type(expr).__name__}", expr.span)

    def lift(self, lam: CLam, env: dict[str, bir.Value]) -> tuple[
            str, list[tuple[str, bir.Value]]]:
        captures = list(env.items())
        number = self.lift_counter[0]
        self.lift_counter[0] += 1
        symbol = f"{self.output_name}_lambda_{number}"
        bind = CBind(symbol, lam.ty, [], lam, lam.span)
        child = _FunctionLowerer(
            bind, lam, self.functions, self.decls, self.tags,
            self.record_fields, self.lifted, self.lift_counter,
            self.globals,
            [(name, slot.layout) for name, slot in captures], symbol,
        )
        function = child.finish()
        self.lifted.append(function)
        return symbol, captures

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
                      failure: bir.Block,
                      hints: dict[str, Type]) -> None:
        if isinstance(pat, ast.PAnnot):
            self.lower_pattern(pat.pat, value, ty, env, block, success, failure,
                               hints)
            return
        if isinstance(pat, ast.PWild):
            block.terminator = bir.Jump(success.name)
            return
        if isinstance(pat, ast.PVar):
            layout = layout_of(hints.get(pat.name, ty))
            stored = self.coerce(block, value, layout)
            slot = self.new_slot(pat.name, layout)
            env[pat.name] = slot
            block.instructions.append(bir.Instruction("slot_store", (slot.name, stored)))
            block.terminator = bir.Jump(success.name)
            return
        if isinstance(pat, ast.PLit):
            literal = (self.emit(block, "string_const", (str(pat.value),), bir.Layout.PTR)
                       if pat.kind == "String" else bir.Constant(
                           value.layout,
                           ord(pat.value) if pat.kind == "Char" else pat.value,
                       ))
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
                field_layout = _pattern_layout(sub, field_ty, hints)
                object_value = self.emit(at, "slot_load", (held.name,), held.layout)
                loaded = self.emit(at, "object_get", (object_value, str(field)), field_layout)
                following = success if index + 1 == len(pieces) else self.new_block("pattern_field")
                self.lower_pattern(sub, loaded, field_ty, env, at, following,
                                   failure, hints)
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
                                   _pattern_layout(sub, field_ty, hints))
                following = success if index + 1 == len(pat.elems) else self.new_block("tuple_field")
                self.lower_pattern(sub, loaded, field_ty, env, at, following,
                                   failure, hints)
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


def _layout_metadata(layouts) -> int:
    codes = {
        bir.Layout.UNIT: 0, bir.Layout.I1: 1, bir.Layout.I8: 2,
        bir.Layout.I32: 3, bir.Layout.I64: 4, bir.Layout.F64: 5,
        bir.Layout.PTR: 6, bir.Layout.BOXED: 7,
    }
    return sum(codes[layout] << (3 * index)
               for index, layout in enumerate(layouts))


def _layout_width(layout: bir.Layout) -> int:
    if layout is bir.Layout.I8:
        return 1
    if layout is bir.Layout.I32:
        return 4
    return 8


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


def _pattern_names(pattern) -> set[str]:
    names: set[str] = set()

    def walk(value) -> None:
        if isinstance(value, ast.PVar):
            names.add(value.name)
        elif isinstance(value, ast.Pattern):
            for item in data_fields(value):
                walk(getattr(value, item.name))
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(pattern)
    return names


def _free_variable_type(expr: CExpr, name: str) -> Type | None:
    """Find an occurrence type without confusing a shadowing source binder."""
    found: Type | None = None

    def walk(value, shadowed: bool = False) -> None:
        nonlocal found
        if found is not None or value is None:
            return
        if isinstance(value, CVar):
            if not shadowed and value.name == name:
                found = value.ty
            return
        if isinstance(value, CLam):
            walk(value.body, shadowed or any(p.name == name for p in value.params))
            return
        if isinstance(value, CLet):
            walk(value.value, shadowed)
            walk(value.body, shadowed or value.name == name)
            return
        if isinstance(value, CLetRec):
            hidden = shadowed or any(bind.name == name for bind in value.binds)
            for bind in value.binds:
                walk(bind.value, hidden)
            walk(value.body, hidden)
            return
        if isinstance(value, CJoin):
            walk(value.body, shadowed or any(p.name == name for p in value.params))
            walk(value.rest, shadowed)
            return
        if isinstance(value, CMatch):
            walk(value.scrutinee, shadowed)
            for alt in value.alts:
                walk(alt.body, shadowed or name in _pattern_names(alt.pat))
            return
        if isinstance(value, CExpr):
            for item in data_fields(value):
                walk(getattr(value, item.name), shadowed)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item, shadowed)

    walk(expr)
    return found


def _pattern_layout(pattern, fallback: Type,
                    hints: dict[str, Type]) -> bir.Layout:
    while isinstance(pattern, ast.PAnnot):
        pattern = pattern.pat
    if isinstance(pattern, ast.PVar) and pattern.name in hints:
        return layout_of(hints[pattern.name])
    if isinstance(pattern, ast.PCon) and pattern.name in (BOOL_FALSE, BOOL_TRUE):
        return bir.Layout.I1
    # Nested constructors and tuples are heap values regardless of the type
    # family carried by their contents.
    if isinstance(pattern, (ast.PCon, ast.PRecord, ast.PTuple)):
        return bir.Layout.PTR
    return layout_of(fallback)


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
    # Specialization sometimes leaves a top-level function as a pure alias.
    # Resolve those aliases to the same native symbol; they need no storage or
    # initialization of their own.
    changed = True
    while changed:
        changed = False
        for bind in program.dicts + program.binds:
            value = _erase_types(bind.value)
            ty = prune(bind.ty)
            if (bind.name not in functions and isinstance(value, CVar)
                    and value.name in functions and isinstance(ty, TFun)):
                functions[bind.name] = (functions[value.name][0], ty)
                changed = True
    if main not in functions:
        raise Unsupported(f"LLVM entry '{main}' is not a function")
    by_name = {bind.name: (bind, lam) for bind, lam in candidates}
    owner = {functions[name][0]: name for name in by_name}
    all_binds = {bind.name: bind for bind in program.dicts + program.binds}
    main_module = all_binds[main].module
    reachable: set[str] = set()
    # Entry-module values retain observable source initialization order. The
    # work list then pulls in every imported function/value they reference.
    pending = [main, *(bind.name for bind in program.binds
                       if bind.module == main_module and bind.name not in functions)]
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        reachable.add(name)
        bind = all_binds[name]
        for dependency in sorted(names_of(bind.value)):
            if dependency in functions:
                actual = owner.get(functions[dependency][0])
                if actual is not None and actual not in reachable:
                    pending.append(actual)
            elif dependency in all_binds and dependency not in reachable:
                pending.append(dependency)
    chosen = [pair for pair in candidates if pair[0].name in reachable]
    lifted: list[bir.Function] = []
    lift_counter = [0]
    runtime_binds = [bind for bind in program.dicts + program.binds
                     if bind.name in reachable and bind.name not in functions]
    globals_ = {
        bind.name: bir.Value(mangle("global." + bind.name),
                             _expr_layout(_erase_types(bind.value)))
        for bind in runtime_binds
    }
    top: list[bir.Function] = []
    for bind, lam in chosen:
        top.append(_FunctionLowerer(
            bind, lam, functions, decls, tags, record_fields,
            lifted, lift_counter, globals_,
        ).finish())

    init_name = "turkey_module_initialize"
    init_type = TFun([], UNIT)
    init_lam = CLam(ty=init_type, params=[], body=CUnit(UNIT),
                    name="<module initialization>")
    init_bind = CBind(init_name, init_type, [], init_lam)
    initializer = _FunctionLowerer(
        init_bind, init_lam, functions, decls, tags, record_fields,
        lifted, lift_counter, globals_, output_name=init_name,
    ).finish_initializers(program, runtime_binds)

    run_name = "turkey_run"
    run_block = bir.Block("entry")
    init_result = bir.Value("initialized", bir.Layout.UNIT)
    run_block.instructions.append(bir.Instruction("call", (init_name,), init_result))
    for global_ in globals_.values():
        if _pointer_layout(global_.layout):
            held = bir.Value("root_" + global_.name, global_.layout)
            run_block.instructions.append(
                bir.Instruction("global_load", (global_.name,), held))
    result_layout = functions[main][1].ret
    result = bir.Value("result", layout_of(result_layout))
    run_block.instructions.append(
        bir.Instruction("call", (functions[main][0],), result))
    run_block.terminator = bir.Return(result)
    runner = bir.Function(run_name, [], result.layout, [run_block])

    module = bir.Module(
        [initializer, *top, *lifted, runner], run_name,
        list(globals_.values()),
    )
    bir.check(module)
    return module


__all__ = ["layout_of", "lower", "mangle"]
