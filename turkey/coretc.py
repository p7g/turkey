"""The Core typechecker: what makes evidence checked rather than trusted.

`plan.txt` item 5 gives the reason for the whole milestone in one clause --
giving the elaborator a datatype "makes evidence checkable rather than
trusted". This is the module that does the checking. Without it `turkey/core.py`
and `turkey/lower.py` would be a rearrangement: the same facts in a nicer shape,
believed for the same reason as before, which is that nobody looked.

## It is a checker, not an inferencer

Every binder in Core is annotated and every node carries its type, so there is
nothing to guess: no constraint, no solver, no ranks, no generalization. Each
rule computes what a node's type *must* be from its parts and compares that
against what the node claims. `types.type_key` is the comparison -- structural
identity up to the current substitution -- and one-way `classes.match` is used
wherever a scheme has to be instantiated, which is the same matching instance
selection itself uses.

Two deliberate slacknesses, both of them things the surface language decided
and not concessions:

* **Bottom absorbs.** `⊥` unifies with anything (design decision 12), so a
  branch that `return`s is compatible with a branch that does not. Insisting
  otherwise would reject `if c { return None }; Some(x)`.
* **A free variable matches anything once.** Inside a type abstraction the
  binders are rigid and compared by identity, but a lowering may leave a
  variable where a program is genuinely polymorphic. Comparison binds such a
  variable rather than failing, which keeps the checker from rejecting a term
  for being *more* general than expected.

## What it actually catches

The point is the dictionaries, and there the checks are exact:

* a dictionary argument must have the class and the head type its parameter
  declares, so passing `Monoid Int` where `Semigroup Int` belongs is rejected;
* a method projection must come from a dictionary of the class that declares
  the method, and its type must be an instance of the method's own scheme with
  the class variable bound to the dictionary's head;
* a superclass projection must name a real superclass of the dictionary's
  class;
* a type application must have one argument per binder, and the result must be
  the operand's type under that substitution.

Ordinary term structure is checked alongside -- an application's arguments
against its function's parameters, a `let`'s value against its annotation, a
constructor against its declaration -- because a checker that only checked
dictionaries would be one a mis-lowering could walk straight past.

## What it derives rather than reads

A `CAlt` records no binding types, on purpose. The checker works out what a
pattern binds from the scrutinee's type and the constructor's declaration, so
a pattern that does not fit its scrutinee is a rejected term. That is a check
the elaborator never had, because in the surface language the same fact was
established by unification during inference and then forgotten.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import ast
from .classes import ClassTable, match
from .core import (
    CAlt, CApp, CArray, CAssign, CBind, CBreak, CCon, CContinue, CDeref, CExpr,
    CField, CForC, CForIn, CIf, CIndex, CLam, CLet, CLetRec, CLit, CLoop,
    CMatch, CPrim, CProgram, CRecord, CRef, CReturn, CTuple, CTyApp, CTyLam,
    CUnit, CVar, CWhile, is_ref, ref_elem, ref_of,
)
from .decls import DeclTable, substitute
from .errors import Span, TurkeyError
from .typed import reduce_deep
from .types import (
    BOOL, STAR, TApp, TBottom, TCon, TFam, TFun, TTuple, TVar, Type,
    array_of, prune, show, spine, type_key,
)


class CoreError(TurkeyError):
    """A Core term that does not typecheck.

    This is never the author's fault. Their program was accepted by inference;
    if the term it lowered to does not check, the lowering or the elaboration
    is wrong. So it renders as an internal error and says which node.
    """

    stage = "internal error"


@dataclass
class Env:
    """Names in scope, each with what it abstracts over.

    One shape for all three binders -- top-level, `let`, `letrec` -- because a
    type application has to find its binders the same way whichever bound the
    name, and because the only alternative is a second agreement to keep.
    """

    names: dict[str, tuple[list[TVar], Type]] = field(default_factory=dict)
    parent: "Env | None" = None

    def child(self) -> "Env":
        return Env({}, self)

    def define(self, name: str, binders: list[TVar], ty: Type) -> None:
        self.names[name] = (binders, ty)

    def lookup(self, name: str):
        env: Env | None = self
        while env is not None:
            found = env.names.get(name)
            if found is not None:
                return found
            env = env.parent
        return None


class Checker:
    def __init__(self, decls: DeclTable, classes: ClassTable) -> None:
        self.decls = decls
        self.classes = classes
        # The equalities the binding being checked states. Consulted before the
        # instance table, exactly as `Solver.reduce` consults its assumptions
        # first and for the same reason: `Item s` over a rigid `s` will never
        # reduce through an instance, and the signature that promised it is the
        # only thing that can say what it is.
        self.equations: list[tuple[Type, Type]] = []

    # -- the program -------------------------------------------------------

    def program(self, prog: CProgram, globals_: Env | None = None) -> None:
        env = Env() if globals_ is None else globals_
        # Every top-level name is in scope for every other: the dictionaries
        # refer to each other (`instance Show a => Show (Array a)` names
        # itself), and the bindings were ordered by dependency but may still
        # be mutually recursive within an SCC.
        for bind in prog.dicts + prog.binds:
            env.define(bind.name, bind.binders, bind.ty)
        for bind in prog.dicts + prog.binds:
            self.bind(bind, env)

    def bind(self, bind: CBind, env: Env) -> None:
        want = ref_elem(bind.ty) if bind.mutable and is_ref(bind.ty) else bind.ty
        saved = self.equations
        self.equations = saved + bind.equations
        try:
            got = self.check(bind.value, env)
        finally:
            self.equations = saved
        if bind.mutable and is_ref(bind.ty):
            got = ref_elem(got) if is_ref(got) else got
        self.expect(want, got, bind.span, f"the binding '{bind.name}'")

    # -- expressions -------------------------------------------------------

    def check(self, e: CExpr | None, env: Env) -> Type:
        """The type this node must have, compared against the one it claims."""
        assert e is not None, "a missing subterm reached the checker"
        method = getattr(self, "_check_" + type(e).__name__, None)
        if method is None:
            raise CoreError(f"no rule for Core node {type(e).__name__}", e.span)
        got = method(e, env)
        self.expect(e.ty, got, e.span, _describe(e))
        # The *derived* type, not the claimed one. They agree or `expect` has
        # already objected -- but "agree" is deliberately slack about
        # variables, so a node claiming a bare variable would otherwise hand
        # its parent a variable to reason from, and a parent that cannot tell
        # what it is looking at checks nothing. Deriving is the whole method.
        #
        # Reduced, too, and for the same reason: a parameter declared
        # `Cursor (Array a)` is an `ArrayCursor`, and a parent asking it for a
        # field has to be looking at the type rather than at the family
        # application that names it.
        return prune(self.reduce(got))

    # trivial forms: they are their own evidence

    def _check_CLit(self, e: CLit, env: Env) -> Type:
        return e.ty

    def _check_CUnit(self, e: CUnit, env: Env) -> Type:
        return e.ty

    def _check_CPrim(self, e: CPrim, env: Env) -> Type:
        return e.ty

    def _check_CCon(self, e: CCon, env: Env) -> Type:
        info = self.decls.constructors.get(e.name)
        if info is None:
            raise CoreError(f"unknown constructor '{e.name}'", e.span)
        # A nullary constructor stands alone rather than being applied
        # (`ast.ECon`), so what it is *worth* is the result rather than the
        # function -- `None` is an `Option a`, not a `fun() -> Option a`.
        declared = info.scheme.body
        if info.arity == 0 and isinstance(declared, TFun) and not declared.params:
            declared = declared.ret
        if not self.instance_of(info.scheme, e.ty) and not compatible(
                declared, self.reduce(e.ty)):
            raise CoreError(
                f"'{e.name}' is not usable at {show(e.ty)}; it is declared "
                f"{show(declared)}", e.span)
        return e.ty

    def _check_CVar(self, e: CVar, env: Env) -> Type:
        found = env.lookup(e.name)
        if found is None:
            # Not every name is in the Core environment: builtins arrive
            # through the type environment the driver seeds this with, and a
            # dictionary parameter is bound by the lambda that takes it. A
            # name that reaches here unknown is a real hole, but the seeding
            # is the driver's business, so say which name.
            raise CoreError(f"'{e.name}' is not in scope", e.span)
        binders, ty = found
        if binders:
            # A polymorphic name used with no type application: legitimate
            # only under a `CTyApp`, which does not check its operand this
            # way. Reaching here means the application went missing.
            raise CoreError(
                f"'{e.name}' is polymorphic in "
                f"{' '.join(show(b) for b in binders)} and is used without a "
                f"type application", e.span)
        return ty

    def _check_CTuple(self, e: CTuple, env: Env) -> Type:
        return TTuple([self.check(x, env) for x in e.elems])

    def _check_CArray(self, e: CArray, env: Env) -> Type:
        elems = [self.check(x, env) for x in e.elems]
        if not elems:
            return e.ty
        return array_of(elems[0])

    # the three that carry the milestone

    def _check_CTyApp(self, e: CTyApp, env: Env) -> Type:
        """A type application: one argument per binder, then substitute.

        The operand is not checked by `check`. Nothing else in this IR is
        polymorphic, so the operand is a name or a method projection, and its
        polymorphic type comes from where it was bound rather than from its
        own `ty` -- which is why `CVar` refuses to be used polymorphically
        anywhere else.
        """
        binders, poly = self.polymorphic(e.fn, env)
        if len(binders) != len(e.args):
            raise CoreError(
                f"type application has {len(e.args)} arguments for "
                f"{len(binders)} binders", e.span)
        mapping = {b.id: a for b, a in zip(binders, e.args)}
        return substitute(poly, mapping)

    def polymorphic(self, fn: CExpr | None, env: Env) -> tuple[list[TVar], Type]:
        """The binders and body of whatever is being type-applied."""
        if isinstance(fn, CVar):
            found = env.lookup(fn.name)
            if found is None:
                raise CoreError(f"'{fn.name}' is not in scope", fn.span)
            return found
        if isinstance(fn, CField):
            return self.method_scheme(fn, env)
        raise CoreError(
            f"a {type(fn).__name__} cannot be type-applied", None if fn is None else fn.span)

    def method_scheme(self, e: CField, env: Env) -> tuple[list[TVar], Type]:
        """A method projected from a dictionary, as a polymorphic thing.

        This is where a wrong dictionary stops being possible: the class comes
        from the *dictionary's own type*, so a `Monoid` dictionary cannot
        answer a `Semigroup` projection, and the method's remaining binders are
        its own scheme's less the class variable the dictionary just fixed.
        """
        dict_ty = self.check(e.target, env)
        cls, head = self.dict_parts(dict_ty, e.span)
        info = self.classes.classes[cls]
        method = info.methods.get(e.name)
        if method is None:
            raise CoreError(
                f"class '{cls}' has no method '{e.name}'", e.span)
        binders = [q for q in method.scheme.quantified if q is not method.class_var]
        return binders, self.method_type(method, head)

    def method_type(self, method, head: Type) -> Type:
        """A method's type as it sits in a dictionary, at one head.

        Derived here rather than read off the term, and derived the same way
        `lower.method_type` derives it -- which is the point of a checker: two
        independent computations of one type, compared. A method's own context
        (`foldMap[Monoid m]`) is per call, so those dictionaries are the
        field's parameters and not the dictionary's.
        """
        mapping = {method.class_var.id: head}
        ty = substitute(method.scheme.body, mapping)
        own = method.scheme.preds[1:]
        if not own:
            return ty
        return TFun([self.dict_type(p.name, substitute(p.args[0], mapping))
                     for p in own], ty)

    def dict_parts(self, ty: Type, span: Span | None) -> tuple[str, Type]:
        """Read `%Dict.C t` back as the class `C` and the type `t`."""
        ty = prune(ty)
        head, args = spine(ty)
        if (not isinstance(head, TCon) or not head.name.startswith("%Dict.")
                or len(args) != 1):
            raise CoreError(f"{show(ty)} is not a dictionary", span)
        cls = head.name[len("%Dict."):]
        if cls not in self.classes.classes:
            raise CoreError(f"no such class '{cls}'", span)
        return cls, args[0]

    def _check_CField(self, e: CField, env: Env) -> Type:
        """Three things wear this shape: a superclass, a method, a record field.

        Which one it is is decided by the *target's type*, not by the name --
        that is what makes the check a check. A superclass selection off a
        record, or a field access on a dictionary, is rejected here.
        """
        target = self.check(e.target, env)
        if self.is_dict(target):
            cls, head = self.dict_parts(target, e.span)
            if e.name.startswith("%super."):
                want = e.name[len("%super."):]
                if not self.has_super(cls, want):
                    raise CoreError(
                        f"'{want}' is not a superclass of '{cls}'", e.span)
                return self.dict_type(want, head)
            binders, body = self.method_scheme(e, env)
            if binders:
                raise CoreError(
                    f"method '{e.name}' is polymorphic in "
                    f"{' '.join(show(b) for b in binders)} and is used "
                    f"without a type application", e.span)
            return body
        return self.record_field(target, e.name, e.span)

    def record_field(self, target: Type, name: str, span: Span | None) -> Type:
        """A field of a single-variant record, or an array's own fields."""
        head, args = spine(prune(target))
        if isinstance(head, TCon) and head.name == "Array":
            if name in ("length", "capacity"):
                return TCon("Int")
        if isinstance(head, TVar) or isinstance(head, TBottom):
            # The lowering kept the type inference gave it; if that is still a
            # variable the field was resolved by a `HasField` the solver
            # discharged and erased, and there is nothing left here to check.
            return _fresh()
        if not isinstance(head, TCon):
            raise CoreError(f"{show(target)} has no fields", span)
        info = self.decls.tycons.get(head.name)
        if info is None:
            raise CoreError(f"unknown type '{head.name}'", span)
        for con in self.decls.constructors.values():
            if con.tycon != head.name or not con.is_record:
                continue
            assert con.field_names is not None
            if name not in con.field_names:
                continue
            scheme = con.scheme
            assert isinstance(scheme.body, TFun)
            mapping = _head_mapping(scheme, target)
            return substitute(scheme.body.params[con.field_names.index(name)],
                              mapping)
        raise CoreError(f"'{head.name}' has no field '{name}'", span)

    def is_dict(self, ty: Type) -> bool:
        head, args = spine(prune(ty))
        return (isinstance(head, TCon) and head.name.startswith("%Dict.")
                and len(args) == 1)

    def dict_type(self, cls: str, arg: Type) -> Type:
        from .lower import dict_con
        return TApp(dict_con(cls, self.classes.classes[cls].kind), arg, STAR)

    def has_super(self, cls: str, want: str) -> bool:
        return any(p.name == want for p in self.classes.classes[cls].supers)

    def _check_CRecord(self, e: CRecord, env: Env) -> Type:
        """A record, which is also how a dictionary is built."""
        if e.con.startswith("%Dict."):
            cls, head = self.dict_parts(e.ty, e.span)
            self.dictionary(e, cls, head, env)
            return e.ty
        info = self.decls.constructors.get(e.con)
        if info is None:
            raise CoreError(f"unknown constructor '{e.con}'", e.span)
        mapping = _head_mapping(info.scheme, e.ty)
        assert isinstance(info.scheme.body, TFun)
        names = info.field_names or []
        for name, value in e.fields:
            if name not in names:
                raise CoreError(f"'{e.con}' has no field '{name}'", e.span)
            want = substitute(info.scheme.body.params[names.index(name)], mapping)
            self.expect(want, self.check(value, env), e.span,
                        f"field '{name}' of '{e.con}'")
        return e.ty

    def dictionary(self, e: CRecord, cls: str, head: Type, env: Env) -> None:
        """Every field of a dictionary, against what the class declares.

        A missing method is as much an error as a wrong one: a dictionary with
        a hole in it is exactly what `AttributeError` from inside the
        evaluator used to mean.
        """
        info = self.classes.classes[cls]
        seen = set()
        for name, value in e.fields:
            seen.add(name)
            if name.startswith("%super."):
                want = self.dict_type(name[len("%super."):], head)
                self.expect(want, self.check(value, env), e.span,
                            f"the '{name}' of a '{cls}' dictionary")
                continue
            method = info.methods.get(name)
            if method is None:
                raise CoreError(f"class '{cls}' has no method '{name}'", e.span)
            got = self.check(value, env)
            self.expect(self.method_type(method, head), got, e.span,
                        f"method '{name}' of '{cls}'")
        for name in info.methods:
            if name not in seen:
                raise CoreError(
                    f"the '{cls}' dictionary for {show(head)} has no "
                    f"'{name}'", e.span)
        for sup in info.supers:
            if f"%super.{sup.name}" not in seen:
                raise CoreError(
                    f"the '{cls}' dictionary for {show(head)} has no "
                    f"'{sup.name}' superclass", e.span)

    # ordinary structure

    def _check_CLam(self, e: CLam, env: Env) -> Type:
        inner = env.child()
        for p in e.params:
            inner.define(p.name, [], p.ty)
        return TFun([p.ty for p in e.params], self.check(e.body, inner))

    def _check_CApp(self, e: CApp, env: Env) -> Type:
        fn = self.check(e.fn, env)
        args = [self.check(a, env) for a in e.args]
        fn = prune(fn)
        if isinstance(fn, TBottom):
            return e.ty
        if not isinstance(fn, TFun):
            raise CoreError(f"{show(fn)} is not a function", e.span)
        if len(fn.params) != len(args):
            raise CoreError(
                f"a function of {len(fn.params)} parameters applied to "
                f"{len(args)} arguments", e.span)
        for i, (want, got) in enumerate(zip(fn.params, args)):
            self.expect(want, got, e.span, f"argument {i + 1}")
        return fn.ret

    def _check_CTyLam(self, e: CTyLam, env: Env) -> Type:
        return self.check(e.body, env)

    def _check_CLet(self, e: CLet, env: Env) -> Type:
        assert e.bound is not None
        value = self.check(e.value, env)
        self.expect(e.bound, value, e.span, f"the binding '{e.name}'")
        inner = env.child()
        inner.define(e.name, e.binders, e.bound)
        return self.check(e.body, inner)

    def _check_CLetRec(self, e: CLetRec, env: Env) -> Type:
        inner = env.child()
        for bind in e.binds:
            inner.define(bind.name, bind.binders, bind.ty)
        for bind in e.binds:
            self.bind(bind, inner)
        return self.check(e.body, inner)

    def _check_CRef(self, e: CRef, env: Env) -> Type:
        return ref_of(self.check(e.value, env))

    def _check_CDeref(self, e: CDeref, env: Env) -> Type:
        target = self.check(e.target, env)
        if not is_ref(target):
            raise CoreError(f"{show(target)} is not a reference cell", e.span)
        return ref_elem(target)

    def _check_CAssign(self, e: CAssign, env: Env) -> Type:
        value = self.check(e.value, env)
        target = self.check(e.target, env)
        want = ref_elem(target) if is_ref(target) else target
        self.expect(want, value, e.span, "the assigned value")
        return TCon("Unit")

    def _check_CIndex(self, e: CIndex, env: Env) -> Type:
        target = prune(self.check(e.target, env))
        self.expect(TCon("Int"), self.check(e.index, env), e.span, "an index")
        head, args = spine(target)
        if isinstance(head, TCon) and head.name == "Array" and args:
            return args[0]
        if isinstance(head, (TVar, TBottom)):
            return e.ty
        raise CoreError(f"{show(target)} is not indexable", e.span)

    def _check_CIf(self, e: CIf, env: Env) -> Type:
        self.expect(BOOL, self.check(e.cond, env), e.span, "a condition")
        then = self.check(e.then, env)
        if e.otherwise is None:
            return e.ty
        other = self.check(e.otherwise, env)
        return _join(then, other)

    def _check_CMatch(self, e: CMatch, env: Env) -> Type:
        scrutinee = self.check(e.scrutinee, env)
        result: Type | None = None
        for alt in e.alts:
            inner = env.child()
            for name, ty in self.pattern(alt.pat, scrutinee, e.span).items():
                inner.define(name, [], ty)
            got = self.check(alt.body, inner)
            result = got if result is None else _join(result, got)
        return e.ty if result is None else result

    def _check_CWhile(self, e: CWhile, env: Env) -> Type:
        self.expect(BOOL, self.check(e.cond, env), e.span, "a condition")
        self.check(e.body, env)
        return TCon("Unit")

    def _check_CLoop(self, e: CLoop, env: Env) -> Type:
        self.check(e.body, env)
        return e.ty

    def _check_CForC(self, e: CForC, env: Env) -> Type:
        inner = env.child()
        if e.init is not None:
            self.check_open(e.init, inner)
        self.expect(BOOL, self.check(e.cond, inner), e.span, "a condition")
        if e.step is not None:
            self.check_open(e.step, inner)
        self.check(e.body, inner)
        return TCon("Unit")

    def check_open(self, e: CExpr, env: Env) -> None:
        """A C-style `for`'s init and step, whose bindings scope over the loop.

        A `let` in that position is checked here rather than through `check`,
        because its body is the rest of the *loop*, not the rest of the term.
        """
        if isinstance(e, CLet):
            assert e.bound is not None
            self.expect(e.bound, self.check(e.value, env), e.span,
                        f"the binding '{e.name}'")
            env.define(e.name, e.binders, e.bound)
            return
        self.check(e, env)

    def _check_CForIn(self, e: CForIn, env: Env) -> Type:
        seq = self.check(e.seq, env)
        iter_fn = prune(self.check(e.iter_fn, env))
        next_fn = prune(self.check(e.next_fn, env))
        if not isinstance(iter_fn, TFun) or not isinstance(next_fn, TFun):
            raise CoreError("a loop's iterator is not a function", e.span)
        self.expect(iter_fn.params[0], seq, e.span, "the sequence iterated")
        element = _option_arg(next_fn.ret)
        if element is None:
            raise CoreError(
                f"a loop's `next` answers {show(next_fn.ret)}, not an Option",
                e.span)
        inner = env.child()
        for name, ty in self.pattern(e.pat, element, e.span).items():
            inner.define(name, [], ty)
        self.check(e.body, inner)
        return TCon("Unit")

    def _check_CReturn(self, e: CReturn, env: Env) -> Type:
        if e.value is not None:
            self.check(e.value, env)
        return e.ty

    def _check_CBreak(self, e: CBreak, env: Env) -> Type:
        if e.value is not None:
            self.check(e.value, env)
        return e.ty

    def _check_CContinue(self, e: CContinue, env: Env) -> Type:
        return e.ty

    # -- patterns, derived rather than read --------------------------------

    def pattern(self, pat, scrutinee: Type, span: Span | None) -> dict[str, Type]:
        """What a pattern binds, worked out from what it is matched against.

        Reduced first, and here rather than at the call sites: a scrutinee may
        be `Item a`, and whether its constructors are `Op`'s is exactly what
        the equality in scope decides (delta 39). Matching `Inc(n)` against an
        unreduced family would reject a program the checker had been told about.
        """
        scrutinee = self.reduce(scrutinee)
        pat = _unannot(pat)
        if isinstance(pat, ast.PVar):
            return {pat.name: scrutinee}
        if isinstance(pat, (ast.PWild, ast.PLit)):
            return {}
        if isinstance(pat, ast.PTuple):
            target = prune(scrutinee)
            if not isinstance(target, TTuple):
                if isinstance(target, (TVar, TBottom)):
                    return {n: _fresh() for n in _vars_of(pat)}
                raise CoreError(
                    f"a tuple pattern against {show(target)}", span)
            out: dict[str, Type] = {}
            for sub, ty in zip(pat.elems, target.elems):
                out.update(self.pattern(sub, ty, span))
            return out
        if isinstance(pat, (ast.PCon, ast.PRecord)):
            info = self.decls.constructors.get(pat.name)
            if info is None:
                raise CoreError(f"unknown constructor '{pat.name}'", span)
            target = prune(scrutinee)
            if isinstance(target, (TVar, TBottom)):
                return {n: _fresh() for n in _vars_of(pat)}
            head, args = spine(target)
            if not isinstance(head, TCon) or head.name != info.tycon:
                raise CoreError(
                    f"'{pat.name}' is a constructor of '{info.tycon}', "
                    f"matched against {show(target)}", span)
            mapping = _head_mapping(info.scheme, target)
            assert isinstance(info.scheme.body, TFun)
            fields = [substitute(p, mapping) for p in info.scheme.body.params]
            out = {}
            if isinstance(pat, ast.PCon):
                if len(pat.args) != len(fields):
                    raise CoreError(
                        f"'{pat.name}' takes {len(fields)} arguments, "
                        f"matched with {len(pat.args)}", span)
                for sub, ty in zip(pat.args, fields):
                    out.update(self.pattern(sub, ty, span))
                return out
            names = info.field_names or []
            for name, sub in pat.fields:
                if name not in names:
                    raise CoreError(
                        f"'{pat.name}' has no field '{name}'", span)
                out.update(self.pattern(sub, fields[names.index(name)], span))
            return out
        raise CoreError(f"no rule for pattern {type(pat).__name__}", span)

    # -- comparison --------------------------------------------------------

    def expect(self, want: Type, got: Type, span: Span | None,
               where: str) -> None:
        """Compare, after reducing both sides' families throughout.

        A class declares `iter` as `fun(a) -> Cursor a`, and at `Array a` that
        is `Cursor (Array a)` -- which is `ArrayCursor`, the type the body was
        inferred at. The two are one type and only one of them is reduced, so
        reducing is part of comparing rather than something the lowering was
        supposed to have done first.
        """
        want, got = self.reduce(want), self.reduce(got)
        if not compatible(want, got):
            raise CoreError(
                f"{where} should be {show(want)} but is {show(got)}", span)

    def reduce(self, ty: Type) -> Type:
        return reduce_deep(ty, _Fams(self.classes, self.equations))

    def instance_of(self, scheme, ty: Type) -> bool:
        """Whether `ty` is an instantiation of `scheme`. One-way, as
        `classes.match` is: the scheme's variables may be bound, the type's
        may not."""
        return match(scheme.body, prune(ty)) is not None or compatible(
            scheme.body, ty)


# -- the comparison itself ---------------------------------------------------


def compatible(want: Type, got: Type) -> bool:
    """Whether two types may stand for each other in a Core term.

    Structural, with the two slacknesses the module docstring names: `⊥`
    absorbs, and a variable on either side binds rather than fails. Binding is
    per-comparison and unrecorded, which is deliberate -- this is a check that
    a term is well-typed, not an inference that decides what its variables are.
    """
    want, got = prune(want), prune(got)
    if isinstance(want, TBottom) or isinstance(got, TBottom):
        return True
    if isinstance(want, TVar) or isinstance(got, TVar):
        return True
    if type_key(want) == type_key(got):
        return True
    if isinstance(want, TFun) and isinstance(got, TFun):
        return (len(want.params) == len(got.params)
                and all(compatible(a, b) for a, b in zip(want.params, got.params))
                and compatible(want.ret, got.ret))
    if isinstance(want, TTuple) and isinstance(got, TTuple):
        return (len(want.elems) == len(got.elems)
                and all(compatible(a, b) for a, b in zip(want.elems, got.elems)))
    if isinstance(want, TApp) and isinstance(got, TApp):
        return compatible(want.fn, got.fn) and compatible(want.arg, got.arg)
    if isinstance(want, TFam) and isinstance(got, TFam):
        # Two stuck families. Neither reduced, so what is left to compare is
        # the family and its argument -- and the arguments are variables the
        # binder made rigid, which the rule above already lets match.
        return want.name == got.name and compatible(want.arg, got.arg)
    return False


class _Fams:
    """`types.Families`: the binding's equalities first, then the instances.

    The order is `Solver.reduce`'s, and copied deliberately. A given equality
    is a reduction rule for the family it names, and it has to be consulted
    first because a family over a rigid variable never reduces through the
    instance table at all -- `bf.tl`'s `run[Iterator s, Item s ~ Op]` is the
    case, and its `match op { Inc(n) -> ... }` needs `Item s` to genuinely
    *become* `Op`.
    """

    def __init__(self, classes: ClassTable,
                 equations: list[tuple[Type, Type]] | None = None) -> None:
        self.classes = classes
        self.equations = equations or []

    def reduce(self, t):
        key = type_key(t)
        for left, right in self.equations:
            if type_key(left) == key:
                return right
        return self.classes.reduce_fam(t)

    def defer(self, a, b, span=None, context: str = "") -> None:
        raise AssertionError("a checker does not solve equations")


def _describe(e: CExpr) -> str:
    """A node, said in a way a reader can find in the term.

    "CVar" names the rule that failed; "the variable 'x'" names the thing that
    is wrong, which is what someone chasing a mis-lowering needs.
    """
    if isinstance(e, CVar):
        return f"the variable '{e.name}'"
    if isinstance(e, CField):
        return f"the field '{e.name}'"
    if isinstance(e, CCon):
        return f"the constructor '{e.name}'"
    if isinstance(e, CPrim):
        return f"the builtin '{e.name}'"
    if isinstance(e, CLet):
        return f"the body of 'let {e.name}'"
    if isinstance(e, CApp):
        return "the result of an application"
    if isinstance(e, CTyApp):
        return "the result of a type application"
    return type(e).__name__


def _join(a: Type, b: Type) -> Type:
    """The type of two branches. `⊥` is the one that yields (decision 12)."""
    if isinstance(prune(a), TBottom):
        return b
    return a


def _fresh() -> Type:
    return TVar(0)


def _unannot(pat):
    while isinstance(pat, ast.PAnnot):
        pat = pat.pat
    return pat


def _vars_of(pat) -> set[str]:
    from .deps import pattern_vars
    return set(pattern_vars(pat))


def _head_mapping(scheme, target: Type) -> dict[int, Type]:
    """Bind a constructor's own variables to the type it was used at.

    By matching its declared *result* against the target, not by zipping its
    quantified variables against the target's arguments. The two are not the
    same list: a scheme's variables are in the order the scheme was built in,
    which for `Ret(r) : Flow a b r` puts `r` first. Zipping would have bound
    `a` where `r` belongs -- and did, until the checker said so.
    """
    result = scheme.body.ret if isinstance(scheme.body, TFun) else scheme.body
    mapping = match(result, prune(target))
    if mapping is not None:
        return mapping
    # The target is not an instance of the result -- a variable somewhere, or a
    # bottom. Nothing can be derived, and the caller's own checks report it.
    return {}


def _option_arg(t: Type) -> Type | None:
    head, args = spine(prune(t))
    if isinstance(head, TCon) and head.name.endswith("Option") and args:
        return args[0]
    if isinstance(head, (TVar, TBottom)):
        return _fresh()
    return None


def globals_of(env) -> Env:
    """The Core environment a `constraints.Env` implies.

    Builtins reach Core the way they reach everything else -- through the type
    environment -- so the checker is seeded from it rather than being told
    about `Prim.intAdd` a second time. Outermost scope first, so an inner
    definition shadows as it should.
    """
    out = Env()
    chain = []
    scope = env
    while scope is not None:
        chain.append(scope)
        scope = scope.parent
    for scope in reversed(chain):
        for name, binding in scope.names.items():
            out.define(name, list(binding.scheme.quantified),
                       binding.scheme.body)
    return out


def check_program(prog: CProgram, decls: DeclTable, classes: ClassTable,
                  globals_: Env | None = None) -> None:
    Checker(decls, classes).program(prog, globals_)


__all__ = ["Checker", "CoreError", "Env", "check_program", "compatible",
           "globals_of"]
