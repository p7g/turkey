"""One compiled body per distinct *layout* of the type arguments (M25).

`plan.txt` item 10's second tier, and the pass that makes unboxing total.

Monomorphization specializes a binding per distinct tuple of type arguments,
and it is partial **by decision**: Turkey admits polymorphic recursion, so the
set of types a binding is used at can be infinite -- `fun depth(x : a, n : Int)`
recursing at `Pair a` asks for `Pair Int`, `Pair (Pair Int)`, and so on without
end -- and item 6 caps it. Past the cap a call site keeps its `CTyApp` and
calls a body that is still generic.

That body is what `mono.check_layouts` refuses, and rightly: it may take
polymorphic data apart, and the layout it would read a field at is decided
where it is compiled while the layout the field was written at was decided at
the construction site. Nothing makes the two agree.

This pass is the same specialization keyed on the *layout* of each type
argument rather than on the type, and it needs no cap for the reason the cap
existed. There are seven layouts and infinitely many types; `layout(Pair a)`
is `ptr` whatever `a` is, so the chain that diverged under `type_key` --
`Pair Int`, `Pair (Pair Int)`, ... -- collapses to a single key. The thing
that could not be bounded was the type index. The layout index is bounded by
construction, so the worklist below terminates on any program.

## The copies stay polymorphic

A copy is *not* the body with a type substituted into it. Substituting one
would be a lie: `Data.Array#push` at layout `ptr` is called with `Array String`
and with `Array (Option Int)` alike, and there is no type to write that both
of those check against. So the copy keeps the original's scheme -- its call
sites type-check unchanged, and `coretc` checks it exactly as it checked the
original -- and carries the one extra fact it is compiled under: `CBind.layouts`,
the layout each abstracted variable stands for. `backend_lower.layout_of`
consults it, and a variable that used to have no layout and be held `BOXED`
now has one.

Which is the whole difference. Nothing type-directed happens at run time that
did not happen before: no witness table is passed, no value becomes
address-only, no closure needs reabstracting across the boundary, because
`layout(fun(a) -> b)` is `ptr` and the closure's own body is a binding this
pass shares in its turn. The cost `plan.txt` item 10 names is unchanged and
still paid -- a shared body builds its dictionaries as it recurses and the
inliner cannot fire there -- and that region is slow, not wrong.

## What gets a copy

Only the bindings whose layouts have to be known: the ones
`mono.transparent_parameters` would refuse, which are those holding a
parameter whose type *mentions* an abstracted variable without *being* one.
`Array a` is transparent and `a` is not, and the distinction is parametricity:
a body may hold an `a` and pass it on knowing nothing, and that is why a bare
`a` parameter needs no copy and gets none.

The set is closed under one more rule. A generic binding that calls a
transparent one at a type mentioning its *own* binders cannot say which copy
it means, so it needs its layouts known too, and joins the set. Closing that
is a fixed point, reached below.
"""

from __future__ import annotations

import itertools
from dataclasses import fields as _dataclass_fields, replace

from . import ast
from . import backend_ir as bir
from .backend_lower import layout_of
from .core import (CAlt, CApp, CBind, CCon, CExpr, CMatch, CProgram, CRecord,
                   CTyApp, CVar, abstraction_binders, names_of, openings,
                   transparent_parameters as core_transparent)
from .types import Type, TVar, prune, vars_of

#: Every layout a packed variable can have been stored at, in the order an
#: opened arm is copied for them (SPEC-DELTAS 68).
OPENED_LAYOUTS = tuple(layout.value for layout in bir.Layout)

_FIELDS: dict[type, tuple] = {}


def _fields(node):
    cls = type(node)
    found = _FIELDS.get(cls)
    if found is None:
        found = _FIELDS[cls] = tuple(_dataclass_fields(cls))
    return found


def transparent(bind: CBind) -> bool:
    """Whether this binding could take polymorphic data apart.

    Every binder counts as abstracted here, which is the difference from
    `mono.check_layouts`: that one asks after this pass has run and so may
    discount a variable whose layout a copy already carries, while this one is
    deciding which bindings need such a copy in the first place. The predicate
    itself is `core.transparent_parameters`, shared with it, because when the
    two were written separately they were blind to constrained bindings
    together.
    """
    return bool(core_transparent(bind, {v.id for v in abstraction_binders(bind)}))


def _applications(node, out: list[CTyApp]) -> None:
    """Every `CTyApp` of a top-level name anywhere inside a term."""
    if isinstance(node, CTyApp) and isinstance(node.fn, CVar):
        out.append(node)
    if isinstance(node, (CExpr, CAlt, CBind)):
        for f in _fields(node):
            _applications(getattr(node, f.name), out)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _applications(item, out)


def _with_layouts(pat, keys: dict[int, tuple[str, ...]]):
    """A copy of `pat` whose openings record the layouts `keys` gives them,
    by the identity of the opening in the original."""
    if isinstance(pat, ast.PAnnot):
        return replace(pat, pat=_with_layouts(pat.pat, keys))
    if isinstance(pat, ast.PTuple):
        return replace(pat, elems=[_with_layouts(e, keys) for e in pat.elems])
    if isinstance(pat, ast.PCon):
        return replace(pat, args=[_with_layouts(a, keys) for a in pat.args],
                       layouts=keys.get(id(pat), pat.layouts))
    if isinstance(pat, ast.PRecord):
        return replace(pat, fields=[(n, _with_layouts(s, keys))
                                    for n, s in pat.fields],
                       layouts=keys.get(id(pat), pat.layouts))
    return pat


def _opens(node) -> bool:
    """Whether an existential arm appears anywhere inside a term."""
    if isinstance(node, CAlt) and openings(node.pat):
        return True
    if isinstance(node, (CExpr, CAlt, CBind)):
        return any(_opens(getattr(node, f.name)) for f in _fields(node))
    if isinstance(node, (list, tuple)):
        return any(_opens(item) for item in node)
    return False


def _constructs(node, abstracted: set[int], decls) -> bool:
    """Whether a term builds a value whose field layout it cannot know.

    A transparent body *reads* a field at a layout decided elsewhere; this is
    the other half, a body that *writes* one. `fun mk(x : a) -> Box a` left
    generic past the cap holds `x` boxed and stores the box, while a ground
    reader of `Box Int` loads the same word as an `i64` (NATIVE-BACKEND.md, "A
    hole to close first"). Such a body needs its layouts known exactly as a
    transparent one does, so it is shared too.

    The field that matters is one *declared* at a bare type variable and given
    a value of one of this body's own variables: that is the only place the
    written layout depends on an instantiation this body does not know. A
    field declared `Prim.Array a` is a pointer whatever `a` is, and a newtype
    is never built at all.

    Packing an existential is stricter (SPEC-DELTAS 68): the value
    records its hidden variables' layouts, so *any* argument mentioning this
    body's variables -- an `Array a` included -- makes the stored code a guess.
    """
    if decls is not None and isinstance(node, CApp) and isinstance(node.fn, CCon):
        info = decls.constructors.get(node.fn.name)
        if info is not None and _writes_unknown(
                info, info.scheme.body.params, [a.ty for a in node.args],
                abstracted, node.ty, decls):
            return True
    if (decls is not None and isinstance(node, CRecord)
            and node.con in decls.constructors):
        info = decls.constructors[node.con]
        declared = dict(zip(info.field_names or [], info.scheme.body.params))
        if _writes_unknown(info, [declared[name] for name, _ in node.fields],
                           [value.ty for _, value in node.fields],
                           abstracted, node.ty, decls):
            return True
    if isinstance(node, (CExpr, CAlt, CBind)):
        return any(_constructs(getattr(node, f.name), abstracted, decls)
                   for f in _fields(node))
    if isinstance(node, (list, tuple)):
        return any(_constructs(item, abstracted, decls) for item in node)
    return False


def _writes_unknown(info, declared: list[Type], given: list[Type],
                    abstracted: set[int], built: Type, decls) -> bool:
    if info.is_existential:
        return any({v.id for v in vars_of(t)} & abstracted for t in given)
    if decls.erased_payload(built) is not None:
        return False
    for field_ty, value_ty in zip(declared, given):
        value_ty = prune(value_ty)
        if (isinstance(prune(field_ty), TVar) and isinstance(value_ty, TVar)
                and value_ty.id in abstracted):
            return True
    return False


def _needs_layouts(binds: dict[str, CBind], decls=None) -> set[str]:
    """The bindings whose abstracted layouts have to be known.

    The transparent ones, and then whatever calls them at a type of its own.

    Every binding, not only the ones reachable from `main`. There is more than
    one notion of reachable here -- `mono.transparent_parameters` walks from
    `main`, `backend_lower.lower` walks from `main` *and* every value the entry
    module defines -- and a binding this pass declined to share because one
    walk could not see it is a binding the other walk hands the backend with
    no layouts. `Data.Array#map` is exactly that: it calls `#push` at its own
    `a`, so it must know its layouts, and it is not reachable by the first
    walk. Sharing an unreachable binding costs a copy nothing emits.
    """
    found = {name for name, bind in binds.items() if transparent(bind)}
    found |= {name for name, bind in binds.items()
              if abstraction_binders(bind) and _constructs(
                  bind.value, {v.id for v in abstraction_binders(bind)}, decls)}
    while True:
        grew = False
        for name in binds:
            if name in found:
                continue
            bind = binds[name]
            if not abstraction_binders(bind):
                continue
            abstracted = {variable.id for variable in abstraction_binders(bind)}
            applications: list[CTyApp] = []
            _applications(bind.value, applications)
            for application in applications:
                assert isinstance(application.fn, CVar)
                if application.fn.name not in found:
                    continue
                if any({v.id for v in vars_of(a)} & abstracted
                       for a in application.args):
                    found.add(name)
                    grew = True
                    break
        if not grew:
            return found


def _key(args: list[Type], abstracted: dict[int, str],
         decls) -> tuple[str, ...] | None:
    """The layout of each type argument, or None if one is not knowable.

    Asked directly now. This used to test for `BOXED`, because that was the
    answer `layout_of` gave a variable it had no layout for -- reading an
    absence out of a value that also means "there is a box here". `layout_of`
    says which it means, so this passes the absence along instead of
    reconstructing it.
    """
    out = []
    for arg in args:
        layout = layout_of(arg, abstracted, decls)
        if layout is None:
            return None
        out.append(layout.value)
    return tuple(out)


def _packed_key(info, node: CApp, abstracted: dict[int, str],
                decls) -> tuple[str, ...] | None:
    """The layouts one packing stores for its hidden variables, or None if
    they are not knowable here. Matched one way, so no type is changed."""
    from .classes import match
    from .types import TVar, prune
    found: dict[int, Type] = {}
    for param, argument in zip(info.scheme.body.params,
                               node.args[len(info.context):]):
        found.update(match(param, prune(argument.ty)) or {})
    key = []
    for variable in info.exists:
        packed = found.get(variable.id)
        if packed is None:
            return None
        chosen = layout_of(packed, abstracted, decls)
        if chosen is None:
            if not isinstance(prune(packed), TVar):
                return None
            chosen = bir.Layout.BOXED
        key.append(chosen.value)
    return tuple(key)


def _packed_layouts(program: CProgram, decls) -> dict[str, set[tuple[str, ...]]]:
    """For each existential constructor, the layout keys some reachable
    packing stores.

    A packing inside a copy of an opened arm happens only if that
    copy is ever taken, so it counts only once the key its arm was copied for
    is itself packed somewhere. Counting it unconditionally would let a repack
    at the skolem in the `unit` copy of an arm justify keeping the `unit` copy.
    """
    records: list[tuple[str, tuple[str, ...] | None, frozenset]] = []

    def walk(node, abstracted: dict[int, str], conditions: frozenset) -> None:
        if isinstance(node, CMatch):
            walk(node.scrutinee, abstracted, conditions)
            for alt in node.alts:
                inner = dict(abstracted)
                held = set(conditions)
                for pat in openings(alt.pat):
                    if pat.layouts is None:
                        continue
                    inner.update({-s.uid: layout
                                  for s, layout in zip(pat.skolems, pat.layouts)})
                    held.add((pat.name, pat.layouts))
                walk(alt.body, inner, frozenset(held))
            return
        if isinstance(node, CApp) and isinstance(node.fn, CCon):
            info = decls.constructors.get(node.fn.name)
            if info is not None and info.is_existential:
                records.append((info.name,
                                _packed_key(info, node, abstracted, decls),
                                conditions))
        if isinstance(node, (CExpr, CAlt, CBind)):
            for f in _fields(node):
                walk(getattr(node, f.name), abstracted, conditions)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item, abstracted, conditions)

    for bind in program.dicts + program.binds:
        walk(bind.value, bind.layouts, frozenset())
    live: dict[str, set[tuple[str, ...]]] = {}
    changed = True
    while changed:
        changed = False
        for name, key, conditions in records:
            if not all(want in live.get(con, set()) for con, want in conditions):
                continue
            arity = len(decls.constructors[name].exists)
            keys = ([key] if key is not None else
                    list(itertools.product(OPENED_LAYOUTS, repeat=arity)))
            have = live.setdefault(name, set())
            for one in keys:
                if one not in have:
                    have.add(one)
                    changed = True
    return live


class _Sharer:
    def __init__(self, program: CProgram, decls,
                 packed: dict[str, set[tuple[str, ...]]] | None = None) -> None:
        self.decls = decls
        #: Which layout keys each existential constructor is packed at, when
        #: known; an opened arm is copied for those alone. None copies every
        #: layout, which is the first of `share`'s two runs.
        self.packed = packed
        self.binds = {b.name: b for b in program.dicts + program.binds}
        self.shared = _needs_layouts(self.binds, decls)
        # key -> the name built for it, and the copies in request order.
        self.done: dict[tuple[str, tuple[str, ...]], str] = {}
        self.made: dict[str, list[CBind]] = {}
        self.queue: list[tuple[str, str, tuple[str, ...]]] = []
        self.used = set(self.binds)

    def run(self, program: CProgram) -> CProgram:
        if not self.shared and not _opens(program.dicts + program.binds):
            return program
        rewritten = {
            name: replace(bind, value=self.rewrite(bind.value, bind.layouts))
            for name, bind in self.binds.items()
            if name not in self.shared
        }
        while self.queue:
            name, original, key = self.queue.pop(0)
            self.made.setdefault(original, []).append(
                self.build(name, self.binds[original], key))

        kept = self.reachable(rewritten)
        out = CProgram()
        for group, target in ((program.dicts, out.dicts),
                              (program.binds, out.binds)):
            for bind in group:
                # Beside the original and ahead of it, as `mono` places its
                # specializations, and for the same reason: a copy depends on
                # no more than the original does, and top-level bindings are
                # evaluated in this order.
                target.extend(self.made.get(bind.name, []))
                if bind.name in kept:
                    target.append(rewritten.get(bind.name, bind))
        return out

    def reachable(self, rewritten: dict[str, CBind]) -> set[str]:
        """The names still worth emitting, which is every one but a shared
        original that nothing calls any more.

        A shared binding's original is the one body here that is *not*
        rewritten, because rewriting it would need the layouts it does not
        have. Once every call site has gone to a copy it is dead, and leaving
        it is not harmless: it names the other originals, so one that survives
        keeps the rest alive, and `mono.check_layouts` then refuses a program
        for a body nothing would have compiled.

        A fixed point rather than one pass, since dropping one can orphan the
        next.
        """
        made = {b.name: b for copies in self.made.values() for b in copies}
        live = {name for name in self.binds if name not in self.shared}
        live |= set(made)
        while True:
            grew = False
            for name in sorted(live):
                bind = made.get(name) or rewritten.get(name) or self.binds[name]
                for used in names_of(bind.value) & set(self.binds):
                    if used not in live:
                        live.add(used)
                        grew = True
            if not grew:
                return live

    def request(self, name: str, key: tuple[str, ...]) -> str:
        found = self.done.get((name, key))
        if found is not None:
            return found
        made = f"{name}@[{','.join(key)}]"
        while made in self.used:
            made += "~"
        self.used.add(made)
        # Recorded before the body is built, which is what makes a recursive
        # binding terminate: the copy's own call to itself, at its own
        # layouts, finds this entry rather than asking for another copy.
        self.done[(name, key)] = made
        self.queue.append((made, name, key))
        return made

    def build(self, made: str, bind: CBind, key: tuple[str, ...]) -> CBind:
        abstracted = {variable.id: layout
                      for variable, layout in zip(abstraction_binders(bind), key)}
        return replace(bind, name=made, layouts=abstracted,
                       value=self.rewrite(bind.value, abstracted))

    def rewrite(self, node, abstracted: dict[int, str]):
        """Every call to a shared binding, pointed at the copy it means."""
        if isinstance(node, CTyApp) and isinstance(node.fn, CVar):
            if node.fn.name in self.shared:
                # Positional, so a binding that states its `forall` in both
                # places at once cannot be keyed from one type application and
                # is left alone -- `mono.check_layouts` then says so, which is
                # a limit that is visible rather than one that is guessed at.
                want = len(abstraction_binders(self.binds[node.fn.name]))
                key = (_key(node.args, abstracted, self.decls)
                       if len(node.args) == want else None)
                if key is not None:
                    made = self.request(node.fn.name, key)
                    return replace(node, fn=replace(node.fn, name=made))
        if isinstance(node, CMatch):
            return self.open_arms(node, abstracted)
        if isinstance(node, (CExpr, CAlt, CBind)):
            return type(node)(**{
                f.name: self.rewrite(getattr(node, f.name), abstracted)
                for f in _fields(node)
            })
        if isinstance(node, list):
            return [self.rewrite(item, abstracted) for item in node]
        if isinstance(node, tuple):
            return tuple(self.rewrite(item, abstracted) for item in node)
        return node


    def _known_key(self, scrutinee, whole, pat,
                   abstracted: dict[int, str]) -> tuple[str, ...] | None:
        """The layouts an opening must be for, when what it opens is a packing
        written right here -- `match C(d, xs) { C(ys) -> ... }`, which is what
        an inlined opener leaves behind. None when the value comes from
        anywhere else, or when the opening is nested inside a larger pattern,
        where nothing lines the two up.
        """
        top = whole
        while isinstance(top, ast.PAnnot):
            top = top.pat
        if top is not pat:
            return None
        if not (isinstance(scrutinee, CApp) and isinstance(scrutinee.fn, CCon)):
            return None
        if scrutinee.fn.name != pat.name:
            return None
        info = self.decls.constructors.get(pat.name)
        if info is None or not info.is_existential:
            return None
        return _packed_key(info, scrutinee, abstracted, self.decls)

    def open_arms(self, node: CMatch, abstracted: dict[int, str]) -> CMatch:
        """One copy of each existential arm per layout it may have been packed at.

        Contract 1 of ERRORS.md, "Contracts considered". Inside a
        copy the opened skolems have a layout -- keyed by `-uid`, so that
        `layout_of` can tell them from the binding's own variables -- and the
        body is rewritten under it like any layout-keyed copy: a call at the
        skolem finds `f@[i64]`, an element read is at `i64`, a closure is
        called at `i64`. The copy's pattern records its layouts, and the
        backend takes the arm only when the value's stored codes match them.
        Nothing is converted, so an opened array is the array that was packed.
        """
        alts: list[CAlt] = []
        for alt in node.alts:
            opened = [p for p in openings(alt.pat) if p.layouts is None]
            if not opened:
                alts.append(self.rewrite(alt, abstracted))
                continue
            # One key per opening, and one copy per combination of them --
            # unless the value being opened is a packing right here, in which
            # case it is the one key that packing stores. That is the
            # pack-then-match case, and it is what keeps an opener inlined at
            # several call sites from carrying every layout at each of them
            # (ERRORS.md, finding 3).
            choices = []
            for pat in opened:
                known = self._known_key(node.scrutinee, alt.pat, pat, abstracted)
                if known is not None:
                    choices.append([known])
                    continue
                allowed = (None if self.packed is None
                           else self.packed.get(pat.name, set()))
                choices.append([key for key in itertools.product(
                                    OPENED_LAYOUTS, repeat=len(pat.skolems))
                                if allowed is None or key in allowed])
            for combination in itertools.product(*choices):
                inner = dict(abstracted)
                for pat, key in zip(opened, combination):
                    inner.update({-skolem.uid: layout
                                  for skolem, layout in zip(pat.skolems, key)})
                keys = {id(pat): key for pat, key in zip(opened, combination)}
                alts.append(CAlt(_with_layouts(alt.pat, keys),
                                 self.rewrite(alt.body, inner)))
        return replace(node, scrutinee=self.rewrite(node.scrutinee, abstracted),
                       alts=alts)


def share(program: CProgram, decls) -> CProgram:
    """The program with one body per layout of every binding that needs one.

    Twice when the program opens an existential (SPEC-DELTAS 68): once copying every
    opened arm for every layout, which is what makes every packing's layouts
    knowable, and again copying each arm only for the keys some reachable
    packing stores. The second run can only drop copies, so the keys the
    first found are still a superset of what the second can pack.
    """
    first = _Sharer(program, decls).run(program)
    if not _opens(program.dicts + program.binds):
        return first
    return _Sharer(program, decls, _packed_layouts(first, decls)).run(program)


__all__ = ["share", "transparent"]
