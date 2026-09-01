"""Free-variable and dependency analysis over turkey-lite ASTs.

Two public entry points:

* ``free_names(node, bound)`` -- the set of unbound variable / constructor names
  referenced anywhere inside ``node``.
* ``sccs(graph)`` -- Tarjan's strongly-connected components, emitted with
  dependencies first.

``pattern_vars`` is exported too as a small shared helper.
"""

from __future__ import annotations

from . import ast

__all__ = ["free_names", "pattern_vars", "sccs"]


# --------------------------------------------------------------------- patterns


def pattern_vars(pat: ast.Pattern) -> set[str]:
    """The variable names a pattern binds."""
    t = type(pat)
    if t is ast.PVar:
        return {pat.name}
    if t is ast.PWild or t is ast.PLit:
        return set()
    if t is ast.PCon:
        out: set[str] = set()
        for arg in pat.args:
            out |= pattern_vars(arg)
        return out
    if t is ast.PRecord:
        out = set()
        for _label, sub in pat.fields:
            out |= pattern_vars(sub)
        return out
    if t is ast.PTuple:
        out = set()
        for elem in pat.elems:
            out |= pattern_vars(elem)
        return out
    if t is ast.PAnnot:
        return pattern_vars(pat.pat)
    raise AssertionError(
        f"pattern_vars: unrecognized pattern node {t.__name__}"
    )


# ------------------------------------------------------------------ free names


def free_names(node, bound: frozenset[str] = frozenset()) -> set[str]:
    """Unbound names referenced inside ``node``.

    ``node`` may be any Expr, Stmt, Pattern, TypeExpr, FunDecl or MatchArm.
    """
    t = type(node)

    # -- categories that contribute nothing -----------------------------------
    if t is ast.ELit or t is ast.EUnit or t is ast.EContinue:
        return set()
    if isinstance(node, ast.TypeExpr):
        return set()
    if isinstance(node, ast.Pattern):
        return set()

    # -- references ---------------------------------------------------------
    if t is ast.EVar or t is ast.ECon:
        return set() if node.name in bound else {node.name}

    # -- straightforward structural descent -------------------------------
    if t is ast.ETuple or t is ast.EArray:
        return _union(node.elems, bound)
    if t is ast.EField:
        return free_names(node.obj, bound)
    if t is ast.EProject:
        return free_names(node.obj, bound)
    if t is ast.ERecord:
        out = set() if node.con in bound else {node.con}
        for _label, value in node.fields:
            out |= free_names(value, bound)
        return out
    if t is ast.ECall:
        return free_names(node.fn, bound) | _union(node.args, bound)
    if t is ast.EIndex:
        return free_names(node.arr, bound) | free_names(node.index, bound)
    if t is ast.EUnary:
        return free_names(node.operand, bound)
    if t is ast.EBinary:
        return free_names(node.left, bound) | free_names(node.right, bound)
    if t is ast.EAnnot:
        return free_names(node.expr, bound)
    if t is ast.EIf:
        out = free_names(node.cond, bound) | free_names(node.then, bound)
        if node.otherwise is not None:
            out |= free_names(node.otherwise, bound)
        return out
    if t is ast.EWhile:
        return free_names(node.cond, bound) | free_names(node.body, bound)
    if t is ast.ELoop:
        return free_names(node.body, bound)
    if t is ast.EReturn or t is ast.EBreak:
        return set() if node.value is None else free_names(node.value, bound)

    # -- binding forms -----------------------------------------------------
    if t is ast.ELambda:
        return free_names(node.body, _extend(bound, _params(node.params)))
    if t is ast.FunDecl:
        # The function's own name is deliberately NOT bound here; the caller
        # decides whether it is recursive.
        return free_names(node.body, _extend(bound, _params(node.params)))
    if t is ast.MatchArm:
        inner = set(bound)
        for p in node.patterns:
            inner |= pattern_vars(p)
        return free_names(node.body, frozenset(inner))
    if t is ast.EMatch:
        out = free_names(node.scrutinee, bound)
        for arm in node.arms:
            out |= free_names(arm, bound)
        return out
    if t is ast.EForIn:
        out = free_names(node.iterable, bound)
        out |= free_names(node.body, _extend(bound, pattern_vars(node.pat)))
        return out
    if t is ast.EForC:
        # init, cond, step and body share one scope; only init introduces
        # bindings that the rest can see.
        current = set(bound)
        out: set[str] = set()
        if node.init is not None:
            free, newly = _free_stmt(node.init, frozenset(current))
            out |= free
            current |= newly
        scope = frozenset(current)
        out |= free_names(node.cond, scope)
        if node.step is not None:
            free, _newly = _free_stmt(node.step, scope)
            out |= free
        out |= free_names(node.body, scope)
        return out
    if t is ast.EBlock:
        return _free_block(node.stmts, bound)

    # -- statements reached directly ------------------------------------
    if isinstance(node, ast.Stmt):
        free, _newly = _free_stmt(node, bound)
        return free

    raise AssertionError(f"free_names: unrecognized node {t.__name__}")


def _union(nodes, bound: frozenset[str]) -> set[str]:
    out: set[str] = set()
    for n in nodes:
        out |= free_names(n, bound)
    return out


def _params(params) -> set[str]:
    out: set[str] = set()
    for p in params:
        out |= pattern_vars(p)
    return out


def _extend(bound: frozenset[str], names: set[str]) -> frozenset[str]:
    return bound | frozenset(names)


def _free_block(stmts, bound: frozenset[str]) -> set[str]:
    """Walk a statement list left to right; each binding is in scope for all
    subsequent statements."""
    current = set(bound)
    out: set[str] = set()
    for stmt in stmts:
        free, newly = _free_stmt(stmt, frozenset(current))
        out |= free
        current |= newly
    return out


def _free_stmt(stmt, bound: frozenset[str]) -> tuple[set[str], set[str]]:
    """Return ``(free_names, newly_bound_names)`` for a single statement."""
    t = type(stmt)
    if t is ast.SLet or t is ast.SVar:
        return free_names(stmt.value, bound), pattern_vars(stmt.pat)
    if t is ast.SFun:
        decl = stmt.decl
        # The local function's own name is in scope for its body (so it may
        # recurse) and for later statements.
        inner = _extend(bound, {decl.name})
        return free_names(decl, inner), {decl.name}
    if t is ast.SAssign:
        # `x = e` still references `x`, so walk the target too.
        return (
            free_names(stmt.target, bound) | free_names(stmt.value, bound),
            set(),
        )
    if t is ast.SExpr:
        return free_names(stmt.expr, bound), set()
    raise AssertionError(
        f"free_names: unrecognized statement node {t.__name__}"
    )


# ------------------------------------------------- strongly-connected components


def sccs(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan's SCC algorithm, iterative.

    ``graph`` maps a name to the set of names it depends on. Edges to names that
    are not keys of ``graph`` are ignored. The returned components are ordered
    dependencies-first: if component A depends on component B, B comes first.
    Names within a component are sorted.
    """
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    tarjan_stack: list[str] = []
    result: list[list[str]] = []
    counter = 0

    def successors(name: str):
        return iter(sorted(graph.get(name, ())))

    for start in graph:
        if start in index_of:
            continue

        index_of[start] = lowlink[start] = counter
        counter += 1
        tarjan_stack.append(start)
        on_stack[start] = True
        work: list[tuple[str, object]] = [(start, successors(start))]

        while work:
            node, it = work[-1]
            descended = False
            for succ in it:  # type: ignore[assignment]
                if succ not in graph:
                    continue
                if succ not in index_of:
                    index_of[succ] = lowlink[succ] = counter
                    counter += 1
                    tarjan_stack.append(succ)
                    on_stack[succ] = True
                    work.append((succ, successors(succ)))
                    descended = True
                    break
                if on_stack.get(succ):
                    if index_of[succ] < lowlink[node]:
                        lowlink[node] = index_of[succ]
            if descended:
                continue

            work.pop()
            if lowlink[node] == index_of[node]:
                component: list[str] = []
                while True:
                    w = tarjan_stack.pop()
                    on_stack[w] = False
                    component.append(w)
                    if w == node:
                        break
                component.sort()
                result.append(component)
            if work:
                parent = work[-1][0]
                if lowlink[node] < lowlink[parent]:
                    lowlink[parent] = lowlink[node]

    return result
