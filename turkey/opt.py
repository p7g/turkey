"""The local reductions, from inlining through loop-result fusion (M15c-M16e).

`plan.txt` item 7's local passes, around the discovery `joins.py` does. None of
them knows what a monad is, which is the whole reason item 4's `?`-aware fast
lowering was deleted in favour of this: a `bind` chain is reached the way any
other saturated call to a known small function is reached.

Within either side of join discovery they are one traversal rather than a pass
per rule, because they feed each other and it would be dishonest to pretend
otherwise. Inlining `bind` puts a lambda in the function position of an
application, which is beta; beta binds a parameter to a constructor
application, which is case-of-known-constructor; and that selects a branch,
which exposes the next call. Splitting those rules into separate passes over
the whole program would mean running the sequence to a fixed point from
outside, which is the same computation with more traversals and a less obvious
bound. Join discovery is the representation boundary between two such local
reductions: it exposes jumps which the second traversal can specialize.

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

Two smaller bounds sit under that one. A binding is ordinarily inlined only if
its body is under `INLINE_LIMIT` nodes. A body under the larger speculative
ceiling may be reduced against a particular call's known arguments, but is
committed only when the residual comes back under `INLINE_LIMIT`, and large
speculation never nests. Thus the acyclic walk cannot blow up on the way down.
A name already being inlined is never inlined into its own expansion, which
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

from dataclasses import fields as _dataclass_fields, replace

from . import ast
from .core import (
    CAlt, CApp, CBind, CCon, CExpr, CIf,
    CJoin, CJump, CLam, CLet, CLetRec, CLit, CMatch, CParam, CPrim, CProgram,
    CTuple, CTyApp, CUnit, CVar, names_of,
)
from .decls import substitute
from .deps import pattern_vars, sccs
from .types import TBottom, Type, type_key

# `dataclasses.fields` builds a fresh tuple on every call, and the six generic
# walks below call it once per node per traversal -- fifty-five million times
# while compiling the bootstrap compiler, which is a sixth of that compile. The
# answer depends only on the class, so it is asked once per class.
#
# Found by profiling `boot`, which is the first program large enough for this
# module's constant factors to be visible at all (`plan.txt` item 9: the scale
# test for the new execution path).
_FIELDS: dict[type, tuple] = {}


def fields(node):
    """`dataclasses.fields`, memoised by class."""
    cls = type(node)
    found = _FIELDS.get(cls)
    if found is None:
        found = _FIELDS[cls] = tuple(_dataclass_fields(cls))
    return found


# How large a body may be and still be inlined, counted in Core nodes. Small
# enough that a call site does not become unreadable, large enough for the
# things this exists to reach: a method's body, an instance's `bind`.
INLINE_LIMIT = 32

# A shared join is copied only when specializing a known constructor makes
# the copy small. This is deliberately the same scale as ordinary inlining:
# both transformations trade a bounded amount of code for exposing a local
# reduction, and neither fixture-specific traffic nor the number of callers
# gets to raise that bound implicitly.
JOIN_SPECIALIZE_LIMIT = INLINE_LIMIT

# A larger body may be *examined* at a call site, but is admitted only if
# applying the known arguments and reducing it brings the residual back under
# INLINE_LIMIT.  This bounds the work spent asking the question as well as the
# code ultimately copied.  It is deliberately not another emission limit.
SPECULATIVE_INLINE_LIMIT = INLINE_LIMIT * 4
# What a call may cost when its result is scrutinised right there. The
# ordinary limit asks only how much code a call site grows by, which is the
# wrong question when the caller is about to take the constructor apart:
# inlining `Iterator.next` puts a `Some` next to the `match` that reads it,
# `case_of_case` pushes the match through the join the callee's early `return`
# left behind, and `known_constructor` then deletes both the allocation and
# the branch. GHC gives the same discount by name -- a case on a call's result
# raises the inliner's budget, so `Option` never reaches the heap -- and
# LLVM's inliner does it too, crediting a call whose result feeds a branch.
# The whole cost of a `for` loop's `Some` per element rides on this.
SCRUTINEE_INLINE_LIMIT = INLINE_LIMIT * 2
# What a callee that always panics may carry into its caller. A bare
# forwarding call -- `fun error(message) = Prim.error(message)` -- is worth
# copying, because it costs the caller nothing and keeps the standard library
# out of the panic trace. Anything larger is the failure path charging the
# success path, which is what this exists to stop. On `_size`'s scale that
# forwarder is the single call and nothing else, since its argument is a
# variable and variables are free.
BOTTOM_INLINE_LIMIT = 1


def reduce_program(program: CProgram) -> CProgram:
    """Every binding's body, reduced -- callees before their callers.

    The order is not cosmetic. `body_of` reduces a callee the first time some
    call site asks for it, and if that call site is itself inside a body being
    reduced, the callee's traversal runs *nested inside* the caller's. On a
    program with a deep call graph that makes the traversal's stack depth the
    product of call-graph depth and term depth, which is what made this pass
    fail to compile the bootstrap compiler at all.

    Reducing in dependency order -- `deps.sccs` already emits it -- means the
    memo is warm before anyone asks, so `body_of` is a dictionary lookup and
    every traversal is one term deep. Within a cycle there is no such order and
    the nesting can still happen, but it is then bounded by the size of the
    component rather than by the height of the whole graph.
    """
    inliner = _Reducer(program)
    binds = program.dicts + program.binds
    first: dict[str, int] = {}
    for index, bind in enumerate(binds):
        first.setdefault(bind.name, index)
    done: dict[int, CExpr] = {}
    for component in sccs(call_graph(binds)):
        for name in component:
            index = first[name]
            value = inliner.expr(binds[index].value)
            done[index] = value
            inliner.memoize(name, value)
    # A name bound twice has one entry in the graph, so its later bindings are
    # in no component. Nothing else can be missing.
    for index, bind in enumerate(binds):
        if index not in done:
            done[index] = inliner.expr(bind.value)
    split = len(program.dicts)
    return CProgram(
        dicts=[replace(b, value=done[i]) for i, b in enumerate(program.dicts)],
        binds=[replace(b, value=done[split + i])
               for i, b in enumerate(program.binds)],
    )


# -- the call graph, and the cycles in it ------------------------------------


def call_graph(binds: list[CBind]) -> dict[str, set[str]]:
    """Which top-level bindings each one mentions, and nothing else."""
    known = {b.name for b in binds}
    return {b.name: names_of(b.value) & known for b in binds}


def loop_breakers(program: CProgram) -> set[str]:
    """One binding per cycle, never to be inlined.

    Deterministic on purpose: `deps.sccs` sorts each component, so taking the
    first is the lexicographically first name, and a golden that depends on
    which binding was broken does not move when an unrelated one is added.
    """
    graph = call_graph(program.dicts + program.binds)
    out: set[str] = set()
    for component in sccs(graph):
        if len(component) > 1 or component[0] in graph[component[0]]:
            out.add(component[0])
    return out


# -- the traversal -----------------------------------------------------------


class _Reducer:
    def __init__(self, program: CProgram) -> None:
        self.breakers = loop_breakers(program)
        # Only an immutable, lambda-valued binding can be inlined. A mutable
        # one is a cell, and reading it twice is not reading it once. Type
        # binders are fine: a call through `CTyApp` instantiates the lambda in
        # place, which is type-level beta reduction rather than M14's creation
        # of another top-level specialization.
        self.bindings = {
            b.name: b
            for b in program.dicts + program.binds
            if not b.mutable and isinstance(b.value, CLam)
        }
        # What is currently being inlined. Belt and braces beside the loop
        # breakers: this makes non-termination impossible in the code rather
        # than merely absent from the graph.
        self.active: list[str] = []
        # `_size` and `_transfers` of each reduced body, beside the body
        # itself: both are questions about the callee, so both are asked
        # once per binding rather than once per call site.
        self.sizes: dict[tuple, int] = {}
        self.moves: dict[tuple, bool] = {}
        self.ends: dict[tuple, bool] = {}
        # A speculative large inline may reduce ordinary small calls inside
        # its residual, but does not recursively speculate about another large
        # call.  Besides bounding compile time, this makes the profitability
        # decision local rather than letting a chain spend the ceiling once at
        # every level.
        self.speculating = False
        # A binding's body, reduced, computed once. Not an optimization of an
        # optimization: without it a chain `f` calls `g` calls `h` re-reduces
        # `h` once per path that reaches it, which is exponential in the depth
        # of the call graph and was, measurably, the whole cost of this pass.
        self.reduced: dict[tuple[str, tuple], CExpr] = {}
        # For the join points case-of-case invents. Per program and in
        # traversal order, so it is deterministic and a golden can hold it.
        self.counter = 0
        self.used_names = set()
        for bind in program.dicts + program.binds:
            self.used_names.add(bind.name)
            self.used_names |= names_of(bind.value) | _binders_of(bind.value)

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

        Whatever it returns has reduced subterms: inlining and join
        specialization reduce the bodies they bring in, and the remaining
        rules only rearrange terms that came from here.
        """
        if isinstance(e, CApp):
            return self.beta(e) if isinstance(e.fn, CLam) else self.inline(e)
        if isinstance(e, CMatch):
            return (self.known_constructor(e) or self.inline_scrutinee(e)
                    or self.float_let(e)
                    or self.fuse_recursive_join_result(e)
                    or self.case_of_case(e) or self.case_of_join(e))
        if isinstance(e, CLet):
            return (self.dead_let(e) or self.trivial_let(e)
                    or self.let_to_match(e) or self.float_let_through_join(e))
        if isinstance(e, CJoin):
            return self.specialize_join(e)
        return None

    def fresh(self, hint: str) -> str:
        while True:
            self.counter += 1
            name = f"%{hint}{self.counter}"
            if name not in self.used_names:
                self.used_names.add(name)
                return name

    # -- the reductions --------------------------------------------------

    def inline_scrutinee(self, e: CMatch):
        """A call under a `match`, inlined on the larger budget.

        `expr` works bottom-up, so by the time this `match` is reduced its
        scrutinee has already been offered to `inline` and turned down on
        size. Being scrutinised is the information that offer did not have.
        """
        if not isinstance(e.scrutinee, CApp):
            return None
        made = self.inline(e.scrutinee, limit=SCRUTINEE_INLINE_LIMIT)
        return None if made is None else replace(e, scrutinee=made)

    def inline(self, e: CApp, limit: int = INLINE_LIMIT):
        """A saturated call to a small, known, non-loop-breaking binding."""
        fn = e.fn
        targs = None
        if isinstance(fn, CVar):
            name = fn.name
        elif isinstance(fn, CTyApp) and isinstance(fn.fn, CVar):
            name, targs = fn.fn.name, fn.args
        else:
            return None
        if name in self.breakers or name in self.active:
            return None
        bind = self.bindings.get(name)
        if bind is None:
            return None
        if targs is None:
            if bind.binders:
                return None
            lam = bind.value
            key = (name, ())
        else:
            if not bind.binders or len(bind.binders) != len(targs):
                return None
            mapping = {b.id: a for b, a in zip(bind.binders, targs)}
            lam = _instantiate_types(bind.value, mapping)
            key = (name, tuple(type_key(a) for a in targs))
        assert isinstance(lam, CLam)
        if len(lam.params) != len(e.args):
            return None
        body = self.body_of(name, key, lam)
        # The *reduced* body drives the ordinary limit, not the written one. A
        # small source body that expands to five hundred nodes is five hundred
        # nodes at every call site. A larger body gets the separate speculative
        # path below, whose post-application residual must become small again.
        if body is None:
            return None
        # Both questions are about the *callee's* body, which `body_of` has
        # already computed once and shares with every call site. Asking them
        # here, before anything is copied, is what keeps a body that can never
        # be inlined from being copied and substituted at every site that
        # mentions it -- which is most of the work this pass did on a program
        # the size of the bootstrap compiler.
        if self.transfers(key, body):
            return None
        size = self.size_of(key, body)
        if size > SPECULATIVE_INLINE_LIMIT or (self.speculating
                                               and size > limit):
            return None
        if self.bottoms(key, body) and size > BOTTOM_INLINE_LIMIT:
            # A callee that always panics has no useful residual -- there is
            # nothing for the caller to reduce against -- so copying in
            # anything it carries lets the *failure* path decide what the
            # success path costs. `Data.Array#bounds` is the case that
            # matters: every string in its message is allocated, so inlining
            # the message put thirteen live pointers across allocations into a
            # function that is otherwise two comparisons, and every in-range
            # access paid for a root frame it could not use. Rust marks these
            # paths `#[cold] #[inline(never)]` and GHC keeps bottoming
            # expressions off the hot path with `exprIsBottom`.
            #
            # Only when it carries something, though. `Std.Classes#error` is
            # `Prim.error(message)` and nothing else, so inlining it costs the
            # caller nothing -- and *not* inlining it would put a frame in
            # every panic trace pointing at the standard library rather than
            # at the code that failed.
            return None
        # A copied callee body becomes part of this call site.  Keep spans on
        # caller-supplied arguments, but make every node copied from the callee
        # blame the invocation if a later Core check rejects the residual.
        body = _rebase_spans(body, e.span)
        made = _apply(lam.params, e.args, body, e.ty)
        if made is None:
            return None
        if size <= limit:
            return made

        # Restored to what it was, not to False. Speculation is meant not to
        # nest -- the flag is the whole mechanism for that -- and clearing it
        # on the way out of an inner speculation re-armed the outer one, so a
        # chain of large calls could speculate at every level after all. That
        # is unbounded rather than merely expensive: the traversal's depth grew
        # with whatever stack it was given (68k levels at 1 GiB, 457k at 3 GiB)
        # and the bootstrap compiler could not be optimized at any size.
        was = self.speculating
        self.speculating = True
        try:
            residual = self.expr(made)
        finally:
            self.speculating = was
        return residual if _size(residual) <= limit else None

    def size_of(self, key: tuple[str, tuple], body) -> int:
        """`_size` of a reduced body, once per binding rather than per site."""
        found = self.sizes.get(key)
        if found is None:
            found = self.sizes[key] = _size(body)
        return found

    def transfers(self, key: tuple[str, tuple], body) -> bool:
        """`_transfers` of a reduced body, once per binding rather than per
        site. Both are answers about the callee, and the callee is shared."""
        found = self.moves.get(key)
        if found is None:
            found = self.moves[key] = _transfers(body)
        return found

    def bottoms(self, key: tuple[str, tuple], body) -> bool:
        """`_bottoms` of a reduced body, on the same terms as the other two."""
        found = self.ends.get(key)
        if found is None:
            found = self.ends[key] = _bottoms(body)
        return found

    def body_of(self, name: str, key: tuple[str, tuple], lam: CLam):
        """A binding's reduced body, computed once.

        The `active` guard cannot actually fire -- every cycle in the call
        graph contains a loop breaker and a breaker is never inlined, so a
        chain of expansions cannot come back to a name it is already inside.
        It is here so that termination is a property of this function rather
        than of that argument. If it ever did fire, the memoized body would be
        one expansion short of fully reduced, which costs an optimization and
        cannot cost an answer.
        """
        found = self.reduced.get(key)
        if found is None:
            self.active.append(name)
            try:
                found = self.expr(lam.body)
            finally:
                self.active.pop()
            self.reduced[key] = found
        return found

    def memoize(self, name: str, value) -> None:
        """Record a top-level binding's reduced body for `body_of`.

        Only the monomorphic ones: a binding with type binders is reduced
        again per instantiation, under a key that names the type arguments,
        and the uninstantiated body is not an answer to any of those.
        """
        bind = self.bindings.get(name)
        if bind is None or bind.binders or not isinstance(value, CLam):
            return
        self.reduced.setdefault((name, ()), value.body)

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

    def case_of_join(self, e: CMatch):
        """`match (join j(p) = B in R) { alts }`, with the match moved into B.

        What an inlined `Iterator.next` leaves behind. The callee's early
        `return` became a join, so the constructor the caller wants to take
        apart is not the scrutinee itself but the argument of a `jump` inside
        it, and `case_of_case` -- which knows how to push a match into the
        arms of a `match` or an `if` -- does not reach it.

        Moving the match into the join's *body* puts it exactly one step from
        those jumps, and `specialize_join` closes the gap: it copies the body
        once per constructor signature seen at a jump, and the copy is a match
        on a known constructor, which `known_constructor` deletes along with
        the allocation. This is GHC's case-of-case over join points, and the
        reason it is one rule there and two here is that this compiler's join
        specialization is a separate rule already.

        `R` must not fall through, or its own value would escape unmatched.
        That is not a restriction in practice: a join stands where a `return`
        or a `?` was, and control reaches its continuation by jumping.
        """
        scrut = e.scrutinee
        if not isinstance(scrut, CJoin) or _falls_through(scrut.rest):
            return None
        if {param.name for param in scrut.params} & names_of(e.alts):
            return None
        body = CMatch(e.ty, e.span, scrut.body, e.alts)
        return replace(scrut, ty=e.ty, body=body,
                       rest=_retype(scrut.rest, e.ty))

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

    def fuse_recursive_join_result(self, e: CMatch):
        """Push a recursive join's result consumer through its boundary.

        A lifted loop commonly has this shape::

            match (join rec loop() : Option (Flow ...) = body
                   in jump loop()) { exits }

        The loop breaker correctly prevents ordinary inlining from unfolding
        ``loop``, so the produced ``Flow`` cannot meet ``exits`` unless the
        consumer moves into the loop.  Change the join's answer type to the
        match's answer type and consume both its body and its entry there.
        Self-jumps stay self-jumps, so recursion is neither copied nor turned
        into Python recursion; only the loop's actual exits are rewritten.

        This is a general worker/result-wrapper fusion rule.  It knows neither
        ``Flow`` nor ``Option`` and is useful whenever a recursive join returns
        a value that is immediately scrutinised.

        Moving the alternatives under the join parameters could capture a
        same-named free variable.  That rare shape is declined rather than
        renamed, consistently with the other capture-avoiding reductions in
        this module.
        """
        join = e.scrutinee
        if not isinstance(join, CJoin) or not join.recursive:
            return None
        free = _free_names(e.alts)
        if free & (_binders_of(join.body) | _binders_of(join.rest)
                   | {p.name for p in join.params}):
            return None
        body = self.expr(_consume_tail(join.body, e.alts, e.ty, e.span))
        rest = self.expr(_consume_tail(join.rest, e.alts, e.ty, e.span))
        return replace(join, ty=e.ty, body=body, rest=rest)

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
        # Pattern variables scope over their alternative bodies, not over the
        # scrutinee. A free name in `value` therefore cannot be captured by an
        # equally named pattern when `value` becomes that scrutinee.
        return CMatch(body.ty, body.span, e.value, body.alts)

    def float_let_through_join(self, e: CLet):
        """Move a binding used only by a join's `rest` to that `rest`.

        Inlined `bind` commonly leaves exactly this shape: the producer is an
        `if`, its consumer is a `match`, and the continuation join introduced
        to keep case-of-case from duplicating code sits between them. Moving
        the `let` across a non-recursive join puts producer and consumer back
        together; case-of-case can then expose constructor-valued jumps for
        `specialize_join` below.

        The value is not moved into the join body, and the rewrite is declined
        when that body reads the binding. A recursive join is left alone: its
        rest is an entry to a loop rather than merely the continuation beside
        this binding, and M16e owns transformations of that boundary.
        """
        join = e.body
        if (e.binders or not isinstance(join, CJoin) or join.recursive
                or e.name in _free_names(join.body)):
            return None
        rest = CLet(join.ty, e.span, e.name, e.bound, e.value, join.rest,
                    list(e.binders))
        return replace(join, ty=e.ty, rest=rest)

    def specialize_join(self, e: CJoin):
        """Carry known constructor tags across a non-recursive join.

        A single-use join is beta-reduced when doing so is capture-safe. For a
        shared join, calls with the same constructor signature are redirected
        to one specialized copy. Constructor fields, and arguments whose tag
        is unknown, become parameters of that copy; rebuilding the known
        constructor in its body lets the ordinary match rule select an arm.

        This preserves the reason the join exists: a continuation is copied
        once per useful tag, never once per incoming edge. Copies which do not
        shrink after reduction, or remain over `JOIN_SPECIALIZE_LIMIT`, are
        declined.
        """
        if e.recursive:
            return None
        jumps = _join_jumps(e.rest, e.name)
        if not jumps:
            return e.rest

        known = [j for j in jumps if any(_constructor(a)[0] is not None
                                         for a in j.args)]
        if not known:
            return None

        if len(jumps) == 1:
            # Moving the body to the jump site must not capture one of its
            # free variables under a same-named binder in `rest`.
            if _free_names(e.body) & _binders_of(e.rest):
                return None
            made = _apply(e.params, jumps[0].args, e.body, e.ty)
            if made is None:
                return None
            made = self.expr(made)
            return _replace_jumps(e.rest, {id(jumps[0]): made})

        groups: dict[tuple, list[CJump]] = {}
        for jump in known:
            signature = tuple((con, len(args)) if con is not None else None
                              for con, args in map(_constructor, jump.args))
            groups.setdefault(signature, []).append(jump)

        replacements: dict[int, CExpr] = {}
        variants = []
        for signature, calls in groups.items():
            params: list[CParam] = []
            templates: list[CExpr] = []
            first = calls[0]
            for original, arg, part in zip(e.params, first.args, signature):
                if part is None:
                    name = self.fresh("jv")
                    params.append(CParam(name, original.ty))
                    templates.append(CVar(original.ty, arg.span, name))
                    continue
                _, fields_ = _constructor(arg)
                field_vars = []
                for field in fields_:
                    name = self.fresh("jf")
                    params.append(CParam(name, field.ty))
                    field_vars.append(CVar(field.ty, field.span, name))
                templates.append(_rebuild_constructor(arg, field_vars))

            body = _apply(e.params, templates, e.body, e.ty)
            if body is None:
                continue
            body = self.expr(body)
            if (_size(body) >= _size(e.body)
                    or _size(body) > JOIN_SPECIALIZE_LIMIT):
                continue

            name = self.fresh("js")
            for jump in calls:
                args = []
                for arg, part in zip(jump.args, signature):
                    args.extend(_constructor(arg)[1] if part is not None
                                else [arg])
                replacements[id(jump)] = CJump(TBottom(), jump.span,
                                                name, args)
            variants.append((name, params, body))

        if not variants:
            return None

        rest = _replace_jumps(e.rest, replacements)
        if len(replacements) != len(jumps):
            rest = replace(e, rest=rest)
        for name, params, body in reversed(variants):
            rest = CJoin(e.ty, e.span, name, params, body, rest, False)
        return rest

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


def _consume_tail(e, alts, ty, span):
    """Apply a result match at every yielding tail of ``e``.

    A jump is already an exit and must remain in tail position.  The other
    cases mirror ``core.TAIL_FIELDS`` but are written out because their result
    type also changes from the consumed value's type to ``ty``.
    """
    if isinstance(e, CJump):
        return e
    if isinstance(e, CIf):
        otherwise = (e.otherwise if e.otherwise is not None
                     else CUnit(e.ty, e.span))
        return replace(e, ty=ty,
                       then=_consume_tail(e.then, alts, ty, span),
                       otherwise=_consume_tail(otherwise, alts, ty, span))
    if isinstance(e, CLet):
        return replace(e, ty=ty, body=_consume_tail(e.body, alts, ty, span))
    if isinstance(e, CLetRec):
        return replace(e, ty=ty, body=_consume_tail(e.body, alts, ty, span))
    if isinstance(e, CMatch):
        return replace(e, ty=ty,
                       alts=[CAlt(a.pat, _consume_tail(a.body, alts, ty, span))
                             for a in e.alts])
    if isinstance(e, CJoin):
        return replace(e, ty=ty,
                       body=_consume_tail(e.body, alts, ty, span),
                       rest=_consume_tail(e.rest, alts, ty, span))
    return CMatch(ty, span, e, alts)


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

    The `let`s this builds are themselves binders, and a substituted argument
    lands *inside* them, so `bound` cannot be read off the callee's body alone.
    `internal(name, b.name)` -- a caller's local spelled like a callee's second
    parameter -- substitutes `name` for `owner` and then wraps `let name =
    b.name` around it, and the body reads the field twice. So a parameter that
    becomes a `let` disqualifies substituting any argument that mentions it,
    and disqualifying one turns it into a `let` in turn, which is why this
    settles rather than deciding in one pass.
    """
    bound = _binders_of(body)
    candidates = {}
    letters: list[str] = []  # parameters that will be bound by a `let`
    for index, (name, arg) in enumerate(zip(names, args)):
        free = _free_names(arg)
        if free & set(names[:index]):
            return None
        if _is_value(arg) and name not in bound and not (free & bound):
            candidates[name] = arg
        else:
            letters.append(name)
    while True:
        captured = [name for name, arg in candidates.items()
                    if _free_names(arg) & set(letters)]
        if not captured:
            break
        for name in captured:
            del candidates[name]
        letters.extend(captured)

    substitution = {n: a for n, a in candidates.items()}
    remaining = [(n, a) for n, a in zip(names, args) if n not in substitution]
    if substitution:
        body = _substitute(body, substitution)
    for name, arg in reversed(remaining):
        body = CLet(ty, arg.span, name, arg.ty, arg, body)
    return body


def _instantiate_types(value, mapping: dict[int, Type]):
    """`value` with free type variables replaced by a call's type arguments.

    This is the erased-language equivalent of beta-reducing a `CTyApp`. It is
    deliberately happy with non-ground arguments: unlike monomorphization it
    makes no top-level copy and cannot chase polymorphic recursion, and a
    phantom variable such as a lifted loop's uninhabited `Brk` slot is exactly
    the case that brought this rule here.

    A field named `binders` introduces type variables rather than using them,
    so those objects themselves are preserved. Their ids are distinct from
    the outer binding's ids; the types under them may still mention an outer
    variable and are therefore walked normally.
    """
    if isinstance(value, Type):
        return substitute(value, mapping)
    if isinstance(value, (CExpr, CBind, CAlt, CParam)):
        return type(value)(**{
            f.name: (getattr(value, f.name) if f.name == "binders"
                     else _instantiate_types(getattr(value, f.name), mapping))
            for f in fields(value)
        })
    if isinstance(value, list):
        return [_instantiate_types(x, mapping) for x in value]
    if isinstance(value, tuple):
        return tuple(_instantiate_types(x, mapping) for x in value)
    return value


def _rebase_spans(value, span):
    """Give a copied Core subtree one diagnostic origin.

    This runs before value arguments are substituted, so those argument nodes
    retain their own caller spans when `_substitute` inserts them.
    """
    if isinstance(value, (CExpr, CBind)):
        return type(value)(**{
            f.name: (span if f.name == "span"
                     else _rebase_spans(getattr(value, f.name), span))
            for f in fields(value)
        })
    if isinstance(value, CAlt):
        return CAlt(value.pat, _rebase_spans(value.body, span))
    if isinstance(value, list):
        return [_rebase_spans(x, span) for x in value]
    if isinstance(value, tuple):
        return tuple(_rebase_spans(x, span) for x in value)
    return value


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

    Identity, not equality, and that is the point: it asks whether *this* match
    survived the reduction, which is exactly the question "did pushing it into
    the branch buy anything". A structural comparison would answer a different
    and less useful question.

    It is also a question that can no longer be asked, and so this answers
    false always -- see FINDINGS.md 42. `self.expr` rebuilds every node it
    walks, list included, before any rule fires, so the object `alts` names is
    gone by the time the reduction it is being asked about has happened.
    Nothing here reuses it: the rules that build a `CMatch` around existing
    alternatives build it around the *rebuilt* ones.

    Making it fire is not a matter of comparing something that does survive
    (the patterns do). That was tried, and it costs optimization: a branch
    whose pushed-in match has not collapsed *yet* becomes a jump carrying the
    whole unreduced branch, and the join specialization downstream can no
    longer see a constructor tag to split on -- `tests/test_opt.py`'s
    `clamp` keeps its four-way `Flow` match instead of erasing it. The
    protection this guard is meant to provide is real and the shape it should
    key on is an open question; what is not open is that this spelling of it
    has never once fired.
    """
    return any(isinstance(n, CMatch) and n.alts is alts for n in _nodes(e))


def _transfers(e, bound: frozenset[str] = frozenset()) -> bool:
    """Whether `e` contains a jump that leaves it.

    A term is movable when everything it transfers to travels with it. This
    used to be a much blunter question, because `return`, `break` and
    `continue` were nodes that named their target by *where they were*: the
    `return 0` inside `depth`, dropped into `depth`'s caller, returns from the
    caller, so a body holding one could not be moved at all. About one in
    eight of the functions in the suite were un-inlinable for that reason
    alone.

    A jump names its target, so the question is whether the name is bound
    inside `e`. A function whose body holds a `return` now carries its own
    `%ret` join *inside* its lambda, so the whole body is self-contained and
    this answers false where it used to answer true -- which is the reduction
    the join points were for.

    A jump under a nested `CLam` cannot exist: a lambda body is out of tail
    position and the checker gives it an empty join scope. So a lambda is not
    descended into, and a `CJoin` extends the bound set for its `rest` always
    and for its own `body` only when it says it is recursive, exactly as
    `coretc._check_CJoin` does.
    """
    if isinstance(e, CJump):
        return e.name not in bound
    if isinstance(e, CLam):
        return False
    if isinstance(e, CJoin):
        inner = bound | {e.name}
        return (_transfers(e.body, inner if e.recursive else bound)
                or _transfers(e.rest, inner))
    if isinstance(e, (CExpr, CBind, CAlt)):
        return any(_transfers(getattr(e, f.name), bound) for f in fields(e))
    if isinstance(e, (list, tuple)):
        return any(_transfers(x, bound) for x in e)
    return False


def _bottoms(e, known: frozenset[str] = frozenset()) -> bool:
    """Whether evaluating `e` always diverges.

    GHC's `exprIsBottom`. The one primitive producer is `Prim.error`; `known`
    carries the top-level functions already shown to reduce to it, so that
    `Data.Array#bounds` calling `outOfBounds` counts as well. Conservative in
    the direction of answering false, which costs an inline allowed rather
    than a term moved wrongly.
    """
    if isinstance(e, CApp):
        fn = e.fn
        while isinstance(fn, CTyApp):
            fn = fn.fn
        name = getattr(fn, "name", None)
        return name == "Prim.error" or (name is not None and name in known)
    if isinstance(e, CLet):
        return _bottoms(e.body, known)
    if isinstance(e, CJoin):
        return _bottoms(e.rest, known)
    if isinstance(e, CMatch):
        return bool(e.alts) and all(_bottoms(alt.body, known) for alt in e.alts)
    if isinstance(e, CIf):
        return (e.otherwise is not None and _bottoms(e.then, known)
                and _bottoms(e.otherwise, known))
    return False


def bottoming(program: CProgram) -> frozenset[str]:
    """The top-level functions that never return.

    Grown from nothing rather than assumed and narrowed, so a function that
    only calls itself is not mistaken for one that panics: a name is added
    only once its body is bottom given the names already added.

    The backend uses this to say what it cannot otherwise know -- that the
    edge back from a call to one of these is dead. `Data.Array#outOfBounds`
    returns to its caller like anything else, and the caller then tests the
    panic flag and returns; but the flag is always set, so the arm that
    carries on is unreachable. Leaving it in the CFG costs far more than the
    branch: it merges into the code after the bounds check, and because the
    call may write anything, every load after the merge has to be redone.
    `Main#inc` reloaded the array header and its length four times, and
    checked the same index against the same length twice, for that reason.
    """
    found: set[str] = set()
    changed = True
    while changed:
        changed = False
        for bind in program.binds:
            if bind.name in found:
                continue
            value = bind.value
            body = value.body if isinstance(value, CLam) else value
            if _bottoms(body, frozenset(found)):
                found.add(bind.name)
                changed = True
    return frozenset(found)


def _falls_through(e) -> bool:
    """Whether `e` can produce a value of its own rather than jumping away.

    A join's type is the type of its result, which says nothing about where
    that result comes from: the `if` an inlined `return` leaves behind is
    typed `Option a` while both its branches are jumps, so nothing ever
    reaches the expression after it. `case_of_join` needs that distinction,
    and only the syntax has it.

    Conservative in the direction of answering true: an unrecognised term is
    assumed to fall through, which costs a rewrite declined rather than a
    value dropped.
    """
    if isinstance(e, CJump):
        return False
    if isinstance(e, CLet):
        return _falls_through(e.body)
    if isinstance(e, CJoin):
        return _falls_through(e.body) or _falls_through(e.rest)
    if isinstance(e, CMatch):
        return any(_falls_through(alt.body) for alt in e.alts)
    if isinstance(e, CIf):
        return (e.otherwise is None or _falls_through(e.then)
                or _falls_through(e.otherwise))
    return True


def _retype(e, ty):
    """`e`, with the result type of every tail position replaced.

    Only ever called where `_falls_through(e)` is false, and that is what
    makes it sound: nothing here can produce a value, so the type each of
    these nodes carries is a label on a result that never happens. Because
    `_falls_through` decides a join by *both* its body and its rest, and a
    match by all of its arms, the recursion below reaches exactly the terms
    that inherited the phantom type and stops at the jumps that gave it away.
    """
    if isinstance(e, CLet):
        return replace(e, ty=ty, body=_retype(e.body, ty))
    if isinstance(e, CJoin):
        return replace(e, ty=ty, body=_retype(e.body, ty),
                       rest=_retype(e.rest, ty))
    if isinstance(e, CMatch):
        return replace(e, ty=ty,
                       alts=[CAlt(alt.pat, _retype(alt.body, ty))
                             for alt in e.alts])
    if isinstance(e, CIf):
        return replace(e, ty=ty, then=_retype(e.then, ty),
                       otherwise=_retype(e.otherwise, ty))
    return e


def _constructor(e):
    """`e` read as a saturated constructor application, or `(None, [])`."""
    if isinstance(e, CCon):
        return e.name, []
    if isinstance(e, CApp) and isinstance(e.fn, CCon):
        return e.fn.name, list(e.args)
    return None, []


def _rebuild_constructor(e, args):
    """A constructor shaped like `e`, with `args` as its fields."""
    if isinstance(e, CCon):
        assert not args
        return e
    assert isinstance(e, CApp) and isinstance(e.fn, CCon)
    return replace(e, args=list(args))


def _join_jumps(e, name: str) -> list[CJump]:
    """The jumps in `e` bound by the surrounding join named `name`."""
    out = []

    def walk(v, shadowed=False):
        if isinstance(v, CJump):
            if not shadowed and v.name == name:
                out.append(v)
            for arg in v.args:
                walk(arg, shadowed)
            return
        if isinstance(v, CJoin):
            same = v.name == name
            walk(v.body, shadowed or (same and v.recursive))
            walk(v.rest, shadowed or same)
            return
        if isinstance(v, (CExpr, CBind, CAlt)):
            for f in fields(v):
                walk(getattr(v, f.name), shadowed)
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item, shadowed)

    walk(e)
    return out


def _replace_jumps(e, replacements: dict[int, CExpr]):
    """Replace the particular jump objects named by their identities."""
    found = replacements.get(id(e))
    if found is not None:
        return found
    if isinstance(e, CAlt):
        return CAlt(e.pat, _replace_jumps(e.body, replacements))
    if isinstance(e, CBind):
        return replace(e, value=_replace_jumps(e.value, replacements))
    if isinstance(e, CExpr):
        return type(e)(**{
            f.name: _replace_jumps(getattr(e, f.name), replacements)
            for f in fields(e)
        })
    if isinstance(e, list):
        return [_replace_jumps(x, replacements) for x in e]
    if isinstance(e, tuple):
        return tuple(_replace_jumps(x, replacements) for x in e)
    return e


def _size(e) -> int:
    """Roughly the code `e` becomes, for the inliner to spend a budget of.

    Counting every node as one is what this did, and it charges for a great
    deal that is not code. `Main#inc` in the brainfuck benchmark -- one array
    element read, incremented and written back -- measured 98 against a budget
    of 40 and so stayed a call, while the loop that called it three times a
    cycle paid a frame, a spill set and a panic-flag test for each. Most of
    those 98 nodes were names: `match xs { Data.Array#Array(value) -> value }`
    appeared four times, which is a single-constructor unpack and lowers to
    one load, and the `let`s naming each intermediate lower to nothing at all,
    since the backend is in SSA already.

    So: a variable, a literal and a nullary constructor are operands and cost
    nothing; a type application is erased; a `let` is a name for a value, and
    costs what it names. A match with one alternative is a projection -- the
    backend does not even test the tag -- while one with several costs a
    branch each. This is GHC's `sizeExpr`, which is free for variables,
    literals, types and coercions and charges per case alternative, and it is
    the same reasoning as the discounts already here: the budget should track
    what a call site actually grows by.
    """
    if isinstance(e, (list, tuple)):
        return sum(_size(x) for x in e)
    if not isinstance(e, (CExpr, CBind, CAlt)):
        return 0
    if isinstance(e, (CVar, CLit, CUnit, CCon)):
        return 0
    if isinstance(e, CTyApp):
        return _size(e.fn)
    if isinstance(e, CLet):
        return _size(e.value) + _size(e.body)
    if isinstance(e, CBind):
        return _size(e.value)
    if isinstance(e, CLam):
        return _size(e.body)
    if isinstance(e, CJoin):
        return _size(e.body) + _size(e.rest)
    if isinstance(e, CAlt):
        return _size(e.body)
    if isinstance(e, CMatch):
        branches = 0 if len(e.alts) <= 1 else len(e.alts)
        return branches + _size(e.scrutinee) + _size(e.alts)
    return 1 + sum(_size(getattr(e, f.name)) for f in fields(e))


def _free_names(e) -> set[str]:
    """The term variables `e` mentions and does not itself bind.

    `core.names_of` is the over-approximation -- every name, bound or not --
    and it is the right one for keeping a binding alive. It is the wrong one
    for asking whether moving a term could capture something, and using it
    there cost most of this pass's reductions: the continuation a `?` builds
    contains `let x = ... in ...`, and `Option`'s `bind` calls its first
    parameter `x`, so a guard reading every mentioned name refused to inline
    `bind` at all.

    Binders are read from field names, as in `_binders_of`, plus the scopes
    that are not simply "this field's binders cover that field": a `CLet`
    binds only over its body, and a `CJoin`'s parameters only over its own.
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
    silently omitted the pattern a `for` loop binds: the `Array` instance's
    `bind` walks its elements in one, so a continuation substituted into it
    was captured by the very element it was meant to be applied to. A rule
    about field names is one a node added later cannot fall outside of by
    accident -- and the `for` is a `CMatch` now, which is exactly the kind of
    change that would have reintroduced the bug under a case list.

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


__all__ = [
    "INLINE_LIMIT", "JOIN_SPECIALIZE_LIMIT", "SPECULATIVE_INLINE_LIMIT",
    "loop_breakers", "reduce_program",
]
