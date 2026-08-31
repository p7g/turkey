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
  top-level record. That is what M14c needs to turn a method selection into a
  direct call, and what item 4's fast lowering needs before it can inline an
  instance's `bind`.

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
compiles today compiles here; some of them are only partly specialized, and the
pass says which through the ordinary warning channel rather than silently.

Two consequences of "survives unchanged" worth stating, because they are what
keeps this pass small:

* A **generic binding's body is not rewritten at all.** Only a ground binding
  and a specialized copy are. So inside anything this pass rewrites, every type
  argument that came from a binder is ground, and a `CTyApp` that is *not*
  ground is one whose variable came from somewhere else -- an ambiguous
  residual, or a method's own quantification -- and is left alone.
* Nothing is deleted. A binding nothing reaches any more is still emitted;
  dropping it is reachability, which is M14c's, and doing it here would mean
  this pass had to be right about liveness as well as about types.

## What it does not do

A method's own polymorphism -- `map` in a `Functor` dictionary is still
`forall a b` -- lives in a record *field*, not in a binding, so `CTyApp` over a
`CField` stays. Erasing it means hoisting the field into a binding, which is
devirtualization, which is M14c. Likewise the dictionary *parameters* of an
ordinary function: `Main#squash@Int` still takes its `%Dict.Monoid Int`, it is
just always handed the same one. Dropping a parameter that is provably constant
is an optimization over ground code, and ground code is what this pass produces
rather than what it consumes.

The result is checked by `turkey/coretc.py`, unconditionally, exactly as the
lowering is. A pass whose output nobody checks is believed for the reason the
pre-M13 elaborator was believed, which is that nobody looked.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace

from .classes import ClassTable
from .core import CAlt, CBind, CExpr, CLam, CLet, CLetRec, CParam, CProgram, CRecord, CTyApp, CVar
from .coretc import Fams
from .decls import DeclTable, substitute
from .typed import reduce_deep
from .types import TApp, TCon, TFam, TFun, TTuple, TVar, Type, prune, spine, type_key, vars_of

# How many distinct specializations one binding may have, and how large a type
# argument may be. Either limit stops the unrolling a polymorphically recursive
# binding would otherwise ask for. The numbers are deliberately generous: no
# program in the suite comes near them, so tripping one is a real signal rather
# than a routine event.
MAX_SPECIALIZATIONS = 32
MAX_TYPE_NODES = 64


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


def _dict_constructor(bind: CBind) -> bool:
    """Whether this binding is an instance *with a context*: a function from
    dictionaries to a dictionary.

    Asked structurally rather than by the `%inst.` prefix, because the shape is
    what the collapse below actually depends on -- a lambda whose body is the
    record -- and a name is a convention a later pass could change.
    """
    return (isinstance(bind.value, CLam)
            and isinstance(bind.value.body, CRecord))


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
        """`%inst.C.T[t](ev)` -- an instance with a context, applied -- becomes
        one top-level binding of the record it builds.

        This is the per-request rebuild M13 left behind. The application is
        collapsed rather than merely specialized because specializing alone
        would leave `%inst.Monoid.Array@Int` a *function*, still applied, still
        allocating. Coherence is what makes it sound: the dictionary arguments
        are ground, so they are the only ones this instance could ever be
        applied to at this head, and building the record once is building it
        with the same contents.
        """
        fn, targs = e.fn, []
        if isinstance(fn, CTyApp):
            targs = [self.ty(a) for a in fn.args]
            fn = fn.fn
        if not isinstance(fn, CVar):
            return None
        name = self.rename.get(fn.name, fn.name)
        bind = self.owner.byname.get(name)
        if bind is None or name not in self.owner.dict_ctors:
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
                 classes: ClassTable) -> None:
        self.program = program
        self.decls = decls
        self.classes = classes
        self.byname: dict[str, CBind] = {}
        self.generic: dict[str, CBind] = {}
        self.dict_ctors: set[str] = set()
        # Every top-level dictionary binding. A collapse may only be handed
        # one of these, since the binding it makes lives out here with them.
        self.top_dicts: set[str] = set()
        # key -> the name built for it. The key is the original's name, the
        # `type_key` of each type argument, and -- for an instance whose
        # context was collapsed -- the dictionaries it was applied to.
        self.done: dict[tuple, str] = {}
        self.counts: dict[str, int] = {}
        self.made: dict[str, list[CBind]] = {}
        self.queue: list[tuple[str, CBind, list[Type], list[str] | None]] = []
        self.used: set[str] = set()
        self.capped: set[str] = set()
        self.warnings: list[str] = []

    def run(self) -> CProgram:
        for group, is_dict in ((self.program.dicts, True),
                               (self.program.binds, False)):
            for bind in group:
                self.byname[bind.name] = bind
                self.used.add(bind.name)
                if bind.binders:
                    self.generic[bind.name] = bind
                if is_dict and not bind.binders:
                    self.top_dicts.add(bind.name)
                if _dict_constructor(bind):
                    self.dict_ctors.add(bind.name)
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
        if self.counts.get(bind.name, 0) >= MAX_SPECIALIZATIONS:
            self.cap(bind, f"is used at more than {MAX_SPECIALIZATIONS} types")
            return None
        if any(_size(t) > MAX_TYPE_NODES for t in targs):
            self.cap(bind, f"is used at a type of more than {MAX_TYPE_NODES} "
                           f"parts")
            return None
        self.counts[bind.name] = self.counts.get(bind.name, 0) + 1
        name = self.fresh(bind.name, targs)
        # Recorded *before* the body is built, which is what makes a recursive
        # binding terminate: the copy's own call to itself, at its own
        # arguments, finds this entry rather than asking for another copy.
        self.done[key] = name
        self.queue.append((name, bind, targs, dicts))
        return name

    def cap(self, bind: CBind, why: str) -> None:
        """Said once per binding, and said at all because a program that
        quietly stops being specialized is a program whose performance depends
        on something nobody was told about."""
        if bind.name in self.capped:
            return
        self.capped.add(bind.name)
        where = f"{bind.span}: " if bind.span is not None else ""
        self.warnings.append(
            f"{where}warning: '{bind.name}' {why}, so its remaining uses are "
            f"left polymorphic")

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
            # An instance's context, supplied. The lambda goes away and its
            # parameters are renamed to the dictionaries they were handed --
            # a rename and not a `let`, because the argument is a top-level
            # name, so substituting it is capture-free and leaves the record's
            # fields projecting from something M14c can see through.
            assert isinstance(value, CLam)
            rewriter.rename = dict(zip((p.name for p in value.params), dicts))
            value = value.body
            ty = rewriter.ty(value.ty)
        return CBind(name, ty, [], rewriter.rewrite(value), bind.span,
                     bind.mutable, bind.module, rewriter.equations)


def monomorphize(program: CProgram, decls: DeclTable,
                 classes: ClassTable) -> tuple[CProgram, list[str]]:
    pass_ = Monomorphizer(program, decls, classes)
    return pass_.run(), pass_.warnings


__all__ = ["MAX_SPECIALIZATIONS", "MAX_TYPE_NODES", "Monomorphizer",
           "monomorphize"]
