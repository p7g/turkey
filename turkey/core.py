"""The typed Core IR (`plan.txt` item 5).

The elaborator has been a proto-Core since M6 and has had no datatype. What it
produces is a handful of mutable side objects hung on surface AST nodes -- a
`Use` per occurrence, an `Abstraction` per binding group, an `InstancePlan` per
instance -- which the evaluator reads back and trusts. Nothing checks them. A
dictionary passed in the wrong position is not a compile error; it is a wrong
answer, or an `AttributeError` from inside the interpreter.

This module is the datatype that stops that being true. It holds the shape and
a printer, and no construction logic at all: `turkey/lower.py` builds it and
`turkey/coretc.py` checks it, so what is written here can be read as a
specification rather than as the incidental output of a pass.

## What Core makes explicit, and what it leaves alone

Three things, and only three:

* **Types.** Every node carries the `types.Type` inference gave it (delta 48).
* **Dictionaries.** A class becomes a record type, an instance becomes a
  top-level binding of that type, and evidence becomes ordinary term syntax --
  a variable, an application, a field projection. `evidence.Evidence` stops
  being a small separate language only the evaluator understands.
* **Polymorphism.** A generalized binding becomes a type abstraction and every
  use of it a type application, at the argument list delta 48 recorded.

Everything else keeps the shape it had, with one exception. `CIf` and `CMatch`
are the surface constructs they always were, but there is no node here for a
loop and none for a control transfer: `while`, `loop`, the C-style `for` and
`for ... in` are all `CJoin`, and `return`, `break` and `continue` are all
`CJump`. `turkey/lower.py` makes them so, on the way down from the AST, which
is what "unrepresentable" means -- not that no pass emits one, but that there
is nothing to emit. A term that names its control target by *where it sits*
cannot be written here at all.

## The one thing that is not a translation

Core has **no statements**. A block is nested `CLet`s, and the value of a block
is the body of the innermost one; sequencing is a `CLet` whose name nothing
reads. This is not a simplification for its own sake -- it is what lets the
checker be a walk with one rule per node instead of a walk plus a separate
statement discipline, and a checker simple enough to trust is the entire point
of the milestone.

## `var` is a reference cell, and this is where that gets written down

`let` is an immutable binding and is a plain `CLet`. `var` is mutable, and the
evaluator gives it *capture by reference*: closures share one mutable `REnv`
scope chain (`eval.py:43-73`), so a lambda that writes a captured `var` writes
through to the original. Nothing in `design.md` says so. It was invisible until
M12's `?` began putting the rest of a block inside a lambda, and delta 47's
`spread` is the case where it decides the answer.

So a `var` lowers to a cell: `CRef` makes one, `CDeref` reads it, `CAssign`
writes it, and its type is `%Ref t`. Capture is then by value of a reference,
which is exactly what the evaluator already does -- but stated in the IR, where
a checker can see it, rather than left to the representation of a scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields

from .errors import Span
from .lexer import literal_text
from .types import (
    STAR, TApp, TCon, TVar, Type, kind_arrow, prune, show, spine, vars_of,
)

# The reference-cell constructor. `%` cannot start a source identifier, so this
# can never collide with a declared type, and no program can write it.
REF = TCon("%Ref", kind_arrow(1))


def ref_of(t: Type) -> Type:
    return TApp(REF, t, STAR)


def is_ref(t: Type) -> bool:
    return isinstance(t, TApp) and isinstance(t.fn, TCon) and t.fn.name == REF.name


def ref_elem(t: Type) -> Type:
    assert is_ref(t), f"not a reference cell: {show(t)}"
    assert isinstance(t, TApp)
    return t.arg


# ------------------------------------------------------------------ terms


@dataclass(eq=False)
class CExpr:
    """Every Core node carries its type. That is what "typed" means here."""

    ty: Type
    span: Span | None = None


@dataclass(eq=False)
class CLit(CExpr):
    kind: str = ""
    value: object = None


@dataclass(eq=False)
class CUnit(CExpr):
    pass


@dataclass(eq=False)
class CVar(CExpr):
    """A term variable: a local, a top-level binding, or a dictionary.

    A dictionary is not a separate form. That is the milestone's whole claim --
    evidence became a value, so it is fetched the way any other value is.
    """

    name: str = ""


@dataclass(eq=False)
class CCon(CExpr):
    """A data constructor, referred to but not yet applied."""

    name: str = ""


@dataclass(eq=False)
class CPrim(CExpr):
    """A builtin, named as `builtins.py` names it."""

    name: str = ""


@dataclass(eq=False)
class CTuple(CExpr):
    elems: list[CExpr] = field(default_factory=list)


@dataclass(eq=False)
class CArray(CExpr):
    elems: list[CExpr] = field(default_factory=list)


@dataclass(eq=False)
class CRecord(CExpr):
    """A record built by naming its fields, which is how a dictionary is built."""

    con: str = ""
    fields: list[tuple[str, CExpr]] = field(default_factory=list)


@dataclass(eq=False)
class CField(CExpr):
    """A field projection. A method selection and a superclass selection are
    both this, which is the point: they were two shapes of `Evidence` and are
    one shape of term."""

    target: CExpr | None = None
    name: str = ""


@dataclass(eq=False)
class CProject(CExpr):
    target: CExpr | None = None
    index: int = 0


@dataclass(eq=False)
class CIndex(CExpr):
    target: CExpr | None = None
    index: CExpr | None = None


@dataclass(eq=False)
class CParam:
    name: str
    ty: Type


@dataclass(eq=False)
class CLam(CExpr):
    params: list[CParam] = field(default_factory=list)
    body: CExpr | None = None
    name: str = "<anonymous>"


@dataclass(eq=False)
class CApp(CExpr):
    fn: CExpr | None = None
    args: list[CExpr] = field(default_factory=list)


@dataclass(eq=False)
class CTyLam(CExpr):
    """Type abstraction. `binders` are the scheme's own quantified variables,
    used as binders directly -- a `TVar` is already a distinct identity, and
    reusing the scheme's means the type arguments a use site recorded line up
    with no separate agreement to keep."""

    binders: list[TVar] = field(default_factory=list)
    body: CExpr | None = None


@dataclass(eq=False)
class CTyApp(CExpr):
    """Type application, at the arguments delta 48 recorded on the use."""

    fn: CExpr | None = None
    args: list[Type] = field(default_factory=list)


@dataclass(eq=False)
class CLet(CExpr):
    """`let name : ty = value in body`. The only sequencing form there is.

    `name` may be one nothing reads (`%seq`), which is how a statement whose
    value is discarded is expressed.
    """

    name: str = ""
    bound: Type | None = None
    value: CExpr | None = None
    body: CExpr | None = None
    # A `let` generalizes (design decision 6), so a local binding may be
    # polymorphic and its uses may be type applications. The binders are the
    # scheme's own quantified variables, exactly as `CBind.binders` are.
    binders: list[TVar] = field(default_factory=list)


@dataclass(eq=False)
class CLetRec(CExpr):
    """A binding group that may refer to itself and to its siblings.

    One form for the whole group rather than one per name, because that is what
    an SCC is (design.md 5.2) and what the dictionary abstraction is shared
    over (`evidence.py`: a group's context is shared, as Haskell 98 shares it).
    """

    binds: list["CBind"] = field(default_factory=list)
    body: CExpr | None = None


@dataclass(eq=False)
class CJoin(CExpr):
    """`join j(params) = body in rest` -- a label, not a function.

    A join point is a `let`-bound continuation that is only ever *jumped* to:
    every mention is a saturated call in tail position, and none of them
    escapes. That is what makes it compilable as a label rather than as a
    closure, which is the whole content of Maurer, Downen, Ariola and Peyton
    Jones, *Compiling without continuations* (PLDI 2017).

    It is a distinct node rather than a flag on `CLetRec`, and a jump is a
    distinct node rather than a `CApp`, because the restriction is the point.
    A walker that has not been taught about these fails loudly; a walker that
    has not been taught about a flag treats a join point as a closure and is
    silently right, which is exactly the shape of trust `plan.txt` item 5 was
    written to remove.

    `recursive` says whether the body may jump to the join itself -- a loop
    compiled as a label, which is what item 7's last step makes of `while`.
    A non-recursive join is the case-of-case duplication: one continuation,
    several branches reaching it.

    The join's result type is the node's own `ty`: a jump replaces the value
    of the whole `join ... in ...` expression, so the body and the rest agree
    on it by construction.
    """

    name: str = ""
    params: list[CParam] = field(default_factory=list)
    body: CExpr | None = None
    rest: CExpr | None = None
    recursive: bool = False


@dataclass(eq=False)
class CJump(CExpr):
    """`jump j(args)`. Typed `!`: it never yields to its context.

    Which is why it needs no type of its own beyond bottom, and why a branch
    that jumps stays compatible with a branch that does not -- `coretc._join`
    already makes bottom absorb, for `return`, and a jump is the same kind of
    thing.
    """

    name: str = ""
    args: list[CExpr] = field(default_factory=list)


@dataclass(eq=False)
class CRef(CExpr):
    """Make a reference cell. What a `var` binding is."""

    value: CExpr | None = None


@dataclass(eq=False)
class CDeref(CExpr):
    """Read a reference cell. What a mention of a `var` is."""

    target: CExpr | None = None


@dataclass(eq=False)
class CAssign(CExpr):
    """Write a reference cell, an array slot, or a mutable record field.

    All three are assignment in the surface language and all three are
    reference semantics (design decision 7), so they are one node with three
    shapes of target rather than three nodes.
    """

    target: CExpr | None = None
    value: CExpr | None = None


@dataclass(eq=False)
class CIf(CExpr):
    cond: CExpr | None = None
    then: CExpr | None = None
    otherwise: CExpr | None = None


@dataclass(eq=False)
class CAlt:
    """One arm. What its pattern *binds*, and at what types, is deliberately
    not recorded: the checker derives it from the scrutinee's type and the
    constructor's declaration, which makes it a check rather than a
    restatement. A pattern that does not fit its scrutinee is then a rejected
    Core term instead of an agreement nobody verified."""

    pat: object  # ast.Pattern -- patterns are unchanged, see the note below
    body: CExpr


@dataclass(eq=False)
class CMatch(CExpr):
    """Pattern matching, over `ast.Pattern` unchanged.

    Patterns are the one part of the surface syntax Core does not restate.
    They are already a small, total, type-directed language with no implicit
    anything, and `exhaustive.py` has decided about them before this point.
    Rewriting them into nested single-level cases is a real transformation with
    a real benefit -- for a backend, later -- and none at all here.
    """

    scrutinee: CExpr | None = None
    alts: list[CAlt] = field(default_factory=list)


# Which subterm of which node is a **tail position**: the places a `CJump` may
# stand, because the value of what stands there is the value of the whole
# enclosing term. Stated once, here in the IR, because it is a fact about the
# IR -- `coretc.py` reads it to decide what a join scope reaches, and
# `joins.py` reads it to decide what a tail call is, and two statements of one
# fact is exactly the kind of agreement `plan.txt` item 5 exists to be rid of.
#
# Everything absent is not a tail position, and the one absence that carries
# weight is `CLam`'s body: a closure outlives the frame that binds the join, so
# a label is not something it can jump to. There is no loop body to say the
# same of any more -- a loop *is* a `CJoin` here, so its body is a join's body
# and reaches whatever that reaches.
TAIL_FIELDS: dict[str, tuple[str, ...]] = {
    "CIf": ("then", "otherwise"),
    "CLet": ("body",),
    "CLetRec": ("body",),
    "CJoin": ("body", "rest"),
    "CMatch": ("alts",),
}


# ------------------------------------------------------------- the program


@dataclass
class CBind:
    """One top-level binding, with the type variables it abstracts over.

    `binders` and `dicts` are separate because they are abstracted at different
    times and by different rules: the type variables come from generalization,
    the dictionary parameters from the predicates the scheme retained, and the
    order of the second is the scheme's predicate order (`evidence.py`).
    """

    name: str
    ty: Type
    binders: list[TVar]
    value: CExpr
    span: Span | None = None
    mutable: bool = False
    # Which module declared it. Only `turkey core` reads this, and for the
    # reason `turkey types` prints one module's signatures: a whole program's
    # Core is mostly the Prelude's, and a reader asking what their own file
    # elaborated to does not want four hundred lines of it first.
    module: str = ""
    # The equalities the binding's context states (delta 39): `Item s ~ Op`.
    # They are not evidence -- nothing is passed for them, and they are erased
    # at run time -- but they are *facts the body was checked under*, and a
    # family over a rigid variable does not reduce without them. Solving read
    # them from its assumptions and let them go; a checker that reads this
    # binding later has nowhere else to learn that `Item s` is `Op`.
    equations: list[tuple[Type, Type]] = field(default_factory=list)
    # The layout each abstracted variable stands for, by `TVar.id`, when this
    # binding is one of `layout.share`'s copies. Empty on everything else.
    #
    # A copy is still polymorphic -- its scheme is the original's, so its call
    # sites type-check unchanged -- and this is the one thing it knows that the
    # original does not: not *which* type each variable is, which is not
    # decidable and is what monomorphization gave up on, but how wide it is and
    # whether it is a pointer. That is all the backend ever needed. See
    # `turkey/layout.py`.
    layouts: dict[int, str] = field(default_factory=dict)


@dataclass
class CProgram:
    """A whole program: the dictionaries, then the bindings, in run order."""

    dicts: list[CBind] = field(default_factory=list)
    binds: list[CBind] = field(default_factory=list)


# A class's dictionary type is named after the class, and this is that naming.
# Not a prefix constant: the encoding is a *bijection*, and it was spread over
# a maker in `lower.dict_con` and seven hand-written `startswith`/slice pairs
# in five other modules -- so every consumer knew how the name is built, and
# changing it would have meant finding all of them. `%` cannot start a source
# identifier, so none of these can collide with a declared type.
_DICT_PREFIX = "%Dict."


def dict_name(cls: str) -> str:
    """The name of the dictionary type for `cls`."""
    return _DICT_PREFIX + cls


def class_of_dict(name: str) -> str | None:
    """The class a dictionary type is for, given its name, or `None`."""
    if not name.startswith(_DICT_PREFIX):
        return None
    return name[len(_DICT_PREFIX):]


def dict_class(ty: Type) -> str | None:
    """The class `ty` is the dictionary of, or `None` if it is not one.

    Saturated: `dict_con` gives the constructor kind `k -> *`, so an
    unapplied one is not the type of any value. Every caller wanted that and
    two of the three checked it separately.
    """
    head, args = spine(prune(ty))
    if not isinstance(head, TCon) or len(args) != 1:
        return None
    return class_of_dict(head.name)


def is_dictionary(ty: Type) -> bool:
    """Whether `ty` is a `%Dict.C a`."""
    return dict_class(ty) is not None


def abstraction_parameters(value) -> list[CParam]:
    """Every parameter of a binding's own abstraction, not the first lambda's.

    Elaboration gives a constrained binding more than one lambda: the
    dictionaries in the outer, the value parameters in another inside it. A
    reader that stops at the first therefore sees a constrained function's
    dictionaries and *nothing else*.

    The spine only. A lambda deeper in the body is a closure the body makes
    for itself, and what that may take apart is decided by what the binding
    already holds.
    """
    out: list[CParam] = []
    while isinstance(value, (CLam, CTyLam)):
        if isinstance(value, CLam):
            out.extend(value.params)
        value = value.body
    return out


def transparent_parameters(bind: CBind, abstracted: set[int]) -> list[CParam]:
    """The parameters through which `bind` could take polymorphic data apart.

    A generic body may hold a value of an abstracted type and pass it on, and
    nothing else: that is parametricity, and it is why a bare `a` parameter is
    always fine. What it may not do is *destructure* one, because the layout
    it would read a field at is decided here while the layout the field was
    written at is decided at the construction site. So the parameter to look
    at is a *transparent* one -- a type that mentions an abstracted variable
    without being one. `Array a` is transparent and `a` is not.

    A dictionary is exempt. It is a record of closures, and passing polymorphic
    data to the closures inside it is the mechanism the design rests on rather
    than a leak.

    One definition, because there were two and they were wrong together.
    `mono.check_layouts` asks this to *refuse* a program and `layout.share`
    asks it to decide which bindings need a copy per layout -- a producer and
    its checker, which is worth having only while both mean the same thing by
    it. Both used to read the outermost lambda alone, so both were blind to
    every constrained binding, and `Data.Map#findSlot[Eq k]` was neither
    shared nor refused (FINDINGS 53). They differ in *which* variables count as
    abstracted, which is the argument, and in nothing else.
    """
    if not bind.binders or not isinstance(bind.value, CLam) or not abstracted:
        return []
    found = []
    for param in abstraction_parameters(bind.value):
        ty = prune(param.ty)
        if isinstance(ty, TVar) or is_dictionary(ty):
            continue
        if {variable.id for variable in vars_of(ty)} & abstracted:
            found.append(param)
    return found


def names_of(e) -> set[str]:
    """Every term name mentioned anywhere inside a term.

    Deliberately an over-approximation: a local that happens to share a
    top-level binding's name counts as a mention of it. Being wrong in this
    direction costs a binding kept alive or a call left un-inlined; being wrong
    in the other costs a program that does not run.

    Here rather than in one of its callers because it has three -- dead-code
    elimination in `mono.py`, the call graph the loop breakers come out of in
    `opt.py`, and `joins.py` -- and a fact about a term belongs with the term.
    """
    out: set[str] = set()

    def walk(v) -> None:
        if isinstance(v, CVar):
            out.add(v.name)
        if isinstance(v, (CExpr, CBind, CAlt)):
            for f in fields(v):
                walk(getattr(v, f.name))
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    walk(e)
    return out


# --------------------------------------------------------------- printing


def show_expr(e: CExpr | None, indent: int = 0,
              names: dict[int, str] | None = None,
              alias=None) -> str:
    """A readable rendering, for `turkey core` and for tests.

    Types are shown where they are decided -- at a binder, at a type
    application -- and not on every node, which would bury the term in its own
    annotations. The checker reads the tree, not this.
    """
    pad = "  " * indent
    if e is None:
        return f"{pad}<none>"
    if isinstance(e, CLit):
        return f"{pad}{literal_text(e.kind, e.value)}"
    if isinstance(e, CUnit):
        return f"{pad}()"
    if isinstance(e, CVar):
        return f"{pad}{alias(e.name)}"
    if isinstance(e, CCon):
        return f"{pad}{alias(e.name)}"
    if isinstance(e, CPrim):
        return f"{pad}#{e.name}"
    if isinstance(e, CTuple):
        return f"{pad}({', '.join(show_expr(x, 0, names, alias).strip() for x in e.elems)})"
    if isinstance(e, CArray):
        return f"{pad}[{', '.join(show_expr(x, 0, names, alias).strip() for x in e.elems)}]"
    if isinstance(e, CRecord):
        if not e.fields:
            return f"{pad}{e.con} {{}}"
        out = [f"{pad}{e.con} {{"]
        for name, value in e.fields:
            out.append(f"{pad}  {name} =")
            out.append(show_expr(value, indent + 2, names, alias))
        out.append(f"{pad}}}")
        return "\n".join(out)
    if isinstance(e, CField):
        return f"{pad}{show_expr(e.target, 0, names, alias).strip()}.{alias(e.name)}"
    if isinstance(e, CProject):
        return f"{pad}{show_expr(e.target, 0, names, alias).strip()}.{e.index}"
    if isinstance(e, CIndex):
        return f"{pad}{show_expr(e.target, 0, names, alias).strip()}[{show_expr(e.index, 0, names, alias).strip()}]"
    if isinstance(e, CLam):
        params = ", ".join(f"{alias(p.name)} : {show(p.ty, names)}" for p in e.params)
        return f"{pad}fun({params}) {{\n{show_expr(e.body, indent + 1, names, alias)}\n{pad}}}"
    if isinstance(e, CApp):
        return _call(e.fn, "", e.args, pad, indent, names, alias)
    if isinstance(e, CTyLam):
        binders = " ".join(show(b, names) for b in e.binders)
        return f"{pad}/\\{binders}.\n{show_expr(e.body, indent + 1, names, alias)}"
    if isinstance(e, CTyApp):
        args = ", ".join(show(a, names) for a in e.args)
        return f"{pad}{show_expr(e.fn, 0, names, alias).strip()}[{args}]"
    if isinstance(e, CLet):
        forall = (f" forall {' '.join(show(b, names) for b in e.binders)}."
                  if e.binders else "")
        return (f"{pad}let {alias(e.name)} :{forall} {show(e.bound, names)} =\n"
                f"{show_expr(e.value, indent + 1, names, alias)}\n{show_expr(e.body, indent, names, alias)}")
    if isinstance(e, CLetRec):
        out = [f"{pad}letrec"]
        for bind in e.binds:
            forall = (f" forall {' '.join(show(b, names) for b in bind.binders)}."
                      if bind.binders else "")
            out.append(f"{pad}  {alias(bind.name)} :{forall} {show(bind.ty, names)} =")
            out.append(show_expr(bind.value, indent + 2, names, alias))
        out.append(show_expr(e.body, indent, names, alias))
        return "\n".join(out)
    if isinstance(e, CJoin):
        params = ", ".join(f"{alias(p.name)} : {show(p.ty, names)}" for p in e.params)
        kind = "join rec" if e.recursive else "join"
        return (f"{pad}{kind} {alias(e.name)}({params}) : {show(e.ty, names)} = {{\n"
                f"{show_expr(e.body, indent + 1, names, alias)}\n{pad}}}\n"
                f"{show_expr(e.rest, indent, names, alias)}")
    if isinstance(e, CJump):
        return _call(None, f"jump {alias(e.name)}", e.args, pad, indent, names, alias)
    if isinstance(e, CRef):
        return _call(None, "ref", [e.value], pad, indent, names, alias)
    if isinstance(e, CDeref):
        return f"{pad}!{show_expr(e.target, 0, names, alias).strip()}"
    if isinstance(e, CAssign):
        value = show_expr(e.value, 0, names, alias).strip()
        target = show_expr(e.target, 0, names, alias).strip()
        if "\n" in value:
            # An assigned value that is itself a block, which is what a
            # dereference-and-add becomes once the dictionary is inlined.
            return f"{pad}{target} :=\n{show_expr(e.value, indent + 1, names, alias)}"
        return f"{pad}{target} := {value}"
    if isinstance(e, CIf):
        cond = show_expr(e.cond, 0, names, alias).strip()
        if "\n" in cond:
            # A condition that is itself a block -- which is what `&&` lowers
            # to, and what inlining leaves behind. Flat, it would put a `let`
            # at column zero between the `if` and its brace.
            out = [f"{pad}if", show_expr(e.cond, indent + 1, names, alias),
                   f"{pad}{{", show_expr(e.then, indent + 1, names, alias)]
        else:
            out = [f"{pad}if {cond} {{",
                   show_expr(e.then, indent + 1, names, alias)]
        if e.otherwise is not None:
            out.append(f"{pad}}} else {{")
            out.append(show_expr(e.otherwise, indent + 1, names, alias))
        out.append(f"{pad}}}")
        return "\n".join(out)
    if isinstance(e, CMatch):
        out = [f"{pad}match {show_expr(e.scrutinee, 0, names, alias).strip()} {{"]
        for alt in e.alts:
            out.append(f"{pad}  {_pattern(alt.pat)} ->")
            out.append(show_expr(alt.body, indent + 2, names, alias))
        out.append(f"{pad}}}")
        return "\n".join(out)
    raise AssertionError(f"unprintable Core node {type(e).__name__}")


def _call(fn, name: str, args, pad: str, indent: int, names, alias) -> str:
    """`fn(args)` or `name(args)`, on one line when every argument fits on one.

    An argument that spans lines -- a lambda, since that is what a `?` makes of
    the rest of a block, or a `let` chain, since that is what inlining makes of
    a call -- gets a line of its own, indented under the call. Rendering it
    flat would put a function body at column zero in the middle of a nested
    expression, which is what `turkey opt` output looked like before this.
    """
    # Arguments before the head, which matters: `show` numbers type variables
    # in the order it meets them, so rendering the two the other way round
    # renames every variable in the program and moves every `.core` golden.
    rendered = [show_expr(a, 0, names, alias).strip() for a in args]
    head = name if fn is None else show_expr(fn, 0, names, alias).strip()
    if not any("\n" in a for a in rendered):
        return f"{pad}{head}({', '.join(rendered)})"
    out = [f"{pad}{head}("]
    for i, arg in enumerate(args):
        tail = "," if i < len(args) - 1 else ""
        out.append(show_expr(arg, indent + 1, names, alias) + tail)
    out.append(f"{pad})")
    return "\n".join(out)


def _pattern(pat) -> str:
    """Patterns are `ast.Pattern`, so their rendering is small and local."""
    from . import ast
    if isinstance(pat, ast.PVar):
        return pat.name
    if isinstance(pat, ast.PWild):
        return "_"
    if isinstance(pat, ast.PLit):
        return literal_text(pat.kind, pat.value)
    if isinstance(pat, ast.PCon):
        if not pat.args:
            return pat.name
        return f"{pat.name}({', '.join(_pattern(a) for a in pat.args)})"
    if isinstance(pat, ast.PRecord):
        inner = ", ".join(f"{n} = {_pattern(p)}" for n, p in pat.fields)
        return f"{pat.name} {{ {inner} }}"
    if isinstance(pat, ast.PTuple):
        return f"({', '.join(_pattern(p) for p in pat.elems)})"
    if isinstance(pat, ast.PAnnot):
        return _pattern(pat.pat)
    raise AssertionError(f"unprintable pattern {type(pat).__name__}")


def show_program(program: CProgram, module: str | None = None) -> str:
    """The whole program, or just one module's part of it.

    `module` is what `turkey core` passes: a reader wants their own file's
    elaboration, not the Prelude's four hundred lines of it, exactly as
    `turkey types` prints one module's signatures.
    """
    out = []
    for group, title in ((program.dicts, "dictionaries"), (program.binds, "bindings")):
        chosen = [b for b in group if module is None or b.module == module]
        if not chosen:
            continue
        out.append(f"-- {title}")
        for bind in chosen:
            out.append(show_bind(bind))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


_GENERATED = re.compile(r"^%([A-Za-z]+)(\d+)(\..*)?$")


class _Aliases:
    """Renumbers the binders a pass invented, per binding, in encounter order.

    `%seq24` and `%d52.Show` carry a counter that is global to a whole
    compilation, so adding a generated name anywhere renumbers every name after
    it. That makes a `.core` golden churn on changes it has nothing to do with,
    and the numbers mean nothing to a reader anyway -- they exist to be
    unique, and they still are after this.

    The same decision the printer already makes for type variables, which are
    named `a, b, c` in encounter order rather than by their global ids.
    """

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}
        self.counts: dict[tuple[str, str], int] = {}

    def __call__(self, name: str) -> str:
        found = self.seen.get(name)
        if found is not None:
            return found
        match = _GENERATED.match(name)
        if match is None:
            self.seen[name] = name
            return name
        hint, _, suffix = match.groups()
        key = (hint, suffix or "")
        self.counts[key] = self.counts.get(key, 0) + 1
        out = f"%{hint}{self.counts[key]}{suffix or ''}"
        self.seen[name] = out
        return out


def show_bind(bind: CBind) -> str:
    """One binding. The `names` dictionary starts here and is shared by every
    type printed under it, so the `a` a binder introduces is the `a` its body
    mentions -- which is the whole reason a reader can follow a type variable
    from a `forall` to the parameter that uses it."""
    names: dict[int, str] = {}
    alias = _Aliases()
    binders = " ".join(show(b, names) for b in bind.binders)
    forall = f"forall {binders}. " if bind.binders else ""
    return (f"{bind.name} : {forall}{show(bind.ty, names)} =\n"
            f"{show_expr(bind.value, 1, names, alias)}")


__all__ = [
    "CAlt", "CApp", "CArray", "CAssign", "CBind", "CCon",
    "CDeref", "CExpr", "CField", "CIf",
    "CIndex", "CJoin", "CJump", "CLam", "CLet", "CLetRec", "CLit",
    "CMatch", "CParam",
    "CPrim", "CProgram", "CProject", "CRecord", "CRef", "CTuple", "CTyApp",
    "CTyLam", "CUnit", "CVar", "REF", "TAIL_FIELDS", "is_ref",
    "abstraction_parameters", "class_of_dict", "dict_class",
    "dict_name", "is_dictionary",
    "names_of", "ref_elem", "ref_of", "transparent_parameters",
    "show_bind", "show_expr", "show_program",
]
