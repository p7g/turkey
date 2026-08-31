"""What `?` and `do` turn into (delta 46).

`question.tl` pins the behaviour and `monads.tl` writes the chains out by hand.
Neither can show the thing this feature actually is: the tree the pass produces.
So the tests here read it back as source-ish text and assert on it, because
"`?` is sugar for `bind` plus a lambda" is a claim about a *shape*, and a claim
about a shape is worth checking directly rather than inferring from output.

The generated binder names (`%k1`, `%k2`, ...) are normalized away, since which
counter value a lambda got is not part of the claim.
"""

from __future__ import annotations

import re

import pytest

from turkey import ast, desugar
from turkey.errors import Unsupported
from turkey.parser import parse


def lower(src: str, name: str = "f") -> str:
    """`fun name`'s body after desugaring, printed."""
    program = parse(src)
    desugar.program(program)
    for decl in program.decls:
        if isinstance(decl, ast.SFun) and decl.decl.name == name:
            return _normalize(show(decl.decl.body))
    raise AssertionError(f"no 'fun {name}' in the program")


def refuse(src: str) -> str:
    with pytest.raises(Unsupported) as exc:
        desugar.program(parse(src))
    return exc.value.message


def _normalize(text: str) -> str:
    """`%k7` -> `%k1`, `%k9` -> `%k2`, in order of first appearance."""
    seen: dict[str, str] = {}
    for name in re.findall(r"%k\d+", text):
        seen.setdefault(name, f"%k{len(seen) + 1}")
    return re.sub(r"%k\d+", lambda m: seen[m.group()], text)


def show(e) -> str:
    """A small printer -- enough of one to read a lowering back."""
    t = type(e)
    if t is ast.EVar:
        return e.name
    if t is ast.ECon:
        return _short(e.name)
    if t is ast.ELit:
        return f'"{e.value}"' if e.kind == "String" else str(e.value)
    if t is ast.EUnit:
        return "()"
    if t is ast.ECall:
        return f"{show(e.fn)}({', '.join(show(a) for a in e.args)})"
    if t is ast.ELambda:
        params = ", ".join(_pat(p) for p in e.params)
        return f"fun({params}) {show(e.body)}"
    if t is ast.EBlock:
        return "{ " + "; ".join(_stmt(s) for s in e.stmts) + " }"
    if t is ast.EBinary:
        return f"({show(e.left)} {e.op} {show(e.right)})"
    if t is ast.EUnary:
        return f"({e.op}{show(e.operand)})"
    if t is ast.EIf:
        tail = f" else {show(e.otherwise)}" if e.otherwise is not None else ""
        return f"if {show(e.cond)} {show(e.then)}{tail}"
    if t is ast.EField:
        return f"{show(e.obj)}.{e.name}"
    if t is ast.EIndex:
        return f"{show(e.arr)}[{show(e.index)}]"
    if t is ast.EMatch:
        arms = " ".join(
            f"{' | '.join(_pat(p) for p in a.patterns)} -> {show(a.body)}"
            for a in e.arms
        )
        return f"match {show(e.scrutinee)} {{ {arms} }}"
    if t is ast.EReturn:
        return "return" + (f" {show(e.value)}" if e.value is not None else "")
    if t is ast.EArray:
        return f"[{', '.join(show(x) for x in e.elems)}]"
    if t is ast.EQuestion:
        return f"{show(e.expr)}?"  # only reachable if the pass missed one
    if t is ast.EDo:
        return f"do {show(e.body)}"
    return f"<{t.__name__}>"


def _stmt(s) -> str:
    t = type(s)
    if t is ast.SLet:
        return f"let {_pat(s.pat)} = {show(s.value)}"
    if t is ast.SVar:
        return f"var {_pat(s.pat)} = {show(s.value)}"
    if t is ast.SAssign:
        return f"{show(s.target)} = {show(s.value)}"
    if t is ast.SExpr:
        return show(s.expr)
    if t is ast.SFun:
        return f"fun {s.decl.name}(...) {show(s.decl.body)}"
    return f"<{t.__name__}>"


def _pat(p) -> str:
    t = type(p)
    if t is ast.PVar:
        return p.name
    if t is ast.PWild:
        return "_"
    if t is ast.PCon:
        inner = f"({', '.join(_pat(x) for x in p.args)})" if p.args else ""
        return f"{_short(p.name)}{inner}"
    return f"<{t.__name__}>"


def _short(name: str) -> str:
    return name.rpartition("#")[2] or name


# -- the straight-line case is exactly the hand-written chain ------------------


def test_one_question_is_one_bind_and_one_lambda():
    """The whole claim, in one assertion."""
    assert lower("fun f(a) { let x = a?; g(x) }") == \
        "bind(a, fun(%k1) { let x = %k1; g(x) })"


def test_the_rest_of_the_block_is_the_continuation():
    assert lower("fun f(a, b) { let x = a?; let y = b?; h(x, y) }") == \
        ("bind(a, fun(%k1) { let x = %k1; "
         "bind(b, fun(%k2) { let y = %k2; h(x, y) }) })")


def test_statements_before_a_question_stay_where_they_were_written():
    """A block splits only where it has to, so the prefix is not disturbed."""
    assert lower("fun f(a) { let n = 1; let x = a?; g(n, x) }") == \
        "{ let n = 1; bind(a, fun(%k1) { let x = %k1; g(n, x) }) }"


def test_a_question_nested_in_a_call_is_hoisted_out_of_it():
    assert lower("fun f(a) { g(h(a?)) }") == \
        "bind(a, fun(%k1) g(h(%k1)))"


def test_two_questions_in_one_expression_bind_left_to_right():
    assert lower("fun f(a, b) { g(a?, b?) }") == \
        "bind(a, fun(%k1) bind(b, fun(%k2) g(%k1, %k2)))"


def test_a_question_in_tail_position_needs_no_binding():
    assert lower("fun f(a) { g(a?) }") == "bind(a, fun(%k1) g(%k1))"


def test_a_chained_question_binds_twice():
    assert lower("fun f(a) { g(a?.inner?) }") == \
        "bind(a, fun(%k1) bind(%k1.inner, fun(%k2) g(%k2)))"


# -- `do` ---------------------------------------------------------------------


def test_a_do_block_with_no_question_emits_nothing_at_all():
    """No `bind`, so no `Monad` constraint, so nothing to be ambiguous about."""
    assert lower("fun f(n) { let v = do { n + 1 }; v }") == \
        "{ let v = { (n + 1) }; v }"


def test_an_empty_do_block_is_an_empty_block():
    assert lower("fun f() { do { } }") == "{ {  } }"


def test_a_do_block_bounds_the_continuation():
    """The `g(x)` is outside the `do`, so it is not inside the lambda."""
    assert lower("fun f(a) { let v = do { let x = a?; k(x) }; g(v) }") == \
        "{ let v = bind(a, fun(%k1) { let x = %k1; k(x) }); g(v) }"


def test_a_lambda_is_a_context_of_its_own():
    assert lower("fun f(xs) { map(xs, fun(n) { let x = n?; k(x) }) }") == \
        "{ map(xs, fun(n) bind(n, fun(%k1) { let x = %k1; k(x) })) }"


# -- lifting ------------------------------------------------------------------


def test_an_if_in_tail_position_costs_neither_a_pure_nor_a_bind():
    """Its branches already *are* the do block's tail, and the tail of a do
    block is the monadic value -- so the continuation goes straight into them.

    Both branches are translated even though only one holds the `?`; the `else`
    is a `?`-free block that simply came through the translation unchanged."""
    assert lower("fun f(c, a) { if c { let x = a?; k(x) } else { j() } }") == \
        "if c bind(a, fun(%k1) { let x = %k1; k(x) }) else j()"


def test_an_if_that_is_not_the_tail_is_lifted_with_pure_and_bound():
    """Here the `if` is a statement, so its value is `Unit` by the language's
    own rule and something has to carry it into the monad."""
    assert lower("fun f(c, a) { if c { k(a?) }; done() }") == \
        ("bind(if c bind(a, fun(%k1) pure(k(%k1))) else pure(()), "
         "fun(%k2) done())")


def test_a_question_in_a_match_arm_lifts_the_whole_match():
    """Every arm is translated, not only the one holding the `?` -- a `match`
    is one expression and its arms have to agree on a type."""
    src = "fun f(o, a) { match o {\n Some(x) -> k(a?)\n None -> j()\n } }"
    assert lower(src) == \
        "match o { Some(x) -> bind(a, fun(%k1) k(%k1)) None -> j() }"


def test_a_condition_alone_does_not_lift_the_if():
    """Only the branches force a lift; a `?` in the test just hoists."""
    assert lower("fun f(c, a) { if c? { k() } else { j() } }") == \
        "bind(c, fun(%k1) if %k1 { k() } else { j() })"


def test_short_circuiting_is_preserved_by_reading_the_operator_as_an_if():
    """`&&` does not evaluate its right operand, and no argument to `bind`
    can promise that -- so the right operand stays under a branch."""
    assert lower("fun f(c, a) { g(c && a?) }") == \
        ("bind(if c bind(a, fun(%k1) pure(%k1)) else pure(False), "
         "fun(%k2) g(%k2))")


# -- what is refused, for now -------------------------------------------------


def test_a_question_in_a_loop_body_is_refused():
    assert "not supported inside a loop yet" in \
        refuse("fun f(xs) { for x in xs { let y = x?; k(y) } }")


def test_a_return_after_a_question_is_refused():
    """It would land inside the generated lambda and mean the wrong thing."""
    assert "cannot cross a '?' yet" in \
        refuse("fun f(a) { let x = a?; if x { return None }; k(x) }")


def test_a_return_before_every_question_is_allowed():
    """It stays in the prefix, outside every lambda the lowering makes."""
    assert lower("fun f(c, a) { if c { return None }; k(a?) }") == \
        "{ if c { return None }; bind(a, fun(%k1) k(%k1)) }"


def test_a_question_outside_any_context_is_refused():
    assert "only meaningful inside a function body or a 'do' block" in \
        refuse("type T = T(Int)\nlet x = y?")
