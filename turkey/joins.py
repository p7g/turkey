"""Join-point discovery (`plan.txt` item 7).

A `let`-bound lambda is a **join point** when every mention of it is a
saturated call in tail position and none of them escapes. Then it is not a
function at all: it is a label, and the calls are jumps. That is the analysis
Maurer, Downen, Ariola and Peyton Jones describe in *Compiling without
continuations* (PLDI 2017) and the one GHC 8.2 runs on Core, for the reason
item 7 records -- a separate IR below Core is not where the information is.

M15a put `CJoin` and `CJump` in the IR and gave them a rule. This is the pass
that produces them, and it is an analysis rather than a transformation of
meaning: the term it emits computes exactly what the term it consumed did, and
what changed is only that a fact already true of the program is now written
down where a backend can act on it.

## It knows nothing about monads

Deliberately, and that is the whole reason item 4's `?`-aware lowering was
deleted in favour of this. A `bind` chain reaches this pass the way any other
saturated call to a known small function reaches it. What makes `?` fast is
inlining (M15c) and case-of-case (M15d) meeting a general join-point pass here,
not a second desugaring that knows what `Flow` is.

## What it does not find, yet, and why that is fine

Very little, today. The lambda a `?` builds is an *argument* to `bind`, so it
escapes by construction and no analysis can call it a label until inlining has
put it in tail position. M15b is the pass; M15c and M15d are what give it
something to chew on. Emitting nothing on a program where nothing qualifies is
the correct answer and not a failure of the pass.

## Disagreeing with the checker is loud

Which positions are tail positions is stated once, in `core.TAIL_FIELDS`, and
read by both this pass and the rule in `coretc.py`. Where the two could still
drift -- this pass walks to find tail positions, the checker threads a join
environment into them -- the disagreement is caught automatically, because
`driver.check` runs `coretc.check_program` after this pass on every program in
the suite. Calling a position a tail when the checker does not means a jump
with nowhere to go, which is a rejected term rather than a wrong answer. Being
conservative in the other direction costs an optimization and nothing else.
"""

from __future__ import annotations

from dataclasses import fields, replace

from .core import (
    TAIL_FIELDS, CAlt, CApp, CBind, CExpr, CJoin, CJump, CLam, CLet, CLetRec,
    CProgram, CVar,
)
from .coretc import compatible
from .types import TBottom, Type


def discover(program: CProgram) -> CProgram:
    """Every binding's body, with its join points found."""
    return CProgram(
        dicts=[_bind(b) for b in program.dicts],
        binds=[_bind(b) for b in program.binds],
    )


def _bind(bind: CBind) -> CBind:
    return replace(bind, value=_expr(bind.value))


# -- the walk ----------------------------------------------------------------


def _expr(e):
    """Rewrite `e`, converting any binding under it that is a join point.

    Note what this walk does *not* carry: whether `e` is itself in tail
    position. It does not need to. Whether `f` is a join point is a question
    about tail position within `f`'s own scope -- the body of the `let` that
    binds it -- and that is the same question wherever the `let` sits. A
    candidate that fails inside an argument fails for its own reasons.
    """
    if e is None or not isinstance(e, CExpr):
        return e
    if isinstance(e, CLet) and not e.binders:
        found = _as_join(e.name, e.value, e.body, e.ty, recursive=False)
        if found is not None:
            return _finish(found, e.value, e.body)
    if isinstance(e, CLetRec) and len(e.binds) == 1 and not e.binds[0].binders:
        bind = e.binds[0]
        found = _as_join(bind.name, bind.value, e.body, e.ty, recursive=True)
        if found is not None:
            return _finish(found, bind.value, e.body)
    return _children(e)


def _finish(join: CJoin, lam: CLam, body) -> CJoin:
    """Fill in a join whose eligibility is already settled.

    Both halves are rewritten from tail position, because both halves *are*
    tail positions of this join -- that is what `core.TAIL_FIELDS` says about
    `CJoin`, and the checker reads the same entry. A non-recursive join has no
    self-calls for the body pass to find, so passing `recursive` there is a
    statement rather than a filter.
    """
    join.params = list(lam.params)
    join.body = _jumps(_expr(lam.body), join.name,
                       len(lam.params), join.recursive)
    join.rest = _jumps(_expr(body), join.name, len(lam.params), True)
    return join


def _children(e):
    """Copy `e`, rewriting each subterm."""
    return type(e)(**{f.name: _value(getattr(e, f.name)) for f in fields(e)})


def _value(v):
    if isinstance(v, CExpr):
        return _expr(v)
    if isinstance(v, CAlt):
        return CAlt(v.pat, _expr(v.body))
    if isinstance(v, CBind):
        return replace(v, value=_expr(v.value))
    if isinstance(v, list):
        return [_value(x) for x in v]
    if isinstance(v, tuple):
        return tuple(_value(x) for x in v)
    return v


# -- eligibility -------------------------------------------------------------


def _as_join(name: str, value, body, ty: Type, recursive: bool) -> CJoin | None:
    """A `CJoin` shell if this binding qualifies, or None.

    Five conditions, and each is the same one seen from a different side: the
    binding is a lambda; nothing rebinds its name underneath, so every mention
    below really is a mention of it; every mention is a saturated tail call;
    the self-calls are there only if the join says it is recursive; and the
    lambda answers what the surrounding term answers, since a jump replaces the
    value of the whole join expression.
    """
    if not isinstance(value, CLam) or value.body is None:
        return None
    arity = len(value.params)
    if _rebinds(body, name) or _rebinds(value.body, name):
        return None
    if not _only_tail_calls(body, name, arity, True):
        return None
    if not _only_tail_calls(value.body, name, arity, recursive):
        return None
    if recursive and not _mentions(value.body, name):
        # A `letrec` whose one binding never calls itself. Legal, and not this
        # pass's business to tidy: leaving it alone keeps the recursive case
        # about recursion.
        return None
    if not compatible(ty, value.body.ty):
        return None
    return CJoin(ty, value.span, name, [], None, None, recursive)


def _only_tail_calls(e, name: str, arity: int, allowed: bool) -> bool:
    """Whether every mention of `name` in `e` is a saturated call in tail
    position -- and, when `allowed` is false, whether there are none at all."""
    if not _mentions(e, name):
        return True
    if not allowed:
        return False
    return _walk_calls(e, name, arity, True)


def _walk_calls(e, name: str, arity: int, tail: bool) -> bool:
    if isinstance(e, CVar):
        # A bare mention: it escapes -- into an argument, a data structure, a
        # returned value -- and a label cannot be passed anywhere.
        return e.name != name
    if isinstance(e, CApp) and isinstance(e.fn, CVar) and e.fn.name == name:
        if not tail or len(e.args) != arity:
            return False
        return all(_walk_calls(a, name, arity, False) for a in e.args)
    if isinstance(e, CExpr):
        tails = TAIL_FIELDS.get(type(e).__name__, ())
        return all(
            _walk_calls_value(getattr(e, f.name), name, arity,
                              tail and f.name in tails)
            for f in fields(e)
        )
    return True


def _walk_calls_value(v, name: str, arity: int, tail: bool) -> bool:
    if isinstance(v, CExpr):
        return _walk_calls(v, name, arity, tail)
    if isinstance(v, CAlt):
        return _walk_calls(v.body, name, arity, tail)
    if isinstance(v, CBind):
        return _walk_calls(v.value, name, arity, False)
    if isinstance(v, (list, tuple)):
        return all(_walk_calls_value(x, name, arity, tail) for x in v)
    return True


def _jumps(e, name: str, arity: int, tail: bool):
    """Turn the calls the analysis just licensed into jumps."""
    if (isinstance(e, CApp) and isinstance(e.fn, CVar) and e.fn.name == name
            and tail):
        assert len(e.args) == arity, "an unlicensed call reached _jumps"
        return CJump(TBottom(), e.span, name,
                     [_jumps(a, name, arity, False) for a in e.args])
    if not isinstance(e, CExpr):
        return e
    tails = TAIL_FIELDS.get(type(e).__name__, ())
    return type(e)(**{
        f.name: _jumps_value(getattr(e, f.name), name, arity,
                             tail and f.name in tails)
        for f in fields(e)
    })


def _jumps_value(v, name: str, arity: int, tail: bool):
    if isinstance(v, CExpr):
        return _jumps(v, name, arity, tail)
    if isinstance(v, CAlt):
        return CAlt(v.pat, _jumps(v.body, name, arity, tail))
    if isinstance(v, CBind):
        return replace(v, value=_jumps(v.value, name, arity, False))
    if isinstance(v, list):
        return [_jumps_value(x, name, arity, tail) for x in v]
    if isinstance(v, tuple):
        return tuple(_jumps_value(x, name, arity, tail) for x in v)
    return v


# -- two small questions about names -----------------------------------------


def _mentions(e, name: str) -> bool:
    if isinstance(e, CVar) and e.name == name:
        return True
    if isinstance(e, (CExpr, CBind, CAlt)):
        return any(_mentions(getattr(e, f.name), name) for f in fields(e))
    if isinstance(e, (list, tuple)):
        return any(_mentions(x, name) for x in e)
    return False


def _rebinds(e, name: str) -> bool:
    """Whether anything under `e` binds `name` again.

    Names are unique after resolution and the generated ones carry a counter,
    so this should never fire -- which is exactly why it is worth asking. If it
    ever did, an inner mention would not be a mention of the join point, and
    rewriting it to a jump would be a miscompilation the checker could well
    accept, since the two would agree on types and differ only on which binding
    they meant.
    """
    if isinstance(e, CLam) and any(p.name == name for p in e.params):
        return True
    if isinstance(e, CJoin) and (e.name == name
                                 or any(p.name == name for p in e.params)):
        return True
    if isinstance(e, CLet) and e.name == name:
        return True
    if isinstance(e, CLetRec) and any(b.name == name for b in e.binds):
        return True
    if isinstance(e, CAlt):
        from .deps import pattern_vars
        return name in set(pattern_vars(e.pat)) or _rebinds(e.body, name)
    if isinstance(e, (CExpr, CBind)):
        return any(_rebinds(getattr(e, f.name), name) for f in fields(e))
    if isinstance(e, (list, tuple)):
        return any(_rebinds(x, name) for x in e)
    return False


__all__ = ["discover"]
