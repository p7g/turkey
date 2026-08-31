"""`?` and `do`, rewritten into `bind` (SPEC-DELTAS.md 46).

Every other sugar in this language is a node the later stages interpret: `a + b`
carries the `add` it means, `for x in xs` carries its `iter` and `next`. `?`
cannot be one of those, because what it binds is *the rest of the enclosing
statement sequence*, and no node annotated in place can name that. So this is a
pass, and it runs early enough that nothing downstream ever sees a `?`:
`turkey/infer.py`, `turkey/eval.py`, `turkey/deps.py` and
`turkey/exhaustive.py` are untouched by the whole feature.

It runs *after* `turkey/resolve.py`, which buys two things. The code it moves
into lambdas has already been resolved, so moving it cannot change what a name
means; and the code it generates may name internal constructors directly rather
than going looking for surface names a module might not export.

## What a `?` unwinds to

A **do-context** is the block a `?` unwinds to, and there are two: an explicit
`do { ... }`, and the body of a `fun` or lambda that contains a `?`. Everything
else -- `if`, `match`, a bare block -- is transparent, and a `?` inside one
lifts outward through it. A lambda is opaque, so a `?` in a callback belongs to
the callback.

A `do` with no `?` in it emits nothing whatsoever and means exactly the block it
wraps. That is the answer to "what monad is an empty `do`": it does not have
one, because it never asked for a `bind`.

## The translation

`seq` translates a statement sequence, `expr` an expression; both take a
continuation `k` that is an ordinary Python function from the value's AST to the
rest of the program's AST. Straight-line code comes out as exactly the chain
someone would have written by hand:

    do { let x = f(a?) + 1; g(x) }
      =>  bind(a, fun(%k1) { let x = f(%k1) + 1; g(x) })

A control construct containing a `?` is *lifted* into the monad instead: each
branch is translated with `pure` as its continuation, and the construct as a
whole becomes the left argument of a `bind`.

    if c { A } else { B }
      =>  bind(if c { A' } else { B' }, fun(%k1) { <rest> })

That `pure` is the one place the lowering adds something the author did not
write, and it is not the auto-`pure` that was ruled out: the tail of a *do
block* is still taken to be already monadic. A lifted branch is not a do block
anyone wrote -- its value was `Unit` by the language's own rule -- and something
has to carry it into the monad the rest of the block is in.

## Not yet: what crosses a bind

A `return`, `break` or `continue` that would land inside a generated lambda is
rejected here rather than mistranslated. Carrying one across a `bind` means
carrying it as a *value*, since a value is the only thing a `bind` propagates,
and that -- along with loops, whose continuation is dynamic and so needs the
same machinery -- is delta 47.

Note what is *not* rejected: a `return` before any `?` in the same block stays
where it was written and needs nothing. `do { if c { return None }; let x = a?;
g(x) }` lowers with the `if` untouched in the prefix, so the common Rust-shaped
early exit works today.
"""

from __future__ import annotations

import dataclasses
from typing import Callable

from . import ast, prelude
from .errors import Span, Unsupported

# A continuation: given the AST of a value, the AST of everything after it.
Cont = Callable[[ast.Expr], ast.Expr]


# ---------------------------------------------------------------- the scanners
#
# Three questions get asked about a subtree, and they differ only in where they
# stop. Rather than three hand-written walks over twenty node types, each is a
# generic walk over the dataclass fields with its own stopping rule -- so a node
# added to `turkey/ast.py` later cannot be silently missed by one of them.


def _children(node: object):
    """Every `Expr`, `Stmt`, `MatchArm` or `FunDecl` directly under `node`."""
    for field in dataclasses.fields(node):  # type: ignore[arg-type]
        value = getattr(node, field.name)
        if isinstance(value, (ast.Expr, ast.Stmt, ast.MatchArm, ast.FunDecl)):
            yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (ast.Expr, ast.Stmt, ast.MatchArm,
                                     ast.FunDecl)):
                    yield item
                elif isinstance(item, tuple) and len(item) == 2 and \
                        isinstance(item[1], ast.Expr):
                    yield item[1]  # `ERecord.fields`


def _sugared(node: object) -> bool:
    """Is there a `?` or a `do` anywhere under here at all?

    Stops nowhere. Used only to prune: a subtree this says nothing about is left
    exactly as it was found.
    """
    if isinstance(node, (ast.EQuestion, ast.EDo)):
        return True
    return any(_sugared(child) for child in _children(node))


def _owns(node: object) -> bool:
    """Is there a `?` under here that unwinds to *this* do-context?

    Stops at a lambda, a local `fun` and a `do`, each of which is a context of
    its own. It does not stop at a loop -- a `?` in a loop body has to be *seen*
    in order to be refused.
    """
    if isinstance(node, ast.EQuestion):
        return True
    if isinstance(node, (ast.EDo, ast.ELambda, ast.SFun)):
        return False
    return any(_owns(child) for child in _children(node))


def _escapes(node: object, *, in_loop: bool = False) -> ast.Expr | None:
    """A `return`/`break`/`continue` here that targets something outside `node`.

    Stops at a lambda, whose `return` is its own. A `break` inside a loop that
    is itself part of `node` belongs to that loop and does not escape, which is
    what `in_loop` tracks.
    """
    if isinstance(node, ast.EReturn):
        return node
    if isinstance(node, (ast.EBreak, ast.EContinue)) and not in_loop:
        return node
    if isinstance(node, (ast.ELambda, ast.SFun)):
        return None
    inner = in_loop or isinstance(node, (ast.EWhile, ast.EForIn, ast.EForC,
                                         ast.ELoop))
    for child in _children(node):
        found = _escapes(child, in_loop=inner)
        if found is not None:
            return found
    return None


# ------------------------------------------------------------------- the pass


class Desugarer:
    def __init__(self) -> None:
        self._n = 0

    def fresh(self, span: Span) -> tuple[ast.PVar, Cont]:
        """A binder nothing can capture, and the way to read it back.

        `%` is not a character an identifier may contain, so a generated name
        cannot collide with one the author wrote -- the same guarantee
        `%sig.{name}` relies on in `turkey/infer.py`.
        """
        self._n += 1
        name = f"%k{self._n}"
        return ast.PVar(span, name), lambda s=span: ast.EVar(s, name)

    # -- declarations ------------------------------------------------------

    def program(self, program: ast.Program) -> None:
        for decl in program.decls:
            if isinstance(decl, (ast.ClassDecl, ast.InstanceDecl)):
                for method in decl.methods:
                    self.fun(method)
            elif isinstance(decl, ast.Stmt):
                self.walk(decl)

    def fun(self, decl: ast.FunDecl) -> None:
        """A `fun` body is a do-context exactly when it contains a `?`."""
        if decl.body is None or not _sugared(decl.body):
            return
        decl.body = self.context(decl.body)

    def context(self, body: ast.Expr) -> ast.Expr:
        if not _owns(body):
            self.walk(body)
            return body
        return self.block(body, _identity)

    def block(self, body: ast.Expr, k: Cont) -> ast.Expr:
        stmts = body.stmts if isinstance(body, ast.EBlock) else [
            ast.SExpr(body.span, body)]
        return self.seq(stmts, k, body.span)

    # -- the transparent walk ----------------------------------------------

    def walk(self, node: ast.Expr | ast.Stmt) -> ast.Expr | ast.Stmt:
        """Rewrite the do-contexts nested inside `node`, and nothing else.

        This is the path taken by every expression that has no `?` of its own.
        It rebuilds nothing: only the contexts underneath it change, and they
        are installed where they were found.
        """
        if not _sugared(node):
            return node
        if isinstance(node, ast.EDo):
            return self.context(node.body)
        if isinstance(node, ast.ELambda):
            node.body = self.context(node.body)
            return node
        if isinstance(node, ast.SFun):
            self.fun(node.decl)
            return node
        if isinstance(node, ast.EQuestion):
            raise Unsupported(
                "'?' is only meaningful inside a function body or a 'do' block",
                node.span,
            )
        for field in dataclasses.fields(node):  # type: ignore[arg-type]
            value = getattr(node, field.name)
            if isinstance(value, (ast.Expr, ast.Stmt)):
                setattr(node, field.name, self.walk(value))
            elif isinstance(value, ast.FunDecl):
                self.fun(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.MatchArm):
                        item.body = self.walk(item.body)
                setattr(node, field.name, [
                    self.walk(x) if isinstance(x, (ast.Expr, ast.Stmt)) else x
                    for x in value
                ])
        return node

    # -- statement sequences -----------------------------------------------

    def seq(self, stmts: list[ast.Stmt], k: Cont, span: Span) -> ast.Expr:
        """`S[stmts] k`. Statements with no `?` accumulate into a prefix, so a
        block splits only where it actually has to."""
        prefix: list[ast.Stmt] = []
        for i, stmt in enumerate(stmts):
            last = i == len(stmts) - 1
            rest = stmts[i + 1:]

            if not _owns(stmt):
                self.walk(stmt)
                if last and isinstance(stmt, ast.SExpr):
                    return _wrap(prefix, k(stmt.expr), span)
                prefix.append(stmt)
                continue

            # This statement splits the block. Everything after it is about to
            # become the body of a lambda, so nothing in it may escape.
            self._refuse_escape(stmt, rest)

            def after(value: ast.Expr, stmt=stmt, rest=rest, last=last) -> ast.Expr:
                return k(ast.EUnit(stmt.span)) if last else self.seq(rest, k, span)

            if isinstance(stmt, (ast.SLet, ast.SVar)):
                make = type(stmt)

                def bound(v: ast.Expr, stmt=stmt, make=make, after=after) -> ast.Expr:
                    return ast.EBlock(stmt.span, [
                        make(stmt.span, stmt.pat, v),
                        ast.SExpr(stmt.span, after(ast.EUnit(stmt.span))),
                    ])

                body = self.expr(stmt.value, bound)
            elif isinstance(stmt, ast.SAssign):
                def assigned(v: ast.Expr, stmt=stmt, after=after) -> ast.Expr:
                    return ast.EBlock(stmt.span, [
                        ast.SAssign(stmt.span, stmt.target, v),
                        ast.SExpr(stmt.span, after(ast.EUnit(stmt.span))),
                    ])

                body = self.expr(stmt.value, assigned)
            elif isinstance(stmt, ast.SExpr):
                body = (self.expr(stmt.expr, k, tail=True) if last
                        else self.expr(stmt.expr, after))
            else:
                raise Unsupported(
                    "'?' is not supported in this kind of statement yet",
                    stmt.span,
                )
            return _wrap(prefix, body, span)

        return _wrap(prefix, k(ast.EUnit(span)), span)

    def _refuse_escape(self, stmt: ast.Stmt, rest: list[ast.Stmt]) -> None:
        for node in [stmt, *rest]:
            found = _escapes(node)
            if found is not None:
                raise Unsupported(
                    "a 'return', 'break' or 'continue' cannot cross a '?' yet: "
                    "it would have to be carried through the monad's 'bind' as "
                    "a value, which is not implemented",
                    found.span,
                )

    # -- expressions --------------------------------------------------------

    def expr(self, e: ast.Expr, k: Cont, tail: bool = False) -> ast.Expr:
        """`E[e] k`. Hoists each `?` in `e`, left to right.

        `tail` says that `e` *is* the value of the enclosing do-context, so `k`
        is that context's own continuation rather than "the rest of the block".
        A lifted `if` or `match` there needs neither the `pure` nor the `bind`
        that lifting normally costs: its branches already are the tail, so `k`
        goes straight into them. That is not an optimization the monad laws are
        being trusted for -- it is the no-auto-`pure` rule, which says the tail
        of a do block is already the monadic value.
        """
        if not _owns(e):
            return k(self.walk(e))

        t = type(e)

        if t is ast.EQuestion:
            return self.expr(e.expr, lambda m: self._bind(m, k, e.span, e.bind_fn))
        if t is ast.EBlock:
            # A bare block is transparent: its statements belong to the context
            # around it, and its scope is preserved by `seq`'s own blocks.
            return self.seq(e.stmts, k, e.span)
        if t is ast.EAnnot:
            return self.expr(e.expr, lambda v: k(ast.EAnnot(e.span, v, e.type_expr)))
        if t is ast.EField:
            return self.expr(e.obj, lambda o: k(ast.EField(e.span, o, e.name)))
        if t is ast.EIndex:
            return self.expr(e.arr, lambda a: self.expr(
                e.index, lambda i: k(ast.EIndex(e.span, a, i))))
        if t is ast.EUnary:
            return self.expr(e.operand,
                             lambda v: k(ast.EUnary(e.span, e.op, v, e.fn)))
        if t is ast.ECall:
            return self.expr(e.fn, lambda f: self._thread(
                e.args, [], lambda vs: k(ast.ECall(e.span, f, vs))))
        if t is ast.ETuple:
            return self._thread(e.elems, [],
                                lambda vs: k(ast.ETuple(e.span, vs)))
        if t is ast.EArray:
            return self._thread(e.elems, [],
                                lambda vs: k(ast.EArray(e.span, vs)))
        if t is ast.ERecord:
            names = [name for name, _ in e.fields]
            return self._thread(
                [value for _, value in e.fields], [],
                lambda vs: k(ast.ERecord(e.span, e.con, list(zip(names, vs)))))
        if t is ast.EBinary:
            return self._binary(e, k, tail)
        if t is ast.EIf:
            return self._if(e, k, tail)
        if t is ast.EMatch:
            return self._match(e, k, tail)

        if t in (ast.EWhile, ast.EForIn, ast.EForC, ast.ELoop):
            raise Unsupported(
                "'?' is not supported inside a loop yet: the loop would have to "
                "become a recursive function in the monad, which is not "
                "implemented",
                e.span,
            )
        if t in (ast.EReturn, ast.EBreak):
            raise Unsupported(
                "'?' cannot appear in the value of a 'return' or 'break' yet",
                e.span,
            )
        raise Unsupported(f"'?' is not supported in {t.__name__} yet", e.span)

    def _thread(self, todo: list[ast.Expr], done: list[ast.Expr],
                k: Callable[[list[ast.Expr]], ast.Expr]) -> ast.Expr:
        """Left to right, so `g(a?, b?)` binds `a` before `b`."""
        if not todo:
            return k(done)
        return self.expr(
            todo[0], lambda v: self._thread(todo[1:], [*done, v], k))

    def _binary(self, e: ast.EBinary, k: Cont, tail: bool = False) -> ast.Expr:
        if e.op in ("&&", "||") and _owns(e.right):
            # These short-circuit, which no `bind` argument does. Reading them
            # as the `if` they already mean is what keeps that true: the right
            # operand stays under a branch instead of being hoisted out of one.
            other = ast.ECon(e.span, prelude.BOOL_FALSE if e.op == "&&"
                             else prelude.BOOL_TRUE)
            branches = ((e.right, other) if e.op == "&&" else (other, e.right))
            return self._if(
                ast.EIf(e.span, e.left, branches[0], branches[1]), k, tail)
        return self.expr(e.left, lambda l: self.expr(
            e.right, lambda r: k(ast.EBinary(e.span, e.op, l, r, e.fn))))

    def _if(self, e: ast.EIf, k: Cont, tail: bool = False) -> ast.Expr:
        lifted = _owns(e.then) or (e.otherwise is not None and _owns(e.otherwise))

        if not lifted:
            def plain(c: ast.Expr) -> ast.Expr:
                self.walk(e.then)
                if e.otherwise is not None:
                    self.walk(e.otherwise)
                return k(ast.EIf(e.span, c, e.then, e.otherwise))
            return self.expr(e.cond, plain)

        # In tail position `k` goes into the branches and there is no `bind` at
        # all; otherwise each branch ends at `pure` and the `if` is bound.
        inner: Cont = k if tail else self._pure

        def branch(c: ast.Expr) -> ast.Expr:
            self._refuse_escape(ast.SExpr(e.span, e.then), [])
            if e.otherwise is not None:
                self._refuse_escape(ast.SExpr(e.span, e.otherwise), [])
            then = self.block(e.then, inner)
            other = (self.block(e.otherwise, inner)
                     if e.otherwise is not None
                     else inner(ast.EUnit(e.span)))
            built = ast.EIf(e.span, c, then, other)
            return built if tail else self._bind(built, k, e.span)

        return self.expr(e.cond, branch)

    def _match(self, e: ast.EMatch, k: Cont, tail: bool = False) -> ast.Expr:
        lifted = any(_owns(arm.body) for arm in e.arms)

        if not lifted:
            def plain(s: ast.Expr) -> ast.Expr:
                for arm in e.arms:
                    arm.body = self.walk(arm.body)
                return k(ast.EMatch(e.span, s, e.arms))
            return self.expr(e.scrutinee, plain)

        inner: Cont = k if tail else self._pure

        def arms(s: ast.Expr) -> ast.Expr:
            built = []
            for arm in e.arms:
                self._refuse_escape(ast.SExpr(arm.span, arm.body), [])
                built.append(ast.MatchArm(arm.span, arm.patterns,
                                          self.block(arm.body, inner)))
            made = ast.EMatch(e.span, s, built)
            return made if tail else self._bind(made, k, e.span)

        return self.expr(e.scrutinee, arms)

    # -- the two calls the lowering makes ----------------------------------

    def _bind(self, m: ast.Expr, k: Cont, span: Span,
              fn: ast.EVar | None = None) -> ast.Expr:
        param, read = self.fresh(span)
        lam = ast.ELambda(span, [param], None, k(read()))
        if fn is None:
            fn = ast.EVar(span, prelude.MONAD_BIND, method=True)
        return ast.ECall(span, fn, [m, lam])

    @staticmethod
    def _pure(value: ast.Expr) -> ast.Expr:
        span = value.span
        return ast.ECall(
            span, ast.EVar(span, prelude.MONAD_PURE, method=True), [value])


def _identity(value: ast.Expr) -> ast.Expr:
    return value


def _wrap(prefix: list[ast.Stmt], value: ast.Expr, span: Span) -> ast.Expr:
    if not prefix:
        return value
    return ast.EBlock(span, [*prefix, ast.SExpr(value.span, value)])


def program(program_: ast.Program) -> None:
    """Rewrite every `?` and `do` in one module away."""
    Desugarer().program(program_)


__all__ = ["Desugarer", "program"]
