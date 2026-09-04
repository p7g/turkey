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

from dataclasses import fields as _dataclass_fields, replace

from . import backend_ir as bir
from .backend_lower import layout_of
from .core import (CAlt, CBind, CExpr, CProgram, CTyApp, CVar,
                   abstraction_binders, names_of,
                   transparent_parameters as core_transparent)
from .types import Type, vars_of

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


def _needs_layouts(binds: dict[str, CBind]) -> set[str]:
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


class _Sharer:
    def __init__(self, program: CProgram, decls) -> None:
        self.decls = decls
        self.binds = {b.name: b for b in program.dicts + program.binds}
        self.shared = _needs_layouts(self.binds)
        # key -> the name built for it, and the copies in request order.
        self.done: dict[tuple[str, tuple[str, ...]], str] = {}
        self.made: dict[str, list[CBind]] = {}
        self.queue: list[tuple[str, str, tuple[str, ...]]] = []
        self.used = set(self.binds)

    def run(self, program: CProgram) -> CProgram:
        if not self.shared:
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


def share(program: CProgram, decls) -> CProgram:
    """The program with one body per layout of every binding that needs one."""
    return _Sharer(program, decls).run(program)


__all__ = ["share", "transparent"]
