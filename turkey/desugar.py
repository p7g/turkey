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

## What crosses a bind (delta 47)

A `return`, `break` or `continue` after a `?` would land inside a generated
lambda, where `return` means "return from the lambda" -- which is not what
anyone wrote. It cannot *escape* through the `bind` either: escaping is not
something a `bind` does, and for `Array`, whose bind runs its continuation once
per element, there is nothing coherent for an escape to mean.

So it travels as a value, in the Prelude's `Flow`, because a value is the only
thing a `bind` propagates. A context that needs this runs in **flow mode**:
every statement answers with `m (Flow ...)`, the next one runs only under
`Fall`, and the context's boundary is where `Ret` becomes a result again.

A `return` before every `?` in its block needs none of that -- it stays in the
prefix, at the nesting it was written at -- and `_needs_flow` is what keeps the
common case free of the machinery.

Loops are the same problem twice over: a loop's continuation is not known until
its body has run, so it cannot be a lambda written once. A lifted loop becomes a
recursive local `fun` answering with a `Flow`, where `Brk` becomes the loop's own
`Fall` -- which is why `let v = loop { ... break x }` still works -- and `Ret`
keeps travelling. `for x in xs` is expanded to its cursor form here
(design.md §6.5), since only the expansion has a loop to lift.
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


def _stmts(body: ast.Expr) -> list[ast.Stmt]:
    if isinstance(body, ast.EBlock):
        return list(body.stmts)
    return [ast.SExpr(body.span, body)]


def _needs_flow(stmts: list[ast.Stmt]) -> bool:
    """Must this context carry control transfers as values?

    Two reasons it must. Either a transfer sits at or after the *first*
    statement holding a `?`, so it would end up inside a lambda -- everything
    before that stays in the prefix, at the nesting the author wrote it at,
    where a `return` still means what it says. Or the context holds a lifted
    loop, which answers with a `Flow` whether or not its body transfers.
    """
    if any(_has_lifted_loop(stmt) for stmt in stmts):
        return True
    for i, stmt in enumerate(stmts):
        if _owns(stmt):
            return any(_escapes(s) is not None for s in stmts[i:])
    return False


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
    def __init__(self, methods: dict[str, str] | None = None) -> None:
        self._n = 0
        self.methods = methods or {}
        # How many lifted loops enclose the code being translated. A `break`
        # only becomes a `Brk` inside one; outside, it is left alone so that the
        # checker still reports it as a `break` with no loop, which is what it
        # is.
        self._depth = 0
        # One bound method, made once: the flow-mode tail continuation is
        # compared by identity below, and `self._fall` would make a new bound
        # object on every attribute access.
        self.fall: Cont = self._fall

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
        """A do-context: a `do` block, or the body of a `fun` holding a `?`."""
        if not _owns(body):
            # The *result*, not `body`: `walk` replaces a node rather than
            # rewriting it when the node is a `do`, and `fun f() = do { a? }`
            # is a body that is one. Returning `body` there left the `?` in the
            # tree for `deps.free_names` to fall over on.
            return self.walk(body)
        if not _needs_flow(_stmts(body)):
            return self.block(body, _identity)
        # Something at or after the first `?` transfers control, so it would end
        # up inside a lambda where `return` means the wrong thing. The whole
        # context runs in flow mode, and its boundary is where `Ret` is cashed
        # in. A transfer that is only ever *before* every `?` does not trigger
        # this: it stays in the prefix and needs nothing.
        return self._unflow(self.block(body, self.fall, flow=True), body.span)

    def block(self, body: ast.Expr, k: Cont, flow: bool = False) -> ast.Expr:
        stmts = body.stmts if isinstance(body, ast.EBlock) else [
            ast.SExpr(body.span, body)]
        return self.seq(list(stmts), k, body.span, flow)

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
                    self.walk(x) if isinstance(x, (ast.Expr, ast.Stmt))
                    else (x[0], self.walk(x[1]))
                    if isinstance(x, tuple) and len(x) == 2
                    and isinstance(x[1], ast.Expr)
                    else x
                    for x in value
                ])
        return node

    # -- statement sequences -----------------------------------------------

    def seq(self, stmts: list[ast.Stmt], k: Cont, span: Span,
            flow: bool = False) -> ast.Expr:
        """`S[stmts] k`. Statements with no `?` accumulate into a prefix, so a
        block splits only where it actually has to.

        In `flow` mode every statement answers with `m (Flow ...)` rather than a
        plain value, `k` is `pure . Fall`, and a control transfer becomes one of
        the other three constructors instead of a jump. A statement that neither
        holds a `?` nor transfers control still just goes in the prefix.
        """
        prefix: list[ast.Stmt] = []
        for i, stmt in enumerate(stmts):
            last = i == len(stmts) - 1
            rest = stmts[i + 1:]

            if flow and (transfer := _transfer_stmt(stmt)) is not None \
                    and (self._depth > 0
                         or isinstance(transfer, ast.EReturn)):
                # Whatever follows is unreachable, so the sequence ends here.
                return _wrap(prefix, self._transfer(transfer, k), span)

            if not _owns(stmt) and not (flow and _escapes(stmt) is not None):
                self.walk(stmt)
                if last and isinstance(stmt, ast.SExpr):
                    return _wrap(prefix, k(stmt.expr), span)
                prefix.append(stmt)
                continue

            if not flow:
                # Everything after this is about to become a lambda body, so a
                # transfer in it would change meaning. `context` decides whether
                # to run in flow mode; reaching here with one is a bug.
                self._refuse_escape(stmt, rest)

            def after(value: ast.Expr, stmt=stmt, rest=rest, last=last) -> ast.Expr:
                return (k(ast.EUnit(stmt.span)) if last
                        else self.seq(rest, k, span, flow))

            if isinstance(stmt, (ast.SLet, ast.SVar)):
                make = type(stmt)

                def bound(v: ast.Expr, stmt=stmt, make=make, after=after) -> ast.Expr:
                    return ast.EBlock(stmt.span, [
                        make(stmt.span, stmt.pat, v),
                        ast.SExpr(stmt.span, after(ast.EUnit(stmt.span))),
                    ])

                if flow and _flowing(stmt):
                    # `let found = loop { ... break v }`: the value comes out of
                    # the loop's `Fall`, and the other three keep travelling.
                    head = self.expr(stmt.value, self.fall, tail=True,
                                     flow=True)
                    body = self._sequence(head, bound, stmt.span)
                else:
                    body = self.expr(stmt.value, bound, flow=flow)
            elif isinstance(stmt, ast.SAssign):
                def assigned(v: ast.Expr, stmt=stmt, after=after) -> ast.Expr:
                    return ast.EBlock(stmt.span, [
                        ast.SAssign(stmt.span, stmt.target, v),
                        ast.SExpr(stmt.span, after(ast.EUnit(stmt.span))),
                    ])

                body = self.expr(stmt.value, assigned, flow=flow)
            elif isinstance(stmt, ast.SExpr):
                if last:
                    body = self.expr(stmt.expr, k, tail=True, flow=flow)
                elif flow and _flowing(stmt):
                    # A lifted construct holding a transfer, or a lifted loop:
                    # it answers with a `Flow`, so the rest of the block runs
                    # only under `Fall` and the other three travel outward.
                    head = self.expr(stmt.expr, self.fall, tail=True,
                                     flow=True)
                    body = self._sequence(head, after, stmt.span)
                else:
                    body = self.expr(stmt.expr, after, flow=flow)
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

    def expr(self, e: ast.Expr, k: Cont, tail: bool = False,
             flow: bool = False) -> ast.Expr:
        """`E[e] k`. Hoists each `?` in `e`, left to right.

        `tail` says that `e` *is* the value of the enclosing do-context, so `k`
        is that context's own continuation rather than "the rest of the block".
        A lifted `if` or `match` there needs neither the `pure` nor the `bind`
        that lifting normally costs: its branches already are the tail, so `k`
        goes straight into them. That is not an optimization the monad laws are
        being trusted for -- it is the no-auto-`pure` rule, which says the tail
        of a do block is already the monadic value.
        """
        # In flow mode a construct with no `?` in it still needs lowering if
        # it transfers control: the transfer has to become a value even though
        # nothing here binds.
        if not _owns(e) and not (flow and _escapes(e) is not None):
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
        if t is ast.EProject:
            return self.expr(e.obj, lambda o: k(ast.EProject(e.span, o, e.index)))
        if t is ast.EIndex:
            return self.expr(e.arr, lambda a: self.expr(
                e.index, lambda i: k(ast.EIndex(
                    e.span, a, i, e.get_fn, e.set_fn))))
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
            return self._binary(e, k, tail, flow)
        if t is ast.EIf:
            return self._if(e, k, tail, flow)
        if t is ast.EMatch:
            return self._match(e, k, tail, flow)

        if t in (ast.EWhile, ast.EForIn, ast.EForC, ast.ELoop):
            if k is not self.fall:
                raise Unsupported(
                    "a loop containing a '?' can only be a statement, not a "
                    "value: its result has to carry whether the body broke, "
                    "continued or returned",
                    e.span,
                )
            return self._loop(e)
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

    def _binary(self, e: ast.EBinary, k: Cont, tail: bool = False,
                flow: bool = False) -> ast.Expr:
        if e.op in ("&&", "||") and _owns(e.right):
            # These short-circuit, which no `bind` argument does. Reading them
            # as the `if` they already mean is what keeps that true: the right
            # operand stays under a branch instead of being hoisted out of one.
            other = ast.ECon(e.span, prelude.BOOL_FALSE if e.op == "&&"
                             else prelude.BOOL_TRUE)
            branches = ((e.right, other) if e.op == "&&" else (other, e.right))
            return self._if(
                ast.EIf(e.span, e.left, branches[0], branches[1]), k, tail, flow)
        return self.expr(e.left, lambda l: self.expr(
            e.right, lambda r: k(ast.EBinary(e.span, e.op, l, r, e.fn))))

    def _if(self, e: ast.EIf, k: Cont, tail: bool = False,
            flow: bool = False) -> ast.Expr:
        lifted = _branching(e.then, flow) or (
            e.otherwise is not None and _branching(e.otherwise, flow))

        if not lifted:
            def plain(c: ast.Expr) -> ast.Expr:
                # Rebuilt from what `walk` answers, for the reason `context`
                # is: a branch that *is* a `do` is replaced, not rewritten.
                then = self.walk(e.then)
                other = (self.walk(e.otherwise)
                         if e.otherwise is not None else None)
                return k(ast.EIf(e.span, c, then, other))
            return self.expr(e.cond, plain)

        # In tail position `k` goes into the branches and there is no `bind` at
        # all; otherwise each branch ends at `pure` and the `if` is bound.
        inner: Cont = k if tail else self._pure

        def branch(c: ast.Expr) -> ast.Expr:
            if not flow:
                # Outside flow mode a branch becomes a lambda body, so a
                # transfer in it would change meaning.
                self._refuse_escape(ast.SExpr(e.span, e.then), [])
                if e.otherwise is not None:
                    self._refuse_escape(ast.SExpr(e.span, e.otherwise), [])
            then = self.block(e.then, inner, flow)
            other = (self.block(e.otherwise, inner, flow)
                     if e.otherwise is not None
                     else inner(ast.EUnit(e.span)))
            built = ast.EIf(e.span, c, then, other)
            return built if tail else self._bind(built, k, e.span)

        return self.expr(e.cond, branch)

    def _match(self, e: ast.EMatch, k: Cont, tail: bool = False,
               flow: bool = False) -> ast.Expr:
        lifted = any(_branching(arm.body, flow) for arm in e.arms)

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
                if not flow:
                    self._refuse_escape(ast.SExpr(arm.span, arm.body), [])
                built.append(ast.MatchArm(arm.span, arm.patterns,
                                          self.block(arm.body, inner, flow)))
            made = ast.EMatch(e.span, s, built)
            return made if tail else self._bind(made, k, e.span)

        return self.expr(e.scrutinee, arms)

    # -- the two calls the lowering makes ----------------------------------

    def _bind(self, m: ast.Expr, k: Cont, span: Span,
              fn: ast.EVar | None = None) -> ast.Expr:
        param, read = self.fresh(span)
        lam = ast.ELambda(span, [param], None, k(read()))
        if fn is None:
            fn = ast.EVar(span, self.methods.get(
                prelude.MONAD_BIND, prelude.MONAD_BIND), method=True)
        return ast.ECall(span, fn, [m, lam])

    def _pure(self, value: ast.Expr) -> ast.Expr:
        span = value.span
        return ast.ECall(
            span, ast.EVar(span, self.methods.get(
                prelude.MONAD_PURE, prelude.MONAD_PURE), method=True), [value])

    # -- flow mode: control transfers as values (delta 47) -----------------

    def _fall(self, value: ast.Expr) -> ast.Expr:
        """The flow-mode tail: fell off the end of the block with this value."""
        return self._pure(_con(prelude.FLOW_FALL, [value], value.span))

    def _transfer(self, e: ast.Expr, k: Cont) -> ast.Expr:
        """`return e` / `break e` / `continue`, as the value that carries it."""
        span = e.span
        if isinstance(e, ast.EContinue):
            return self._pure(_con(prelude.FLOW_CONT, [], span))
        con = prelude.FLOW_RET if isinstance(e, ast.EReturn) else prelude.FLOW_BRK
        if e.value is None:
            return self._pure(_con(con, [ast.EUnit(span)], span))
        return self.expr(e.value, lambda v: self._pure(_con(con, [v], span)))

    def _sequence(self, head: ast.Expr, after: Cont, span: Span) -> ast.Expr:
        """Run `after` only if `head` fell through; carry the rest outward.

        `after` receives the value `head` fell through *with*, which is how
        `let found = loop { ... break v }` gets its `v`: a lifted loop's `Fall`
        carries the value it broke with.

        The three propagating arms rebuild rather than passing on the value they
        matched, because the `Flow` `head` answers with and the one this answers
        with differ in their first parameter -- `head`'s own value type is not
        the rest of the block's.
        """
        self._n += 1
        fell = f"%fell{self._n}"
        return self._bind(head, lambda f: ast.EMatch(span, f, [
            _arm(prelude.FLOW_FALL, fell, span, after(_var(fell, span))),
            _arm(prelude.FLOW_BRK, "%b", span,
                 self._pure(_con(prelude.FLOW_BRK, [_var("%b", span)], span))),
            _arm(prelude.FLOW_CONT, None, span,
                 self._pure(_con(prelude.FLOW_CONT, [], span))),
            _arm(prelude.FLOW_RET, "%r", span,
                 self._pure(_con(prelude.FLOW_RET, [_var("%r", span)], span))),
        ]), span)

    # -- lifted loops -------------------------------------------------------

    def _loop(self, e: ast.Expr) -> ast.Expr:
        """A loop holding a `?` becomes a recursive local function.

        There is no way around the recursion: a loop's continuation is not
        known until the body has run, so it cannot be a lambda written once. The
        function answers with a `Flow`, which is what carries a `break` out and
        a `return` further out still; `Fall` and `Cont` both mean "go round
        again", and differ only in where they came from.

        `for x in xs` is expanded to its cursor form here (design.md §6.5)
        rather than left for `turkey/infer.py`, because only the expansion has a
        loop to lift.
        """
        span = e.span
        self._n += 1
        name = f"%loop{self._n}"
        call = ast.ECall(span, _var(name, span), [])

        self._depth += 1
        body_flow = self.block(e.body, self.fall, flow=True)
        self._depth -= 1

        step = [e.step] if isinstance(e, ast.EForC) and e.step is not None else []
        again = _wrap(list(step), call, span) if step else call
        # `Brk(v)` is the loop's own value, so it becomes this expression's
        # `Fall`; `Ret` keeps travelling.
        round_again = self._bind(body_flow, lambda f: ast.EMatch(span, f, [
            _arm(prelude.FLOW_FALL, "%_", span, again),
            _arm(prelude.FLOW_CONT, None, span, again),
            _arm(prelude.FLOW_BRK, "%b", span,
                 self._fall(_var("%b", span))),
            _arm(prelude.FLOW_RET, "%r", span,
                 self._pure(_con(prelude.FLOW_RET, [_var("%r", span)], span))),
        ]), span)

        done = self._fall(ast.EUnit(span))

        if isinstance(e, ast.ELoop):
            inner: ast.Expr = round_again  # only a `break` ever ends it
            prefix: list[ast.Stmt] = []
        elif isinstance(e, ast.EForIn):
            return self._for_in(e, name, call, round_again, span)
        else:
            cond = e.cond
            inner = self.expr(cond, lambda c: ast.EIf(
                span, c, round_again, done), flow=True)
            prefix = ([e.init] if isinstance(e, ast.EForC)
                      and e.init is not None else [])

        return _wrap([*prefix, _fun(name, inner, span)], call, span)

    def _for_in(self, e: ast.EForIn, name: str, call: ast.Expr,
                round_again: ast.Expr, span: Span) -> ast.Expr:
        """`for x in xs` in its cursor form, then lifted like any other loop."""
        self._n += 1
        seq_name, cur_name = f"%seq{self._n}", f"%cur{self._n}"
        item = f"%item{self._n}"

        def built(xs: ast.Expr) -> ast.Expr:
            advance = ast.ECall(span, e.next_fn, [
                _var(seq_name, span), _var(cur_name, span)])
            body = ast.EMatch(span, advance, [
                _arm(prelude.OPTION_NONE, None, span,
                     self._fall(ast.EUnit(span))),
                ast.MatchArm(span, [ast.PCon(span, prelude.OPTION_SOME,
                                             [ast.PVar(span, item)])],
                             ast.EBlock(span, [
                                 ast.SLet(span, e.pat, _var(item, span)),
                                 ast.SExpr(span, round_again),
                             ])),
            ])
            return _wrap([
                ast.SLet(span, ast.PVar(span, seq_name), xs),
                ast.SLet(span, ast.PVar(span, cur_name),
                         ast.ECall(span, e.iter_fn, [_var(seq_name, span)])),
                _fun(name, body, span),
            ], call, span)

        return self.expr(e.iterable, built)

    def _unflow(self, body: ast.Expr, span: Span) -> ast.Expr:
        """A do-context's boundary: where `Ret` stops being a value again.

        `Brk` and `Cont` cannot arrive -- a `break` outside a loop is refused
        while this pass runs -- but nothing in the type system knows that, so
        the arms exist and diverge.
        """
        unreachable = ast.ECall(
            span, ast.EVar(span, prelude.ERROR, method=False),
            [ast.ELit(span, "String", "internal error: a loop transfer escaped "
                                     "its loop")])
        return self._bind(body, lambda f: ast.EMatch(span, f, [
            _arm(prelude.FLOW_FALL, "%v", span, _var("%v", span)),
            _arm(prelude.FLOW_RET, "%r", span, _var("%r", span)),
            _arm(prelude.FLOW_BRK, "%_", span, unreachable),
            _arm(prelude.FLOW_CONT, None, span, unreachable),
        ]), span)


def _identity(value: ast.Expr) -> ast.Expr:
    return value


def _var(name: str, span: Span) -> ast.EVar:
    return ast.EVar(span, name)


def _con(name: str, args: list[ast.Expr], span: Span) -> ast.Expr:
    """A `Flow` constructor. Nullary ones are values, not calls."""
    made = ast.ECon(span, name)
    return made if not args else ast.ECall(span, made, args)


def _fun(name: str, body: ast.Expr, span: Span) -> ast.Stmt:
    """`fun name() { body }`, as a statement, so the loop can call itself.

    Not generalized (`FunDecl.monomorphic`). It has one use site -- the `call`
    that `_loop` builds beside it -- plus its own recursion, so there is
    nothing for a scheme to be used at more than one type, and generalizing it
    is what made the loops in `question_control.tl` survive every pass.

    The helper answers with the enclosing block's `Flow`, and the arms above
    only ever build `Fall` and `Ret` at that type: a `Brk` becomes the
    enclosing `Fall`, which is what makes `break v` the loop's value. So the
    `Brk` slot is uninhabited *by construction*, for every lifted loop, and
    generalizing left a variable there that nothing could ever solve. Every
    call site then read `%loopN[c, Option Int]` -- ground in the slot that
    matters and open in the one that cannot -- and `mono` specializes only at
    ground instantiations, so one dead parameter kept the whole loop generic
    and out of reach of the inliner behind it.
    """
    return ast.SFun(span, ast.FunDecl(span, name, [], None, body,
                                      monomorphic=True))


def _arm(con: str, bind: str | None, span: Span, body: ast.Expr) -> ast.MatchArm:
    args = [] if bind is None else [
        ast.PWild(span) if bind == "%_" else ast.PVar(span, bind)]
    return ast.MatchArm(span, [ast.PCon(span, con, args)], body)


def _transfer_stmt(stmt: ast.Stmt) -> ast.Expr | None:
    """`return e`, `break e` or `continue` written as a whole statement."""
    if isinstance(stmt, ast.SExpr) and isinstance(
            stmt.expr, (ast.EReturn, ast.EBreak, ast.EContinue)):
        return stmt.expr
    return None


def _has_lifted_loop(node: object) -> bool:
    """A loop holding a `?`, which therefore answers with a `Flow`."""
    if isinstance(node, (ast.EWhile, ast.EForIn, ast.EForC, ast.ELoop)) \
            and _owns(node):
        return True
    if isinstance(node, (ast.EDo, ast.ELambda, ast.SFun)):
        return False
    return any(_has_lifted_loop(child) for child in _children(node))


def _branching(branch: ast.Expr, flow: bool) -> bool:
    """Does this branch force its `if`/`match` to be lifted into the monad?

    A `?` always does. In flow mode a control transfer does too, even with no
    `?` anywhere near it: the transfer has to leave as a value, and only a
    lifted branch can answer with one.
    """
    return _owns(branch) or (flow and _escapes(branch) is not None)


def _flowing(stmt: ast.Stmt) -> bool:
    """Does this statement answer with a `Flow` when translated in flow mode?"""
    return _escapes(stmt) is not None or _has_lifted_loop(stmt)


def _wrap(prefix: list[ast.Stmt], value: ast.Expr, span: Span) -> ast.Expr:
    if not prefix:
        return value
    return ast.EBlock(span, [*prefix, ast.SExpr(value.span, value)])


def program(program_: ast.Program, methods: dict[str, str] | None = None) -> None:
    """Rewrite every surface-only construct in one module away."""
    Desugarer(methods).program(program_)
    _desugar_indexes(program_)


def _desugar_indexes(program_: ast.Program) -> None:
    """Turn bracket reads/writes into resolved `Index` method calls."""
    def rewrite(value):
        if isinstance(value, ast.SAssign) and isinstance(value.target, ast.EIndex):
            target = value.target
            if target.set_fn is None:
                return value
            return ast.SExpr(value.span, ast.ECall(
                value.span, target.set_fn,
                [rewrite(target.arr), rewrite(target.index), rewrite(value.value)]))
        if isinstance(value, ast.EIndex):
            if value.get_fn is None:
                return value
            return ast.ECall(value.span, value.get_fn,
                             [rewrite(value.arr), rewrite(value.index)])
        if isinstance(value, ast.Node):
            for field in dataclasses.fields(value):
                held = getattr(value, field.name)
                if isinstance(held, ast.Node):
                    setattr(value, field.name, rewrite(held))
                elif isinstance(held, list):
                    setattr(value, field.name, [
                        (item[0], rewrite(item[1]))
                        if isinstance(item, tuple) and len(item) == 2
                        and isinstance(item[1], ast.Expr)
                        else rewrite(item) if isinstance(item, ast.Node) else item
                        for item in held
                    ])
            return value
        return value

    for i, decl in enumerate(program_.decls):
        program_.decls[i] = rewrite(decl)


__all__ = ["Desugarer", "program"]
