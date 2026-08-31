"""The local reductions: inlining, beta, case-of-known-constructor (M15c).

`plan.txt` item 7's three passes, less the one `joins.py` already does. None of
them knows what a monad is, which is the whole reason item 4's `?`-aware fast
lowering was deleted in favour of this: a `bind` chain is reached the way any
other saturated call to a known small function is reached.

They are one traversal rather than three because they feed each other and it
would be dishonest to pretend otherwise. Inlining `bind` puts a lambda in the
function position of an application, which is beta; beta binds a parameter to a
constructor application, which is case-of-known-constructor; and that selects a
branch, which exposes the next call. Splitting them into three passes over the
whole program would mean running the sequence to a fixed point from outside,
which is the same computation with more traversals and a less obvious bound.

## Termination is structural, not budgeted

Item 6's cap is a budget: monomorphization stops because it has spent enough,
not because it has finished. This pass does not work that way, and it should
not have to -- inlining terminates when the call graph it walks is acyclic, and
the standard way to make it acyclic is to choose a **loop breaker** in each
cycle and never inline that one. That is GHC's answer and it is the one taken
here.

The graph comes out of `core.names_of` and `deps.sccs`, which is Tarjan's
algorithm and already emits components dependencies-first. In each component of
more than one binding, and in each self-recursive singleton, one name is marked
never-inlined. The choice within a component is **arbitrary but stable**: the
lexicographically first, which is the first element because `deps.sccs` sorts
each component. Arbitrary because no criterion here is better than another;
stable because a `.opt` golden would otherwise move whenever a name changed.

Two smaller bounds sit under that one. A binding is inlined only if its body is
under `INLINE_LIMIT` nodes, so the acyclic walk cannot blow up on the way down;
and a name already being inlined is never inlined into its own expansion, which
makes termination true of the code rather than of an argument about the graph.

## Arguments are bound, not substituted -- unless they are values

The evaluator is strict and left to right, so substituting an argument
expression into a body that mentions its parameter twice would evaluate it
twice, and into one that mentions it never would evaluate it never. Neither is
what the call meant. So an argument becomes a `CLet`, which is exactly a
call-by-value binding.

The exception is an argument that is already a **value** -- a literal, a
variable, a saturated constructor application of values. Duplicating one costs
nothing and evaluates nothing, and substituting it is what lets the next
reduction see through it: `pick(Some(5))` inlines to `match Some(5) { ... }`
and then to `5`, where let-binding `o` would have left a `match` on a variable
and stopped the chain there.

Substitution is capture-avoiding by refusing rather than by renaming: if the
value mentions a name the body binds, or the body rebinds the parameter itself,
the argument is let-bound instead. That costs an occasional reduction and needs
no fresh-name supply, and a pass that renames binders is a pass whose output no
one can read against its input.
"""

from __future__ import annotations

from dataclasses import fields, replace

from . import ast
from .core import (
    CAlt, CApp, CBind, CBreak, CCon, CContinue, CExpr, CForC, CForIn, CIf,
    CJoin, CJump, CLam, CLet, CLetRec, CLit, CMatch, CParam, CPrim, CProgram,
    CReturn, CTuple, CUnit, CVar, names_of,
)
from .deps import pattern_vars, sccs
from .types import TBottom

# How large a body may be and still be inlined, counted in Core nodes. Small
# enough that a call site does not become unreadable, large enough for the
# things this exists to reach: a method's body, an instance's `bind`.
INLINE_LIMIT = 40


def reduce_program(program: CProgram) -> CProgram:
    """Every binding's body, reduced."""
    inliner = _Reducer(program)
    return CProgram(
        dicts=[replace(b, value=inliner.expr(b.value)) for b in program.dicts],
        binds=[replace(b, value=inliner.expr(b.value)) for b in program.binds],
    )


# -- the call graph, and the cycles in it ------------------------------------


def loop_breakers(program: CProgram) -> set[str]:
    """One binding per cycle, never to be inlined.

    Deterministic on purpose: `deps.sccs` sorts each component, so taking the
    first is the lexicographically first name, and a golden that depends on
    which binding was broken does not move when an unrelated one is added.
    """
    binds = program.dicts + program.binds
    known = {b.name for b in binds}
    graph = {b.name: names_of(b.value) & known for b in binds}
    out: set[str] = set()
    for component in sccs(graph):
        if len(component) > 1 or component[0] in graph[component[0]]:
            out.add(component[0])
    return out


# -- the traversal -----------------------------------------------------------


class _Reducer:
    def __init__(self, program: CProgram) -> None:
        self.breakers = loop_breakers(program)
        # Only a monomorphic, immutable, lambda-valued binding can be inlined.
        # A binding with `binders` is a `CTyLam` and its call sites are type
        # applications, which is M14's business and not this one's; a mutable
        # one is a cell, and reading it twice is not reading it once.
        self.bodies = {
            b.name: b.value
            for b in program.dicts + program.binds
            if not b.binders and not b.mutable and isinstance(b.value, CLam)
        }
        # What is currently being inlined. Belt and braces beside the loop
        # breakers: this makes non-termination impossible in the code rather
        # than merely absent from the graph.
        self.active: list[str] = []
        # A binding's body, reduced, computed once. Not an optimization of an
        # optimization: without it a chain `f` calls `g` calls `h` re-reduces
        # `h` once per path that reaches it, which is exponential in the depth
        # of the call graph and was, measurably, the whole cost of this pass.
        self.reduced: dict[str, CExpr] = {}
        # For the join points case-of-case invents. Per program and in
        # traversal order, so it is deterministic and a golden can hold it.
        self.counter = 0

    def expr(self, e):
        """`e` with its subterms reduced, then reduced itself to a fixed point.

        Bottom-up, so each rewrite sees children that are already as small as
        they are going to get -- which is what lets one traversal do the work
        of running three passes to a fixed point.
        """
        if not isinstance(e, CExpr):
            return e
        e = self.children(e)
        while True:
            made = self.step(e)
            if made is None:
                return e
            # And walk it again. A reduction does not only rewrite the node it
            # fires on: substituting a continuation for a parameter puts a
            # lambda where `bind`'s body had `f(x)`, and that beta redex is in
            # a subterm this traversal has already been past. Reducing only the
            # root left every `?` chain one beta short, which is exactly the
            # reduction the whole milestone is for.
            e = self.children(made)

    def children(self, e):
        return type(e)(**{f.name: self.value(getattr(e, f.name))
                          for f in fields(e)})

    def value(self, v):
        if isinstance(v, CExpr):
            return self.expr(v)
        if isinstance(v, CAlt):
            return CAlt(v.pat, self.expr(v.body))
        if isinstance(v, CBind):
            return replace(v, value=self.expr(v.value))
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if isinstance(v, tuple):
            return tuple(self.value(x) for x in v)
        return v

    def step(self, e):
        """One reduction at the root of `e`, or None if none applies.

        Whatever it returns has reduced subterms: `_inline` reduces the body it
        brings in, and the other two only rearrange terms that came from here.
        """
        if isinstance(e, CApp):
            return self.beta(e) if isinstance(e.fn, CLam) else self.inline(e)
        if isinstance(e, CMatch):
            return (self.known_constructor(e) or self.float_let(e)
                    or self.case_of_case(e))
        if isinstance(e, CLet):
            return (self.dead_let(e) or self.trivial_let(e)
                    or self.let_to_match(e))
        return None

    def fresh(self, hint: str) -> str:
        self.counter += 1
        return f"%{hint}{self.counter}"

    # -- the three reductions --------------------------------------------

    def inline(self, e: CApp):
        """A saturated call to a small, known, non-loop-breaking binding."""
        fn = e.fn
        if not isinstance(fn, CVar):
            return None
        name = fn.name
        if name in self.breakers or name in self.active:
            return None
        lam = self.bodies.get(name)
        if lam is None or len(lam.params) != len(e.args):
            return None
        body = self.body_of(name)
        # The *reduced* body against the limit, not the written one. A body of
        # ten nodes that inlines to five hundred is five hundred nodes at every
        # call site, and measuring the source would let exactly that through
        # one level at a time.
        if body is None or _size(body) > INLINE_LIMIT:
            return None
        if _transfers(body):
            return None
        return _apply(lam.params, e.args, body, e.ty)

    def body_of(self, name: str):
        """A binding's reduced body, computed once.

        The `active` guard cannot actually fire -- every cycle in the call
        graph contains a loop breaker and a breaker is never inlined, so a
        chain of expansions cannot come back to a name it is already inside.
        It is here so that termination is a property of this function rather
        than of that argument. If it ever did fire, the memoized body would be
        one expansion short of fully reduced, which costs an optimization and
        cannot cost an answer.
        """
        found = self.reduced.get(name)
        if found is None:
            self.active.append(name)
            try:
                found = self.expr(self.bodies[name].body)
            finally:
                self.active.pop()
            self.reduced[name] = found
        return found

    def beta(self, e: CApp):
        """A lambda applied directly. What inlining a call site leaves behind
        when the callee's own body was itself a call."""
        lam = e.fn
        assert isinstance(lam, CLam)
        if len(lam.params) != len(e.args) or _transfers(lam.body):
            return None
        return _apply(lam.params, e.args, lam.body, e.ty)

    def dead_let(self, e: CLet):
        """A binding nothing reads, of a value, is not a binding.

        Not swept in for tidiness: it is what the other three leave behind.
        Devirtualization turns `d.mul(x, 10)` into `%inst.Mul.Int#mul(x, 10)`
        and leaves the `let %d1.Mul` that fetched the dictionary standing, and
        inlining a method's body drags one of those to every call site. The
        value must be a value -- a `let %seq = print(x)` reads nothing either,
        and dropping it would drop the printing.
        """
        if not _is_value(e.value) or e.binders:
            return None
        return e.body if e.name not in names_of(e.body) else None

    def float_let(self, e: CMatch):
        """`match (let x = v in b) { alts }` is `let x = v in match b { alts }`.

        Not a reduction in itself: it is what lets the other rules see. Every
        call this pass inlines becomes a `let` chain around the body, so a
        scrutinee that is morally `Some(5)` arrives as several bindings wrapped
        around it, and case-of-known-constructor -- which looks only at the
        node in front of it -- would stop there.

        Declined when the alternatives mention the name, since moving them
        inside the binding would rebind it out from under them.
        """
        scrut = e.scrutinee
        if not isinstance(scrut, CLet) or scrut.binders:
            return None
        if scrut.name in names_of(e.alts):
            return None
        inner = CMatch(e.ty, e.span, scrut.body, e.alts)
        return CLet(e.ty, scrut.span, scrut.name, scrut.bound, scrut.value,
                    inner)

    def case_of_case(self, e: CMatch):
        """`match (match S {...}) { alts }`, with the outer match pushed in.

        The pass item 7 names as the one that actually erases `Flow`. A `?`
        builds a `Flow` in one branch and scrutinises it downstream, and the
        two never meet until the downstream match is moved next to the
        constructor -- at which point case-of-known-constructor collapses both.

        It is also the pass that duplicates continuations, which is why item 7
        insists on join points *first*. `alts` goes into a `CJoin`, each branch
        that does not reduce becomes a `jump` to it, and only the branches that
        genuinely collapse carry a copy. Without the join, a chain of `?` over
        a four-constructor `Flow` multiplies its continuation by four at every
        step, which is the classic way to get a correct compiler that emits
        absurd amounts of code.

        Declined outright when no branch reduces: the join would then be pure
        overhead, and this is not a rewrite worth doing for its own sake.
        """
        scrut = e.scrutinee
        if isinstance(scrut, CMatch):
            branches = [alt.body for alt in scrut.alts]
        elif isinstance(scrut, CIf) and scrut.otherwise is not None:
            branches = [scrut.then, scrut.otherwise]
        else:
            return None
        if _transfers(scrut):
            # A branch holding a `return` is one whose value is not what the
            # outer match will scrutinise. Item 7's last step is what makes
            # this case ordinary; until then it is one to leave alone.
            return None

        name = self.fresh("cc")
        pushed = []
        landed = False
        for branch in branches:
            made = self.expr(CMatch(e.ty, e.span, branch, e.alts))
            if _mentions_alts(made, e.alts):
                pushed.append(CJump(TBottom(), branch.span, name, [branch]))
            else:
                pushed.append(made)
                landed = True
        if not landed:
            return None

        param = CParam(self.fresh("cv"), scrut.ty)
        body = CMatch(e.ty, e.span, CVar(scrut.ty, e.span, param.name), e.alts)
        if isinstance(scrut, CMatch):
            rest = CMatch(e.ty, scrut.span, scrut.scrutinee,
                          [CAlt(alt.pat, made)
                           for alt, made in zip(scrut.alts, pushed)])
        else:
            rest = CIf(e.ty, scrut.span, scrut.cond, pushed[0], pushed[1])
        if not any(isinstance(n, CJump) and n.name == name
                   for n in _nodes(rest)):
            # Every branch reduced, so nothing jumps and the join is dead.
            return rest
        return CJoin(e.ty, e.span, name, [param], body, rest, False)

    def trivial_let(self, e: CLet):
        """A binding of a name to a name is not a binding.

        `let %k1 = x in let a = %k1 in ...` is what a `?` chain becomes once
        `bind` is inlined and its continuation beta-reduced: three names for
        one value. Substituting costs nothing -- reading a variable is free and
        may be done twice or never -- and it is what puts the `if` that built
        the value next to the `match` that scrutinises it.

        Only names and literals, not every value: substituting a `CLam` would
        duplicate its body at each mention, which is inlining and has a size
        limit for a reason.
        """
        if e.binders or not isinstance(e.value, (CVar, CLit, CUnit, CCon)):
            return None
        bound = _binders_of(e.body)
        if e.name in bound or (_free_names(e.value) & bound):
            return None
        return _substitute(e.body, {e.name: e.value})

    def let_to_match(self, e: CLet):
        """`let x = v in match x {...}` is `match v {...}`, when `x` is read
        only there.

        The other half of putting a producer next to its consumer, and the one
        that hands case-of-case something to work on: `v` is an `if` returning
        `Some` or `None`, and the `match` that reads it is one binding away.
        """
        body = e.body
        if e.binders or not isinstance(body, CMatch):
            return None
        if not isinstance(body.scrutinee, CVar) or body.scrutinee.name != e.name:
            return None
        if e.name in _free_names(body.alts):
            return None
        if _free_names(e.value) & _binders_of(body.alts):
            return None
        return CMatch(body.ty, body.span, e.value, body.alts)

    def known_constructor(self, e: CMatch):
        """A `match` on a constructor whose identity is right there.

        Scanned in source order and stopped at the first alternative that is
        not a *definite* mismatch, so an earlier arm that might have matched
        keeps this from firing. A pattern that binds anything but plain
        variables is left alone: the nested case is a real transformation and
        this is not the milestone for it.

        The scrutinee need not be a value, but nothing it computes may be
        thrown away. Selecting a branch discards the arms not taken -- which a
        `match` would not have run anyway -- and it discards the constructor
        application itself, which it *would* have. So every argument is bound,
        in order, including the ones the pattern ignores, under a name nothing
        reads. `Some(launch())` still launches.
        """
        con, args = _constructor(e.scrutinee)
        if con is None:
            return None
        for alt in e.alts:
            pat = _unannot(alt.pat)
            if isinstance(pat, ast.PCon):
                if pat.name != con:
                    continue  # a different constructor: cannot match
                subs = [_unannot(a) for a in pat.args]
                if not all(isinstance(p, (ast.PVar, ast.PWild)) for p in subs):
                    return None
                names = [p.name if isinstance(p, ast.PVar) else self.fresh("seq")
                         for p in subs]
                return _apply_names(names, args, alt.body, e.ty)
            if isinstance(pat, ast.PVar):
                return _apply_names([pat.name], [e.scrutinee], alt.body, e.ty)
            if isinstance(pat, ast.PWild):
                return _apply_names([self.fresh("seq")], [e.scrutinee],
                                    alt.body, e.ty)
            return None  # a literal, a tuple, a record: not decided here
        return None


# -- binding a call's arguments ----------------------------------------------


def _apply(params, args, body, ty):
    return _apply_names([p.name for p in params], list(args), body, ty)


def _apply_names(names, args, body, ty):
    """`body` with each name bound to its argument, or None if it cannot be.

    A value argument is substituted when that cannot capture anything, because
    seeing through it is what lets the next reduction fire. Everything else
    becomes a `CLet`, which is call by value written down.

    None when the bindings would capture each other. `let a = e1 in let b = e2`
    puts `a` in scope for `e2`, and a call's arguments are all evaluated in the
    caller's scope, so if `e2` mentions a name some earlier parameter also has,
    the chain means something the call did not. The names in question are a
    callee's parameters and a caller's locals, so it takes a coincidence -- but
    a reduction that is wrong on a coincidence is wrong.
    """
    bound = _binders_of(body)
    substitution = {}
    remaining: list[tuple[str, object]] = []
    for index, (name, arg) in enumerate(zip(names, args)):
        free = _free_names(arg)
        if free & set(names[:index]):
            return None
        if _is_value(arg) and name not in bound and not (free & bound):
            substitution[name] = arg
        else:
            remaining.append((name, arg))
    if substitution:
        body = _substitute(body, substitution)
    for name, arg in reversed(remaining):
        body = CLet(ty, arg.span, name, arg.ty, arg, body)
    return body


def _substitute(e, mapping):
    if isinstance(e, CVar):
        return mapping.get(e.name, e)
    if isinstance(e, CAlt):
        return CAlt(e.pat, _substitute(e.body, mapping))
    if isinstance(e, CBind):
        return replace(e, value=_substitute(e.value, mapping))
    if isinstance(e, CExpr):
        return type(e)(**{f.name: _substitute(getattr(e, f.name), mapping)
                          for f in fields(e)})
    if isinstance(e, list):
        return [_substitute(x, mapping) for x in e]
    if isinstance(e, tuple):
        return tuple(_substitute(x, mapping) for x in e)
    return e


# -- small questions about terms ---------------------------------------------


def _is_value(e) -> bool:
    """Whether evaluating `e` does no work and can be done twice or never.

    A `CVar` counts: the evaluator's variables are immutable bindings, and a
    `var` is a `CRef` read through a `CDeref`, which is not this.
    """
    if isinstance(e, (CLit, CUnit, CVar, CCon, CPrim, CLam)):
        return True
    if isinstance(e, CTuple):
        return all(_is_value(x) for x in e.elems)
    if isinstance(e, CApp) and isinstance(e.fn, CCon):
        return all(_is_value(a) for a in e.args)
    return False


def _nodes(e):
    if isinstance(e, (CExpr, CBind, CAlt)):
        yield e
        for f in fields(e):
            yield from _nodes(getattr(e, f.name))
    elif isinstance(e, (list, tuple)):
        for x in e:
            yield from _nodes(x)


def _mentions_alts(e, alts) -> bool:
    """Whether the alternatives `alts` are still being matched on inside `e`.

    Identity, not equality, and that is the point: it asks whether *this*
    match survived the reduction, which is exactly the question "did pushing
    it into the branch buy anything". A structural comparison would answer a
    different and less useful question.
    """
    return any(isinstance(n, CMatch) and n.alts is alts for n in _nodes(e))


def _transfers(e) -> bool:
    """Whether `e` contains a control transfer that leaves it.

    `return`, `break` and `continue` are still nodes rather than jumps -- item
    7's last step is what changes that -- and they name their target by *where
    they are*. So a body holding one cannot be moved into another function or
    out of the lambda that bound it: the `return 0` inside `depth`, dropped
    into `depth`'s caller, returns from the caller.

    A transfer under a nested `CLam` belongs to that lambda and travels with
    it, and is not counted. Nothing else is exempt, including the bodies of the
    loop forms: `break` there is the loop's, and until M15e the loop is not
    something this pass may move either.
    """
    if isinstance(e, (CReturn, CBreak, CContinue)):
        return True
    if isinstance(e, CLam):
        return False
    if isinstance(e, (CExpr, CBind, CAlt)):
        return any(_transfers(getattr(e, f.name)) for f in fields(e))
    if isinstance(e, (list, tuple)):
        return any(_transfers(x) for x in e)
    return False


def _constructor(e):
    """`e` read as a saturated constructor application, or `(None, [])`."""
    if isinstance(e, CCon):
        return e.name, []
    if isinstance(e, CApp) and isinstance(e.fn, CCon):
        return e.fn.name, list(e.args)
    return None, []


def _size(e) -> int:
    if isinstance(e, (CExpr, CBind, CAlt)):
        return 1 + sum(_size(getattr(e, f.name)) for f in fields(e))
    if isinstance(e, (list, tuple)):
        return sum(_size(x) for x in e)
    return 0


def _free_names(e) -> set[str]:
    """The term variables `e` mentions and does not itself bind.

    `core.names_of` is the over-approximation -- every name, bound or not --
    and it is the right one for keeping a binding alive. It is the wrong one
    for asking whether moving a term could capture something, and using it
    there cost most of this pass's reductions: the continuation a `?` builds
    contains `let x = ... in ...`, and `Option`'s `bind` calls its first
    parameter `x`, so a guard reading every mentioned name refused to inline
    `bind` at all.

    Binders are read from field names, as in `_binders_of`, plus the two
    scopes that are not simply "this field's binders cover that field": a
    `CLet` binds only over its body, and a `CForC`'s init binds over the rest
    of the loop.
    """
    if e is None or isinstance(e, (str, int, float, bool)):
        return set()
    if isinstance(e, CVar):
        return {e.name}
    if isinstance(e, CLam):
        return _free_names(e.body) - {p.name for p in e.params}
    if isinstance(e, CLet):
        return _free_names(e.value) | (_free_names(e.body) - {e.name})
    if isinstance(e, CLetRec):
        inner = _free_names(e.body)
        for bind in e.binds:
            inner |= _free_names(bind.value)
        return inner - {b.name for b in e.binds}
    if isinstance(e, CJoin):
        body = _free_names(e.body) - {p.name for p in e.params}
        if e.recursive:
            body -= {e.name}
        return body | (_free_names(e.rest) - {e.name})
    if isinstance(e, CJump):
        return set().union(*(_free_names(a) for a in e.args)) if e.args else set()
    if isinstance(e, CAlt):
        return _free_names(e.body) - set(pattern_vars(e.pat))
    if isinstance(e, CForIn):
        bound = set(pattern_vars(e.pat))
        return ((_free_names(e.seq) | _free_names(e.iter_fn)
                 | _free_names(e.next_fn)) | (_free_names(e.body) - bound))
    if isinstance(e, CForC):
        rest = (_free_names(e.cond) | _free_names(e.step)
                | _free_names(e.body))
        if isinstance(e.init, CLet):
            return _free_names(e.init.value) | (rest - {e.init.name})
        return _free_names(e.init) | rest
    if isinstance(e, (CExpr, CBind)):
        out: set[str] = set()
        for f in fields(e):
            out |= _free_names(getattr(e, f.name))
        return out
    if isinstance(e, (list, tuple)):
        out = set()
        for x in e:
            out |= _free_names(x)
        return out
    return set()


def _binders_of(e) -> set[str]:
    """Every name bound anywhere inside `e`.

    Driven by *field names* rather than by a case per node -- a `pat` binds
    what its pattern binds, a `params` binds its parameters' names, and a node
    that binds one name calls it `name`. A case list is what this was, and it
    silently omitted `CForIn.pat`: the `Array` instance's `bind` walks its
    elements in a `for`, so a continuation substituted into it was captured by
    the very element it was meant to be applied to. A rule about field names is
    one a node added later cannot fall outside of by accident.

    An over-approximation, and in the safe direction: a name that appears here
    but binds nothing on the path to an occurrence only costs a substitution
    this pass declines to make.
    """
    out: set[str] = set()

    def walk(v) -> None:
        if isinstance(v, (CLet, CJoin, CBind)):
            out.add(v.name)
        if isinstance(v, (CExpr, CBind, CAlt)):
            for f in fields(v):
                held = getattr(v, f.name)
                if f.name == "pat":
                    out.update(pattern_vars(held))
                elif f.name == "params":
                    out.update(p.name for p in held)
                else:
                    walk(held)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    walk(e)
    return out


def _unannot(pat):
    while isinstance(pat, ast.PAnnot):
        pat = pat.pat
    return pat


__all__ = ["INLINE_LIMIT", "loop_breakers", "reduce_program"]
