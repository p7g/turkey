"""A deliberately naive reference type checker, for differential testing.

The real inferencer is Algorithm J: type variables are mutable cells and
generalization is a level comparison (Remy's trick). That is fast, but the
level bookkeeping is exactly the kind of thing that goes subtly wrong -- a
variable whose level is not lowered when it should be gets generalized when it
should not, and the result is a program that wrongly typechecks rather than one
that crashes.

So this module computes the same answer the slow, obvious way: an explicit
substitution that is threaded rather than written into the types, and
generalization by *scanning the environment* for free variables instead of
comparing levels. `fv(env)` is the definition levels are an optimization of, so
if the two disagree the level bookkeeping is wrong.

It covers only the fragment `tests/test_infer_reference.py` generates -- no
bottom, no loops, no records, no annotations -- and raises `Unsupported` for
anything else, so it can never silently agree by skipping the hard part.

References: Milner (JCSS 17(3), 1978) for W and J; Damas & Milner (POPL '82)
for W's completeness; Pottier & Remy (ATTAPL ch. 10) for the modern treatment.
"""

from __future__ import annotations

from turkey import ast
from turkey.parser import parse
from turkey.types import (
    BOOL, CHAR, FLOAT, INT, STRING, Scheme, TCon, TFun, TTuple, TVar, Type,
    vars_of,
)

LITERALS = {"Int": INT, "Float": FLOAT, "String": STRING, "Char": CHAR, "Bool": BOOL}

Subst = dict[int, Type]
Env = dict[str, Scheme]


class RefError(Exception):
    """A type error, as this checker sees it. Only its existence is compared."""


class Unsupported(Exception):
    """Outside the fragment. Never treated as a type error -- the test skips."""


# ------------------------------------------------------------------ substitution


def resolve(t: Type, s: Subst) -> Type:
    """Follow the substitution at the top level only."""
    while isinstance(t, TVar) and t.id in s:
        t = s[t.id]
    return t


def substitute(t: Type, s: Subst) -> Type:
    """Apply the substitution everywhere, building a fresh type."""
    t = resolve(t, s)
    if isinstance(t, TCon):
        return TCon(t.name, [substitute(a, s) for a in t.args])
    if isinstance(t, TFun):
        return TFun([substitute(p, s) for p in t.params], substitute(t.ret, s))
    if isinstance(t, TTuple):
        return TTuple([substitute(e, s) for e in t.elems])
    return t


def occurs(var: int, t: Type, s: Subst) -> bool:
    return var in {v.id for v in vars_of(substitute(t, s))}


def unify(a: Type, b: Type, s: Subst) -> None:
    """Extend `s` so that `a` and `b` agree. Raises `RefError` if they cannot.

    Note what is missing compared to `turkey.types.unify`: no level adjustment,
    because there are no levels here. That is the whole point.
    """
    a, b = resolve(a, s), resolve(b, s)

    if a is b:
        return
    if isinstance(a, TVar):
        if occurs(a.id, b, s):
            raise RefError("infinite type")
        s[a.id] = b
        return
    if isinstance(b, TVar):
        return unify(b, a, s)

    if isinstance(a, TCon) and isinstance(b, TCon):
        if a.name != b.name or len(a.args) != len(b.args):
            raise RefError(f"{a.name} vs {b.name}")
        for x, y in zip(a.args, b.args):
            unify(x, y, s)
        return
    if isinstance(a, TFun) and isinstance(b, TFun):
        if len(a.params) != len(b.params):
            raise RefError("arity")
        for x, y in zip(a.params, b.params):
            unify(x, y, s)
        unify(a.ret, b.ret, s)
        return
    if isinstance(a, TTuple) and isinstance(b, TTuple):
        if len(a.elems) != len(b.elems):
            raise RefError("tuple width")
        for x, y in zip(a.elems, b.elems):
            unify(x, y, s)
        return
    raise RefError(f"{type(a).__name__} vs {type(b).__name__}")


# ---------------------------------------------------------------- schemes


def free_in_env(env: Env, s: Subst) -> set[int]:
    """Every variable the environment still holds a claim on.

    This is the scan levels replace. A variable in here is one some enclosing
    binding may still constrain, so it must not be quantified.
    """
    out: set[int] = set()
    for scheme in env.values():
        bound = {v.id for v in scheme.quantified}
        out |= {v.id for v in vars_of(substitute(scheme.body, s))} - bound
    return out


def generalize(t: Type, env: Env, s: Subst) -> Scheme:
    """Quantify what the environment does not hold.

    The quantified list is ordered by first occurrence in the body, matching
    `turkey.types.generalize`, so that `show_scheme` names the variables the
    same way in both checkers and the rendered types can be compared directly.
    """
    body = substitute(t, s)
    held = free_in_env(env, s)
    return Scheme([v for v in vars_of(body) if v.id not in held], body)


def instantiate(scheme: Scheme) -> Type:
    if not scheme.quantified:
        return scheme.body
    mapping: Subst = {v.id: TVar(0) for v in scheme.quantified}
    return substitute(scheme.body, mapping)


def nonexpansive(e: ast.Expr) -> bool:
    """The value restriction, over this fragment (design.md section 4.4)."""
    if isinstance(e, (ast.ELit, ast.EUnit, ast.EVar, ast.ELambda)):
        return True
    if isinstance(e, ast.ETuple):
        return all(nonexpansive(x) for x in e.elems)
    return False


# ---------------------------------------------------------------- inference


def infer(e: ast.Expr, env: Env, s: Subst) -> Type:
    if isinstance(e, ast.ELit):
        return LITERALS[e.kind]

    if isinstance(e, ast.EUnit):
        return TCon("Unit")

    if isinstance(e, ast.EVar):
        if e.name not in env:
            raise RefError(f"unbound {e.name}")
        return instantiate(env[e.name])

    if isinstance(e, ast.ELambda):
        if e.ret is not None:
            raise Unsupported("annotated lambda")
        scope = dict(env)
        params: list[Type] = []
        for p in e.params:
            if not isinstance(p, ast.PVar):
                raise Unsupported("non-variable parameter")
            tv = TVar(0)
            params.append(tv)
            scope[p.name] = Scheme([], tv)
        return TFun(params, infer(e.body, scope, s))

    if isinstance(e, ast.ECall):
        fn = infer(e.fn, env, s)
        args = [infer(a, env, s) for a in e.args]
        result = TVar(0)
        unify(fn, TFun(args, result), s)
        return result

    if isinstance(e, ast.ETuple):
        return TTuple([infer(x, env, s) for x in e.elems])

    if isinstance(e, ast.EBlock):
        scope = dict(env)
        result: Type = TCon("Unit")
        for stmt in e.stmts:
            result = infer_stmt(stmt, scope, s)
        return result

    raise Unsupported(type(e).__name__)


def infer_stmt(stmt: ast.Stmt, env: Env, s: Subst) -> Type:
    """Infer a statement in `env`, which it may extend. Returns its value."""
    if isinstance(stmt, ast.SExpr):
        return infer(stmt.expr, env, s)

    if isinstance(stmt, ast.SLet):
        if not isinstance(stmt.pat, ast.PVar):
            raise Unsupported("destructuring let")
        value = infer(stmt.value, env, s)
        env[stmt.pat.name] = (
            generalize(value, env, s) if nonexpansive(stmt.value)
            else Scheme([], substitute(value, s))
        )
        return TCon("Unit")

    if isinstance(stmt, ast.SFun):
        infer_fun(stmt.decl, env, s)
        return TCon("Unit")

    raise Unsupported(type(stmt).__name__)


def infer_fun(decl: ast.FunDecl, env: Env, s: Subst) -> None:
    """A `fun` binds monomorphically while its own body is checked, then
    generalizes -- so it may recurse, but only at one type."""
    if decl.ret is not None:
        raise Unsupported("annotated return")
    placeholder = TVar(0)
    env[decl.name] = Scheme([], placeholder)

    scope = dict(env)
    params: list[Type] = []
    for p in decl.params:
        if not isinstance(p, ast.PVar):
            raise Unsupported("non-variable parameter")
        tv = TVar(0)
        params.append(tv)
        scope[p.name] = Scheme([], tv)

    body = infer(decl.body, scope, s)
    unify(placeholder, TFun(params, body), s)
    # `fun` is syntactically a value, so the value restriction never applies.
    outer = {k: v for k, v in env.items() if k != decl.name}
    env[decl.name] = generalize(placeholder, outer, s)


def check(src: str) -> list[tuple[str, Scheme]]:
    """Check a program, returning its top-level signatures in source order.

    Items are processed in the order written, so the fragment must not use a
    name before it is bound. Dependency ordering is the real checker's job and
    is not what this module exists to cross-check.
    """
    program = parse(src)
    if program.header is not None or program.imports:
        raise Unsupported("module syntax")
    if any(isinstance(d, ast.TypeDecl) for d in program.decls):
        raise Unsupported("type declaration")

    env: Env = {}
    s: Subst = {}
    names: list[str] = []
    for item in program.decls:
        infer_stmt(item, env, s)
        if isinstance(item, ast.SFun):
            names.append(item.decl.name)
        elif isinstance(item, ast.SLet) and isinstance(item.pat, ast.PVar):
            names.append(item.pat.name)
        else:
            raise Unsupported(type(item).__name__)

    # Re-generalize at the end: a later binding may have constrained an earlier
    # one, and the printed signature has to show that.
    return [(n, Scheme(
        [v for v in vars_of(substitute(env[n].body, s))
         if v.id in {q.id for q in env[n].quantified}],
        substitute(env[n].body, s),
    )) for n in names]
