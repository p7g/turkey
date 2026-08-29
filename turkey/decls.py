"""Type and constructor declarations, resolved into semantic types.

This is the bridge between what the author wrote (`ast.TypeExpr`) and what
inference manipulates (`turkey.types`). It holds the program's type
constructors, its value constructors, and the translation of type syntax --
including alias expansion, which is transparent per section 7.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import ast
from .errors import Span, TypeError_
from .types import (
    PRIMITIVES, Fresh, Scheme, TCon, TFun, TTuple, TVar, Type, generalize,
    instantiate,
)


@dataclass
class ConInfo:
    """One value constructor."""

    name: str
    tycon: str
    field_names: list[str] | None  # None for the positional form
    arity: int
    scheme: Scheme  # always forall params. fun(args...) -> TCon(tycon, params)

    @property
    def is_record(self) -> bool:
        return self.field_names is not None


@dataclass
class TyconInfo:
    name: str
    params: list[str]
    variants: list[ConInfo] = field(default_factory=list)
    is_alias: bool = False
    alias_body: ast.TypeExpr | None = None
    span: Span | None = None

    @property
    def is_mutable_record(self) -> bool:
        """Section 4.5: exactly one variant, carrying a record payload."""
        return len(self.variants) == 1 and self.variants[0].is_record


class DeclTable:
    def __init__(self) -> None:
        self.tycons: dict[str, TyconInfo] = {}
        self.constructors: dict[str, ConInfo] = {}
        for name in PRIMITIVES:
            self.tycons[name] = TyconInfo(name, [])
        self.tycons["Array"] = TyconInfo("Array", ["a"])

    # -- registration ------------------------------------------------------

    def register_all(self, decls: list[ast.TypeDecl]) -> None:
        """Declare every type before resolving any, so they may refer to each
        other in any order."""
        for d in decls:
            if d.name in PRIMITIVES or d.name == "Array":
                raise TypeError_(f"cannot redefine the built-in type '{d.name}'", d.span)
            if d.name in self.tycons:
                raise TypeError_(f"type '{d.name}' is declared more than once", d.span)
            self.tycons[d.name] = TyconInfo(
                d.name, d.params, [], d.is_alias, d.alias, d.span
            )
        for d in decls:
            if not d.is_alias:
                self._resolve_variants(d)
        for d in decls:
            if d.is_alias:
                self._check_alias_acyclic(d.name, d.name, set())

    def _resolve_variants(self, decl: ast.TypeDecl) -> None:
        info = self.tycons[decl.name]
        # The type's own parameters become variables shared by every variant, so
        # `Some a` and `None` both land in `Option a`.
        tyvars = {p: TVar(1) for p in decl.params}
        result = TCon(decl.name, [tyvars[p] for p in decl.params])

        for con in decl.variants or []:
            if con.name in self.constructors:
                other = self.constructors[con.name].tycon
                raise TypeError_(
                    f"constructor '{con.name}' is already declared by type '{other}'",
                    con.span,
                )
            if con.is_record:
                names = [n for n, _ in con.fields]
                arg_types = [self.to_type(t, tyvars, 1) for _, t in con.fields]
            else:
                names = None
                arg_types = [self.to_type(t, tyvars, 1) for t in con.args]
            scheme = generalize(TFun(arg_types, result), 0)
            cinfo = ConInfo(con.name, decl.name, names, len(arg_types), scheme)
            info.variants.append(cinfo)
            self.constructors[con.name] = cinfo

    def _check_alias_acyclic(self, root: str, name: str, seen: set[str]) -> None:
        """Section 7: type aliases may not be recursive."""
        info = self.tycons.get(name)
        if info is None or not info.is_alias or name in seen:
            return
        seen = seen | {name}
        for referenced in _referenced_tycons(info.alias_body):
            if referenced == root:
                raise TypeError_(f"type alias '{root}' is recursive", info.span)
            self._check_alias_acyclic(root, referenced, seen)

    # -- translation -------------------------------------------------------

    def to_type(self, te: ast.TypeExpr, tyvars: dict[str, TVar], fresh: Fresh) -> Type:
        """Translate type syntax into a semantic type.

        `tyvars` maps annotation type variable names to unification variables and
        is shared across an entire function, so the `a` in a parameter and the
        `a` in a body annotation are the same variable (SPEC-DELTAS.md 13).
        """
        if isinstance(te, ast.TEVar):
            if te.name not in tyvars:
                tyvars[te.name] = fresh()
            return tyvars[te.name]

        if isinstance(te, ast.TETuple):
            return TTuple([self.to_type(e, tyvars, fresh) for e in te.elems])

        if isinstance(te, ast.TEFun):
            return TFun(
                [self.to_type(p, tyvars, fresh) for p in te.params],
                self.to_type(te.ret, tyvars, fresh),
            )

        assert isinstance(te, ast.TECon)
        info = self.tycons.get(te.name)
        if info is None:
            raise TypeError_(f"unknown type '{te.name}'", te.span)
        args = [self.to_type(a, tyvars, fresh) for a in te.args]
        if len(args) != len(info.params):
            raise TypeError_(
                f"type '{te.name}' expects {len(info.params)} argument(s), "
                f"but {len(args)} were given",
                te.span,
            )
        if info.is_alias:
            # Aliases are transparent: expand rather than build a TCon.
            substitution = dict(zip(info.params, args))
            return self._expand_alias(info, substitution, fresh, te.span)
        return TCon(te.name, args)

    def _expand_alias(
        self, info: TyconInfo, substitution: dict[str, Type], fresh: Fresh, span: Span
    ) -> Type:
        # The alias body's own parameter names are bound to the supplied
        # arguments; anything else in it is a fresh variable.
        local: dict[str, TVar] = {}
        body = self.to_type(info.alias_body, local, fresh)
        return _substitute(body, {v.id: substitution[k] for k, v in local.items()
                                  if k in substitution})

    # -- constructor helpers -----------------------------------------------

    def instantiate_con(self, name: str, fresh: Fresh, span: Span) -> TFun:
        info = self.constructors.get(name)
        if info is None:
            raise TypeError_(f"unknown constructor '{name}'", span)
        return instantiate(info.scheme, fresh)  # always a TFun

    def con(self, name: str) -> ConInfo | None:
        return self.constructors.get(name)

    # -- record field lookup -----------------------------------------------

    def record_fields(self, receiver: TCon) -> list[str] | None:
        """`receiver`'s field names, or None if it is not a mutable record."""
        info = self.tycons.get(receiver.name)
        if info is None or not info.is_mutable_record:
            return None
        return info.variants[0].field_names

    def field_type(self, receiver: TCon, label: str) -> Type:
        """The type of `receiver.label`, for a receiver already resolved.

        The receiver's arguments *are* the constructor's parameters, so
        substituting them directly is exact. Instantiating the constructor's
        scheme and unifying its result with the receiver -- which is what
        `HasField` solving would otherwise do -- gives the same type but
        introduces fresh variables first, and unification would then impose
        their level on the receiver's. Solving happens after generation, at
        whatever level the solver has reached rather than the one the field
        access was written at, so that level is not ours to impose.
        """
        con = self.tycons[receiver.name].variants[0]
        body = con.scheme.body
        assert isinstance(body, TFun) and isinstance(body.ret, TCon)
        mapping = {v.id: arg for v, arg in zip(body.ret.args, receiver.args)
                   if isinstance(v, TVar)}
        return _substitute(body.params[con.field_names.index(label)], mapping)


def _substitute(t: Type, mapping: dict[int, Type]) -> Type:
    from .types import prune

    t = prune(t)
    if isinstance(t, TVar):
        return mapping.get(t.id, t)
    if isinstance(t, TCon):
        return TCon(t.name, [_substitute(a, mapping) for a in t.args]) if t.args else t
    if isinstance(t, TFun):
        return TFun([_substitute(p, mapping) for p in t.params], _substitute(t.ret, mapping))
    if isinstance(t, TTuple):
        return TTuple([_substitute(e, mapping) for e in t.elems])
    return t


def _referenced_tycons(te: ast.TypeExpr | None) -> list[str]:
    if te is None:
        return []
    if isinstance(te, ast.TECon):
        return [te.name] + [n for a in te.args for n in _referenced_tycons(a)]
    if isinstance(te, ast.TETuple):
        return [n for e in te.elems for n in _referenced_tycons(e)]
    if isinstance(te, ast.TEFun):
        return [n for p in te.params for n in _referenced_tycons(p)] + _referenced_tycons(te.ret)
    return []
