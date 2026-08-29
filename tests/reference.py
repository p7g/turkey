"""A deliberately naive reference type checker, for differential testing.

The real inferencer generalizes by *rank*: variables are pooled by the binder
depth they were created under, and leaving a binder is a walk over the pool
born there (Remy's trick). That is fast, but the bookkeeping is exactly the
kind of thing that goes subtly wrong -- a variable that is not lowered into the
parent pool when it should be gets generalized when it should not, and the
result is a program that wrongly typechecks rather than one that crashes.

So this module computes the same answer the slow, obvious way: an explicit
substitution that is threaded rather than written into the types, and
generalization by *scanning the environment* for free variables instead of
scanning it. `fv(env)` is the definition ranks are an optimization of, so if
the two disagree the rank bookkeeping is wrong.

It also carries the `HasField` predicate, the second constraint form in the
domain, and settles it the same obvious way: a flat list retried until nothing
more can be discharged, with a predicate travelling into a scheme only when the
environment holds none of its variables. That last test is the naive reading of
the real checker's `pred.level() > level`, which is precisely the seam this
module exists to check.

It covers only the fragment `tests/test_infer_reference.py` generates -- no
bottom, no loops, no polymorphic records, no annotations -- and raises
`Unsupported` for anything else, so it can never silently agree by skipping the
hard part.

References: Milner (JCSS 17(3), 1978) for W and J; Damas & Milner (POPL '82)
for W's completeness; Pottier & Remy (ATTAPL ch. 10) for the modern treatment.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from turkey import ast
from turkey.parser import parse
from turkey.constraints import CPred, reach
from turkey.types import (
    BOOL, CHAR, FLOAT, INT, STRING, Pred, Scheme, TCon, TFun, TLabel, TTuple,
    TVar, Type, type_key, vars_of,
)

LITERALS = {"Int": INT, "Float": FLOAT, "String": STRING, "Char": CHAR, "Bool": BOOL}

Subst = dict[int, Type]
Env = dict[str, Scheme]


@dataclass
class RefPred:
    """`HasField label receiver result`, in the shape this module works with."""

    label: str
    receiver: Type
    result: Type

    def to_pred(self, s: Subst) -> Pred:
        return Pred("HasField", [TLabel(self.label),
                                 substitute(self.receiver, s),
                                 substitute(self.result, s)])


@dataclass
class State:
    """Everything inference threads: the substitution and the pending demands.

    The real checker keeps the second of these in `Solver.deferred` and settles
    it against levels; here it is a flat list settled against `fv(env)`.
    """

    subst: Subst = field(default_factory=dict)
    preds: list[RefPred] = field(default_factory=list)
    records: dict[str, dict[str, Type]] = field(default_factory=dict)


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
        held = vars_of(substitute(scheme.body, s))
        for pred in scheme.preds:
            held += vars_of(*[substitute(a, s) for a in pred.args])
        out |= {v.id for v in held} - bound
    return out


def pred_vars(p: RefPred, s: Subst) -> set[int]:
    return {v.id for v in vars_of(substitute(p.receiver, s), substitute(p.result, s))}


def improve(st: State) -> None:
    """One field of one receiver has one type, so equate the results.

    The counterpart of `Solver.improve`. Written out separately rather than
    imported, since a shared implementation would agree with itself for free.
    """
    seen: dict[tuple, Type] = {}
    for p in st.preds:
        key = (p.label, type_key(substitute(p.receiver, st.subst)))
        if key in seen:
            unify(seen[key], p.result, st.subst)
        else:
            seen[key] = p.result


def discharge(p: RefPred, st: State) -> bool:
    """Settle one demand, or report that its receiver is still unknown."""
    receiver = substitute(p.receiver, st.subst)
    if isinstance(receiver, TVar):
        return False
    if isinstance(receiver, TCon):
        if receiver.name == "Array":
            if p.label not in ("length", "capacity"):
                raise RefError(f"Array has no field {p.label}")
            unify(p.result, INT, st.subst)
            return True
        fields = st.records.get(receiver.name)
        if fields is not None:
            if p.label not in fields:
                raise RefError(f"{receiver.name} has no field {p.label}")
            unify(p.result, fields[p.label], st.subst)
            return True
    raise RefError(f"no field {p.label} on {receiver}")


def settle(st: State) -> None:
    """Retry every demand until a round discharges none of them."""
    while st.preds:
        improve(st)
        rest = [p for p in st.preds if not discharge(p, st)]
        stuck = len(rest) >= len(st.preds)
        st.preds = rest
        if stuck:
            return


def generalize(t: Type, env: Env, st: State) -> Scheme:
    """Quantify what the environment does not hold, with its context.

    The quantified list is ordered by first occurrence in the body, matching
    `turkey.types.generalize`, so that `show_scheme` names the variables the
    same way in both checkers and the rendered types can be compared directly.

    A demand travels only if the environment holds *none* of its variables --
    which is what `pred.level() > level` says, since that level is the minimum
    over the same variables. Of those, a demand belongs to this scheme if it is
    reachable from the quantified variables by following demands, `HasField`
    being a function of its receiver. One that travels but is reachable from
    nothing is stranded: no later unification can name it, so it is an error.
    """
    settle(st)
    body = substitute(t, st.subst)
    held = free_in_env(env, st.subst)
    quantified = [v for v in vars_of(body) if v.id not in held]

    travelling = [p for p in st.preds if not (pred_vars(p, st.subst) & held)]

    # Attribution is *shared* with the real solver rather than reimplemented.
    # This module exists to check one thing -- generalization by scanning the
    # environment against generalization by rank -- and a second copy of a rule
    # with no independent specification would only confirm that the same thing
    # was typed twice. The transitive closure is the subtle part, so it is
    # imported; `improve` and the declaration lookup below stay local because
    # they are four lines each and reimplementing them costs less than the
    # indirection would.
    reachable = reach([CPred(p.to_pred(st.subst)) for p in travelling], [body])

    mine: list[RefPred] = []
    seen: set[tuple] = set()
    for p in travelling:
        if not (pred_vars(p, st.subst) & reachable):
            raise RefError(f"stranded demand for field {p.label}")
        key = type_key(TTuple([TLabel(p.label), substitute(p.receiver, st.subst),
                               substitute(p.result, st.subst)]))
        if key not in seen:
            seen.add(key)
            mine.append(p)

    st.preds = [p for p in st.preds if p not in travelling]
    preds = [p.to_pred(st.subst) for p in mine]
    # A demand may mention a variable the body does not, so quantify over both.
    for p in preds:
        for v in vars_of(*p.args):
            if v.id not in held and all(q.id != v.id for q in quantified):
                quantified.append(v)
    return Scheme(quantified, body, preds)


def instantiate(scheme: Scheme, st: State) -> Type:
    """Instantiate at a use site, re-emitting the scheme's demands."""
    if not scheme.quantified:
        for p in scheme.preds:
            st.preds.append(_as_ref_pred(p))
        return scheme.body
    mapping: Subst = {v.id: TVar(0) for v in scheme.quantified}
    for p in scheme.preds:
        st.preds.append(_as_ref_pred(Pred(p.name, [substitute(a, mapping) for a in p.args])))
    return substitute(scheme.body, mapping)


def _as_ref_pred(p: Pred) -> RefPred:
    label, receiver, result = p.args
    assert isinstance(label, TLabel)
    return RefPred(label.name, receiver, result)


def nonexpansive(e: ast.Expr) -> bool:
    """The value restriction, over this fragment (design.md section 4.4)."""
    if isinstance(e, (ast.ELit, ast.EUnit, ast.EVar, ast.ELambda)):
        return True
    if isinstance(e, ast.ETuple):
        return all(nonexpansive(x) for x in e.elems)
    return False


# ---------------------------------------------------------------- inference


def infer(e: ast.Expr, env: Env, st: State) -> Type:
    if isinstance(e, ast.ELit):
        return LITERALS[e.kind]

    if isinstance(e, ast.EUnit):
        return TCon("Unit")

    if isinstance(e, ast.EVar):
        if e.name not in env:
            raise RefError(f"unbound {e.name}")
        return instantiate(env[e.name], st)

    if isinstance(e, ast.EField):
        receiver = infer(e.obj, env, st)
        result = TVar(0)
        st.preds.append(RefPred(e.name, receiver, result))
        return result

    if isinstance(e, ast.ERecord):
        fields = st.records.get(e.con)
        if fields is None:
            raise Unsupported(f"record {e.con}")
        given = [label for label, _ in e.fields]
        if sorted(given) != sorted(fields):
            raise RefError(f"wrong fields for {e.con}")
        for label, value in e.fields:
            unify(fields[label], infer(value, env, st), st.subst)
        return TCon(e.con)

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
        return TFun(params, infer(e.body, scope, st))

    if isinstance(e, ast.ECall):
        fn = infer(e.fn, env, st)
        args = [infer(a, env, st) for a in e.args]
        result = TVar(0)
        unify(fn, TFun(args, result), st.subst)
        return result

    if isinstance(e, ast.ETuple):
        return TTuple([infer(x, env, st) for x in e.elems])

    if isinstance(e, ast.EBlock):
        scope = dict(env)
        result: Type = TCon("Unit")
        for stmt in e.stmts:
            result = infer_stmt(stmt, scope, st)
        return result

    raise Unsupported(type(e).__name__)


def infer_stmt(stmt: ast.Stmt, env: Env, st: State) -> Type:
    """Infer a statement in `env`, which it may extend. Returns its value."""
    if isinstance(stmt, ast.SExpr):
        return infer(stmt.expr, env, st)

    if isinstance(stmt, ast.SLet):
        if not isinstance(stmt.pat, ast.PVar):
            raise Unsupported("destructuring let")
        value = infer(stmt.value, env, st)
        env[stmt.pat.name] = (
            generalize(value, env, st) if nonexpansive(stmt.value)
            else Scheme([], substitute(value, st.subst))
        )
        return TCon("Unit")

    if isinstance(stmt, ast.SFun):
        infer_fun(stmt.decl, env, st)
        return TCon("Unit")

    raise Unsupported(type(stmt).__name__)


def infer_fun(decl: ast.FunDecl, env: Env, st: State) -> None:
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

    body = infer(decl.body, scope, st)
    unify(placeholder, TFun(params, body), st.subst)
    # `fun` is syntactically a value, so the value restriction never applies.
    outer = {k: v for k, v in env.items() if k != decl.name}
    env[decl.name] = generalize(placeholder, outer, st)


def _record_table(decls: list[ast.TypeDecl]) -> dict[str, dict[str, Type]]:
    """Monomorphic single-variant record declarations, and nothing else.

    Anything with parameters or several variants is outside the fragment: it
    would drag in constructor schemes and pattern matching, neither of which
    this module is trying to cross-check.
    """
    table: dict[str, dict[str, Type]] = {}
    for d in decls:
        if d.is_alias or d.params or len(d.variants or []) != 1:
            raise Unsupported("type declaration")
        con = d.variants[0]
        if not con.is_record or con.name != d.name:
            raise Unsupported("type declaration")
        fields = {}
        for label, te in con.fields:
            if not isinstance(te, ast.TECon) or te.args or te.name not in LITERALS:
                raise Unsupported("field type")
            fields[label] = LITERALS[te.name]
        table[d.name] = fields
    return table


def check(src: str) -> list[tuple[str, Scheme]]:
    """Check a program, returning its top-level signatures in source order.

    Items are processed in the order written, so the fragment must not use a
    name before it is bound. Dependency ordering is the real checker's job and
    is not what this module exists to cross-check.
    """
    program = parse(src)
    if program.header is not None or program.imports:
        raise Unsupported("module syntax")

    st = State(records=_record_table(
        [d for d in program.decls if isinstance(d, ast.TypeDecl)]
    ))
    env: Env = {}
    names: list[str] = []
    for item in program.decls:
        if isinstance(item, ast.TypeDecl):
            continue
        infer_stmt(item, env, st)
        if isinstance(item, ast.SFun):
            names.append(item.decl.name)
        elif isinstance(item, ast.SLet) and isinstance(item.pat, ast.PVar):
            names.append(item.pat.name)
        else:
            raise Unsupported(type(item).__name__)

    settle(st)
    if st.preds:
        raise RefError(f"unsettled demand for field {st.preds[0].label}")

    # Re-generalize at the end: a later binding may have constrained an earlier
    # one, and the printed signature has to show that.
    return [(n, Scheme(
        [v for v in vars_of(substitute(env[n].body, st.subst),
                            *[a for p in env[n].preds for a in p.args])
         if v.id in {q.id for q in env[n].quantified}],
        substitute(env[n].body, st.subst),
        [Pred(p.name, [substitute(a, st.subst) for a in p.args]) for p in env[n].preds],
    )) for n in names]
