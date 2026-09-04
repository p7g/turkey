"""Lower optimized Core into :mod:`turkey.backend_ir`.

This first native slice covers the scalar language and real CFG joins.  Every
unsupported heap or closure form is diagnosed here, before LLVM construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import fields as data_fields

from . import ast, backend_ir as bir
from .core import (
    CAlt, CApp, CArray, CAssign, CBind, CCon, CDeref, CExpr, CField, CIf,
    CIndex, CJoin, CJump, CLam, CLet, CLetRec, CLit, CMatch, CPrim, CProgram, CProject,
    CRecord, CRef, CTuple, CTyApp, CTyLam, CUnit, CVar, is_ref, names_of,
)
from .builtins import PRIM_NAMES
from .errors import Unsupported
from .opt import bottoming
from .prelude import BOOL_FALSE, BOOL_TRUE
from .types import (
    BOTTOM, BYTE, CHAR, FLOAT, INT, STRING, UNIT, TApp, TCon, TFam, TFun,
    TTuple, Type, TVar, instantiate, prune, spine, unify,
)


@dataclass(frozen=True)
class _Scattered:
    """A record taken apart: each of its fields in a slot of its own.

    An environment entry, beside the ordinary `bir.Value` slots, because that
    is what the name *is* here -- not one value held somewhere but a set of
    values that were never assembled. Keeping it in `env` is what gives it
    scope, and scope is the whole point: `_flat_records` compares names bare,
    so it can call one `b` flat while another `b` in the same body is an
    ordinary record with different fields, and the environment is already the
    thing that tells those two apart.

    A distinct type rather than a stand-in slot because `lift` captures the
    whole environment: a fake `bir.Value` here becomes a capture of a slot
    that was never allocated. Saying what the entry is makes that a filter on
    a type instead of a special case on a name.
    """

    fields: dict[str, bir.Value]


def layout_of(ty: Type, abstracted: dict[int, str] | None = None
              ) -> bir.Layout:
    """The layout a value of `ty` is held at.

    `abstracted` is the binding's `layouts`: the layout each of its abstracted
    variables stands for, when it is one of `layout.share`'s copies. Without
    it an abstracted variable has no layout and the answer is `BOXED`, which
    is the uniform representation `mono.check_layouts` exists to keep out of
    any body that would take polymorphic data apart.
    """
    ty = prune(ty)
    if abstracted and isinstance(ty, TVar) and ty.id in abstracted:
        return bir.Layout(abstracted[ty.id])
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


def _expr_layout(expr: CExpr, abstracted: dict[int, str] | None = None
                 ) -> bir.Layout:
    if isinstance(expr, CLit):
        resolved = prune(expr.ty)
        if not isinstance(resolved, TVar):
            return layout_of(resolved, abstracted)
        return {
            "Int": bir.Layout.I64, "Byte": bir.Layout.I8,
            "Char": bir.Layout.I32, "Float": bir.Layout.F64,
            "String": bir.Layout.PTR,
        }.get(expr.kind, layout_of(expr.ty, abstracted))
    return layout_of(expr.ty, abstracted)


# Every symbol this backend defines begins with this, and no symbol the
# runtime exports may. That is the whole of the guarantee that a Turkey
# function cannot land on a runtime entry point: `mangle` is the only way a
# Turkey name becomes a symbol and it always prepends this, so the two
# namespaces cannot meet. Anything weaker is an accident -- `Module#name`
# mangles to a `_23_` no runtime name contains, but `%bound11757` is a real
# top-level binding with no `#` in it, so that separation was never a rule.
#
# `test_no_runtime_symbol_can_be_named_by_a_turkey_program` holds up the other
# half, that the runtime keeps off this prefix.
COMPILED_PREFIX = "turkeyfn_"


def mangle(name: str) -> str:
    """A Turkey name as a native symbol, one name to one symbol.

    An underscore doubles rather than passing through, which is what makes
    this injective and is not decoration. With `_` left alone, `_25_` in a
    symbol could have come from a `%` in the name or from the four characters
    `_25_` in it, and those are different names: `Main#f%lambda0` and
    `Main#f_25_lambda0` both used to mangle to `turkeyfn_Main_23_f_25_lambda0`.

    That was reachable. A lifted lambda is named after the binding it came out
    of, so `Main#f`'s first lambda was `turkeyfn_Main_23_f_lambda_0` and so was
    a Turkey function actually called `f_lambda_0`. Two definitions, one
    symbol, and llvmlite raises `DuplicatedNameError` from somewhere that says
    nothing about either.

    Doubling makes the reading unambiguous: an escape is `_`, two hex digits,
    `_`, and a literal underscore is always a pair, so no run of literal
    underscores and digits can spell one.
    """
    pieces = []
    for byte in name.encode("utf-8"):
        char = chr(byte)
        if char == "_":
            pieces.append("__")
        else:
            pieces.append(char if char.isalnum() else f"_{byte:02x}_")
    return COMPILED_PREFIX + "".join(pieces)


@dataclass(frozen=True)
class _Destination:
    block: bir.Block | None


class _FunctionLowerer:
    def __init__(self, bind: CBind, lam: CLam,
                 functions: dict[str, tuple[str, TFun, dict[int, str]]], decls,
                 tags: dict[str, int], record_fields: dict[str, list[str]],
                 lifted: list[bir.Function], lift_counter: list[int],
                 globals_: dict[str, bir.Value],
                 bottoming: frozenset[str] = frozenset(),
                 captures: list[tuple[str, bir.Layout]] | None = None,
                 output_name: str | None = None) -> None:
        self.bind = bind
        self.lam = lam
        # The layouts this body's abstracted variables stand for, if it is one
        # of `layout.share`'s copies; empty otherwise, which is every ground
        # binding and every generic one the sharing pass left alone.
        self.abstracted = bind.layouts
        self.functions = functions
        self.decls = decls
        self.tags = tags
        self.record_fields = record_fields
        self.lifted = lifted
        self.lift_counter = lift_counter
        self.globals = globals_
        self.bottoming = bottoming
        self.closure_abi = captures is not None
        self.captures = captures or []
        self.output_name = output_name or mangle(bind.name)
        function_type = prune(bind.ty)
        assert isinstance(function_type, TFun)
        self.result_layout = (bir.Layout.BOXED if self.closure_abi
                              else self.layout(function_type.ret))
        self.count = 0
        self.flat_refs = _flat_refs(lam.body)
        self.flat_records = _flat_records(lam.body)
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
        internal_layouts = [self.layout(parameter_hints.get(p.name, p.ty))
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

    def layout(self, ty: Type) -> bir.Layout:
        """`layout_of`, under this body's abstracted layouts."""
        return layout_of(ty, self.abstracted)

    def expr_layout(self, expr: CExpr) -> bir.Layout:
        return _expr_layout(expr, self.abstracted)

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
             frame: bir.Frame | None = None,
             diverges: bool = False) -> bir.Value:
        result = bir.Value(self.fresh(op), layout)
        block.instructions.append(
            bir.Instruction(op, args, result, frame, diverges))
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
                    self.expr_layout(by_name[field]) for field in fields)
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
                after = self.new_block("store_global", self.expr_layout(value))
                after.instructions.append(bir.Instruction(
                    "global_store", (self.globals[name].name, after.params[0])))
                if name in global_roots:
                    after.instructions.append(bir.Instruction(
                        "slot_store", (global_roots[name].name, after.params[0])))
                after.terminator = bir.Jump(following.name)
                self.lower(value, {}, {}, entry, _Destination(after))
            else:
                _, name, index, value = action
                after = self.new_block("store_field", self.expr_layout(value))
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

    def discarding(self, expr: CIf, dest: _Destination) -> _Destination:
        """Where a one-armed `if`'s `then` branch delivers its value.

        Nowhere, is the answer: an `if` with no `else` *is* the statement form,
        and section 6.7 gives it the type `Unit` whatever its branch answers
        (`infer._gen_EIf`). So `if c { Array.pop(xs) }` is a well-typed program
        whose branch hands back an `Option` the `if` does not have -- and
        handing that to `dest` is asking for a pointer to become a unit.

        The branch is therefore given a destination of its own, one that takes
        the value it really produces and passes `dest` the unit the `if`
        really answers. Unreachable when the branch diverges, and `finish`
        drops it then, so a `return` inside the branch costs nothing.
        """
        if expr.otherwise is not None:
            return dest
        block = self.new_block("discard", self.expr_layout(expr.then))
        self.transfer(block, dest, bir.Constant(bir.Layout.UNIT, 0))
        return _Destination(block)

    def flat_slots(self, target, env: dict[str, bir.Value]):
        """The scattered slots of the record `target` names, or `None`.

        Through `env` rather than by the Core name directly, because Core
        names shadow: `_flat_records` compares names bare, so it can call one
        `b` flat while a different `b` elsewhere in the body is an ordinary
        record with different fields. Looking the name up in the environment
        that scoping already maintains asks about *this* `b` -- the flattened
        binding puts a stand-in there and nothing else does, so an inner `b`
        finds its own slot, which is not a record's, and falls through to the
        ordinary `object_get`.
        """
        if not isinstance(target, CVar):
            return None
        found = env.get(target.name)
        return found.fields if isinstance(found, _Scattered) else None

    def wrap_array(self, block: bir.Block, storage: bir.Operand,
                   length: bir.Operand) -> bir.Operand:
        """`Data.Array#Array` around raw storage and the length it holds.

        Three primitives answer a `Data.Array.Array` that the runtime can only
        build the *contents* of: it allocates the flat storage, and the record
        and constructor around it carry tags that live in this table. The
        runtime reads the same shape back structurally (`array_parts`), which
        needs no tags -- only building one does.
        """
        fields = self.record_fields["Data.Array#ArrayStorage"]
        by_name = {"storage": storage, "length": length}
        ordered = [by_name[name] for name in fields]
        inner = self.emit(block, "object_new", (
            "1", str(self.tags["Data.Array#ArrayStorage"]), str(len(ordered)),
            str(_layout_metadata(item.layout for item in ordered)),
        ), bir.Layout.PTR)
        for index, item in enumerate(ordered):
            block.instructions.append(bir.Instruction(
                "object_set", (inner, str(index), item)))
        outer = self.emit(block, "object_new", (
            "1", str(self.tags["Data.Array#Array"]), "1",
            str(_layout_metadata([bir.Layout.PTR])),
        ), bir.Layout.PTR)
        block.instructions.append(bir.Instruction("object_set", (outer, "0", inner)))
        return outer

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
        if (isinstance(expr, CLet) and not expr.binders
                and isinstance(expr.value, CRecord)
                and expr.name in self.flat_records):
            # A record no one can see the identity of is its fields and
            # nothing else. See `_flat_records`.
            record = expr.value
            names = [name for name, _ in record.fields]

            def scattered(at: bir.Block, values: list[bir.Operand]) -> None:
                held = {}
                for name, value in zip(names, values):
                    slot = self.new_slot(f"{expr.name}_{name}", value.layout)
                    at.instructions.append(
                        bir.Instruction("slot_store", (slot.name, value)))
                    held[name] = slot
                inner = dict(env)
                inner[expr.name] = _Scattered(held)
                self.lower(expr.body, inner, joins, at, dest)
            self.lower_values([value for _, value in record.fields],
                              env, joins, block, scattered)
            return
        if isinstance(expr, CLet):
            # A `var` whose cell never escapes is the slot itself: the value
            # goes in where the pointer to it used to, and the `CRef` around
            # it is dropped. See `_flat_refs`.
            # The value has to be checked as well as the name: `flat_refs`
            # compares names bare, so another binding of the same name -- one
            # that is not a cell at all -- must not have a `CRef` unwrapped
            # off it.
            flattened = (expr.name in self.flat_refs
                         and isinstance(expr.value, CRef))
            bound = expr.value.value if flattened else expr.value
            value_layout = self.expr_layout(bound)
            after = self.new_block("let", value_layout)
            slot = self.new_slot(expr.name, value_layout)
            after.instructions.append(
                bir.Instruction("slot_store", (slot.name, after.params[0])))
            inner = dict(env)
            inner[expr.name] = slot
            self.lower(expr.body, inner, joins, after, dest)
            self.lower(bound, env, joins, block, _Destination(after))
            return
        if isinstance(expr, CIf):
            def branch(at: bir.Block, values: list[bir.Operand]) -> None:
                yes, no = self.new_block("then"), self.new_block("else")
                at.terminator = bir.Branch(values[0], yes.name, no.name)
                self.lower(expr.then, env, joins, yes, self.discarding(expr, dest))
                other = expr.otherwise or CUnit(UNIT, expr.span)
                self.lower(other, env, joins, no, dest)
            self.lower_values([expr.cond], env, joins, block, branch)
            return
        if isinstance(expr, CMatch):
            after_scrutinee = self.new_block("match_value", self.layout(expr.scrutinee.ty))
            scrutinee_slot = self.new_slot("scrutinee", self.layout(expr.scrutinee.ty))
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
            param_slots = [self.new_slot(p.name, self.layout(p.ty)) for p in expr.params]
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
            after = self.new_block("operand", self.expr_layout(expr))
            slot = self.new_slot("operand", self.expr_layout(expr))
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
            layout = self.expr_layout(expr)
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
            found = env.get(expr.name)
            assert not isinstance(found, _Scattered), (
                f"'{expr.name}' was scattered into slots, so a bare mention of "
                f"it is a record that does not exist; `_flat_records` should "
                f"not have offered it")
            if found is None:
                if expr.name in self.functions:
                    done(block, self.emit(block, "function_closure",
                                          (self.functions[expr.name][0],),
                                          bir.Layout.PTR))
                    return
                if expr.name in self.globals:
                    global_ = self.globals[expr.name]
                    loaded = self.emit(block, "global_load",
                                       (global_.name,), global_.layout)
                    done(block, self.coerce(block, loaded, self.expr_layout(expr)))
                    return
                raise Unsupported(f"LLVM backend cannot use top-level value '{expr.name}'", expr.span)
            loaded = self.emit(block, "slot_load", (found.name,), found.layout)
            done(block, self.coerce(block, loaded, self.expr_layout(expr)))
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
            # `env` as well as the name, because `flat_refs` compares names
            # bare: a top-level `var` sharing a name with a flattened local is
            # reached through `self.globals`, not through a slot.
            if (isinstance(expr.target, CVar)
                    and expr.target.name in self.flat_refs
                    and isinstance(env.get(expr.target.name), bir.Value)):
                slot = env[expr.target.name]
                assert isinstance(slot, bir.Value)
                loaded = self.emit(block, "slot_load", (slot.name,), slot.layout)
                done(block, self.coerce(block, loaded, self.layout(expr.ty)))
                return
            self.lower_values([expr.target], env, joins, block,
                              lambda at, xs: done(at, self.emit(
                                  at, "cell_load", (xs[0],), self.layout(expr.ty))))
            return
        if isinstance(expr, CAssign):
            held = self.flat_slots(getattr(expr.target, "target", None), env)
            if isinstance(expr.target, CField) and held is not None:
                slot = held[expr.target.name]
                def scattered_set(at: bir.Block, xs: list[bir.Operand]) -> None:
                    at.instructions.append(bir.Instruction(
                        "slot_store",
                        (slot.name, self.coerce(at, xs[0], slot.layout))))
                    done(at, bir.Constant(bir.Layout.UNIT, 0))
                self.lower_values([expr.value], env, joins, block,
                                  scattered_set)
                return
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
                    value = self.coerce(at, xs[0], self.layout(expr.target.ty))
                    at.instructions.append(bir.Instruction(
                        "array_set", (xs[1], xs[2], value)))
                    done(at, bir.Constant(bir.Layout.UNIT, 0))
                self.lower_values([expr.value, expr.target.target, expr.target.index],
                                  env, joins, block, set_index)
                return
            if (isinstance(expr.target, CVar)
                    and expr.target.name in self.flat_refs
                    and isinstance(env.get(expr.target.name), bir.Value)):
                slot = env[expr.target.name]
                assert isinstance(slot, bir.Value)
                def store(at: bir.Block, xs: list[bir.Operand]) -> None:
                    at.instructions.append(bir.Instruction(
                        "slot_store",
                        (slot.name, self.coerce(at, xs[0], slot.layout))))
                    done(at, bir.Constant(bir.Layout.UNIT, 0))
                self.lower_values([expr.value], env, joins, block, store)
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
                    element_layout = (self.layout(type_args[0]) if type_args else
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
        held = (self.flat_slots(expr.target, env)
                if isinstance(expr, CField) else None)
        if held is not None:
            slot = held[expr.name]
            loaded = self.emit(block, "slot_load", (slot.name,), slot.layout)
            done(block, self.coerce(block, loaded, self.layout(expr.ty)))
            return
        if isinstance(expr, (CField, CProject, CIndex)):
            targets = ([expr.target, expr.index] if isinstance(expr, CIndex)
                       else [expr.target])
            def project(at: bir.Block, values: list[bir.Operand]) -> None:
                if isinstance(expr, CIndex):
                    made = self.emit(at, "array_get", tuple(values),
                                     self.layout(expr.ty), self.frame(expr.span))
                else:
                    index = expr.index if isinstance(expr, CProject) else self.field_index(
                        expr.target.ty, expr.name)
                    made = self.emit(at, "object_get", (values[0], str(index)),
                                     self.layout(expr.ty))
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
                        done(at, self.wrap_array(at, raw, length))
                    self.lower_values(expr.args, env, joins, block, to_bytes)
                    return
                if primitive in ("Prim.args", "Prim.readFileBytes"):
                    # The runtime answers raw storage; the `Array` around it is
                    # built here, where the constructor tags are. `array_parts`
                    # reads that shape structurally on the way back in, but
                    # building one needs tags the runtime has no way to know.
                    storage_prim = ("prim.argsStorage" if primitive == "Prim.args"
                                    else "prim.readFileStorage")
                    def from_storage(at: bir.Block,
                                     values: list[bir.Operand]) -> None:
                        raw = self.emit(at, storage_prim, tuple(values),
                                        bir.Layout.PTR, self.frame(expr.span))
                        # The storage is allocated at exactly the length it
                        # holds, so its own element count is the length.
                        length = self.emit(at, "prim.arrayLength", (raw,),
                                           bir.Layout.I64)
                        done(at, self.wrap_array(at, raw, length))
                    self.lower_values(expr.args, env, joins, block, from_storage)
                    return
                operation = "prim." + primitive.removeprefix("Prim.")
                array_element_layout = None
                if primitive in ("Prim.arrayNew", "Prim.arrayNewUninit"):
                    _, type_args = spine(expr.ty)
                    element_layout = self.layout(type_args[0]) if type_args else bir.Layout.BOXED
                    array_element_layout = element_layout
                    operation += "." + element_layout.value
                elif primitive == "Prim.arrayGet":
                    operation += "." + self.layout(expr.ty).value
                elif primitive == "Prim.arraySet":
                    operation += "." + self.expr_layout(expr.args[2]).value
                def primitive_call(at: bir.Block, values: list[bir.Operand]) -> None:
                    if array_element_layout is not None and len(values) == 2:
                        values[1] = self.coerce(at, values[1], array_element_layout)
                    done(at, self.emit(
                        at, operation, tuple(values), self.layout(expr.ty),
                        self.frame(expr.span)))
                self.lower_values(expr.args, env, joins, block, primitive_call)
                return
            if isinstance(fn, CVar) and fn.name in self.functions:
                symbol, function_type, callee = self.functions[fn.name]
                def direct_call(at: bir.Block, values: list[bir.Operand]) -> None:
                    # The *callee's* abstracted layouts, not this function's.
                    # A layout-shared copy holds its parameters at the layouts
                    # its key names, and a caller that read them off the shared
                    # scheme would pass boxed values to a body expecting bare
                    # ones. See `turkey/layout.py`.
                    arguments = [self.coerce(at, value,
                                             layout_of(expected, callee))
                                 for value, expected in zip(values,
                                                            function_type.params)]
                    called = self.emit(at, "call", (symbol, *arguments),
                                       layout_of(function_type.ret, callee),
                                       self.frame(expr.span),
                                       diverges=fn.name in self.bottoming)
                    done(at, self.coerce(at, called, self.layout(expr.ty)))
                self.lower_values(expr.args, env, joins, block, direct_call)
                return
            if isinstance(fn, CCon):
                info = self.decls.constructors[fn.name]
                def construct(at: bir.Block, values: list[bir.Operand]) -> None:
                    con_type = instantiate(info.scheme, lambda: TVar(1))
                    assert isinstance(con_type, TFun)
                    unify(con_type.ret, expr.ty)
                    values = [self.coerce(at, value, self.layout(expected))
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
                done(at, self.coerce(at, called, self.layout(expr.ty)))
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
            after = self.new_block("value", self.layout(expr.ty))
            done(after, after.params[0])
            self.lower(expr, env, joins, block, _Destination(after))
            return
        raise Unsupported(f"LLVM backend does not support {type(expr).__name__}", expr.span)

    def lift(self, lam: CLam, env: dict[str, bir.Value]) -> tuple[
            str, list[tuple[str, bir.Value]]]:
        # Slots only. A `_Scattered` names no slot to capture, and cannot be
        # free in a lambda anyway: `_flat_records` escapes any name a closure
        # reads a field of, precisely so that a record a closure shares stays
        # a record.
        captures = [(name, value) for name, value in env.items()
                    if isinstance(value, bir.Value)]
        number = self.lift_counter[0]
        self.lift_counter[0] += 1
        # `_25_` is the escape for `%`, and `%` is the compiler's own
        # character: the lexer will not accept one in an identifier, so no
        # Turkey name mangles to a symbol containing this. Appending a bare
        # `_lambda_0` instead is what a function named `f_lambda_0` used to
        # collide with.
        symbol = f"{self.output_name}_25_lambda{number}"
        # The lifted lambda is compiled under the same abstracted layouts as
        # the body it came out of: a closure inside a layout-shared copy holds
        # the copy's variables at the copy's layouts, and a synthetic binding
        # that forgot them would read its captures at `BOXED`.
        bind = CBind(symbol, lam.ty, [], lam, lam.span,
                     layouts=self.abstracted)
        child = _FunctionLowerer(
            bind, lam, self.functions, self.decls, self.tags,
            self.record_fields, self.lifted, self.lift_counter,
            self.globals, self.bottoming,
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
            layout = self.layout(hints.get(pat.name, ty))
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
            contents = self.new_block("pattern_contents")
            # A type with one constructor has nothing to distinguish. Reading
            # the tag to compare it against the only value it can hold costs a
            # load, a compare and a branch on the hot path -- `Array a =
            # Array(ArrayStorage a)` pays it on every element access -- and
            # the arm it branches to is unreachable. Exhaustiveness already
            # knows the match is total; this is the lowering agreeing.
            variants = self.decls.tycons[
                self.decls.constructors[pat.name].tycon].variants
            if len(variants) == 1:
                block.terminator = bir.Jump(contents.name)
            else:
                tag = self.emit(block, "object_tag", (value,), bir.Layout.I32)
                wanted = self.tags[pat.name]
                condition = self.emit(
                    block, "scalar_eq",
                    (tag, bir.Constant(bir.Layout.I32, wanted)), bir.Layout.I1)
                block.terminator = bir.Branch(condition, contents.name,
                                              failure.name)
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
                field_layout = _pattern_layout(sub, field_ty, hints, self.abstracted)
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
                                   _pattern_layout(sub, field_ty, hints, self.abstracted))
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


def _flat_records(body) -> set[str]:
    """The record bindings in this body that can be their fields instead.

    A `for` loop's cursor is the case this is for. `Iterator.iter` builds a
    one-field mutable record, `next` reads and writes that field, and once
    `next` is inlined the record is created and destroyed inside one function
    without ever being handed to anything -- an allocation, a GC root and two
    memory operations per loop step, for a value that could be a register.

    The criterion is the one `_flat_refs` uses, and the one every escape
    analysis uses: a record is local when every mention of it is a field of
    it, read or assigned, and none is inside a closure. A bare mention means
    the record itself is going somewhere -- passed, returned, matched on,
    stored in another record -- and then its identity is observable and the
    allocation is what the program means. HotSpot and Go decide the same
    question by escape analysis and LLVM's SROA decides it for an `alloca`;
    the difference here is only that the answer is available in Core.

    Conservative on shadowing, like `_flat_refs`: names are compared bare, so
    one disqualified mention rules out every binding sharing that name, and a
    name bound to a record twice is refused outright rather than reasoned
    about.
    """
    import dataclasses
    candidates: set[str] = set()
    repeated: set[str] = set()
    escaped: set[str] = set()

    def children(node, closed: bool) -> None:
        if not dataclasses.is_dataclass(node):
            return
        for field in dataclasses.fields(node):
            value = getattr(node, field.name)
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, (CExpr, CBind, CAlt)):
                    walk(item, closed)
                elif isinstance(item, tuple):
                    for part in item:
                        if isinstance(part, (CExpr, CBind, CAlt)):
                            walk(part, closed)

    def walk(node, closed: bool) -> None:
        if isinstance(node, CLet) and isinstance(node.value, CRecord):
            if node.name in candidates:
                repeated.add(node.name)
            elif not closed and not node.binders:
                candidates.add(node.name)
            else:
                escaped.add(node.name)
        if isinstance(node, CVar):
            escaped.add(node.name)
            return
        if isinstance(node, CField) and isinstance(node.target, CVar):
            # Reading or writing one field needs no record -- unless a closure
            # is doing it, which needs the record to share.
            if closed:
                escaped.add(node.target.name)
            return
        children(node, closed or isinstance(node, CLam))

    walk(body, False)
    return candidates - escaped - repeated


def _flat_refs(body) -> set[str]:
    """The `var` cells in this body that can be a stack slot instead.

    `lower.py` makes every `var` a `CRef`, because a `var` is captured by
    reference: a closure that writes one writes through to it, and a heap cell
    is what makes that true. A `var` no closure can see owes nothing to that
    rule, and paying for it costs an allocation, an indirection on every read
    and write, a GC root, and a value `mem2reg` can never promote to a
    register.

    This is OCaml's `simplify_local_refs` and MLton's `LocalRef` pass, and the
    criterion is theirs: a cell is local when every mention of it is either a
    read or a write of the cell itself, and none of them is inside a closure.
    Anything else -- passed as an argument, returned, put in a record -- is the
    cell escaping, and then the cell is what the program means.

    Done here rather than in `lower.py` because a `var`'s *type* is `%Ref t`,
    fixed by the elaborator and checked by `coretc`; Core has no mutable
    binding that is not a reference. So the cell stays in Core and stops
    existing here, where the backend IR already has a mutable slot to put it
    in and nothing downstream re-derives the type.

    Conservative on shadowing: names are compared bare, so one disqualified
    mention rules out every binding sharing that name.
    """
    import dataclasses
    candidates: set[str] = set()
    escaped: set[str] = set()

    def children(node, closed: bool) -> None:
        if not dataclasses.is_dataclass(node):
            return
        for field in dataclasses.fields(node):
            value = getattr(node, field.name)
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, (CExpr, CBind, CAlt)):
                    walk(item, closed)

    def walk(node, closed: bool) -> None:
        if isinstance(node, CLet) and isinstance(node.value, CRef) and not closed:
            candidates.add(node.name)
        if isinstance(node, CVar):
            # A bare mention is the cell itself being handed somewhere.
            escaped.add(node.name)
            return
        if isinstance(node, CDeref) and isinstance(node.target, CVar):
            # Reading through the cell needs no cell -- unless a closure is
            # doing the reading, which needs the cell to share.
            if closed:
                escaped.add(node.target.name)
            return
        if isinstance(node, CAssign) and isinstance(node.target, CVar):
            if closed:
                escaped.add(node.target.name)
            walk(node.value, closed)
            return
        children(node, closed or isinstance(node, CLam))

    walk(body, False)
    return candidates - escaped


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
                    hints: dict[str, Type],
                    abstracted: dict[int, str] | None = None) -> bir.Layout:
    while isinstance(pattern, ast.PAnnot):
        pattern = pattern.pat
    if isinstance(pattern, ast.PVar) and pattern.name in hints:
        return layout_of(hints[pattern.name], abstracted)
    if isinstance(pattern, ast.PCon) and pattern.name in (BOOL_FALSE, BOOL_TRUE):
        return bir.Layout.I1
    # Nested constructors and tuples are heap values regardless of the type
    # family carried by their contents.
    if isinstance(pattern, (ast.PCon, ast.PRecord, ast.PTuple)):
        return bir.Layout.PTR
    return layout_of(fallback, abstracted)


def lower(program: CProgram, decls, main: str = "main") -> bir.Module:
    never_returns = bottoming(program)
    record_fields = _record_layouts(program, decls)
    tag_names = sorted(set(decls.constructors) | set(record_fields))
    tags = {name: index for index, name in enumerate(tag_names)}
    functions: dict[str, tuple[str, TFun, dict[int, str]]] = {}
    candidates: list[tuple[CBind, CLam]] = []
    for bind in program.dicts + program.binds:
        value = _erase_types(bind.value)
        ty = prune(bind.ty)
        if isinstance(value, CLam) and isinstance(ty, TFun):
            functions[bind.name] = (mangle(bind.name), ty,
                                    bind.layouts)
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
                functions[bind.name] = (functions[value.name][0], ty,
                                        bind.layouts)
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
            lifted, lift_counter, globals_, never_returns,
        ).finish())

    init_name = COMPILED_PREFIX + "module_initialize"
    init_type = TFun([], UNIT)
    init_lam = CLam(ty=init_type, params=[], body=CUnit(UNIT),
                    name="<module initialization>")
    init_bind = CBind(init_name, init_type, [], init_lam)
    initializer = _FunctionLowerer(
        init_bind, init_lam, functions, decls, tags, record_fields,
        lifted, lift_counter, globals_, never_returns, output_name=init_name,
    ).finish_initializers(program, runtime_binds)

    run_name = COMPILED_PREFIX + "run"
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
