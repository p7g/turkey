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


@dataclass(eq=False)
class EField(Expr):
    obj: Expr
    name: str


@dataclass(eq=False)
class EUnary(Expr):
    op: str  # ! | -
    operand: Expr


@dataclass(eq=False)
class EBinary(Expr):
    op: str
    left: Expr
    right: Expr


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


# ------------------------------------------------------------------ statements


@dataclass(eq=False)
class Stmt(Node):
    pass


@dataclass(eq=False)
class SLet(Stmt):
    pat: Pattern
    value: Expr


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
class FunDecl(Node):
    name: str
    params: list[Pattern]
    ret: TypeExpr | None
    body: Expr


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
class ModuleHeader(Node):
    name: str
    exports: list[str] | None


@dataclass(eq=False)
class ImportDecl(Node):
    name: str
    alias: str | None
    items: list[str] | None
    hiding: list[str] | None
    qualified: bool


@dataclass(eq=False)
class Program(Node):
    header: ModuleHeader | None
    imports: list[ImportDecl]
    decls: list[Stmt | TypeDecl]
