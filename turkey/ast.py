"""Abstract syntax for turkey-lite (design.md section 3).

Nodes are plain mutable dataclasses with identity equality: the type checker
annotates some of them in place, and nothing ever compares two nodes by value.
Every node carries the span of its first token for error reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import Span


@dataclass(eq=False)
class Node:
    span: Span


# ---------------------------------------------------------------- type syntax
# These mirror what the author wrote. `turkey.types` holds the semantic types
# that inference actually manipulates; `infer` translates between the two.


@dataclass(eq=False)
class TypeExpr(Node):
    pass


@dataclass(eq=False)
class TEVar(TypeExpr):
    """A lowercase name in type position: a type variable."""

    name: str


@dataclass(eq=False)
class TECon(TypeExpr):
    """`Int`, `Array a`, `Stack a` -- a type constructor applied to zero or more args."""

    name: str
    args: list[TypeExpr] = field(default_factory=list)


@dataclass(eq=False)
class TEApp(TypeExpr):
    """`f a` -- an application whose head is a type *variable*.

    A named head keeps its own node (`TECon`) because it is also the thing an
    alias is looked up by, and an alias must be saturated where a constructor
    need not be. Only a variable head needs this form, and only since M4 gave
    variables kinds to be applied at.
    """

    fn: TypeExpr
    args: list[TypeExpr]


@dataclass(eq=False)
class TETuple(TypeExpr):
    elems: list[TypeExpr]


@dataclass(eq=False)
class TEFun(TypeExpr):
    """`fun(t1, ..., tn) -> t` -- uncurried, fixed arity."""

    params: list[TypeExpr]
    ret: TypeExpr


# -------------------------------------------------------------------- patterns


@dataclass(eq=False)
class Pattern(Node):
    pass


@dataclass(eq=False)
class PVar(Pattern):
    name: str


@dataclass(eq=False)
class PWild(Pattern):
    pass


@dataclass(eq=False)
class PCon(Pattern):
    """Positional constructor pattern, `C p1 ... pn` or `C(p1, ..., pn)`."""

    name: str
    args: list[Pattern] = field(default_factory=list)


@dataclass(eq=False)
class PRecord(Pattern):
    """`C { f = p, g }` -- the punning form `g` is expanded to `g = g` by the parser."""

    name: str
    fields: list[tuple[str, Pattern]]


@dataclass(eq=False)
class PLit(Pattern):
    kind: str  # Int | Float | String | Char | Bool
    value: object


@dataclass(eq=False)
class PTuple(Pattern):
    elems: list[Pattern]


@dataclass(eq=False)
class PAnnot(Pattern):
    pat: Pattern
    type_expr: TypeExpr


# ----------------------------------------------------------------- expressions


@dataclass(eq=False)
class Expr(Node):
    pass


@dataclass(eq=False)
class ELit(Expr):
    kind: str  # Int | Float | String | Char | Bool
    value: object


@dataclass(eq=False)
class EUnit(Expr):
    """`()` -- the sole value of type Unit. See SPEC-DELTAS.md entry 3."""


@dataclass(eq=False)
class EVar(Expr):
    name: str
    # True when the parser wrote this node itself to mean a *class method* --
    # the `add` an `+` desugars to. Module resolution leaves such a node alone,
    # so a module that defines its own `add` shadows the Prelude's for ordinary
    # calls without quietly capturing every `+` in the file (M11a).
    method: bool = False
    # `evidence.Use`: the dictionaries this occurrence needs. Attached by the
    # generator, filled in by the solver and the elaborator. A name with no
    # class predicates keeps `None` and costs nothing.
    use: object | None = None


@dataclass(eq=False)
class ECon(Expr):
    """A constructor in expression position. Nullary ones stand alone; the rest
    are applied, and are typed as ordinary functions."""

    name: str


@dataclass(eq=False)
class ETuple(Expr):
    elems: list[Expr]


@dataclass(eq=False)
class EArray(Expr):
    """`[e1, ..., en]`. Desugaring lives in the evaluator (section 6.6)."""

    elems: list[Expr]


@dataclass(eq=False)
class ERecord(Expr):
    """`C { f = e, ... }` -- labeled construction."""

    con: str
    fields: list[tuple[str, Expr]]


@dataclass(eq=False)
class ELambda(Expr):
    params: list[Pattern]
    ret: TypeExpr | None
    body: Expr


@dataclass(eq=False)
class ECall(Expr):
    fn: Expr
    args: list[Expr]


@dataclass(eq=False)
class EIndex(Expr):
    arr: Expr
    index: Expr
    get_fn: EVar | None = None
    set_fn: EVar | None = None


@dataclass(eq=False)
class EField(Expr):
    obj: Expr
    name: str


@dataclass(eq=False)
class EUnary(Expr):
    op: str  # ! | -
    operand: Expr
    # `-x` is `neg(x)`; `!x` is not a method and leaves this `None`. The parser
    # fills it in, so the operator is still there to blame in a message while
    # every later stage sees an ordinary use of a class method (M8).
    fn: EVar | None = None


@dataclass(eq=False)
class EBinary(Expr):
    op: str
    left: Expr
    right: Expr
    fn: EVar | None = None  # the method this operator means; see EUnary.fn


@dataclass(eq=False)
class EAnnot(Expr):
    expr: Expr
    type_expr: TypeExpr


@dataclass(eq=False)
class EIf(Expr):
    cond: Expr
    then: Expr  # always a block
    otherwise: Expr | None  # a block, or a nested EIf


@dataclass(eq=False)
class EWhile(Expr):
    cond: Expr
    body: Expr


@dataclass(eq=False)
class EForIn(Expr):
    pat: Pattern
    iterable: Expr
    body: Expr
    # The two `Iterator` methods the loop runs on, as ordinary uses, so that
    # solving demands `Iterator` of the sequence and elaboration hands the loop
    # its dictionary without the loop being a special case anywhere (M8).
    iter_fn: EVar | None = None
    next_fn: EVar | None = None


@dataclass(eq=False)
class EForC(Expr):
    """C-style `for init; cond; step { body }`."""

    init: Stmt | None
    cond: Expr
    step: Stmt | None
    body: Expr


@dataclass(eq=False)
class ELoop(Expr):
    body: Expr


@dataclass(eq=False)
class MatchArm(Node):
    patterns: list[Pattern]
    body: Expr


@dataclass(eq=False)
class EMatch(Expr):
    scrutinee: Expr
    arms: list[MatchArm]


@dataclass(eq=False)
class EReturn(Expr):
    value: Expr | None


@dataclass(eq=False)
class EBreak(Expr):
    value: Expr | None


@dataclass(eq=False)
class EContinue(Expr):
    pass


@dataclass(eq=False)
class EBlock(Expr):
    stmts: list[Stmt]


@dataclass(eq=False)
class EQuestion(Expr):
    """`e?` -- the instance's `bind`, with the rest of the block as its
    continuation (SPEC-DELTAS.md 46).

    Unlike every other sugar in this file, this one cannot be a node the later
    stages interpret: what `?` binds is *the rest of the enclosing statement
    sequence*, which no locally annotated node can name. So `turkey/desugar.py`
    rewrites it away before anything else runs, and `bind_fn` is the marked
    method reference it rewrites to -- the same trick `EBinary.fn` uses to keep
    `+` meaning `Add.add` in a module that defines its own `add`.

    That rewrite is the *only* lowering of `?`, and this note used to say
    otherwise: it promised a second, join-point-aware lowering off the same
    sugared tree, with this one kept as the oracle to differential-test it
    against. `plan.txt` item 4 has since deleted that plan. Nothing about `?`
    is lowered twice, and nothing about a monad is known below `desugar.py`;
    join points arrive instead as a general Core pass under item 7, which
    reaches a `bind` chain the way it reaches any other saturated call to a
    known small function.
    """
    expr: Expr
    bind_fn: EVar | None = None


@dataclass(eq=False)
class EDo(Expr):
    """`do { ... }` -- says which block a `?` inside it unwinds to.

    It is only a marker. A `do` containing no `?` emits nothing at all and means
    exactly the block it wraps, which is why a `?`-free `do` cannot be ambiguous
    about its monad: it does not have one.
    """
    body: EBlock


# ------------------------------------------------------------------ statements


@dataclass(eq=False)
class Stmt(Node):
    pass


@dataclass(eq=False)
class SLet(Stmt):
    pat: Pattern
    value: Expr
    # `evidence.Abstraction`, if this binding generalizes. See `FunDecl.dicts`.
    dicts: object | None = None


@dataclass(eq=False)
class SVar(Stmt):
    pat: Pattern
    value: Expr


@dataclass(eq=False)
class SFun(Stmt):
    decl: FunDecl


@dataclass(eq=False)
class SAssign(Stmt):
    """`x = e`, `r.f = e`, `a[i] = e`. Target is EVar, EField or EIndex."""

    target: Expr
    value: Expr


@dataclass(eq=False)
class SExpr(Stmt):
    expr: Expr


# ---------------------------------------------------------------- declarations


@dataclass(eq=False)
class ClassPred(Node):
    """One constraint as written: `Ord a`, `Monoid m`, `Functor (Either l)`.

    The same node serves every position a constraint appears in -- a `fun`'s
    `[...]` context, a class's superclass list, an instance's context -- because
    they all mean the same thing and differ only in where they are attached.
    """

    name: str
    arg: TypeExpr


@dataclass(eq=False)
class EqPred(Node):
    """One equality as written: `Item c ~ Op` (delta 39).

    A family application is a *function* on types, so an equality is how a
    context says what that function answers -- the thing a class predicate
    cannot say, because `Item c` is not a class. The left side is required to
    be a family application; see `Classes.resolve_context` for why.
    """

    left: TypeExpr
    right: TypeExpr


@dataclass(eq=False)
class FunDecl(Node):
    """A function, or -- with `body is None` -- a bare signature.

    A signature is legal only inside a `class`, and there its parameters are
    *types*, not binders. They are still stored as patterns, because a
    parameter of a stated type with no name is exactly `PAnnot(PWild, ty)`, and
    keeping one representation means the method's type is read off a signature
    and off a defaulted method by the same code. See `Parser.parse_fun_decl`
    for why the two readings cannot be mixed within one declaration.
    """

    name: str
    params: list[Pattern]
    ret: TypeExpr | None
    body: Expr | None
    context: list[ClassPred | EqPred] = field(default_factory=list)
    # `evidence.Abstraction`: the leading dictionary parameters this
    # declaration gained, if its scheme retained any class predicate. Written
    # by the solver, at the one place it decides what a scheme carries.
    dicts: object | None = None
    # Set on a helper this compiler invented for a binding with exactly one
    # use site: it is not generalized. Nothing a program can write sets it --
    # `turkey/desugar.py` is the only writer, for the recursive function a
    # lifted loop becomes -- and it is a fact about *that* declaration, not a
    # policy about local bindings, which is why it is a field here rather than
    # a rule in `turkey/infer.py` about what a name looks like.
    monomorphic: bool = False

    @property
    def is_signature(self) -> bool:
        return self.body is None


@dataclass(eq=False)
class ConDecl(Node):
    """One variant of a data type. Exactly one of `args` / `fields` is meaningful."""

    name: str
    args: list[TypeExpr] = field(default_factory=list)
    fields: list[tuple[str, TypeExpr]] | None = None

    @property
    def is_record(self) -> bool:
        return self.fields is not None

    @property
    def arity(self) -> int:
        return len(self.fields) if self.fields is not None else len(self.args)


@dataclass(eq=False)
class TypeDecl(Node):
    name: str
    params: list[str]
    variants: list[ConDecl] | None  # None for an alias
    alias: TypeExpr | None  # None for a data type

    @property
    def is_alias(self) -> bool:
        return self.alias is not None

    @property
    def is_mutable_record(self) -> bool:
        """Section 4.5: single-variant record types are the mutable ones."""
        return (
            self.variants is not None
            and len(self.variants) == 1
            and self.variants[0].is_record
        )


@dataclass(eq=False)
class FamDecl(Node):
    """`type Elem c` inside a class: an associated type family.

    `param` must be the class's own parameter. A family is a function on types
    determined by that parameter -- there is nothing else in scope for it to be
    a function of -- so its arity is one and its argument is named rather than
    implied, the same way a superclass names the parameter it constrains.
    """

    name: str
    param: str


@dataclass(eq=False)
class FamBind(Node):
    """`type Elem = a` inside an instance: what the family is, here."""

    name: str
    body: TypeExpr


@dataclass(eq=False)
class ClassDecl(Node):
    """`class C a : Super a, ... { methods }`.

    Superclasses use `:` rather than the `[...]` a `fun` writes. The positions
    are disjoint so nothing is ambiguous, and the split keeps `[...]` meaning
    exactly one thing: a context on a *value*'s type.
    """

    name: str
    param: str
    supers: list[ClassPred]
    methods: list[FunDecl]  # signatures, and defaulted methods with bodies
    families: list[FamDecl] = field(default_factory=list)


@dataclass(eq=False)
class InstanceDecl(Node):
    """`instance [context] C head { methods }`.

    `head` is an `atype`, so a partial application parenthesizes:
    `instance Functor (Either l)`.
    """

    cls: str
    head: TypeExpr
    context: list[ClassPred | EqPred]
    methods: list[FunDecl]
    families: list[FamBind] = field(default_factory=list)


@dataclass(eq=False)
class ExportItem(Node):
    """One entry of an export list, or of an import's `(...)` / `hiding (...)`.

    `kind` is `"name"` for `f`, `T(A, B)` and `T(..)`, and `"module"` for
    design.md section 3.1's `module M` re-export -- which is a different thing
    entirely: it names no entity of its own, it says "everything already in
    scope here under that qualification, passed on".
    """

    name: str
    kind: str = "name"  # name | module
    subs: list[str] | None = None  # T(A, B); `None` for `T`, `[".."]` for T(..)


@dataclass(eq=False)
class ModuleHeader(Node):
    name: str
    exports: list[ExportItem] | None


@dataclass(eq=False)
class ImportDecl(Node):
    name: str
    alias: str | None
    items: list[ExportItem] | None
    hiding: list[ExportItem] | None
    qualified: bool


@dataclass(eq=False)
class Program(Node):
    header: ModuleHeader | None
    imports: list[ImportDecl]
    decls: list[Stmt | TypeDecl | ClassDecl | InstanceDecl]
