"""The four loop forms become one recursive join point (M15e).

`plan.txt` item 5 deferred this and said where to: "Collapsing the four loop
forms into one, and turning `return` into a jump, is worth doing -- under item
7, against a Core that already exists" (`core.py`). This is that.

`CWhile`, `CLoop`, `CForC` and `CForIn` all become the same thing -- a
recursive `CJoin` and the jumps into it -- and `CReturn`, `CBreak` and
`CContinue`, which until now named their target by *where they were*, become
`CJump`s that name it outright. `CForIn`'s `iter`/`next` elaboration, which
`core.py` records as something "a later pass should" do, is done here too,
because a loop with a cursor in it is not one form fewer than four.

## Why it is worth the churn

A transfer that names its target by position is a term that cannot be moved,
and `opt.py` has to refuse every reduction that would move one: about one in
eight of the functions in the suite is un-inlinable for no better reason than
holding a `return`. A jump names a join point, and a join point travels with
the term it is in, so the refusal goes away.

It also removes the last places where Core has a construct the checker treats
specially. Four loops with four rules become one join with the rule M15a
already wrote.

## The shape

Every function whose body contains a `return` is wrapped in a join:

    fun(x) { join %ret(%rv) = %rv in <body, with every return a jump to %ret> }

and every loop becomes two:

    join %af(%v) = <what follows the loop>
    in join rec %lp() = <one iteration, ending in a jump to %lp or to %af>
       in jump %lp()

`break` is a jump to `%af`, `continue` a jump to `%lp` -- or, for a C-style
`for`, to a third join holding the step, since `continue` there runs the step
before the test. When the loop already sits somewhere that wants its value --
a `let` binding it, which is what a loop in a statement position is -- `%af` is
that continuation rather than a second one, so the common case adds one join
and not two.

## It is a partial pass, and says so

A transfer inside a call's argument -- `Array.push(ops, match c { ']' -> break,
... })`, which `bf.tl` writes -- cannot simply be hoisted out, because the
arguments beside it would then be evaluated after it and the evaluator is
strict and left to right. `_operands` and `anf` handle that by binding every
operand up to the special one in order, which is A-normal form arrived at
because the order must be preserved rather than because the form is wanted.

What has no rule at all is refused rather than guessed at: the binding is left
exactly as it was, loop nodes and all, and the checker and the evaluator still
know what those are, because `turkey/lower.py` still produces them. Getting a
control-flow rewrite subtly wrong is a silent wrong answer rather than a
rejected term, which is the whole reason for preferring a refusal.
`_Unsupported` is that refusal and `collapse` counts them, so a shape that
turns out to be common is a number rather than a surprise. Across the suite it
is zero, and `tests/test_loops.py` asserts that rather than believing it.

## What is left

The nodes are still *in* Core. Nothing that runs contains one -- the evaluator
runs the optimized program, and no program in the suite reaches it with a loop
node left -- but `turkey/lower.py` still emits them, so `coretc.py` and
`eval.py` still have to know them and `turkey core` still prints them. Deleting
them from the IR means doing this in the lowering instead, which is a different
piece of work: the lowering has a scope chain and a statement shape to keep,
and this has neither.
"""

from __future__ import annotations

from dataclasses import fields, replace

from . import ast
from .core import (
    CAlt, CApp, CArray, CAssign, CBind, CBreak, CContinue, CDeref, CExpr,
    CField, CForC, CForIn, CIf, CIndex, CJoin, CJump, CLam, CLet, CLetRec,
    CLoop, CMatch, CParam, CProgram, CRecord, CRef, CReturn, CTuple, CUnit,
    CVar, CWhile,
)
from .decls import DeclTable
from .types import TBottom, TCon, TFun, Type, prune, spine

UNIT = TCon("Unit")

# The nodes this pass exists to remove.
LOOPS = (CWhile, CLoop, CForC, CForIn)
TRANSFERS = (CReturn, CBreak, CContinue)

# Nodes with a rule below. Anything else holding a transfer or a loop in an
# evaluated position is what `_Unsupported` is for.
HANDLED = (CLet, CLetRec, CIf, CMatch) + LOOPS + TRANSFERS


class _Unsupported(Exception):
    """A term shape the rules do not cover. The binding is left alone."""


def collapse(program: CProgram, decls: DeclTable) -> tuple[CProgram, int]:
    """Every binding's body, with its loops and transfers turned into jumps.

    Returns the program and the number of bindings that were left alone --
    which is a number worth having rather than a silence, since it is the
    measure of how partial this pass is.
    """
    state = _Names()
    refused = 0

    def one(bind: CBind) -> CBind:
        nonlocal refused
        try:
            return replace(bind, value=_Converter(decls, state).top(bind.value))
        except _Unsupported:
            refused += 1
            return bind

    return CProgram(dicts=[one(b) for b in program.dicts],
                    binds=[one(b) for b in program.binds]), refused


class _Names:
    """One counter for the whole program, so the output is deterministic.

    Per program and not per binding because the printer renumbers generated
    names per binding anyway (`core._Aliases`), so the global counter costs a
    golden nothing and saves this pass from threading a second piece of state.
    """

    def __init__(self) -> None:
        self.n = 0

    def fresh(self, hint: str) -> str:
        self.n += 1
        return f"%{hint}{self.n}"


class _Converter:
    def __init__(self, decls: DeclTable, names: _Names) -> None:
        self.decls = decls
        self.names = names
        # The join a `return` jumps to, the join a `break` jumps to and the
        # value it carries, and the join a `continue` jumps to. All three are
        # None where the corresponding transfer would have been a compile
        # error anyway.
        self.ret: str | None = None
        self.brk: str | None = None
        self.cont: str | None = None

    def fresh(self, hint: str) -> str:
        return self.names.fresh(hint)

    # -- entry -------------------------------------------------------------

    def top(self, e: CExpr) -> CExpr:
        """A whole binding's value. Not itself inside any function."""
        return self.plain(e) if not _special(e) else self.conv(e, None)

    # -- the two modes ------------------------------------------------------

    def plain(self, e):
        """A term with no transfer and no loop outside a lambda: copied.

        The lambdas inside still need converting -- each is its own function,
        with its own `return` -- which is the only reason this is not the
        identity.
        """
        if isinstance(e, CLam):
            return self.lam(e)
        if isinstance(e, CAlt):
            return CAlt(e.pat, self.plain(e.body))
        if isinstance(e, CBind):
            return replace(e, value=self.plain(e.value))
        if isinstance(e, CExpr):
            return type(e)(**{f.name: self.plain(getattr(e, f.name))
                              for f in fields(e)})
        if isinstance(e, list):
            return [self.plain(x) for x in e]
        if isinstance(e, tuple):
            return tuple(self.plain(x) for x in e)
        return e

    def conv(self, e, k: str | None):
        """`e`, rewritten so that its value reaches `k`.

        `k` is a join name, or None meaning "the value of this term is the
        value of the term it stands in". Every rule below either produces a
        value in place (when `k` is None) or ends in a jump, which is what
        makes the result satisfy M15a's tail-position discipline without any
        rule having to check it.
        """
        if e is None:
            return e
        if not _special(e):
            return self.finish(self.plain(e), k)
        if isinstance(e, CReturn):
            if self.ret is None:
                raise _Unsupported("a return outside a function")
            return self.conv(e.value if e.value is not None else _unit(e), self.ret)
        if isinstance(e, CBreak):
            if self.brk is None:
                raise _Unsupported("a break outside a loop")
            return self.conv(e.value if e.value is not None else _unit(e),
                             self.brk)
        if isinstance(e, CContinue):
            if self.cont is None:
                raise _Unsupported("a continue outside a loop")
            return CJump(TBottom(), e.span, self.cont, [])
        if isinstance(e, CLet):
            return self.let(e, k)
        if isinstance(e, CLetRec):
            if any(_special(b.value) for b in e.binds):
                raise _Unsupported("a transfer in a letrec binding")
            return CLetRec(e.ty, e.span,
                           [replace(b, value=self.plain(b.value)) for b in e.binds],
                           self.conv(e.body, k))
        if isinstance(e, CIf):
            # An `if` with no `else` still has to reach `k` when it takes the
            # branch that is not written: `if c { return None }` in statement
            # position falls through with unit, and a fall-through is exactly
            # what `conv` promises not to leave. So the missing arm is supplied
            # -- but only where a jump is actually owed, so an `if` in a term
            # this pass is only walking through reads as it did.
            otherwise = e.otherwise
            if otherwise is None and k is not None:
                otherwise = _unit(e)
            return self.branch(e, k, e.cond, lambda c: CIf(
                e.ty, e.span, c, self.conv(e.then, k),
                None if otherwise is None else self.conv(otherwise, k)))
        if isinstance(e, CMatch):
            return self.branch(e, k, e.scrutinee, lambda s: CMatch(
                e.ty, e.span, s,
                [CAlt(alt.pat, self.conv(alt.body, k)) for alt in e.alts]))
        if isinstance(e, LOOPS):
            return self.loop(e, k)
        return self.anf(e, k)

    def finish(self, e, k: str | None):
        return e if k is None else CJump(TBottom(), e.span, k, [e])

    # -- the rules ----------------------------------------------------------

    def let(self, e: CLet, k: str | None):
        """`let x = v in body`.

        When `v` holds a transfer that leaves it -- a `return` in a branch of
        an `if` that is being bound -- the binding becomes a join: `v` runs
        first and jumps to it with the value, and the join's body is the rest.
        That is exactly what a `let` already means, said in a form a jump can
        land in.
        """
        if not _special(e.value):
            return CLet(e.ty, e.span, e.name, e.bound, self.plain(e.value),
                        self.conv(e.body, k), e.binders)
        if e.binders:
            raise _Unsupported("a transfer under a polymorphic let")
        j = self.fresh("bind")
        return CJoin(e.ty, e.span, j, [CParam(e.name, e.bound)],
                     self.conv(e.body, k), self.conv(e.value, j), False)

    def branch(self, e, k, scrutinee, build):
        """An `if` or a `match`, whose branches are the interesting part.

        A jump goes in each branch and `k` is a *name*, so the continuation is
        shared rather than copied -- which is the property join points exist
        for, and the reason this pass can be written at all without a code-size
        argument attached.
        """
        if not _special(scrutinee):
            return build(self.plain(scrutinee))
        j, value = self.fresh("on"), self.fresh("sv")
        return CJoin(e.ty, e.span, j, [CParam(value, scrutinee.ty)],
                     build(CVar(scrutinee.ty, e.span, value)),
                     self.conv(scrutinee, j), False)

    def anf(self, e, k: str | None):
        """A node whose *operands* hold a transfer: `push(ops, match c { ... })`.

        `bf.tl` writes exactly that, with a `break` and a `continue` among the
        arms, and it is the one shape in the suite that none of the rules above
        covers. What makes it awkward is evaluation order: hoisting the `match`
        out and leaving `ops` in place would evaluate `ops` after it, and the
        evaluator is strict and left to right.

        So every operand up to and including the last special one is bound, in
        order, each to a join whose body is the next -- which is A-normal form,
        arrived at because the order has to be preserved rather than because
        the form is desirable. Operands after the last special one are left
        where they are: nothing has moved past them.
        """
        children, rebuild = _operands(e)
        if children is None or not any(_special(c) for c in children):
            raise _Unsupported(f"a transfer inside a {type(e).__name__}")
        last = max(i for i, c in enumerate(children) if _special(c))
        joins = [(self.fresh("arg"), self.fresh("av")) for _ in children[:last + 1]]
        replaced = [CVar(c.ty, e.span, v) for c, (_, v) in zip(children, joins)]
        replaced += [self.plain(c) for c in children[last + 1:]]
        made = self.finish(rebuild(replaced), k)
        for (name, value), child in zip(reversed(joins), reversed(children[:last + 1])):
            made = CJoin(e.ty, e.span, name, [CParam(value, child.ty)], made,
                         self.conv(child, name), False)
        return made

    def loop(self, e, k: str | None):
        """Any of the four, as a recursive join and its jumps.

        One `join rec` for the iteration and, when there is nothing already
        waiting for the loop's value, one more for what follows it. Then the
        four forms differ only in what one iteration is, which is written as
        ordinary Core below and handed straight back to `conv`.
        """
        after, wrapper = k, None
        if after is None:
            after = self.fresh("af")
            wrapper = CParam(self.fresh("av"), e.ty)

        name = self.fresh("lp")
        pending, iteration, step = self.iteration(e)

        saved = (self.brk, self.cont)
        self.brk, self.cont = after, (name if step is None else step[0])
        try:
            body = self.conv(iteration, None)
        finally:
            self.brk, self.cont = saved

        if step is not None:
            # A C-style `for`'s step, as a join of its own, built *after* the
            # body is converted rather than before. Built before, it would be a
            # `CJoin` handed back to `conv`, which has no rule for one -- and
            # `conv` wrapping an already-made jump in a second jump is exactly
            # the kind of wrong that typechecks.
            joined, value = step
            made_step = CLet(TBottom(), e.span, "%seq", value.ty, value,
                             CJump(TBottom(), e.span, name, []))
            body = CJoin(TBottom(), e.span, joined, [], made_step, body, False)

        made = CJoin(TBottom(), e.span, name, [], body,
                     CJump(TBottom(), e.span, name, []), True)
        for bound, ty, value in reversed(pending):
            made = CLet(made.ty, e.span, bound, ty, value, made)
        if wrapper is None:
            return made
        return CJoin(e.ty, e.span, after, [wrapper],
                     CVar(e.ty, e.span, wrapper.name), made, False)

    def iteration(self, e):
        """One turn of the loop, as ordinary Core with `break` and `continue`
        still in it -- `conv` is what turns those into jumps -- plus whatever
        bindings must stand outside the loop and, for a C-style `for`, its step.

        Written this way rather than emitting the jumps directly, and that is
        the point: `while c { b }` *is* `if c { b; continue } else { break }`,
        so saying so leaves one rule to be right about instead of four.
        """
        span = e.span
        go = CContinue(TBottom(), span)
        stop = CBreak(TBottom(), span, _unit(e))
        if isinstance(e, CWhile):
            return [], CIf(TBottom(), span, e.cond, _seq(e.body, go), stop), None
        if isinstance(e, CLoop):
            return [], _seq(e.body, go), None
        if isinstance(e, CForC):
            pending = []
            if isinstance(e.init, CLet):
                pending.append((e.init.name, e.init.bound,
                                self.plain(e.init.value)))
            elif e.init is not None:
                pending.append(("%seq", e.init.ty, self.plain(e.init)))
            step = None
            if e.step is not None:
                # `continue` runs the step before the test, so the step gets a
                # join and every `continue` this loop owns -- the written ones
                # and the one that falls off the end of the body -- names it
                # rather than the loop's. That is the whole of the difference
                # between a C-style `for` and a `while`.
                value = _stripped(e.step)
                if _special(value):
                    raise _Unsupported("a transfer in a for-loop's step")
                step = (self.fresh("st"), self.plain(value))
            return pending, CIf(TBottom(), span, e.cond, _seq(e.body, go),
                                stop), step
        assert isinstance(e, CForIn)
        pending, term = self.for_in(e)
        return pending, term, None

    def for_in(self, e: CForIn):
        """`for p in seq`, with the cursor made explicit.

        design.md 6.5's elaboration, which `core.py` left as a note. The two
        calls already carry the `Iterator` dictionary -- that is why they are
        terms in the node rather than something this has to find -- so what is
        added here is only the cursor binding and the `Option` match that reads
        `next`'s answer.
        """
        span = e.span
        seq_name, cur_name = self.fresh("sq"), self.fresh("cu")
        next_ty = prune(e.next_fn.ty)
        if not isinstance(next_ty, TFun):
            raise _Unsupported("a loop whose `next` is not a function")
        cursor_ty = next_ty.params[1]
        option_ty = next_ty.ret
        some, none, _ = self.option_parts(option_ty)

        pending = [
            (seq_name, e.seq.ty, self.plain(e.seq)),
            (cur_name, cursor_ty,
             CApp(cursor_ty, span, self.plain(e.iter_fn),
                  [CVar(e.seq.ty, span, seq_name)])),
        ]
        step = CApp(option_ty, span, self.plain(e.next_fn),
                    [CVar(e.seq.ty, span, seq_name),
                     CVar(cursor_ty, span, cur_name)])
        return pending, CMatch(TBottom(), span, step, [
            CAlt(ast.PCon(span, none, []), CBreak(TBottom(), span, _unit(e))),
            CAlt(ast.PCon(span, some, [e.pat]),
                 _seq(e.body, CContinue(TBottom(), span))),
        ])

    def option_parts(self, ty: Type) -> tuple[str, str, Type]:
        """The `Option` constructors, found from the type `next` answers.

        By the type rather than by name: the Prelude's `Option` is an ordinary
        declaration under an ordinary qualified name, and a pass that spelled
        that name out would be one more thing to keep in agreement with it.
        """
        head, args = spine(prune(ty))
        if not isinstance(head, TCon) or not args:
            raise _Unsupported("a loop whose `next` does not answer an Option")
        cons = [(name, info) for name, info in self.decls.constructors.items()
                if info.tycon == head.name]
        some = [n for n, i in cons if i.arity == 1]
        none = [n for n, i in cons if i.arity == 0]
        if len(some) != 1 or len(none) != 1:
            raise _Unsupported(f"'{head.name}' is not shaped like an Option")
        return some[0], none[0], args[0]

    def lam(self, e: CLam) -> CLam:
        """A function body, with its `return`s made into jumps.

        The join is added only where there is a `return` to use it. A function
        that never returns early reads exactly as it did, which keeps the
        golden churn of this pass to the programs it actually changes.
        """
        saved = (self.ret, self.brk, self.cont)
        self.ret, self.brk, self.cont = None, None, None
        try:
            if not _has_return(e.body):
                return CLam(e.ty, e.span, e.params, self.conv(e.body, None),
                            e.name)
            fn = prune(e.ty)
            result = fn.ret if isinstance(fn, TFun) else e.body.ty
            self.ret = self.fresh("ret")
            value = self.fresh("rv")
            inner = self.conv(e.body, self.ret)
            body = CJoin(result, e.span, self.ret, [CParam(value, result)],
                         CVar(result, e.span, value), inner, False)
            return CLam(e.ty, e.span, e.params, body, e.name)
        finally:
            self.ret, self.brk, self.cont = saved


# -- small questions about terms ---------------------------------------------


def _operands(e):
    """The subterms of `e` that are evaluated, in the order the evaluator
    evaluates them, and how to put a node back together from them.

    Order taken from `eval.py` and not from the field order, which is not the
    same thing: `CAssign` evaluates its value before its target. A node absent
    from this table cannot be A-normalized, and a transfer inside one is
    declined rather than guessed at -- including a `CAssign` whose *target* is
    special, since a target is an lvalue and hoisting one would evaluate the
    place instead of assigning to it.
    """
    span = e.span
    if isinstance(e, CApp):
        return ([e.fn] + list(e.args),
                lambda xs: CApp(e.ty, span, xs[0], xs[1:]))
    if isinstance(e, CTuple):
        return list(e.elems), lambda xs: CTuple(e.ty, span, xs)
    if isinstance(e, CArray):
        return list(e.elems), lambda xs: CArray(e.ty, span, xs)
    if isinstance(e, CRecord):
        names = [n for n, _ in e.fields]
        return ([v for _, v in e.fields],
                lambda xs: CRecord(e.ty, span, e.con, list(zip(names, xs))))
    if isinstance(e, CIndex):
        return ([e.target, e.index],
                lambda xs: CIndex(e.ty, span, xs[0], xs[1]))
    if isinstance(e, CField):
        return [e.target], lambda xs: CField(e.ty, span, xs[0], e.name)
    if isinstance(e, CRef):
        return [e.value], lambda xs: CRef(e.ty, span, xs[0])
    if isinstance(e, CDeref):
        return [e.target], lambda xs: CDeref(e.ty, span, xs[0])
    if isinstance(e, CAssign) and not _special(e.target):
        return ([e.value],
                lambda xs: CAssign(e.ty, span, e.target, xs[0]))
    return None, None


def _special(e) -> bool:
    """Whether `e` holds a loop or a transfer outside any lambda.

    Outside any lambda, because a lambda is its own function: the `return` in
    it is that lambda's, and it travels with it.
    """
    if isinstance(e, CLam):
        return False
    if isinstance(e, (LOOPS + TRANSFERS)):
        return True
    if isinstance(e, (CExpr, CBind, CAlt)):
        return any(_special(getattr(e, f.name)) for f in fields(e))
    if isinstance(e, (list, tuple)):
        return any(_special(x) for x in e)
    return False


def _has_return(e) -> bool:
    if isinstance(e, CLam):
        return False
    if isinstance(e, CReturn):
        return True
    if isinstance(e, (CExpr, CBind, CAlt)):
        return any(_has_return(getattr(e, f.name)) for f in fields(e))
    if isinstance(e, (list, tuple)):
        return any(_has_return(x) for x in e)
    return False


def _seq(first, then):
    """`first; then` -- a `let` nothing reads, which is what a statement is."""
    return CLet(then.ty, first.span, "%seq", first.ty, first, then)


def _stripped(step):
    """A C-style `for`'s step, which may be a `let` whose body is a placeholder.

    `coretc.check_open` reads it that way -- the binding scopes over the loop,
    not over a body of its own -- so what runs each turn is the value.
    """
    return step.value if isinstance(step, CLet) else step


def _unit(e) -> CUnit:
    return CUnit(UNIT, e.span)


__all__ = ["collapse"]
