"""Tree-walking evaluator (design.md section 6).

Strict, call-by-value, left-to-right. Control transfers -- `return`, `break`,
`continue` -- are Python exceptions, which is the natural fit for constructs
whose type is bottom: they never yield a value to their context.

Reference semantics for records and arrays (section 6.3) need no special
machinery: the objects in `turkey.values` are mutable and are never copied, so
binding a name to one aliases it.
"""

from __future__ import annotations

from . import ast
from .decls import DeclTable
from .deps import pattern_vars
from .errors import TurkeyPanic
from .evidence import Absent, FromDict, FromInstance
from .values import (
    UNIT, ArrayObj, Builtin, Closure, ConstructorFn, ConValue, Dict, DictAbs,
    RecordObj,
)


class ReturnSignal(Exception):
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class BreakSignal(Exception):
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class ContinueSignal(Exception):
    pass


class REnv:
    """A runtime scope chain. Assignment rebinds in whichever scope owns the name."""

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
        raise TurkeyPanic(f"internal error: '{name}' is not bound at run time")

    def assign(self, name: str, value) -> None:
        env: REnv | None = self
        while env is not None:
            if name in env.names:
                env.names[name] = value
                return
            env = env.parent
        raise TurkeyPanic(f"internal error: cannot assign to unbound '{name}'")


def _int_div(a: int, b: int) -> int:
    if b == 0:
        raise TurkeyPanic("division by zero")
    # Truncate toward zero, as machine integer division does; Python's `//`
    # floors instead.
    return -(-a // b) if (a < 0) != (b < 0) else a // b


def _int_mod(a: int, b: int) -> int:
    if b == 0:
        raise TurkeyPanic("remainder by zero")
    return a - b * _int_div(a, b)


def _float_div(a: float, b: float) -> float:
    if b == 0.0:
        raise TurkeyPanic("division by zero")
    return a / b


BINARY = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": _int_div,
    "%": _int_mod,
    "+.": lambda a, b: a + b,
    "-.": lambda a, b: a - b,
    "*.": lambda a, b: a * b,
    "/.": _float_div,
    "++": lambda a, b: a + b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


class Evaluator:
    def __init__(self, decls: DeclTable, globals_: dict):
        self.decls = decls
        self.globals = REnv(None, dict(globals_))
        # One dictionary per (instance, arguments). Shared rather than rebuilt
        # so that `Eq (Array a)` applied to the same `Eq a` is the same object,
        # which is what makes a recursive instance terminate.
        self.dicts: dict[tuple, Dict] = {}
        for name, info in decls.constructors.items():
            mutable = decls.tycons[info.tycon].is_mutable_record
            if info.arity == 0:
                self.globals.define(name, ConValue(name, ()))
            else:
                self.globals.define(
                    name, ConstructorFn(name, info.arity, info.field_names, mutable)
                )

    # -- program ------------------------------------------------------------

    def run(self, ordered: list[ast.Stmt]) -> None:
        """Evaluate top-level items, then call `main` if the program has one.

        The order is the dependency order inference produced, so every binding
        is initialized before anything that reads it.
        """
        for item in ordered:
            self.exec_stmt(item, self.globals)
        main = self.globals.names.get("main")
        if main is not None:
            self.call(main, [], span=None)

    # -- statements ---------------------------------------------------------

    def exec_stmt(self, stmt: ast.Stmt, env: REnv):
        if isinstance(stmt, ast.SExpr):
            return self.eval(stmt.expr, env)

        if isinstance(stmt, (ast.SLet, ast.SVar)):
            dicts = getattr(stmt, "dicts", None)
            if dicts is not None and dicts.params:
                # The value cannot be built until the dictionaries arrive, so
                # each name it binds stands for the binding itself until then.
                for name in pattern_vars(stmt.pat):
                    env.define(name, DictAbs(dicts.params, stmt, env))
                return UNIT
            value = self.eval(stmt.value, env)
            bindings = match_pattern(stmt.pat, value)
            if bindings is None:
                raise TurkeyPanic(
                    f"the pattern in this binding does not match the value {value!r}"
                )
            for name, bound in bindings.items():
                env.define(name, bound)
            return UNIT

        if isinstance(stmt, ast.SFun):
            decl = stmt.decl
            if decl.dicts is not None and decl.dicts.params:
                env.define(decl.name, DictAbs(decl.dicts.params, decl, env))
            else:
                env.define(decl.name, Closure(decl.params, decl.body, env, decl.name))
            return UNIT

        if isinstance(stmt, ast.SAssign):
            self.exec_assign(stmt, env)
            return UNIT

        raise AssertionError(f"unhandled statement {type(stmt).__name__}")

    def exec_assign(self, stmt: ast.SAssign, env: REnv) -> None:
        target = stmt.target
        value = self.eval(stmt.value, env)

        if isinstance(target, ast.EVar):
            env.assign(target.name, value)
            return
        if isinstance(target, ast.EField):
            obj = self.eval(target.obj, env)
            if isinstance(obj, ArrayObj):
                if target.name == "length":
                    obj.set_length(value)
                else:
                    obj.set_capacity(value)
                return
            obj.fields[target.name] = value
            return
        if isinstance(target, ast.EIndex):
            self.eval(target.arr, env).set(self.eval(target.index, env), value)
            return
        raise AssertionError("unhandled assignment target")

    # -- expressions --------------------------------------------------------

    def eval(self, e: ast.Expr, env: REnv):
        return getattr(self, "_eval_" + type(e).__name__)(e, env)

    def _eval_ELit(self, e, env):
        return e.value

    def _eval_EUnit(self, e, env):
        return UNIT

    def _eval_EVar(self, e, env):
        use = e.use
        if use is None or not use.evidence:
            return env.lookup(e.name)
        dicts = [self.evidence(ev, env) for ev in use.evidence]
        if use.method is not None:
            # A method is *selected* from its class's dictionary; the rest of
            # the evidence is the method's own context, which every call takes.
            value = dicts[0].methods[e.name]
            dicts = dicts[1:]
        else:
            value = env.lookup(e.name)
        return self.supply(value, dicts, e.name)

    # -- dictionaries -------------------------------------------------------

    def supply(self, value, dicts, name: str):
        """Hand `dicts` to a binding that abstracts over them."""
        if not isinstance(value, DictAbs):
            return value
        scope = value.env.child()
        for param, d in zip(value.params, dicts):
            scope.define(param, d)
        node = value.node
        if isinstance(node, ast.FunDecl):
            return Closure(node.params, node.body, scope, node.name)
        # A `let`: re-run the binding with the dictionaries in scope and take
        # the name that was asked for.
        bindings = match_pattern(node.pat, self.eval(node.value, scope))
        if bindings is None:
            raise TurkeyPanic("the pattern in this binding does not match its value")
        return bindings[name]

    def evidence(self, ev, env) -> Dict:
        """Build the dictionary one piece of evidence stands for."""
        if isinstance(ev, FromDict):
            d = env.lookup(ev.name)
            for step in ev.path:
                d = d.supers[step]
            return d
        if isinstance(ev, FromInstance):
            return self.instance_dict(ev.inst, [self.evidence(a, env) for a in ev.args])
        assert isinstance(ev, Absent)
        raise TurkeyPanic(
            f"internal error: a '{ev.pred}' dictionary was needed after all"
        )

    def instance_dict(self, inst, args: list[Dict]) -> Dict:
        key = (id(inst), tuple(id(a) for a in args))
        cached = self.dicts.get(key)
        if cached is not None:
            return cached
        plan = inst.plan
        d = Dict(inst.cls, inst.con)
        # Registered *before* the methods are built. A method body may need
        # this very dictionary -- an instance method that recurses, or a
        # superclass whose own instance leads back here -- and without this the
        # construction would not terminate.
        self.dicts[key] = d
        scope = self.globals.child()
        for name, arg in zip(plan.params, args):
            scope.define(name, arg)
        for name, impl in plan.methods.items():
            inner = scope.child()
            inner.define(impl.self_name, d)
            d.methods[name] = (
                DictAbs(impl.dict_params, impl.decl, inner) if impl.dict_params
                else Closure(impl.decl.params, impl.decl.body, inner, name)
            )
        for sup, sup_ev in plan.supers.items():
            d.supers[sup] = self.evidence(sup_ev, scope)
        return d

    def _eval_ECon(self, e, env):
        return env.lookup(e.name)

    def _eval_ETuple(self, e, env):
        return tuple(self.eval(x, env) for x in e.elems)

    def _eval_EArray(self, e, env):
        # Section 6.6: a literal is `Array.new(n)` followed by one push each.
        arr = ArrayObj(len(e.elems))
        for item in e.elems:
            arr.push(self.eval(item, env))
        return arr

    def _eval_ERecord(self, e, env):
        info = self.decls.con(e.con)
        values = {label: self.eval(value, env) for label, value in e.fields}
        ordered = tuple(values[name] for name in info.field_names)
        if self.decls.tycons[info.tycon].is_mutable_record:
            return RecordObj(e.con, dict(zip(info.field_names, ordered)))
        return ConValue(e.con, ordered, info.field_names)

    def _eval_ELambda(self, e, env):
        return Closure(e.params, e.body, env)

    def _eval_ECall(self, e, env):
        fn = self.eval(e.fn, env)
        args = [self.eval(a, env) for a in e.args]
        return self.call(fn, args, e.span)

    def call(self, fn, args, span):
        if isinstance(fn, Closure):
            scope = fn.env.child()
            for pattern, value in zip(fn.params, args):
                bindings = match_pattern(pattern, value)
                if bindings is None:
                    raise TurkeyPanic(
                        f"argument {value!r} does not match the parameter pattern "
                        f"of {fn.name}"
                    )
                for name, bound in bindings.items():
                    scope.define(name, bound)
            try:
                return self.eval(fn.body, scope)
            except ReturnSignal as ret:
                return ret.value
        if isinstance(fn, Builtin):
            return fn.fn(*args)
        if isinstance(fn, ConstructorFn):
            return fn.build(tuple(args))
        raise TurkeyPanic(f"{fn!r} is not callable")

    def _eval_EIndex(self, e, env):
        return self.eval(e.arr, env).get(self.eval(e.index, env))

    def _eval_EField(self, e, env):
        obj = self.eval(e.obj, env)
        if isinstance(obj, ArrayObj):
            return obj.length if e.name == "length" else obj.capacity
        return obj.fields[e.name]

    def _eval_EUnary(self, e, env):
        value = self.eval(e.operand, env)
        return (not value) if e.op == "!" else -value

    def _eval_EBinary(self, e, env):
        # Section 8.2: these two short-circuit; everything else is strict.
        if e.op == "&&":
            return self.eval(e.left, env) and self.eval(e.right, env)
        if e.op == "||":
            return self.eval(e.left, env) or self.eval(e.right, env)
        return BINARY[e.op](self.eval(e.left, env), self.eval(e.right, env))

    def _eval_EAnnot(self, e, env):
        return self.eval(e.expr, env)

    def _eval_EBlock(self, e, env):
        scope = env.child()
        result = UNIT
        for stmt in e.stmts:
            result = self.exec_stmt(stmt, scope)
        return result

    def _eval_EIf(self, e, env):
        if self.eval(e.cond, env):
            return self.eval(e.then, env)
        if e.otherwise is not None:
            return self.eval(e.otherwise, env)
        return UNIT

    def _eval_EWhile(self, e, env):
        while self.eval(e.cond, env):
            try:
                self.eval(e.body, env)
            except ContinueSignal:
                continue
            except BreakSignal:
                break
        return UNIT

    def _eval_EForIn(self, e, env):
        # Section 6.5, with one correction: `continue` still advances the index.
        # Desugaring literally to a `while` would make `continue` spin forever.
        arr = self.eval(e.iterable, env)
        index = 0
        while index < arr.length:
            scope = env.child()
            bindings = match_pattern(e.pat, arr.get(index))
            if bindings is None:
                raise TurkeyPanic("loop pattern does not match an element")
            for name, bound in bindings.items():
                scope.define(name, bound)
            try:
                self.eval(e.body, scope)
            except ContinueSignal:
                pass
            except BreakSignal:
                break
            index += 1
        return UNIT

    def _eval_EForC(self, e, env):
        scope = env.child()
        if e.init is not None:
            self.exec_stmt(e.init, scope)
        while self.eval(e.cond, scope):
            try:
                self.eval(e.body, scope)
            except ContinueSignal:
                pass
            except BreakSignal:
                break
            if e.step is not None:
                self.exec_stmt(e.step, scope)
        return UNIT

    def _eval_ELoop(self, e, env):
        while True:
            try:
                self.eval(e.body, env)
            except ContinueSignal:
                continue
            except BreakSignal as brk:
                return brk.value

    def _eval_EMatch(self, e, env):
        value = self.eval(e.scrutinee, env)
        for arm in e.arms:
            for pattern in arm.patterns:
                bindings = match_pattern(pattern, value)
                if bindings is not None:
                    scope = env.child()
                    for name, bound in bindings.items():
                        scope.define(name, bound)
                    return self.eval(arm.body, scope)
        raise TurkeyPanic(f"no match arm applies to {value!r}")

    def _eval_EReturn(self, e, env):
        raise ReturnSignal(UNIT if e.value is None else self.eval(e.value, env))

    def _eval_EBreak(self, e, env):
        raise BreakSignal(UNIT if e.value is None else self.eval(e.value, env))

    def _eval_EContinue(self, e, env):
        raise ContinueSignal()


def match_pattern(pat: ast.Pattern, value) -> dict[str, object] | None:
    """Match a value, returning the bindings, or None if the pattern does not fit."""
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
        if not isinstance(value, ConValue) or value.con != pat.name:
            return None
        out = {}
        for sub, item in zip(pat.args, value.args):
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
