"""Tree-walking evaluator for the typed Core (design.md section 6).

Strict, call-by-value, left-to-right. Control transfers -- `return`, `break`,
`continue` -- are Python exceptions, which is the natural fit for constructs
whose type is bottom: they never yield a value to their context.

Reference semantics for records and arrays (section 6.3) need no special
machinery: the objects in `turkey.values` are mutable and are never copied, so
binding a name to one aliases it.

## It runs the Core, not the surface tree

Until M13c this walked `turkey/ast.py` and consulted three side channels the
elaborator had filled in -- a `Use` per occurrence, an `Abstraction` per
binding group, an `InstancePlan` per instance -- reading dictionaries back out
of an `Evidence` tree at every method call. All of that is gone. What arrives
here is `turkey/core.py`, already checked by `turkey/coretc.py`, and three
things this file used to do have gone with it:

* **Evidence.** A dictionary is an ordinary record and a method is an ordinary
  field of it, so `%d.Show.Int.show(x)` is a projection and a call. There is no
  `Evidence` interpreter here any more, because there is no `Evidence`.
* **`DictAbs`.** A binding that took dictionaries used to be a value that was
  not yet a value, re-elaborated per instantiation -- a `let`'s right-hand side
  was *re-evaluated* every time it was used at a new type. It is a lambda now.
* **The instance memo table.** Dictionaries were memoised on object identity
  so a recursive instance would terminate. In Core the instance's reference to
  itself sits inside its methods' lambdas, so it is not reached until the
  dictionary exists, and there is nothing to memoise for.

Type abstraction and application are erased: `CTyLam` evaluates its body and
`CTyApp` its operand. Types decided what ran; they do not run.

## What is left that is not obvious

Top-level dictionaries are defined in **two passes**: every one is bound to an
empty record first, then the fields are filled in. `instance Monoid Int` holds
its `Semigroup Int` superclass dictionary, and the two are built in whatever
order the class table happens to hold them. Binding the object before filling
it means an order that would otherwise be a lookup failure is simply a record
that is complete a moment later -- the same trick the memo table used, for the
same reason, in the one place it is still needed.
"""

from __future__ import annotations

from . import ast
from .core import (
    CApp, CArray, CAssign, CCon, CDeref, CExpr, CField,
    CIf, CIndex, CJoin, CJump, CLam, CLet, CLetRec, CLit, CMatch,
    CPrim, CProgram, CRecord, CRef, CTuple, CTyApp, CTyLam, CUnit,
    CVar,
)
from .decls import DeclTable
from .errors import TurkeyPanic
from .values import (
    UNIT, ArrayObj, Builtin, Closure, ConstructorFn, ConValue, RecordObj, truth,
)


class JumpSignal(Exception):
    """A jump to a join point, carrying the name it targets and its arguments.

    An exception, because the interpreter's control flow is Python's. It is
    the only one: `return`, `break` and `continue` were three more, each
    caught by whichever frame happened to be running, and all three are now
    this -- caught by *name*, which is enough to be lexical rather than
    dynamic, because the checker will not let a jump appear inside a lambda.
    A lambda body is out of tail position, so it gets an empty join scope, and
    the innermost `CJoin` with this name on the Python stack is therefore
    always the one that lexically binds it.
    """

    __slots__ = ("name", "args")

    def __init__(self, name, args):
        self.name = name
        self.args = args


class Cell:
    """A mutable binding: what a `var` is (design.md decision 36).

    One object, captured by value, so a closure that writes through it writes
    to the same cell everyone else reads. That was previously a property of the
    evaluator's scope chain and is now a property of the term.
    """

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __repr__(self) -> str:
        return f"<cell {self.value!r}>"


class REnv:
    """A runtime scope chain. Lookup walks outward."""

    __slots__ = ("parent", "names")

    def __init__(self, parent: REnv | None = None, names: dict | None = None):
        self.parent = parent
        self.names: dict[str, object] = names if names is not None else {}

    def child(self) -> REnv:
        return REnv(self)

    def define(self, name: str, value) -> None:
        self.names[name] = value

    def lookup(self, name: str):
        env: REnv | None = self
        while env is not None:
            if name in env.names:
                return env.names[name]
            env = env.parent
        raise TurkeyPanic(f"'{name}' is not defined at run time")


class Evaluator:
    def __init__(self, decls: DeclTable, globals_: dict):
        self.decls = decls
        self.globals = REnv(None, dict(globals_))
        for name, info in decls.constructors.items():
            mutable = decls.tycons[info.tycon].is_mutable_record
            if info.arity == 0:
                self.globals.define(name, ConValue(name, ()))
            else:
                self.globals.define(
                    name, ConstructorFn(name, info.arity, info.field_names, mutable)
                )

    # -- program ------------------------------------------------------------

    def run(self, program: CProgram, main: str = "main") -> None:
        """Build the dictionaries, evaluate the bindings, then call `main`."""
        records: list[tuple[RecordObj, CRecord]] = []
        for bind in program.dicts:
            if isinstance(bind.value, CRecord):
                # Bound before it is filled. See the note in the module
                # docstring: dictionaries hold each other, and the order they
                # are emitted in is the class table's, not a dependency order.
                obj = RecordObj(bind.value.con, {})
                self.globals.define(bind.name, obj)
                records.append((obj, bind.value))
            else:
                self.globals.define(bind.name, self.eval(bind.value, self.globals))
        for obj, node in records:
            for name, value in node.fields:
                obj.fields[name] = self.eval(value, self.globals)

        for bind in program.binds:
            self.globals.define(bind.name, self.eval(bind.value, self.globals))

        entry = self.globals.names.get(main)
        if entry is not None:
            self.call(entry, [])

    # -- expressions --------------------------------------------------------

    def eval(self, e: CExpr, env: REnv):
        method = getattr(self, "_eval_" + type(e).__name__, None)
        if method is None:
            raise AssertionError(f"cannot evaluate {type(e).__name__}")
        return method(e, env)

    def _eval_CLit(self, e: CLit, env: REnv):
        return e.value

    def _eval_CUnit(self, e: CUnit, env: REnv):
        return UNIT

    def _eval_CVar(self, e: CVar, env: REnv):
        return env.lookup(e.name)

    def _eval_CCon(self, e: CCon, env: REnv):
        return env.lookup(e.name)

    def _eval_CPrim(self, e: CPrim, env: REnv):
        return env.lookup(e.name)

    def _eval_CTuple(self, e: CTuple, env: REnv):
        return tuple(self.eval(x, env) for x in e.elems)

    def _eval_CArray(self, e: CArray, env: REnv):
        arr = ArrayObj(len(e.elems))
        for item in e.elems:
            arr.push(self.eval(item, env))
        return arr

    def _eval_CRecord(self, e: CRecord, env: REnv):
        """A record, or a dictionary -- which is a record with no declaration.

        A declared record's fields are stored in *declaration* order, not in
        the order the author wrote the labels: `RecordObj.positional()` reads
        them back that way, and a positional pattern matches them that way.
        Evaluation is still left to right in the order written, because that is
        what section 6.1 says and the two orders are independent.
        """
        values = {label: self.eval(value, env) for label, value in e.fields}
        info = self.decls.constructors.get(e.con)
        if info is None:
            # `%Dict.C`: no declaration, so nothing to reorder against.
            return RecordObj(e.con, values)
        ordered = tuple(values[name] for name in info.field_names or [])
        if self.decls.tycons[info.tycon].is_mutable_record:
            return RecordObj(e.con, dict(zip(info.field_names or [], ordered)))
        return ConValue(e.con, ordered, info.field_names)

    def _eval_CField(self, e: CField, env: REnv):
        obj = self.eval(e.target, env)
        if isinstance(obj, ArrayObj):
            return obj.length if e.name == "length" else obj.capacity
        return obj.fields[e.name]

    def _eval_CIndex(self, e: CIndex, env: REnv):
        return self.eval(e.target, env).get(self.eval(e.index, env))

    def _eval_CLam(self, e: CLam, env: REnv):
        return Closure([p.name for p in e.params], e.body, env, e.name)

    def _eval_CApp(self, e: CApp, env: REnv):
        fn = self.eval(e.fn, env)
        return self.call(fn, [self.eval(a, env) for a in e.args])

    def call(self, fn, args):
        """Apply a value. Parameters are names now, so there is nothing to match.

        Under the surface tree a parameter was a *pattern*, and calling meant
        matching -- which could fail at run time, in a call that typechecked.
        The lowering turns a destructured parameter into a plain binder and a
        `match`, so what is left here is a positional bind.
        """
        if isinstance(fn, Closure):
            scope = fn.env.child()
            for name, value in zip(fn.params, args):
                scope.define(name, value)
            return self.eval(fn.body, scope)
        if isinstance(fn, Builtin):
            return fn.fn(*args)
        if isinstance(fn, ConstructorFn):
            return fn.build(tuple(args))
        raise TurkeyPanic(f"{fn!r} is not callable")

    def _eval_CTyLam(self, e: CTyLam, env: REnv):
        # Types are erased: what a term is polymorphic *in* decided what runs,
        # and does not itself run.
        return self.eval(e.body, env)

    def _eval_CTyApp(self, e: CTyApp, env: REnv):
        return self.eval(e.fn, env)

    def _eval_CLet(self, e: CLet, env: REnv):
        scope = env.child()
        scope.define(e.name, self.eval(e.value, env))
        return self.eval(e.body, scope)

    def _eval_CLetRec(self, e: CLetRec, env: REnv):
        # One scope, filled before any body runs, so a member may call itself
        # and its siblings. The values are lambdas, so evaluating them in the
        # scope they are about to populate is not circular.
        scope = env.child()
        for bind in e.binds:
            scope.define(bind.name, self.eval(bind.value, scope))
        return self.eval(e.body, scope)

    def _eval_CRef(self, e: CRef, env: REnv):
        return Cell(self.eval(e.value, env))

    def _eval_CDeref(self, e: CDeref, env: REnv):
        return self.eval(e.target, env).value

    def _eval_CAssign(self, e: CAssign, env: REnv):
        value = self.eval(e.value, env)
        target = e.target
        if isinstance(target, CIndex):
            self.eval(target.target, env).set(self.eval(target.index, env), value)
            return UNIT
        if isinstance(target, CField):
            obj = self.eval(target.target, env)
            if isinstance(obj, ArrayObj):
                if target.name == "length":
                    obj.length = value
                else:
                    obj.capacity = value
                return UNIT
            obj.fields[target.name] = value
            return UNIT
        # A cell, reached by name. `CDeref` never appears as a target: writing
        # a cell is not writing what it holds.
        self.eval(target, env).value = value
        return UNIT

    def _eval_CIf(self, e: CIf, env: REnv):
        if truth(self.eval(e.cond, env)):
            return self.eval(e.then, env)
        return UNIT if e.otherwise is None else self.eval(e.otherwise, env)

    def _eval_CMatch(self, e: CMatch, env: REnv):
        value = self.eval(e.scrutinee, env)
        for alt in e.alts:
            bindings = match_pattern(alt.pat, value)
            if bindings is None:
                continue
            scope = env.child()
            for name, bound in bindings.items():
                scope.define(name, bound)
            return self.eval(alt.body, scope)
        raise TurkeyPanic(f"no match arm applies to {value!r}")

    def _eval_CJoin(self, e: CJoin, env: REnv):
        """Run the rest; when it jumps here, run the body instead, and repeat.

        A loop rather than a call, which is the whole point of a join point:
        the jump consumes the frame it left rather than stacking one. That a
        non-recursive join goes round at most once is a fact about the term,
        not a case to write here.
        """
        target = e.rest
        scope = env
        while True:
            try:
                return self.eval(target, scope)
            except JumpSignal as jump:
                if jump.name != e.name:
                    raise
                scope = env.child()
                for param, value in zip(e.params, jump.args):
                    scope.define(param.name, value)
                target = e.body

    def _eval_CJump(self, e: CJump, env: REnv):
        raise JumpSignal(e.name, [self.eval(a, env) for a in e.args])


def match_pattern(pat: ast.Pattern, value) -> dict[str, object] | None:
    """Match a value, returning the bindings, or None if the pattern does not fit.

    Patterns are `ast.Pattern` still: Core does not restate them, because they
    are already a small total language with nothing implicit in them, and
    `exhaustive.py` has decided about them before this point. So this function
    is unchanged by M13c -- its input never was Core.
    """
    if isinstance(pat, ast.PWild):
        return {}
    if isinstance(pat, ast.PVar):
        return {pat.name: value}
    if isinstance(pat, ast.PAnnot):
        return match_pattern(pat.pat, value)
    if isinstance(pat, ast.PLit):
        return {} if value == pat.value and type(value) is type(pat.value) else None
    if isinstance(pat, ast.PTuple):
        out: dict[str, object] = {}
        for sub, item in zip(pat.elems, value):
            inner = match_pattern(sub, item)
            if inner is None:
                return None
            out.update(inner)
        return out
    if isinstance(pat, ast.PCon):
        # Either runtime shape: a record variant is a positional one plus
        # names, and a single-variant record is a `RecordObj`.
        if isinstance(value, ConValue):
            args = value.args
        elif isinstance(value, RecordObj):
            args = value.positional()
        else:
            return None
        if value.con != pat.name:
            return None
        out = {}
        for sub, item in zip(pat.args, args):
            inner = match_pattern(sub, item)
            if inner is None:
                return None
            out.update(inner)
        return out
    if isinstance(pat, ast.PRecord):
        if isinstance(value, RecordObj):
            if value.con != pat.name:
                return None
            get = value.fields.__getitem__
        elif isinstance(value, ConValue) and value.field_names:
            if value.con != pat.name:
                return None
            names = value.field_names
            args = value.args
            get = lambda label: args[names.index(label)]  # noqa: E731
        else:
            return None
        out = {}
        for label, sub in pat.fields:
            inner = match_pattern(sub, get(label))
            if inner is None:
                return None
            out.update(inner)
        return out
    raise AssertionError(f"unhandled pattern {type(pat).__name__}")


__all__ = ["Cell", "Evaluator", "JumpSignal", "REnv", "match_pattern"]
