"""Giblets: modules whose code holds no traced value (TIX-63, SPEC-DELTAS 73).

The collector runs when nothing may allocate and has no roots of its own, so
the code it is written in has to be code the collector need not know about.
`RUNTIME-IN-TURKEY.md` decides how that is defined -- **by the types a function
names, not by an effect it has** -- on Modula-3's precedent of traced and
untraced references, and measures the claim it rests on: the collector handles
the heap as bytes, never as Turkey values. Code that names no traced type
allocates nothing because there is nothing to allocate.

That makes this check local: a walk over one binding's Core, asking of each
node whether its type is one `layout_of` holds at a traced pointer. It is on
Core rather than on the surface tree for the reason `CLAUDE.md` gives for any
pass that can live there: the two compilers' Core is compared byte for byte, so
the two implementations of this walk see the same input.

What a giblet may name is exactly what is untraced: `Int`, `Byte`, `Char`,
`Float`, `Unit`, `Bool`, `Prim.Ptr`, and newtypes over them -- the erasure is
`layout_of`'s, so "transitively through the types it names" comes with it.

Four things are allowed that the type rule alone would not, each because it is
not a value the function holds:

* **the head of a call.** `f(x)` has `f` at a function type; calling a named
  function is not making a closure. Mentioning one *without* calling it is,
  and is refused.
* **a dictionary argument** naming a global instance. Specialization turns it
  into a direct call; where it does not, the Low IR check in `boot` names the
  dictionary that survived (step 3 of the ticket).
* **a `var`.** Core spells one as a `%Ref` cell, but a giblet has no closure a
  cell could escape into, so the lowering keeps it in a slot. Its *contents*
  are checked instead.
* **a panic's message.** `Prim.error("...")` with a literal is a panic
  terminator in the Low IR, and the literal is interned and permanently rooted
  -- it is static data, not a heap value a collection could free. The
  collector needs this for "out of memory" and its like. `Prim.cString` and
  `Prim.codeAddress` take a literal on the same terms: it names bytes or a
  symbol the compiler lays out, and the result is an untraced address.

Which modules are giblets is a list written here, not a naming convention or a
declaration form: joining it is a deliberate edit to the compiler, and a module
on it must also come from the shipped `lib/`, the same gate `foreign` has.
`boot/Turkey/Giblets.gob` keeps the same list, and a test says so.
"""

from __future__ import annotations

import os

from .backend_lower import layout_of
from . import backend_ir as bir
from .core import (
    CApp, CArray, CAssign, CBind, CCon, CDeref, CExpr, CField, CIf,
    CIndex, CJoin, CJump, CLam, CLet, CLetRec, CLit, CMatch, CPrim, CProgram,
    CProject, CRecord, CRef, CTuple, CTyApp, CTyLam, CUnit, CVar, is_ref,
    ref_elem,
)
from .errors import Span, TypeError_
from .types import Type, prune, show

#: The giblet modules. See the module docstring for why this is a list.
GIBLET_MODULES: frozenset[str] = frozenset({
    "Turkey.Entry",
    "Turkey.Memory",
})

#: Extra names for the tests, which need a giblet module whose code is wrong on
#: purpose and cannot put one in the list above. Read by both compilers.
TEST_HOOK = "TURKEY_TEST_GIBLETS"

_TRACED = frozenset({bir.Layout.PTR, bir.Layout.BOXED})


def giblet_modules() -> frozenset[str]:
    extra = os.environ.get(TEST_HOOK, "")
    return GIBLET_MODULES | {name for name in extra.split(",") if name}


def check_placement(name: str, library: bool, where: Span) -> None:
    """A giblet module comes from the shipped library, as `foreign` does."""
    if name in giblet_modules() and not library:
        raise TypeError_(
            f"'{name}' is a giblet module, and a giblet module must come from "
            f"the standard library", where)


def traced(ty: Type, decls) -> bool:
    """Whether the collector must find a value of `ty`. An unknown layout is
    the uniform representation, which is a traced pointer."""
    found = layout_of(ty, None, decls)
    return found is None or found in _TRACED


def check_program(program: CProgram, decls) -> None:
    giblets = giblet_modules()
    if not giblets:
        return
    for bind in program.dicts:
        if bind.module in giblets:
            raise TypeError_(
                f"'{bind.module}' is a giblet module and may not declare an "
                f"instance: a dictionary is a heap object the collector traces",
                bind.span)
    dicts = frozenset(bind.name for bind in program.dicts)
    for bind in program.binds:
        if bind.module in giblets:
            _Walk(decls, dicts, bind).top()


class _Walk:
    def __init__(self, decls, dicts: frozenset[str], bind: CBind) -> None:
        self.decls = decls
        self.dicts = dicts
        self.bind = bind

    def top(self) -> None:
        value = self.bind.value
        while isinstance(value, CTyLam):
            value = _node(value.body)
        if isinstance(value, CLam):
            for param in value.params:
                self.require(param.ty, f"the parameter '{param.name}'",
                             value.span)
            body = _node(value.body)
            self.require(body.ty, "the result", value.span)
            self.expr(body)
        else:
            self.expr(value)

    def require(self, ty: Type, what: str, span: Span | None) -> None:
        if traced(ty, self.decls):
            raise TypeError_(
                f"{what} has type {show(prune(ty))}, which the collector "
                f"traces; '{self.bind.module}' is a giblet module, whose code "
                f"holds untraced values only",
                span if span is not None else self.bind.span)

    def expr(self, node: CExpr | None) -> None:
        e = _node(node)
        # A panic's result never exists, and its type is whatever the context
        # wanted -- often a variable nothing fixed.
        if not (isinstance(e, CApp) and _panics(e)):
            self.require(e.ty, _describe(e), e.span)
        self.inside(e)

    def head(self, node: CExpr | None) -> None:
        """The function position of a call: its own type is how the call is
        spelled, so only what is inside it is checked."""
        e = _node(node)
        if isinstance(e, CTyApp):
            self.head(e.fn)
        elif isinstance(e, CField) and self.instance(e.target):
            # A method of an instance the elaboration already chose: the
            # dump prints it as the method's own name, and it is one.
            return
        elif not isinstance(e, (CVar, CPrim, CCon)):
            self.expr(e)

    def instance(self, e: CExpr | None) -> bool:
        return isinstance(e, CVar) and e.name in self.dicts

    def inside(self, e: CExpr) -> None:
        if isinstance(e, (CLit, CUnit, CVar, CCon, CPrim)):
            return
        if isinstance(e, CApp):
            self.head(e.fn)
            message = _panic_message(e)
            for arg in e.args:
                if arg is message:
                    continue
                if self.instance(arg):
                    continue
                self.expr(arg)
        elif isinstance(e, CTyApp):
            self.expr(e.fn)
        elif isinstance(e, CLet):
            value = _node(e.value)
            if isinstance(value, CRef) and e.bound is not None \
                    and is_ref(e.bound):
                self.require(ref_elem(e.bound), f"the variable '{e.name}'",
                             value.span)
                self.expr(value.value)
            else:
                self.expr(value)
            self.expr(e.body)
        elif isinstance(e, CDeref):
            self.cell(e.target)
        elif isinstance(e, CAssign):
            self.cell(e.target)
            self.expr(e.value)
        elif isinstance(e, CIf):
            self.expr(e.cond)
            self.expr(e.then)
            if e.otherwise is not None:
                self.expr(e.otherwise)
        elif isinstance(e, CMatch):
            # Every pattern over an untraced scrutinee binds untraced values:
            # literals, names, `Bool`, and a newtype's erased payload.
            self.expr(e.scrutinee)
            for alt in e.alts:
                self.expr(alt.body)
        elif isinstance(e, CJoin):
            for param in e.params:
                self.require(param.ty, f"the parameter '{param.name}'", e.span)
            self.expr(e.body)
            self.expr(e.rest)
        elif isinstance(e, CJump):
            for arg in e.args:
                self.expr(arg)
        elif isinstance(e, CLetRec):
            for bind in e.binds:
                self.require(bind.ty, f"the local function '{bind.name}'",
                             bind.span)
            self.expr(e.body)
        elif isinstance(e, (CField, CProject)):
            # An untraced field of a traced aggregate: the aggregate is what
            # the code is holding, and it is refused there.
            self.expr(e.target)
        elif isinstance(e, CIndex):
            self.expr(e.target)
            self.expr(e.index)
        else:
            # A tuple, an array, a record, a lambda or a bare cell: each has a
            # traced type by construction, and `expr` refused it already.
            assert isinstance(e, (CTuple, CArray, CRecord, CLam, CTyLam, CRef))
            raise AssertionError(f"{type(e).__name__} passed the giblets check")

    def cell(self, target: CExpr | None) -> None:
        """The target of a read or write of a `var`: the cell itself is a slot,
        and what is in it was checked where the variable was made."""
        if not isinstance(target, CVar):
            self.expr(target)


def _node(e: CExpr | None) -> CExpr:
    assert e is not None
    return e


def _panics(e: CApp) -> bool:
    fn = e.fn
    while isinstance(fn, CTyApp):
        fn = fn.fn
    # `Prim.error` is a `CVar` in Core, not a `CPrim`: it is bound in the
    # environment at a scheme, and only a monomorphic builtin is a `CPrim`.
    return isinstance(fn, (CVar, CPrim)) and fn.name == "Prim.error"


#: The primitives whose one argument is a literal the compiler lays out as
#: static data, so that the literal is never a value the code holds: a panic's
#: message, and the bytes and symbol behind the two static addresses
#: (SPEC-DELTAS 74).
_STATIC_LITERALS = frozenset({"Prim.error", "Prim.cString", "Prim.codeAddress"})


def _panic_message(e: CApp) -> CExpr | None:
    fn = e.fn
    while isinstance(fn, CTyApp):
        fn = fn.fn
    if (isinstance(fn, (CVar, CPrim)) and fn.name in _STATIC_LITERALS
            and len(e.args) == 1 and isinstance(e.args[0], CLit)):
        return e.args[0]
    return None


def _describe(e: CExpr) -> str:
    if isinstance(e, CLit):
        return "a string literal" if e.kind == "String" else "a literal"
    if isinstance(e, CVar):
        return f"'{e.name}'"
    if isinstance(e, CCon):
        return f"the constructor '{e.name}'"
    if isinstance(e, CPrim):
        return f"'{e.name}'"
    if isinstance(e, CTuple):
        return "a tuple"
    if isinstance(e, CArray):
        return "an array"
    if isinstance(e, CRecord):
        return "a record"
    if isinstance(e, (CLam, CTyLam)):
        return "a lambda"
    if isinstance(e, CApp):
        return "a call's result"
    return "a value"


__all__ = ["GIBLET_MODULES", "TEST_HOOK", "check_placement", "check_program",
           "giblet_modules", "traced"]
