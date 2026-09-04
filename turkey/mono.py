"""Monomorphization: one copy of a polymorphic binding per type it is used at.

`plan.txt` item 6. Item 5 left a typed Core in which every dictionary is an
explicit argument at a known type -- which is precisely the input this needs.
A generalized binding is a `CBind` with `binders`, and every use of one is a
`CTyApp` at the arguments delta 48 recorded, so "which types is this used at"
is a question the IR already answers. This pass reads the answer and writes a
copy per answer.

What that buys, in the order `plan.txt` asks for it:

* **A dictionary is built once.** `%inst.Monoid.Array` is a *function* from a
  `Semigroup a` dictionary to a `Monoid (Array a)` one, so
  `%inst.Monoid.Array[Int](%inst.Semigroup.Int)` allocates a fresh record every
  time it is evaluated -- three times in `dicts.tl` alone. M13's note called
  this "correct, terminating, and work 6 should remove". It is removed here:
  a ground instance application becomes one top-level binding of a record.
* **Every dictionary at a call site is a name.** Not the projection of a
  parameter, and not an application to be performed: a `CVar` naming a
  top-level record.
* **And then not even a name.** Given that, a method selection off a ground
  dictionary is a projection out of a record whose definition is right here, so
  it is replaced by a binding: `%inst.Ord.Int#lt`. That is the second half of
  the file (`_Devirtualizer`), it is what item 4's fast lowering needs before
  it can inline an instance's `bind`, and on `tests/programs/dicts.tl` it takes
  the dictionary projections from forty-five to six.
* **And what nothing reaches is dropped.** Specialization only ever adds
  bindings; after it, most of what it copied from is unreachable, and so is
  every Prelude dictionary the program never mentions (`_reachable`).

## It is a *partial* pass, and that is a decision

Turkey admits polymorphic recursion. A complete signature gives a recursive
call a scheme to instantiate (`infer.declared_scheme` says so outright), so

    fun depth(x : a, n : Int) -> Int {
        if n <= 0 { return 0 }
        return 1 + depth(Pair(x, x), n - 1)
    }

elaborates with the recursive call at `Pair a`, and the reachable set
`depth@Int`, `depth@Pair(Int)`, `depth@Pair(Pair(Int))`, ... is infinite. This
is not a corner: it is the standard reason monomorphization is undecidable, and
specializing on dictionaries instead would not escape it -- add
`instance [Show a] Show (Pair a)` and the *dictionary* chain is infinite too,
which the solver accepts, because the evidence it builds is the finite term
`%inst.Show.Pair[a](%d1.Show)` with a variable still in it.

So there is a cap, and when it trips the call site keeps its `CTyApp` and its
dictionary arguments and goes on calling the generic binding. Every generic
binding therefore **survives the pass unchanged**, which is why the output is a
mixture rather than a program with no polymorphism left in it. A program that
compiles today compiles here; some of them are only partly specialized.

And the cap says nothing when it trips. It used to warn, on the grounds that a
program whose performance quietly depends on a budget is a program whose
performance nobody was told about. That was the wrong frame: Turkey does not
*promise* monomorphization, and a binding that stays polymorphic is compiled by
layout-keyed sharing instead -- one body per layout of its type arguments,
which is a finite set, so it always terminates (`plan.txt` items 6 and 9). How
much a backend recovers past that, by inlining and constant propagation, is the
backend's business. So the cap is an optimization budget and not a diagnostic,
and there is nothing here for a program to have gone wrong about.

Two consequences of "survives unchanged" worth stating, because they are what
keeps this pass small:

* A **generic binding's body is not rewritten at all.** Only a ground binding
  and a specialized copy are. So inside anything this pass rewrites, every type
  argument that came from a binder is ground, and a `CTyApp` that is *not*
  ground is one whose variable came from somewhere else -- an ambiguous
  residual, or a method's own quantification -- and is left alone.
* Nothing is deleted *by the specializer*. It emits a binding nothing reaches
  any more and leaves the question of liveness to `_reachable`, which asks it
  once, at the end, over the finished program -- so the specializer never has
  to be right about liveness as well as about types.

## It goes round twice, and the budget does not restart

Specializing and devirtualizing each *make* bindings the other would have
worked on. The specializer collapses a ground instance into a record; the
devirtualizer hoists that record's methods into top-level bindings; and those
bindings have ground call sites that the specializer never saw, because they
did not exist when it ran. `%inst.Foldable.Array#foldMap` was the case that
made this milestone: `Main#render` calls it at ground types with ground
evidence, which is exactly the shape the collapse handles.

So the pipeline runs `ROUNDS` times and reachability runs once at the end.
The thing that has to be got right is not the loop but the cap: a budget spent
per round is no bound at all, since N rounds would grant one binding 32N copies.
`_State` is the answer -- one budget for the whole pass, charged to the original
a copy descends from, plus the memo of what has already been built, so a later
round finds an earlier round's copy instead of making a second one under a
disambiguated name. With that, "a binding descended from `f` has at most
`MAX_SPECIALIZATIONS` copies in the output" is true of the program rather than
of one round of it, and would stay true if `ROUNDS` were raised.

It is two rounds and not a genuine fixed point because the cap is what stands
between this pass and the undecidability above, and "iterate until nothing
changes" is a promise about a number this pass is not in a position to make.
Two is what the measurement asks for: on `tests/programs/dicts.tl` a third
round changes nothing at all, and a second takes the dictionary projections
from sixteen to fourteen and specializes four more methods.

## What it does not do

What cannot be done is what the cap refuses. A capped call site keeps
its type application and its dictionary argument, so the generic binding it
names keeps its dictionary *parameter* and keeps projecting out of it -- and
that projection is the one thing coherence says nothing about, because the
parameter is not a dictionary this pass can see.

The result is checked by `turkey/coretc.py`, unconditionally, exactly as the
lowering is. A pass whose output nobody checks is believed for the reason the
pre-M13 elaborator was believed, which is that nobody looked. It is also the
program the evaluator runs (delta 52), so the goldens are its differential
test: the same source, the same `.expected`, a different Core underneath.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace

from .classes import ClassTable
from .core import (dict_class, CAlt, CBind, CExpr, CField, CIndex, CLam, CLet, CLetRec,
                   CParam, CProgram, CProject, CRecord, CTyApp, CTyLam, CVar,
                   names_of,
                   transparent_parameters as core_transparent)
from .coretc import Fams
from .decls import DeclTable, substitute
from .typed import reduce_deep
from .errors import Unsupported
from .types import (TApp, TCon, TFam, TFun, TTuple, TVar, Type, prune,
                    show, spine, type_key, vars_of)

# How many distinct specializations one binding may have, and how large a type
# argument may be. Either limit stops the unrolling a polymorphically recursive
# binding would otherwise ask for. The numbers are deliberately generous, but
# "no program in the suite comes near them" -- which this said until it was
# measured -- is false: `polyrec.tl` trips MAX_SPECIALIZATIONS on `Main#depth`,
# which is the whole point of that fixture. It is the only one in the suite that
# does, so tripping one is still a signal rather than a routine event.
MAX_SPECIALIZATIONS = 32
MAX_TYPE_NODES = 64

# How many times the whole pipeline runs. Each round can only see the bindings
# that existed when it started, and both halves of a round *make* bindings the
# other half would have specialized: the specializer collapses a ground
# instance into a record, the devirtualizer hoists that record's methods into
# bindings, and those bindings have ground call sites the specializer has never
# looked at. So one round leaves work on the table -- `%inst.Foldable.Array`'s
# `foldMap` was the case that made this milestone -- and a second collects it.
#
# It is a fixed number rather than a fixed point, and the reason is the cap.
# `MAX_SPECIALIZATIONS` is what stands between this pass and the undecidability
# above, and a budget spent per round is not a bound at all: N rounds would
# permit 32N copies of one binding, and N is exactly what a fixed point refuses
# to promise. So the budget is shared across rounds (`_State`) *and* the rounds
# are counted. Either alone would do; both is what makes the bound easy to
# state, which is: a binding descended from `f` has at most 32 copies in the
# output, whatever ROUNDS is set to.
#
# Two, rather than more, because the measurement says so: on the suite, round
# two takes the last of the projections one round left behind and round three
# finds nothing. Raising it is a one-line change that cannot break the bound.
ROUNDS = 2


def _ground(t: Type) -> bool:
    """No unbound variable anywhere in it, so it names one type and not a
    family of them. The test a specialization request has to pass."""
    return not vars_of(t)


def _size(t: Type) -> int:
    t = prune(t)
    if isinstance(t, TApp):
        return 1 + _size(t.fn) + _size(t.arg)
    if isinstance(t, TFam):
        return 1 + _size(t.arg)
    if isinstance(t, TFun):
        return 1 + sum(_size(p) for p in t.params) + _size(t.ret)
    if isinstance(t, TTuple):
        return 1 + sum(_size(e) for e in t.elems)
    return 1


def _mangle(t: Type) -> str:
    """A type, as a fragment of a name.

    Readability is the whole requirement -- `Main#squash@Int` should be
    findable in a `.mono` dump by someone who knows what they are looking for
    -- and *not* uniqueness, which `Monomorphizer.fresh` provides separately by
    keying on `type_key` and disambiguating a collision with a counter. So the
    module prefix comes off (`Data.Option#Option` is `Option`) and a function or
    a tuple is rendered by its shape rather than by its parts.
    """
    t = prune(t)
    if isinstance(t, TCon):
        return t.name.rpartition("#")[2]
    if isinstance(t, TApp):
        head, args = spine(t)
        return f"{_mangle(head)}({','.join(_mangle(a) for a in args)})"
    if isinstance(t, TFam):
        return f"{t.name}({_mangle(t.arg)})"
    if isinstance(t, TFun):
        return "fun"
    if isinstance(t, TTuple):
        return "tuple"
    return "_"


def _dict_class(ty: Type) -> str | None:
    """The class a `%Dict.C h` type names, or None if it is not one."""
    return dict_class(ty)


def _takes_dictionaries(bind: CBind) -> bool:
    """Whether this binding is a lambda over nothing but dictionaries.

    Two things wear that shape and the collapse below treats them alike:
    an instance with a context (`[Semigroup a] Semigroup (Array a)` is a
    function from a dictionary to a dictionary) and any binding whose scheme
    retained a predicate (`fun twice[Semigroup a]` is a function from a
    dictionary to the real lambda). Both are applied to evidence at every use
    and neither needs to be, once the evidence is ground.

    Asked structurally rather than by the `%inst.` prefix, because the shape is
    what the collapse depends on and a name is a convention a later pass could
    change. `%Dict.` is not a convention in the same sense: no source type can
    be spelled that way, so a parameter with one is evidence and nothing else.
    """
    return (isinstance(bind.value, CLam) and bool(bind.value.params)
            and all(_dict_class(p.ty) is not None for p in bind.value.params))


@dataclass
class _State:
    """What one round has to tell the next.

    The pipeline runs more than once (`ROUNDS`), and a round is a fresh
    `Monomorphizer` and a fresh `_Devirtualizer` over the previous round's
    output. Three things must not restart with them.

    **The budget.** A per-round count is not a cap: refuse a binding its
    thirty-third copy and the next round, counting from zero, grants it
    thirty-two more. So the count is kept here and charged to the binding a copy
    *descends from* -- `Main#squash@Int` and `Main#squash@String` are both
    `Main#squash` -- which is what `origin` records, and what makes
    "`f` has at most `MAX_SPECIALIZATIONS` copies" true of the finished program
    rather than of one round of it.

    **The hoists already made.** `%inst.Ord.Int#lt` is a binding the first
    round's devirtualizer created and the first round's rewrites already name.
    Without this the second round would hoist the same field again under a
    fresh name and point its own rewrites at the copy, leaving two bindings
    where the program needs one.
    """

    counts: dict[str, int] = field(default_factory=dict)
    # (original name, type-argument keys, evidence) -> the name built for it.
    # Shared for the same reason `counts` is, and with a sharper symptom: a
    # second round that re-asked for `Data.Array#new` at `Int` would not find
    # round one's answer, and would build a byte-identical second copy under a
    # disambiguated name -- specialization as a source of duplication, which is
    # the opposite of the point.
    done: dict[tuple, str] = field(default_factory=dict)
    # A binding this pass made -> the original binding it descends from.
    # Transitive by construction: `derive` stores the root, never the parent.
    origin: dict[str, str] = field(default_factory=dict)
    # (ground dictionary, field name) -> the binding that field is now.
    hoists: dict[tuple[str, str], str] = field(default_factory=dict)

    def root(self, name: str) -> str:
        return self.origin.get(name, name)

    def spend(self, name: str) -> bool:
        """Charge one copy to whatever `name` descends from, or refuse."""
        root = self.root(name)
        if self.counts.get(root, 0) >= MAX_SPECIALIZATIONS:
            return False
        self.counts[root] = self.counts.get(root, 0) + 1
        return True

    def derive(self, made: str, parent: str) -> None:
        """Record that `made` is a descendant of `parent`'s original.

        Used for a specialization (`Main#squash` -> `Main#squash@Int`) and for
        a hoisted method (`%inst.Ord.Int` -> `%inst.Ord.Int#lt`) alike. The
        method is charged to the dictionary it came out of because that is what
        it is: a piece of it, and a chain that grows one needs the other's
        budget to be bounded by.
        """
        self.origin[made] = self.root(parent)


@dataclass
class _Local:
    """A generalized binding *inside* a body -- a `let` that generalized, or a
    local `fun` -- and the instantiations found for it.

    It cannot be lifted to the top level and specialized there, because its
    body may close over the enclosing binding's parameters. So it is
    specialized in place: one extra `let` beside the original, per type it was
    used at.
    """

    binders: list[TVar]
    insts: dict[tuple, tuple[str, list[Type]]] = field(default_factory=dict)


class _Rewriter:
    """One binding's body, rewritten under one substitution.

    Created per specialization (with the binders mapped to that
    specialization's type arguments) and per ground binding (with an empty
    substitution, where it only rewrites call sites and touches no types).
    """

    def __init__(self, owner: "Monomorphizer", subst: dict[int, Type],
                 equations: list[tuple[Type, Type]],
                 scopes: list[dict[str, _Local]] | None = None) -> None:
        self.owner = owner
        self.subst = subst
        # Substituted before the family reducer is built from them: a given
        # `Item s ~ Op` becomes `Item (Array Int) ~ Op` under the
        # specialization, and it is the substituted form that is a rewrite rule
        # for the types in the body. Plain `substitute` and not `self.ty`,
        # since `self.ty` is what needs the reducer that this makes.
        self.equations = [(substitute(a, subst), substitute(b, subst))
                          for a, b in equations]
        self.fams = Fams(owner.classes, self.equations)
        self.rename: dict[str, str] = {}
        # A local name that is nothing but another name for a top-level
        # dictionary. `%inst.Display.Rose`'s own method opens with
        # `let %d2.Display = %inst.Display.Rose` -- the self-dictionary the
        # lowering binds -- and passing *that* to a recursive instance is what
        # the collapse below has to see through, since the binding it hoists
        # the application into cannot see a local name.
        self.aliases: dict[str, str] = {}
        self.scopes: list[dict[str, _Local]] = [] if scopes is None else scopes

    def child(self, subst: dict[int, Type], equations) -> "_Rewriter":
        """A rewriter for a nested binding, sharing the local scopes so a
        specialized local body can still reach the locals around it."""
        made = _Rewriter(self.owner, subst, equations, self.scopes)
        made.aliases = dict(self.aliases)
        return made

    # -- types -------------------------------------------------------------

    def ty(self, t):
        """A type under the substitution, with families reduced.

        Reducing matters: `Item s` over a rigid `s` is stuck in the generic
        body and becomes `Item (Array Int)` here, which the instance table
        decides -- and a node still claiming the unreduced form would be
        rejected by a checker that had reduced it.

        With no substitution there is nothing to do. That is not just an
        optimization: a ground binding's types were reduced by the lowering
        already, and re-deriving them would be a second opinion where the
        milestone wants none.
        """
        if t is None or not self.subst:
            return t
        return reduce_deep(substitute(t, self.subst), self.fams)

    # -- terms -------------------------------------------------------------

    def rewrite(self, e: CExpr) -> CExpr:
        method = getattr(self, "_rw_" + type(e).__name__, None)
        return method(e) if method is not None else self.generic(e)

    def generic(self, e: CExpr) -> CExpr:
        """Every node not named below: copy it, mapping each field by what it
        holds. Reflection over the dataclass rather than one case per node,
        because there is exactly one rule -- rewrite the children, substitute
        the types -- and writing it thirty times is thirty chances to miss a
        field a later node gains."""
        return type(e)(**{f.name: self.mapped(f.name, getattr(e, f.name))
                          for f in fields(e)})

    def mapped(self, name: str, value):
        # A binder is a variable being *bound*, not one being used, so the
        # substitution must not reach it. `CTyLam` and `CLet` are the two that
        # have them, and this is cheaper than remembering to say so twice.
        return value if name == "binders" else self.value(value)

    def value(self, v):
        if isinstance(v, CExpr):
            return self.rewrite(v)
        if isinstance(v, CParam):
            return CParam(v.name, self.ty(v.ty))
        if isinstance(v, CAlt):
            return CAlt(v.pat, self.rewrite(v.body))
        # A `letrec` member. Reached when `_rw_CLetRec` hands a group with no
        # polymorphic member to `generic`, which is every lifted loop: `for`
        # and `while` become a monomorphic `CLetRec`, so without this case the
        # group's bodies are copied verbatim and the ground call sites inside
        # them -- every `next` of every loop in the program -- are never
        # specialized. `rewrite_bind` rather than `rewrite` because a member
        # may carry equations of its own.
        if isinstance(v, CBind):
            return self.rewrite_bind(v)
        if isinstance(v, Type):
            return self.ty(v)
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if isinstance(v, tuple):
            return tuple(self.value(x) for x in v)
        return v

    def _rw_CVar(self, e: CVar) -> CExpr:
        return CVar(self.ty(e.ty), e.span, self.rename.get(e.name, e.name))

    def _rw_CTyApp(self, e: CTyApp) -> CExpr:
        """A type application: the one place a specialization is asked for.

        `e.fn` is a name or a method projection (`coretc.polymorphic` says so).
        A name is what this pass can specialize; a projection is a method's own
        quantification, which lives in a record field and stays.
        """
        args = [self.ty(a) for a in e.args]
        if isinstance(e.fn, CVar):
            found = self.instantiate(e.fn.name, args, e)
            if found is not None:
                return found
        return CTyApp(self.ty(e.ty), e.span, self.rewrite(e.fn), args)

    def instantiate(self, name: str, args: list[Type], e: CTyApp) -> CExpr | None:
        """The specialized name for `name[args]`, or None to leave it generic."""
        name = self.rename.get(name, name)
        if not all(_ground(a) for a in args):
            return None
        local = self.local(name)
        if local is not None:
            return CVar(self.ty(e.ty), e.span, self.local_inst(name, local, args))
        bind = self.owner.generic.get(name)
        if bind is None or len(bind.binders) != len(args):
            return None
        made = self.owner.request(bind, args, None)
        return None if made is None else CVar(self.ty(e.ty), e.span, made)

    def _rw_CApp(self, e) -> CExpr:
        collapsed = self.collapse(e)
        return self.generic(e) if collapsed is None else collapsed

    def collapse(self, e) -> CExpr | None:
        """An application to ground evidence becomes a binding with the
        evidence already in it.

        For an instance -- `%inst.Monoid.Array[Int](%inst.Semigroup.Int)` --
        that removes the per-request rebuild M13 left behind: the application
        is collapsed rather than merely specialized because specializing alone
        would leave `%inst.Monoid.Array@Int` a *function*, still applied, still
        allocating a record every time.

        For an ordinary binding -- `Main#squash[Int](%inst.Monoid.Int)` -- it
        removes the parameter. M14a's copy still took its dictionary and was
        still handed the same one at every call; a copy per *dictionary* as
        well as per type is what makes the dictionary a top-level name inside
        the body, which is what devirtualization then needs to see.

        Coherence is what makes both sound: the dictionary arguments are
        ground, so they are the only ones this name could be applied to at
        these types, and doing the application once does it with the same
        contents.
        """
        fn, targs = e.fn, []
        if isinstance(fn, CTyApp):
            targs = [self.ty(a) for a in fn.args]
            fn = fn.fn
        if not isinstance(fn, CVar):
            return None
        name = self.rename.get(fn.name, fn.name)
        bind = self.owner.byname.get(name)
        if bind is None or name not in self.owner.takes_dicts:
            return None
        assert isinstance(bind.value, CLam)
        if len(bind.binders) != len(targs) or len(bind.value.params) != len(e.args):
            return None
        args = [self.rewrite(a) for a in e.args]
        supplied = [self.global_dict(a) for a in args]
        if not all(supplied):
            # A context this pass could not reduce to a *top-level* name -- a
            # capped instance, or a dictionary parameter still in scope. The
            # collapse hoists this application to the top level, so an argument
            # that is not visible there cannot come along. Leave it alone; it
            # is exactly what it was.
            return None
        made = self.owner.request(bind, targs, supplied)
        return None if made is None else CVar(self.ty(e.ty), e.span, made)

    def global_dict(self, e: CExpr) -> str | None:
        """The top-level dictionary this expression names, or None."""
        if not isinstance(e, CVar):
            return None
        name = self.aliases.get(e.name, e.name)
        return name if name in self.owner.top_dicts else None

    # -- generalized bindings inside a body --------------------------------

    def local(self, name: str) -> _Local | None:
        for scope in reversed(self.scopes):
            found = scope.get(name)
            if found is not None:
                return found
        return None

    def local_inst(self, name: str, local: _Local, args: list[Type]) -> str:
        key = tuple(type_key(a) for a in args)
        found = local.insts.get(key)
        if found is not None:
            return found[0]
        made = self.owner.fresh(name, args)
        local.insts[key] = (made, args)
        return made

    def _rw_CLet(self, e: CLet) -> CExpr:
        if not e.binders:
            # Written out rather than left to `generic`, because the alias has
            # to be recorded between the value and the body: the value is what
            # decides whether there is one, and the body is where it is used.
            value = self.rewrite(e.value)
            named = self.global_dict(value)
            saved, had = self.aliases.get(e.name), e.name in self.aliases
            if named is not None:
                self.aliases[e.name] = named
            body = self.rewrite(e.body)
            if had:
                self.aliases[e.name] = saved
            else:
                self.aliases.pop(e.name, None)
            return CLet(self.ty(e.ty), e.span, e.name, self.ty(e.bound),
                        value, body, [])
        local = _Local(e.binders)
        self.scopes.append({e.name: local})
        body = self.rewrite(e.body)
        made = self.drain(local, e.binders, e.value)
        self.scopes.pop()
        out = body
        for name, value in reversed(made):
            out = CLet(self.ty(e.ty), e.span, name, value.ty, value, out, [])
        # The generic binding stays, outermost, so that a use this pass could
        # not specialize -- a capped one, or one under a variable that is not
        # this binding's -- still finds something to name. It is a value (the
        # value restriction is what let it generalize), so evaluating it costs
        # an allocation and nothing else, and M14c's reachability is what
        # removes it when nothing reads it.
        return CLet(self.ty(e.ty), e.span, e.name, self.ty(e.bound),
                    self.rewrite(e.value), out, e.binders)

    def drain(self, local: _Local, binders, value) -> list[tuple[str, CExpr]]:
        """Build every instantiation of one local binding, including the ones
        discovered while building the others."""
        out: list[tuple[str, CExpr]] = []
        seen: set[tuple] = set()
        while True:
            todo = [(k, v) for k, v in local.insts.items() if k not in seen]
            if not todo:
                return out
            for key, (name, args) in todo:
                seen.add(key)
                sub = self.child({**self.subst,
                                  **{b.id: a for b, a in zip(binders, args)}},
                                 self.equations)
                sub.rename = self.rename
                out.append((name, sub.rewrite(value)))

    def _rw_CLetRec(self, e: CLetRec) -> CExpr:
        poly = {b.name: _Local(b.binders) for b in e.binds if b.binders}
        if not poly:
            return self.generic(e)
        self.scopes.append(poly)
        body = self.rewrite(e.body)
        # The generic copies first, and for the reason `_rw_CLet` keeps its
        # original: a sibling or a capped call may still name them. Their own
        # recursive calls are at their own binders, which are not ground, so
        # nothing inside them specializes.
        binds = [self.rewrite_bind(b) for b in e.binds]
        pending = {b.name: b for b in e.binds if b.binders}
        seen: set[tuple[str, tuple]] = set()
        while True:
            todo = [(n, k, v) for n, local in poly.items()
                    for k, v in local.insts.items() if (n, k) not in seen]
            if not todo:
                break
            for owner_name, key, (name, args) in todo:
                seen.add((owner_name, key))
                orig = pending[owner_name]
                sub = self.child({**self.subst,
                                  **{b.id: a for b, a in zip(orig.binders, args)}},
                                 orig.equations)
                sub.rename = self.rename
                binds.append(CBind(name, sub.ty(orig.ty), [],
                                   sub.rewrite(orig.value), orig.span,
                                   equations=sub.equations))
        self.scopes.pop()
        return CLetRec(self.ty(e.ty), e.span, binds, body)

    def rewrite_bind(self, bind: CBind) -> CBind:
        """One member of a `letrec`, generic or not, under this rewriter --
        with its own equations added, since a member may carry some."""
        if not bind.equations:
            return replace(bind, ty=self.ty(bind.ty),
                           value=self.rewrite(bind.value))
        sub = self.child(self.subst, bind.equations)
        sub.rename = self.rename
        return replace(bind, ty=sub.ty(bind.ty), value=sub.rewrite(bind.value),
                       equations=sub.equations)


class Monomorphizer:
    """The whole program, and the specializations asked of it."""

    def __init__(self, program: CProgram, decls: DeclTable,
                 classes: ClassTable, state: _State | None = None) -> None:
        self.program = program
        self.decls = decls
        self.classes = classes
        # Shared with every other round; see `_State`. A caller that runs one
        # round in isolation -- a test -- gets a private one.
        self.state = state if state is not None else _State()
        self.byname: dict[str, CBind] = {}
        self.generic: dict[str, CBind] = {}
        self.takes_dicts: set[str] = set()
        # Every top-level dictionary binding. A collapse may only be handed
        # one of these, since the binding it makes lives out here with them.
        # A dictionary this pass *builds* joins the set: the second collapse
        # of a chain is handed the first one's answer, and refusing it there
        # would leave the rebuild in place one level up.
        self.top_dicts: set[str] = set()
        self.dict_group: set[str] = set()
        # key -> the name built for it. The key is the original's name, the
        # `type_key` of each type argument, and -- for an instance whose
        # context was collapsed -- the dictionaries it was applied to. Held on
        # `_State`, so a later round finds an earlier round's copy rather than
        # making its own.
        self.done = self.state.done
        self.made: dict[str, list[CBind]] = {}
        self.queue: list[tuple[str, CBind, list[Type], list[str] | None]] = []
        self.used: set[str] = set()

    def run(self) -> CProgram:
        for group, is_dict in ((self.program.dicts, True),
                               (self.program.binds, False)):
            for bind in group:
                self.byname[bind.name] = bind
                self.used.add(bind.name)
                if bind.binders:
                    self.generic[bind.name] = bind
                if is_dict:
                    self.dict_group.add(bind.name)
                if is_dict and not bind.binders:
                    self.top_dicts.add(bind.name)
                if _takes_dictionaries(bind):
                    self.takes_dicts.add(bind.name)
        # A ground binding is where every specialization starts: its body's
        # type arguments are already types rather than variables. `main` is one
        # of these, and so is every instance dictionary with no context.
        rewritten = {bind.name: self.rewrite_top(bind)
                     for group in (self.program.dicts, self.program.binds)
                     for bind in group if not bind.binders}
        while self.queue:
            name, bind, targs, dicts = self.queue.pop(0)
            self.made.setdefault(bind.name, []).append(
                self.build(name, bind, targs, dicts))

        out = CProgram()
        for group, target in ((self.program.dicts, out.dicts),
                              (self.program.binds, out.binds)):
            for bind in group:
                # Beside its original, which is already in dependency order,
                # and ahead of it, since a specialization depends on no more
                # than the original does. Top-level bindings are evaluated in
                # this order, so it has to be one that works.
                target.extend(self.made.get(bind.name, []))
                target.append(rewritten.get(bind.name, bind))
        return out

    def rewrite_top(self, bind: CBind) -> CBind:
        rewriter = _Rewriter(self, {}, bind.equations)
        return replace(bind, value=rewriter.rewrite(bind.value))

    def request(self, bind: CBind, targs: list[Type],
                dicts: list[str] | None) -> str | None:
        """Ask for `bind` at these arguments. None means the cap said no."""
        if not all(_ground(t) for t in targs):
            return None
        key = (bind.name, tuple(type_key(t) for t in targs),
               tuple(dicts) if dicts is not None else None)
        found = self.done.get(key)
        if found is not None:
            return found
        # Size first, then the budget, so a request refused for its type does
        # not also spend a copy the program never received.
        if any(_size(t) > MAX_TYPE_NODES for t in targs):
            return None
        if not self.state.spend(bind.name):
            return None
        name = self.fresh(bind.name, targs)
        self.state.derive(name, bind.name)
        if bind.name in self.dict_group and dicts is not None:
            self.top_dicts.add(name)
        # Recorded *before* the body is built, which is what makes a recursive
        # binding terminate: the copy's own call to itself, at its own
        # arguments, finds this entry rather than asking for another copy.
        self.done[key] = name
        self.queue.append((name, bind, targs, dicts))
        return name

    def fresh(self, name: str, targs: list[Type]) -> str:
        base = f"{name}@{','.join(_mangle(t) for t in targs)}" if targs else f"{name}@"
        candidate, n = base, 1
        while candidate in self.used:
            n += 1
            candidate = f"{base}~{n}"
        self.used.add(candidate)
        return candidate

    def build(self, name: str, bind: CBind, targs: list[Type],
              dicts: list[str] | None) -> CBind:
        subst = {b.id: t for b, t in zip(bind.binders, targs)}
        rewriter = _Rewriter(self, subst, bind.equations)
        ty, value = rewriter.ty(bind.ty), bind.value
        if dicts is not None:
            # The evidence, supplied. The lambda goes away and its parameters
            # are renamed to the dictionaries they were handed -- a rename and
            # not a `let`, because the argument is a top-level name, so
            # substituting it is capture-free and leaves what the body
            # projects out of something M14c can see through.
            assert isinstance(value, CLam)
            rewriter.rename = dict(zip((p.name for p in value.params), dicts))
            value = value.body
            ty = rewriter.ty(value.ty)
        return CBind(name, ty, [], rewriter.rewrite(value), bind.span,
                     bind.mutable, bind.module, rewriter.equations)


# --------------------------------------------------------- devirtualization


class _Devirtualizer:
    """A method selected from a *known* dictionary becomes a name.

    After specialization `%inst.Ord.Int.lt(!i, n)` is a projection out of a
    record this pass can see the definition of, and then an indirect call. Both
    halves are avoidable, and avoiding them is what `plan.txt` item 6 was for:
    each field of a ground dictionary is hoisted into its own top-level binding
    (`%inst.Ord.Int#lt`) and the projection becomes a `CVar` naming it.

    Coherence is again what makes it sound. A ground dictionary is one record,
    built once, and no other value can ever be the `Ord Int` dictionary, so the
    field's value is decided at compile time and a name for it is a name for
    what the projection would have found.

    Three things fall out rather than being arranged:

    * A `for` loop's `iter` and `next` are ordinary terms -- `lower.py` makes
      the loop a join point and its cursor two applications -- so every `for`
      loop in the suite stops projecting.
    * A method's own quantification stops being a special case. A `CTyApp` over
      a `CField` -- the one form M14a said it had to leave -- becomes a
      `CTyApp` over a `CVar`, which is the form everything else already uses.
      Where the binders come from is `abstraction`, below, and it is not always
      the term.
    * A superclass field is already a top-level dictionary's name, so it is
      followed rather than hoisted, and `d.%super.Semigroup.combine` collapses
      in one step to `%inst.Semigroup.Int#combine`.

    The record itself stays. A dictionary *parameter* is still projected from,
    and a capped call site still passes one; what removes the records nothing
    reads any more is `_reachable`, below, which is a separate question.
    """

    def __init__(self, program: CProgram, classes: ClassTable,
                 state: _State | None = None) -> None:
        self.program = program
        self.classes = classes
        self.state = state if state is not None else _State()
        # Ground dictionaries only: a record whose contents are known. An
        # instance with a context is a lambda and has no fields to hoist.
        self.records: dict[str, CRecord] = {}
        self.used: set[str] = set()
        # (dictionary, field) -> the name that projection is now. Shared across
        # rounds, so a projection this round is the first to see still resolves
        # to the binding a previous round hoisted.
        self.target: dict[tuple[str, str], str] = self.state.hoists
        self.hoisted: list[CBind] = []

    def run(self) -> CProgram:
        for bind in self.program.dicts + self.program.binds:
            self.used.add(bind.name)
        for bind in self.program.dicts:
            if not bind.binders and isinstance(bind.value, CRecord):
                self.records[bind.name] = bind.value
        # Every hoist decided before any rewrite, because a method's body may
        # project out of a dictionary whose fields have not been looked at yet
        # -- including its own.
        for bind in self.program.dicts:
            record = self.records.get(bind.name)
            if record is None:
                continue
            for name, value in record.fields:
                if (bind.name, name) in self.target:
                    # A previous round hoisted it, and the binding it made is
                    # in `program.dicts` already. Hoisting again would make a
                    # second copy under a `~` name and split the call sites
                    # between them.
                    continue
                made = self.hoist(bind, name, value)
                if made is not None:
                    self.target[(bind.name, name)] = made

        out = CProgram()
        out.dicts.extend(self.bind(b) for b in self.program.dicts)
        # The hoisted bindings live with the dictionaries, not after them.
        # `Evaluator.run` defines everything in `dicts` before it fills any
        # record, and a record's field may now name a hoisted binding; a
        # hoisted binding is always a lambda, so defining it builds a closure
        # and cannot read a record that is still empty.
        out.dicts.extend(self.bind(b) for b in self.hoisted)
        out.binds.extend(self.bind(b) for b in self.program.binds)
        return out

    def bind(self, b: CBind) -> CBind:
        return replace(b, value=self.rewrite(b.value))

    def hoist(self, owner: CBind, name: str, value: CExpr) -> str | None:
        """The name `owner.name`'s field now has, or None to leave it a
        projection."""
        if isinstance(value, CVar) and value.name in self.records:
            return value.name
        binders, inner = self.abstraction(owner, name, value)
        if not isinstance(inner, CLam):
            # A field whose value is neither a lambda nor another dictionary's
            # name stays where it is. Hoisting it would move an evaluation to
            # where the dictionaries are built, and what an evaluation there
            # can observe is exactly what delta 50's two-pass loop exists to be
            # careful about.
            return None
        made = f"{owner.name}#{name}"
        while made in self.used:
            made += "~"
        self.used.add(made)
        self.state.derive(made, owner.name)
        self.hoisted.append(CBind(made, inner.ty, binders, inner, owner.span,
                                  False, owner.module))
        return made

    def abstraction(self, owner: CBind, name: str,
                    value: CExpr) -> tuple[list[TVar], CExpr]:
        """A field's own type abstraction, as binders and a body.

        A method that quantifies over more than its class variable is not
        always a `CTyLam`. When the method has a context of its own --
        `fun fold[Monoid m](t m) -> m` -- the lowering emits the dictionary
        lambda and leaves the quantification implicit, because the checker
        reads a projection's binders off the *class table* rather than off the
        term (`coretc.method_scheme`). So this reads them the same way, and
        takes the term's only when the term states them.

        Getting this from the term alone is what a first attempt did, and the
        checker caught it immediately: `%inst.Foldable.Array#fold` was hoisted
        with no binders and the use site still had one type argument.
        """
        if isinstance(value, CTyLam):
            return list(value.binders), value.body
        cls = _dict_class(owner.ty)
        info = None if cls is None else self.classes.classes.get(cls)
        method = None if info is None else info.methods.get(name)
        if method is None and info is not None:
            matches = [m for internal, m in info.methods.items()
                       if internal.rpartition(".")[2].rpartition("#")[2] == name]
            method = matches[0] if len(matches) == 1 else None
        if method is None:
            return [], value
        return [q for q in method.scheme.quantified
                if q is not method.class_var], value

    # -- rewriting ---------------------------------------------------------

    def rewrite(self, e: CExpr) -> CExpr:
        if isinstance(e, CField):
            found = self.projected(e)
            if found is not None:
                return CVar(e.ty, e.span, found)
        return type(e)(**{f.name: self.mapped(getattr(e, f.name))
                          for f in fields(e)})

    def mapped(self, v):
        if isinstance(v, CExpr):
            return self.rewrite(v)
        if isinstance(v, CBind):
            return self.bind(v)
        if isinstance(v, CAlt):
            return CAlt(v.pat, self.rewrite(v.body))
        if isinstance(v, list):
            return [self.mapped(x) for x in v]
        if isinstance(v, tuple):
            return tuple(self.mapped(x) for x in v)
        return v

    def projected(self, e: CField) -> str | None:
        owner = self.owner(e.target)
        return None if owner is None else self.target.get((owner, e.name))

    def owner(self, e) -> str | None:
        """The ground dictionary this expression names, if it names one.

        Recursive, so a superclass chain resolves in one go: the target of
        `.combine` is `d.%super.Semigroup`, which is itself a projection this
        already knows the answer to.
        """
        if isinstance(e, CVar):
            return e.name if e.name in self.records else None
        if isinstance(e, CField):
            found = self.projected(e)
            return found if found in self.records else None
        return None


# ------------------------------------------------------------- reachability


def _droppable(bind: CBind, is_dict: bool) -> bool:
    """Whether not evaluating this binding is unobservable.

    The test is *effects*, not use, because a top-level binding is evaluated
    for its own sake: `turkey/eval.py` walks `program.binds` in order before it
    calls `main`, so a binding whose right-hand side prints has printed whether
    or not anything reads it. Three kinds are safe, and each for a stated
    reason rather than by inspection:

    * a dictionary, which is a record or a lambda;
    * a binding with `binders`, which generalized, and under the value
      restriction (design.md 4.4) a generalized right-hand side is a syntactic
      value;
    * any other binding whose value is literally a lambda -- most of the
      library, since a `fun` declaration is one.

    Everything else stays, evaluated in the order it always was.
    """
    return is_dict or bool(bind.binders) or isinstance(bind.value, (CLam, CTyLam))


def _reachable(program: CProgram, main: str) -> CProgram:
    """Drop the bindings nothing reaches, where dropping one is unobservable.

    This is what makes specialization a saving rather than an addition. A
    program that used `Semigroup` at `Int` and `String` carried the generic
    binding, the two copies, and every dictionary the Prelude declares; after
    this it carries the two copies and the dictionaries they name.

    The guard the cap needs is not written here because it does not need to be:
    a capped call site still holds `CTyApp(CVar(generic), ...)`, so the generic
    binding is named by whatever reaches that call site, and being named is all
    this asks for. `test_the_capped_call_site_still_names_the_generic_binding`
    is that claim as a test.

    Run once, at the end, and not between rounds: an intermediate round's
    output is full of bindings nothing reaches *yet*, and dropping one would be
    answering a question the next round was going to ask.
    """
    byname = {b.name: b for b in program.dicts + program.binds}
    droppable = {b.name for b in program.dicts}
    droppable |= {b.name for b in program.binds if _droppable(b, False)}
    work = [n for n in byname if n not in droppable]
    work.append(main)
    reach = set(work)
    while work:
        bind = byname.get(work.pop())
        if bind is None:
            continue
        for name in names_of(bind.value):
            if name not in reach:
                reach.add(name)
                work.append(name)
    out = CProgram()
    out.dicts.extend(b for b in program.dicts if b.name in reach)
    out.binds.extend(b for b in program.binds if b.name in reach)
    return out


def transparent_parameters(program: CProgram) -> list[tuple[str, str, Type]]:
    """Where a generic body could take polymorphic data apart.

    The predicate is `core.transparent_parameters`, shared with
    `layout.transparent` -- see there for why one definition and not two. What
    is decided here is *which* variables count as abstracted, and the answer is
    the ones no layout was found for: a variable whose layout the binding was
    compiled under is not a variable without a layout. That is what
    `layout.share` produces and the whole of what this check wanted -- not
    which type it is, which is undecidable and is what the cap gave up on, but
    how wide it is and whether it is a pointer.

    Reachability is from `main`, because an unreachable generic binding is
    never compiled.

    Two passes together are what make this hold, which is worth writing down
    because neither does it alone. Specializing dictionaries stops the generic
    `Data.Array#bounds` and `#grow` from being *reached*; but they are still
    in `mono`'s output, along with `#push`, `#state` and `Option#map`, and it
    is `opt` inlining them into their now-ground call sites that removes them.
    So this is checked on the program the backend is handed, not on `mono`'s,
    and it is a guard on the combination.
    """
    binds = {bind.name: bind for bind in program.dicts + program.binds}
    seen: set[str] = set()
    stack = [name for name in binds if name.endswith("#main")]
    while stack:
        name = stack.pop()
        if name in seen or name not in binds:
            continue
        seen.add(name)
        stack.extend(names_of(binds[name].value) & set(binds))

    found: list[tuple[str, str, Type]] = []
    for name in sorted(seen):
        bind = binds[name]
        abstracted = {variable.id for variable in bind.binders
                      if variable.id not in bind.layouts}
        for param in core_transparent(bind, abstracted):
            found.append((name, param.name, prune(param.ty)))
    return found


def check_layouts(program: CProgram) -> None:
    """Refuse to compile a program whose layouts cannot all be known.

    Raises rather than warns because the alternative is silence: the backend
    reads a field at the layout it computes, and if a generic body disagreed
    the result would be a wrong value rather than an error.
    """
    leaks = transparent_parameters(program)
    if not leaks:
        return
    detail = "; ".join(f"{name} takes {param} : {show(ty)}"
                       for name, param, ty in leaks)
    raise Unsupported(
        f"monomorphization left a generic body able to destructure "
        f"polymorphic data, whose layout it cannot know: {detail}")
def monomorphize(program: CProgram, decls: DeclTable, classes: ClassTable,
                 main: str = "main") -> CProgram:
    """Specialize, devirtualize, again, and then drop what nothing reaches.

    The rounds share one `_State`, which is what keeps the cap a cap; see
    `ROUNDS`. Reachability runs once, at the end, over the finished program --
    an intermediate round's output is full of bindings nothing reaches *yet*,
    and dropping one would be answering a question the next round was going to
    ask.
    """
    state = _State()
    out = program
    for _ in range(ROUNDS):
        out = _Devirtualizer(
            Monomorphizer(out, decls, classes, state).run(), classes, state
        ).run()
    return _reachable(out, main)


__all__ = ["MAX_SPECIALIZATIONS", "MAX_TYPE_NODES", "ROUNDS", "Monomorphizer",
           "monomorphize"]
