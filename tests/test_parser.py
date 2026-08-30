"""Grammar decisions in the recursive-descent parser (design.md section 3, 7)."""

from __future__ import annotations

import pytest

from turkey import ast
from turkey.errors import ParseError
from turkey.parser import parse


def type_decls(src: str) -> dict[str, ast.TypeDecl]:
    return {d.name: d for d in parse(src).decls if isinstance(d, ast.TypeDecl)}


def first_value(src: str) -> ast.Expr:
    """The value expression of the first top-level `let`."""
    return parse(src).decls[0].value


def fun_body_stmts(src: str) -> list[ast.Stmt]:
    return parse(src).decls[0].decl.body.stmts


# -- type declaration disambiguation (section 7 / SPEC-DELTAS.md) ------------


def test_type_decl_alias_vs_data_disambiguation():
    src = (
        "type Name = String\n"
        "type Color = Red | Green\n"
        "type Cell = Cell { n : Int }\n"
        "type Pair = Pair(Int, Int)\n"
        "type Wrapper = Cell\n"
    )
    d = type_decls(src)

    assert d["Name"].is_alias  # String resolves as a type constructor

    assert not d["Color"].is_alias
    assert len(d["Color"].variants) == 2

    assert d["Cell"].is_mutable_record  # single-variant record

    assert not d["Pair"].is_alias  # Pair does not name a pre-existing tycon
    assert len(d["Pair"].variants) == 1
    assert len(d["Pair"].variants[0].args) == 2

    assert d["Wrapper"].is_alias  # Cell is declared in this same file


def test_pre_pass_sees_types_declared_later_even_when_the_alias_comes_first():
    src = "type Wrapper = Cell\ntype Cell = Cell { n : Int }\n"
    d = type_decls(src)
    assert d["Wrapper"].is_alias
    assert d["Cell"].is_mutable_record


def test_multi_variant_record_type_is_not_a_mutable_record():
    d = type_decls("type T = A { x : Int } | B { y : Int }\n")["T"]
    assert len(d.variants) == 2
    assert not d.is_mutable_record


# -- record-literal ambiguity in condition position (SPEC-DELTAS.md 12) -----


def test_condition_of_if_is_not_parsed_as_a_record_literal():
    e = first_value("let y = if x { 1 } else { 2 }\n")
    assert isinstance(e, ast.EIf)
    assert isinstance(e.cond, ast.EVar)


def test_condition_of_while_is_not_parsed_as_a_record_literal():
    w = fun_body_stmts("fun f() { while x { 1 } }\n")[0].expr
    assert isinstance(w, ast.EWhile)
    assert isinstance(w.cond, ast.EVar)


def test_record_literal_in_ordinary_expression_position():
    assert isinstance(first_value("let r = Cell { n = 0 }\n"), ast.ERecord)


def test_parenthesized_record_literal_is_allowed_back_into_a_condition():
    e = first_value("let y = if (Cell { n = 0 }).n == 0 { 1 } else { 2 }\n")
    assert isinstance(e, ast.EIf)
    assert isinstance(e.cond, ast.EBinary) and e.cond.op == "=="
    assert isinstance(e.cond.left, ast.EField)


# -- operator precedence (section 3.5) --------------------------------------


def test_multiplication_nests_under_addition():
    v = first_value("let a = 1 + 2 * 3\n")
    assert isinstance(v, ast.EBinary) and v.op == "+"
    assert isinstance(v.right, ast.EBinary) and v.right.op == "*"


def test_and_binds_tighter_than_or():
    v = first_value("let a = x && y || z\n")
    assert v.op == "||"
    assert isinstance(v.left, ast.EBinary) and v.left.op == "&&"


def test_comparison_binds_tighter_than_equality():
    v = first_value("let a = 1 < 2 == True\n")
    assert v.op == "=="
    assert isinstance(v.left, ast.EBinary) and v.left.op == "<"


# -- assignment forms (SPEC-DELTAS.md 1) -----------------------------------


def test_assignment_targets():
    x = fun_body_stmts("fun f() { x = 1 }\n")[0]
    assert isinstance(x, ast.SAssign) and isinstance(x.target, ast.EVar)

    rf = fun_body_stmts("fun f() { r.f = 1 }\n")[0]
    assert isinstance(rf, ast.SAssign) and isinstance(rf.target, ast.EField)

    ai = fun_body_stmts("fun f() { a[0] = 1 }\n")[0]
    assert isinstance(ai, ast.SAssign) and isinstance(ai.target, ast.EIndex)


def test_assignment_to_non_lvalue_is_a_parse_error():
    with pytest.raises(ParseError):
        parse("fun f() { 1 + 1 = 2 }\n")


# -- type position vs expression position ----------------------------------


def test_fun_arrow_in_type_position_is_a_function_type():
    d = type_decls("type F = fun(Int) -> Int\n")["F"]
    assert d.is_alias
    assert isinstance(d.alias, ast.TEFun)


def test_fun_in_expression_position_is_a_lambda():
    assert isinstance(first_value("let f = fun(x) -> Int = x\n"), ast.ELambda)


# -- parenthesized forms --------------------------------------------------


def test_unit_grouping_and_tuple():
    assert isinstance(first_value("let u = ()\n"), ast.EUnit)
    assert isinstance(first_value("let u = (1)\n"), ast.ELit)
    assert isinstance(first_value("let u = (1, 2)\n"), ast.ETuple)


# -- for loops ----------------------------------------------------------


def test_both_for_forms_parse():
    a = fun_body_stmts("fun f() { for x in xs { } }\n")[0].expr
    assert isinstance(a, ast.EForIn)

    b = fun_body_stmts("fun f() { for var i = 0; i < 3; i = i + 1 { } }\n")[0].expr
    assert isinstance(b, ast.EForC)


# -- match arms -------------------------------------------------------


def match_arms(src: str) -> list[ast.MatchArm]:
    return fun_body_stmts(src)[0].expr.arms


def test_alternatives_on_one_line_are_one_arm_with_two_patterns():
    arms = match_arms("fun f(x) { match x { A | B -> 1 } }\n")
    assert len(arms) == 1
    assert len(arms[0].patterns) == 2


def test_leading_bar_on_a_following_line_starts_a_new_arm():
    src = "fun f(x) {\n  match x {\n    A -> 1\n    | B -> 2\n  }\n}\n"
    arms = match_arms(src)
    assert len(arms) == 2
    assert len(arms[0].patterns) == 1
    assert len(arms[1].patterns) == 1


def test_constructor_patterns_are_parenthesized():
    paren = match_arms("fun f(x) { match x { Node(l, v, r) -> 1 } }\n")[0].patterns[0]
    assert isinstance(paren, ast.PCon) and len(paren.args) == 3

    # A nullary constructor stays bare -- no empty argument list.
    nullary = match_arms("fun f(x) { match x { None -> 1 } }\n")[0].patterns[0]
    assert isinstance(nullary, ast.PCon) and nullary.args == []


def test_juxtaposed_constructor_forms_are_rejected():
    # Dropped in favour of the paren form. Both sites report what to write
    # instead, because otherwise the pattern just ends early and the parser
    # complains about whatever token happens to follow.
    with pytest.raises(ParseError, match=r"Node\(\.\.\.\)"):
        parse("fun f(x) { match x { Node l v r -> 1 } }\n")
    with pytest.raises(ParseError, match=r"Some\(\.\.\.\)"):
        parse("type Option a = None | Some a\n")


def test_record_pattern_punning_expands_to_a_pvar():
    p = match_arms("fun f(x) { match x { Cell { n } -> n } }\n")[0].patterns[0]
    assert isinstance(p, ast.PRecord)
    assert len(p.fields) == 1
    name, sub = p.fields[0]
    assert name == "n"
    assert isinstance(sub, ast.PVar) and sub.name == "n"
