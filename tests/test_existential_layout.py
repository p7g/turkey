"""The layout contract for existential constructors (ERRORS.md, "Correctness
milestone: nested layouts"; SPEC-DELTAS 68).

Existential constructors have syntax now, and `tests/programs/existential_*.gob`
is where an ordinary program exercises them. This file is the half a source
program cannot reach: each case is assembled as *Core*, which is what lets it
force the configurations the surface language never produces --

* an ordinary source program supplies the helpers, instances and `print`s,
  and declares a placeholder `type Packed = Packed(Int, Int)`;
* `existential` replaces that constructor with an existential one;
* the pack and open sites are hand-built Core, spliced in before `mono`,
  and the rest of the pipeline is `driver.check`'s from there down.

-- so that a packing can be put inside a body the specialization cap leaves
generic, and a mutant can break one rule at a time and be shown to fail.

Every program runs on the native backend (the one with layouts), the Python
backend and the evaluator, and all three must agree with the expected output.
The native run is repeated under `TURKEY_GC_STRESS` and with the
specialization cap at zero.
"""

from __future__ import annotations

import io
import itertools
from contextlib import redirect_stdout

import pytest

from turkey import ast, coretc, driver, joins, layout, llvmgen, mono, opt, pygen
from turkey.builtins import initial_values
from turkey.core import (CApp, CBind, CCon, CExpr, CIf, CLam, CLet, CLit,
                         CMatch, CAlt, CParam, CProgram, CTyApp, CVar)
from turkey.decls import ConInfo, substitute
from turkey.errors import TurkeyError
from turkey.eval import Evaluator
from turkey.lower import dict_con
from turkey.types import (INT, STAR, STRING, UNIT, Pred, Scheme, TApp, TCon,
                          TFun, TVar, Type)


# ---------------------------------------------------------------- building


class Program:
    """A checked source program, and the means to write Core against it."""

    def __init__(self, source: str) -> None:
        self.checked = driver.check(source)
        self.decls = self.checked.decls
        self.binds = {b.name: b for b in self.checked.core.dicts
                      + self.checked.core.binds}
        self.added: list[CBind] = []
        self.counter = itertools.count()

    # -- types --

    def param(self, name: str, index: int, *targs: Type) -> Type:
        """The type of a helper's `index`th parameter at `targs`."""
        ty = self.type_of(name, *targs)
        while isinstance(ty, TFun) and index >= len(ty.params):
            index -= len(ty.params)
            ty = ty.ret
        assert isinstance(ty, TFun)
        return ty.params[index]

    def type_of(self, name: str, *targs: Type) -> Type:
        bind = self.binds.get(name) or next(b for b in self.added if b.name == name)
        return substitute(bind.ty, {v.id: t for v, t in zip(bind.binders, targs)})

    def array(self, element: Type) -> Type:
        return self.param("Main#arrayOf", 0, element)

    def dictionary(self, cls_helper: str, element: Type) -> Type:
        """The dictionary type a constrained helper's first parameter has."""
        return self.param(cls_helper, 0, element)

    # -- declarations --

    def existential(self, name: str, fields,
                    context: tuple[str, str] | None = None) -> ConInfo:
        """Make `name` an existential constructor over one hidden variable.

        `fields` builds the payload types from the hidden variable. `context`
        is a class and a helper constrained by it, whose first parameter's
        type is the dictionary the value carries.
        """
        placeholder = self.decls.constructors[name]
        hidden = TVar(0, STAR)
        preds = [] if context is None else [Pred(context[0], [hidden])]
        body = TFun(list(fields(hidden)), self.decls.heads[placeholder.tycon])
        info = ConInfo(name, placeholder.tycon, None, len(body.params),
                       Scheme([hidden], body, preds), exists=[hidden])
        self.decls.constructors[name] = info
        variants = self.decls.tycons[placeholder.tycon].variants
        variants[variants.index(placeholder)] = info
        self.decls._newtypes = None
        return info

    # -- terms --

    def ref(self, name: str, *targs: Type) -> CExpr:
        bind = self.binds.get(name) or next(b for b in self.added if b.name == name)
        var = CVar(bind.ty, None, name)
        if not targs:
            return var
        return CTyApp(self.type_of(name, *targs), None, var, list(targs))

    def call(self, fn: CExpr, *args: CExpr) -> CExpr:
        ty = fn.ty
        assert isinstance(ty, TFun), ty
        return CApp(ty.ret, None, fn, list(args))

    def pack(self, name: str, packed: Type, *args: CExpr) -> CExpr:
        info = self.decls.constructors[name]
        ty = substitute(info.scheme.body, {info.exists[0].id: packed})
        assert isinstance(ty, TFun)
        carried = [a.ty for a in args[:len(info.context)]]
        con_ty = TFun(carried + list(ty.params), ty.ret)
        return CApp(ty.ret, None, CCon(con_ty, None, name), list(args))

    def instance(self, cls: str, type_name: str) -> CExpr:
        """The ground dictionary `cls type_name`."""
        name = f"%inst.{cls}.{type_name}"
        return CVar(self.binds[name].ty, None, name)

    def text(self, value: str) -> CExpr:
        return CLit(STRING, None, "String", value)

    def seq(self, *exprs: CExpr) -> CExpr:
        out = exprs[-1]
        for e in reversed(exprs[:-1]):
            out = CLet(out.ty, None, f"%x{next(self.counter)}", e.ty, e, out)
        return out

    def let(self, name: str, value: CExpr, body) -> CExpr:
        rest = body(CVar(value.ty, None, name))
        return CLet(rest.ty, None, name, value.ty, value, rest)

    def open(self, scrutinee: CExpr, name: str, names: list[str], body,
             evidence: list[str] = ()) -> CExpr:
        """`match scrutinee { name[s](evidence)(names) -> body(s, vars) }`."""
        info = self.decls.constructors[name]
        skolem = TCon.skolem("s", STAR)
        ty = substitute(info.scheme.body, {info.exists[0].id: skolem})
        assert isinstance(ty, TFun)
        classes = self.checked.classes.classes
        carried = [TApp(dict_con(p.name, classes[p.name].kind), skolem, STAR)
                   for p in info.context]
        fields = ty.params
        env = {n: CVar(t, None, n) for n, t in zip(names, fields)}
        env.update({n: CVar(t, None, n) for n, t in zip(evidence, carried)})
        result = body(skolem, env)
        pattern = ast.PCon(None, name, [ast.PVar(None, n) for n in names],
                           evidence=list(evidence), skolems=[skolem])
        return CMatch(result.ty, None, scrutinee, [CAlt(pattern, result)])

    def function(self, name: str, params: list[tuple[str, Type]], ret: Type,
                 body, binders: list[TVar] = ()) -> None:
        env = {n: CVar(t, None, n) for n, t in params}
        ty = TFun([t for _, t in params], ret)
        # Registered before the body is built, so the body may call it.
        bind = CBind(name, ty, list(binders), None, module="Main")
        self.added.append(bind)
        bind.value = CLam(ty, None, [CParam(n, t) for n, t in params],
                          body(env), name)

    def main(self, body: CExpr) -> None:
        self.function("Main#main", [], UNIT, lambda _: body)

    # -- the pipeline from Core down, as `driver.check` runs it --

    def compile(self) -> CProgram:
        checked = self.checked
        replaced = {b.name for b in self.added}
        program = CProgram(list(checked.core.dicts),
                           [b for b in checked.core.binds if b.name not in replaced]
                           + self.added)
        env = coretc.globals_of(checked.env)
        coretc.check_program(program, self.decls, checked.classes, env)
        program = mono.monomorphize(program, self.decls, checked.classes,
                                    checked.main)
        coretc.check_program(program, self.decls, checked.classes, env)
        program = opt.reduce_program(joins.discover(opt.reduce_program(program)))
        coretc.check_program(program, self.decls, checked.classes, env)
        program = mono.reduce_types(program, checked.classes)
        program = layout.share(program, self.decls)
        coretc.check_program(program, self.decls, checked.classes, env)
        mono.check_layouts(program)
        return program


# ---------------------------------------------------------------- running


def run_everywhere(build, monkeypatch, capfd) -> str:
    """Build once per configuration and demand one answer from all of them."""
    outputs: dict[str, str] = {}
    for cap, stress in ((None, False), (None, True), (0, False), (0, True)):
        with monkeypatch.context() as patch:
            if cap is not None:
                patch.setattr(mono, "MAX_SPECIALIZATIONS", cap)
            if stress:
                patch.setenv("TURKEY_GC_STRESS", "1")
            prog = build()
            compiled = prog.compile()
            capfd.readouterr()
            llvmgen.execute(compiled, prog.decls, prog.checked.main)
            outputs[f"native cap={cap} stress={stress}"] = capfd.readouterr().out
            if not stress:
                out = io.StringIO()
                with redirect_stdout(out):
                    pygen.execute(compiled, prog.decls, prog.checked.main)
                outputs[f"python cap={cap}"] = out.getvalue()
                out = io.StringIO()
                with redirect_stdout(out):
                    Evaluator(prog.decls, initial_values()).run(
                        compiled, prog.checked.main)
                outputs[f"eval cap={cap}"] = out.getvalue()
    distinct = set(outputs.values())
    assert len(distinct) == 1, outputs
    return distinct.pop()


# ---------------------------------------------------------------- programs


HELPERS = """
type Packed = Packed(Int, Int)

fun arrayOf(xs : Array a) -> Array a = xs

fun firstShown(xs : Array a, f : fun(a) -> String) -> String = f(xs[0])

fun size(xs : Array a) -> Int = len(xs)

fun swap(xs : Array a) -> Unit {
    let t = xs[0]
    xs[0] = xs[1]
    xs[1] = t
}

fun say(s : String) -> Unit {
    print(s)
}

fun sayInt(n : Int) -> Unit {
    print(Int.toString(n))
}

fun intText(n : Int) -> String = Int.toString(n)

fun boolText(b : Bool) -> String = if b { "true" } else { "false" }

fun floatText(x : Float) -> String = Float.toString(x)

fun stringText(s : String) -> String = s

fun byteText(b : Byte) -> String = Int.toString(Byte.toInt(b))

fun charText(c : Char) -> String = Char.toString(c)

fun ints() -> Array Int = [1, 2, 3]

fun bools() -> Array Bool = [True, False]

fun floats() -> Array Float = [1.5, 2.5, 3.5, 4.5]

fun strings() -> Array String = ["x", "y"]

fun bytes() -> Array Byte = [Byte.truncate(7), Byte.truncate(200), Byte.truncate(9)]

fun chars() -> Array Char = ['p', 'q']

fun main() {
}
"""


def packed_arrays(source: str = HELPERS) -> Program:
    """`Packed[a](Array a, fun(a) -> String)`, opened by one function that is
    handed an `Int`, a `Bool`, a `Float` and a `String` payload in turn."""
    p = Program(source)
    p.existential("Main#Packed",
                  lambda a: [p.array(a), TFun([a], STRING)])
    packed_ty = p.decls.heads["Main#Packed"]

    def visit(env):
        return p.open(env["p"], "Main#Packed", ["xs", "f"], lambda s, v: p.seq(
            p.call(p.ref("Main#say"),
                   p.call(p.ref("Main#firstShown", s), v["xs"], v["f"])),
            p.call(p.ref("Main#sayInt"), p.call(p.ref("Main#size", s), v["xs"])),
            p.call(p.ref("Main#swap", s), v["xs"]),
            p.call(p.ref("Main#say"),
                   p.call(p.ref("Main#firstShown", s), v["xs"], v["f"]))))

    p.function("Main#visit", [("p", packed_ty)], UNIT, visit)
    cases = [(INT, "Main#ints", "Main#intText"),
             (p.decls.heads.get("Data.Bool.Type#Bool")
              or p.param("Main#boolText", 0), "Main#bools", "Main#boolText"),
             (p.param("Main#floatText", 0), "Main#floats", "Main#floatText"),
             (STRING, "Main#strings", "Main#stringText"),
             # One byte and four bytes wide: the payloads for which reading at
             # the wrong layout reads the wrong *number* of bytes, rather than
             # the right word under the wrong name.
             (p.param("Main#byteText", 0), "Main#bytes", "Main#byteText"),
             (p.param("Main#charText", 0), "Main#chars", "Main#charText")]
    p.main(p.seq(*[
        p.call(p.ref("Main#visit"),
               p.pack("Main#Packed", ty, p.call(p.ref(make)), p.ref(show)))
        for ty, make, show in cases]))
    return p


MORE_HELPERS = HELPERS.replace("type Packed = Packed(Int, Int)", """
type Packed = Packed(Int, Int)

type Boxed = Boxed(Int, Int)

type Shown = Shown(Int, Int)

type Single = Single(Int, Int)

fun showOne[Show a](x : a) -> String = show(x)

fun pick(x : a, y : a, first : Bool) -> a = if first { x } else { y }

fun firstOf(xs : Array a) -> a = xs[0]

fun done(n : Int) -> Bool = n <= 0

fun less(n : Int) -> Int = n - 1

fun firstSome(xs : Array (Option a), f : fun(a) -> String) -> String = match xs[0] {
    Some(x) -> f(x)
    None -> "none"
}

fun corner(xs : Array (Array a), f : fun(a) -> String) -> String = f(xs[1][0])

fun bump(xs : Array a, g : fun(a) -> a) -> Unit {
    xs[0] = g(xs[0])
}

fun showFirst[Show a](xs : Array a) -> String = show(xs[0])

fun inc(n : Int) -> Int = n + 1

fun half(x : Float) -> Float = x / 2.0

fun nextByte(b : Byte) -> Byte = Byte.truncate(Byte.toInt(b) + 1)

fun shout(s : String) -> String = s + "!"

fun optionalInts() -> Array (Option Int) = [Some(5), None]

fun optionalFloats() -> Array (Option Float) = [Some(0.25)]

fun optionalBytes() -> Array (Option Byte) = [Some(Byte.truncate(42))]

fun gridInts() -> Array (Array Int) = [[1], [8, 9]]

fun gridBytes() -> Array (Array Byte) = [[Byte.truncate(1)], [Byte.truncate(250)]]

fun gridChars() -> Array (Array Char) = [['a'], ['z']]
""")


def payloads(p: Program):
    """One (type, array maker, text function) per interesting layout."""
    return [(INT, "Main#ints", "Main#intText"),
            (p.param("Main#boolText", 0), "Main#bools", "Main#boolText"),
            (p.param("Main#floatText", 0), "Main#floats", "Main#floatText"),
            (STRING, "Main#strings", "Main#stringText"),
            (p.param("Main#byteText", 0), "Main#bytes", "Main#byteText"),
            (p.param("Main#charText", 0), "Main#chars", "Main#charText")]


def test_scalar_payloads_round_trip_through_generic_code(monkeypatch, capfd):
    """`Boxed[a](a, fun(a) -> String)`.

    Each opened value goes through `pick`, a bare-variable generic that no
    layout copy is made of -- so it is called at the boxed convention and the
    arm boxes and unboxes at the skolem's layout -- and is then *repacked* at
    the skolem and opened again, which stores the skolem's layout as the code.
    """
    def program():
        p = Program(MORE_HELPERS)
        p.existential("Main#Boxed", lambda a: [a, TFun([a], STRING)])
        boxed = p.decls.heads["Main#Boxed"]
        true = CCon(p.param("Main#boolText", 0), None, "Data.Bool.Type#True")

        def visit(env):
            def outer(s, v):
                picked = p.call(p.ref("Main#pick", s), v["x"], v["x"], true)
                repacked = p.pack("Main#Boxed", s, picked, v["f"])
                return p.seq(
                    p.call(p.ref("Main#say"), p.call(v["f"], v["x"])),
                    p.open(repacked, "Main#Boxed", ["y", "g"],
                           lambda t, w: p.call(p.ref("Main#say"),
                                               p.call(w["g"], w["y"]))))
            return p.open(env["b"], "Main#Boxed", ["x", "f"], outer)

        p.function("Main#visit", [("b", boxed)], UNIT, visit)
        steps = []
        for ty, make, show in payloads(p):
            # The scalar is element 1 of the helper's array: no byte or char
            # literal syntax is needed in Core.
            element = p.call(p.ref("Main#pick", ty),
                             p.call(p.ref("Main#firstOf", ty), p.call(p.ref(make))),
                             p.call(p.ref("Main#firstOf", ty), p.call(p.ref(make))),
                             true)
            steps.append(p.call(p.ref("Main#visit"),
                                p.pack("Main#Boxed", ty, element, p.ref(show))))
        p.main(p.seq(*steps))
        return p

    out = run_everywhere(program, monkeypatch, capfd)
    assert out == "1\n1\ntrue\ntrue\n1.5\n1.5\nx\nx\n7\n7\np\np\n"


def test_nested_containers_of_the_hidden_type(monkeypatch, capfd):
    """`Array (Option a)` and `Array (Array a)`: the element layout of the
    inner container is the skolem's, two indirections from the packed value."""
    def program():
        p = Program(MORE_HELPERS)
        p.existential("Main#Packed", lambda a: [
            p.param("Main#firstSome", 0, a), TFun([a], STRING)])
        p.existential("Main#Boxed", lambda a: [
            p.param("Main#corner", 0, a), TFun([a], STRING)])
        packed, boxed = p.decls.heads["Main#Packed"], p.decls.heads["Main#Boxed"]
        p.function("Main#optional", [("p", packed)], UNIT, lambda env: p.open(
            env["p"], "Main#Packed", ["xs", "f"], lambda s, v: p.call(
                p.ref("Main#say"),
                p.call(p.ref("Main#firstSome", s), v["xs"], v["f"]))))
        p.function("Main#grid", [("b", boxed)], UNIT, lambda env: p.open(
            env["b"], "Main#Boxed", ["xs", "f"], lambda s, v: p.call(
                p.ref("Main#say"),
                p.call(p.ref("Main#corner", s), v["xs"], v["f"]))))
        by = {name: ty for ty, _, name in payloads(p)}
        p.main(p.seq(
            *[p.call(p.ref("Main#optional"),
                     p.pack("Main#Packed", by[show], p.call(p.ref(make)),
                            p.ref(show)))
              for make, show in (("Main#optionalInts", "Main#intText"),
                                 ("Main#optionalFloats", "Main#floatText"),
                                 ("Main#optionalBytes", "Main#byteText"))],
            *[p.call(p.ref("Main#grid"),
                     p.pack("Main#Boxed", by[show], p.call(p.ref(make)),
                            p.ref(show)))
              for make, show in (("Main#gridInts", "Main#intText"),
                                 ("Main#gridBytes", "Main#byteText"),
                                 ("Main#gridChars", "Main#charText"))]))
        return p

    out = run_everywhere(program, monkeypatch, capfd)
    assert out == "5\n0.25\n42\n8\n250\nz\n"


def test_a_closure_returning_the_hidden_type_writes_back(monkeypatch, capfd):
    """`Packed[a](Array a, fun(a) -> a)`: the closure was compiled at the
    packed type, is called at the skolem's layout, and its result is stored
    into the flat array the arm did not build."""
    def program():
        p = Program(MORE_HELPERS)
        p.existential("Main#Packed", lambda a: [
            p.array(a), TFun([a], a), TFun([a], STRING)])
        packed = p.decls.heads["Main#Packed"]
        p.function("Main#visit", [("p", packed)], UNIT, lambda env: p.open(
            env["p"], "Main#Packed", ["xs", "g", "f"], lambda s, v: p.seq(
                p.call(p.ref("Main#bump", s), v["xs"], v["g"]),
                p.call(p.ref("Main#bump", s), v["xs"], v["g"]),
                p.call(p.ref("Main#say"),
                       p.call(p.ref("Main#firstShown", s), v["xs"], v["f"])))))
        by = {name: ty for ty, _, name in payloads(p)}
        cases = (("Main#ints", "Main#inc", "Main#intText"),
                 ("Main#floats", "Main#half", "Main#floatText"),
                 ("Main#bytes", "Main#nextByte", "Main#byteText"),
                 ("Main#strings", "Main#shout", "Main#stringText"))
        p.main(p.seq(*[
            p.call(p.ref("Main#visit"),
                   p.pack("Main#Packed", by[show], p.call(p.ref(make)),
                          p.ref(step), p.ref(show)))
            for make, step, show in cases]))
        return p

    out = run_everywhere(program, monkeypatch, capfd)
    assert out == "3\n0.375\n9\nx!!\n"


def test_class_evidence_is_called_at_the_skolems_layout(monkeypatch, capfd):
    """`Shown[Show a](Array a)`: the carried dictionary's `show` was compiled
    for the packed instance -- at `i64` for `Int` -- and the arm calls it
    with an element it read at the skolem's layout. The `SomeError` case."""
    def program():
        p = Program(MORE_HELPERS)
        p.existential("Main#Shown", lambda a: [p.array(a)],
                      context=("Std.Classes#Show", "Main#showFirst"))
        shown = p.decls.heads["Main#Shown"]
        p.function("Main#visit", [("v", shown)], UNIT, lambda env: p.open(
            env["v"], "Main#Shown", ["xs"], lambda s, v: p.seq(
                p.call(p.ref("Main#swap", s), v["xs"]),
                p.call(p.ref("Main#say"), p.call(
                    p.call(p.ref("Main#showFirst", s), v["d"]), v["xs"]))),
            evidence=["d"]))
        by = {make: ty for ty, make, _ in payloads(p)}
        cases = (("Main#ints", "Int"), ("Main#floats", "Float"),
                 ("Main#strings", "Data.String.Type#String"), ("Main#bools", "Data.Bool.Type#Bool"))
        p.main(p.seq(*[
            p.call(p.ref("Main#visit"),
                   p.pack("Main#Shown", by[make],
                          p.instance("Std.Classes#Show", instance),
                          p.call(p.ref(make))))
            for make, instance in cases]))
        return p

    out = run_everywhere(program, monkeypatch, capfd)
    assert out == "2\n2.5\ny\nFalse\n"


def test_an_opened_array_is_the_array_that_was_packed(monkeypatch, capfd):
    """No conversion at the boundary: a mutation through one opening is seen
    through another packing of the same array."""
    def program():
        p = packed_arrays(MORE_HELPERS)
        p.added = [b for b in p.added if b.name != "Main#main"]
        p.main(p.let("shared", p.call(p.ref("Main#ints")), lambda shared: p.seq(
            p.call(p.ref("Main#visit"),
                   p.pack("Main#Packed", INT, shared, p.ref("Main#intText"))),
            p.call(p.ref("Main#visit"),
                   p.pack("Main#Packed", INT, shared, p.ref("Main#intText"))))))
        return p

    out = run_everywhere(program, monkeypatch, capfd)
    assert out == "1\n3\n2\n2\n3\n1\n"


def test_packing_inside_a_generic_body_stores_the_layout_it_holds(
        monkeypatch, capfd):
    """A generic `pack[a](xs : Array a, f)` past the cap would store "boxed"
    for an array of `i64`. `layout.share` copies it per layout instead, so the
    code it stores is the layout it was called at."""
    def program():
        p = packed_arrays(MORE_HELPERS)
        p.added = [b for b in p.added if b.name != "Main#main"]
        a = TVar(0, STAR)
        packed = p.decls.heads["Main#Packed"]
        # Recursive, counting down, so that `opt` cannot inline it into `main`
        # and pack at the ground type after all.
        p.function(
            "Main#packIt",
            [("xs", p.array(a)), ("f", TFun([a], STRING)), ("n", INT)], packed,
            lambda env: CIf(
                packed, None, p.call(p.ref("Main#done"), env["n"]),
                p.pack("Main#Packed", a, env["xs"], env["f"]),
                p.call(p.ref("Main#packIt", a), env["xs"], env["f"],
                       p.call(p.ref("Main#less"), env["n"]))),
            binders=[a])
        p.main(p.seq(*[
            p.call(p.ref("Main#visit"),
                   p.call(p.ref("Main#packIt", ty), p.call(p.ref(make)),
                          p.ref(show), CLit(INT, None, "Int", 3)))
            for ty, make, show in payloads(p)]))
        return p

    out = run_everywhere(program, monkeypatch, capfd)
    assert out == ("1\n3\n2\ntrue\n2\nfalse\n1.5\n4\n2.5\n"
                   "x\n2\ny\n7\n3\n200\np\n2\nq\n")


def test_a_generic_body_packing_a_bare_variable_is_copied_per_layout(
        monkeypatch, capfd):
    """`Single[Show a](a)`, packed by a recursive `packOne[a](d, x : a, n)`.

    Nothing about `packOne`'s parameters is transparent -- a dictionary and a
    bare `a` -- so without `layout._packs` it is left generic past the cap,
    holds `x` boxed, and stores "boxed" as the code. The opening then calls an
    `i64` method with a box. This is the case the rule exists for."""
    def program():
        p = Program(MORE_HELPERS)
        p.existential("Main#Single", lambda a: [a],
                      context=("Std.Classes#Show", "Main#showOne"))
        single = p.decls.heads["Main#Single"]
        a = TVar(0, STAR)
        p.function(
            "Main#packOne",
            [("d", p.dictionary("Main#showOne", a)), ("x", a), ("n", INT)],
            single,
            lambda env: CIf(
                single, None, p.call(p.ref("Main#done"), env["n"]),
                p.pack("Main#Single", a, env["d"], env["x"]),
                p.call(p.ref("Main#packOne", a), env["d"], env["x"],
                       p.call(p.ref("Main#less"), env["n"]))),
            binders=[a])
        p.function("Main#visit", [("v", single)], UNIT, lambda env: p.open(
            env["v"], "Main#Single", ["x"], lambda s, v: p.call(
                p.ref("Main#say"),
                p.call(p.call(p.ref("Main#showOne", s), v["d"]), v["x"])),
            evidence=["d"]))
        by = {make: ty for ty, make, _ in payloads(p)}
        cases = (("Main#ints", "Int"), ("Main#floats", "Float"),
                 ("Main#strings", "Data.String.Type#String"), ("Main#bools", "Data.Bool.Type#Bool"))
        p.main(p.seq(*[
            p.call(p.ref("Main#visit"), p.call(
                p.ref("Main#packOne", by[make]),
                p.instance("Std.Classes#Show", instance),
                p.call(p.ref("Main#firstOf", by[make]), p.call(p.ref(make))),
                CLit(INT, None, "Int", 3)))
            for make, instance in cases]))
        return p

    out = run_everywhere(program, monkeypatch, capfd)
    assert out == "1\n1.5\nx\nTrue\n"


def test_a_skolem_may_not_escape_its_arm():
    p = packed_arrays()
    p.added = [b for b in p.added if b.name != "Main#main"]
    leaked = p.open(p.pack("Main#Packed", INT, p.call(p.ref("Main#ints")),
                           p.ref("Main#intText")),
                    "Main#Packed", ["xs", "f"], lambda s, v: v["xs"])
    p.main(p.seq(leaked, CApp(UNIT, None, p.ref("Main#say"), [p.text("no")])))
    with pytest.raises(TurkeyError, match="only exists inside the pattern"):
        p.compile()


def test_arrays_of_every_payload_layout_through_one_opening(monkeypatch, capfd):
    out = run_everywhere(packed_arrays, monkeypatch, capfd)
    assert out == ("1\n3\n2\n"
                   "true\n2\nfalse\n"
                   "1.5\n4\n2.5\n"
                   "x\n2\ny\n"
                   "7\n3\n200\n"
                   "p\n2\nq\n")
