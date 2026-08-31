"""The newline rule, design.md section 2.4 (see also SPEC-DELTAS.md entry 11).

`tokenize` runs the raw lexer and then `apply_newline_rule`, so asserting on the
`kind` sequence of its output is a direct test of which line breaks survive.

`lex` wraps `tokenize` in a watchdog thread. Input ending in an identifier with
no trailing newline (`x'`, `1.foo`) once sent the lexer into an infinite loop --
`_peek()` returns "" at end of input, and "" is a substring of every string, so
`"" in "_\'"` was True forever. That is fixed, but the watchdog stays: a hang is
the one failure mode that produces no output and wedges the whole run, so it is
worth converting back into an ordinary failure.
"""

from __future__ import annotations

import threading

import pytest

from turkey.errors import LexError
from turkey.lexer import Token, tokenize


def lex(src: str) -> list[Token]:
    box: dict[str, object] = {}

    def run() -> None:
        try:
            box["toks"] = tokenize(src)
        except BaseException as exc:  # re-raised on the calling thread below
            box["exc"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(5.0)
    if worker.is_alive():
        pytest.fail(f"tokenize({src!r}) did not terminate within 5s (infinite loop)")
    if "exc" in box:
        raise box["exc"]  # type: ignore[misc]
    return box["toks"]  # type: ignore[return-value]


def kinds(src: str) -> list[str]:
    return [t.kind for t in lex(src)]


# -- newlines that terminate statements --------------------------------------


def test_newline_between_two_let_statements_is_kept():
    assert kinds("let x = 1\nlet y = 2") == [
        "let", "IDENT", "=", "INT", "NEWLINE",
        "let", "IDENT", "=", "INT", "EOF",
    ]


def test_newlines_between_block_statements_are_kept():
    assert kinds("{\nlet x = 1\nlet y = 2\n}") == [
        "{", "let", "IDENT", "=", "INT", "NEWLINE",
        "let", "IDENT", "=", "INT", "}", "EOF",
    ]


# -- newlines that are dropped ---------------------------------------------------


def test_newline_before_else_is_dropped():
    ks = kinds("if c {\n a\n} else {\n b\n}")
    assert "NEWLINE" not in ks
    assert ks[ks.index("else") - 1] == "}"


def test_newlines_inside_parens_and_brackets_are_dropped():
    assert "NEWLINE" not in kinds("f(1,\n2)")
    assert "NEWLINE" not in kinds("[1,\n2]")


def test_braces_nested_in_parens_get_their_newlines_back():
    # Only the innermost bracket decides. A counter cannot express this: once
    # inside `(`, the `{` could never restore separators, so the arms of a match
    # passed as a call argument ran together and failed to parse.
    ks = kinds("f(match x {\n1 -> a\n2 -> b\n})")
    assert ks.count("NEWLINE") == 1
    assert ks[ks.index("NEWLINE") - 1] == "IDENT"  # ends arm one

    # Closing the brace returns to the call's suppression.
    assert "NEWLINE" not in kinds("f({\n1\n},\n2)")


def test_parens_nested_in_braces_still_suppress():
    # The call's own line break is wrapping; the one between the two statements
    # separates them. Exactly one survives.
    assert kinds("{\nf(1,\n2)\ng(3)\n}") == [
        "{", "IDENT", "(", "INT", ",", "INT", ")", "NEWLINE",
        "IDENT", "(", "INT", ")", "}", "EOF",
    ]


def test_newline_after_operator_that_cannot_end_a_production_is_dropped():
    # `-` cannot end a production, so the break is not a statement boundary.
    assert kinds("x -\n1") == ["IDENT", "-", "INT", "EOF"]


def test_newline_after_value_before_minus_is_kept():
    # `x` can end a production and `-` can start one: two statements, not `x - 1`.
    assert kinds("x\n- 1") == ["IDENT", "NEWLINE", "-", "INT", "EOF"]


# -- the semicolon always separates -------------------------------------------


def test_semicolon_forces_a_separator_where_the_two_sided_rule_would_drop_it():
    assert "NEWLINE" not in kinds("1 *\n2")
    assert "NEWLINE" in kinds("1 *;2")


# -- blank-line collapsing and stream edges ----------------------------------


def test_run_of_blank_lines_collapses_to_one_newline():
    assert kinds("a\n\n\n\nb\n").count("NEWLINE") == 1


def test_no_newline_at_start_or_end_of_stream():
    ks = kinds("\n\nlet x = 1\n\n")
    assert ks[0] != "NEWLINE"
    assert ks[-1] == "EOF"
    assert ks[-2] != "NEWLINE"


# -- comments -----------------------------------------------------------------


def test_nested_block_comment_is_fully_consumed():
    assert kinds("{- a {- b -} c -} 1") == ["INT", "EOF"]


def test_unterminated_block_comment_raises():
    with pytest.raises(LexError):
        lex("{- a {- b -}")


# -- string / char literals --------------------------------------------------


def test_string_escapes():
    assert lex('"\\n"')[0].value == "\n"
    assert lex('"\\t"')[0].value == "\t"
    assert lex('"\\\\"')[0].value == "\\"
    assert lex('"\\""')[0].value == '"'
    assert lex('"A"')[0].value == "A"


def test_char_literals():
    a = lex("'a'")
    assert a[0].kind == "CHAR" and a[0].value == "a"
    nl = lex("'\\n'")
    assert nl[0].kind == "CHAR" and nl[0].value == "\n"


def test_identifier_with_trailing_quote_lexes_as_one_ident():
    # Mid-stream (not at EOF): the trailing-quote handling itself is fine.
    toks = lex("x' + 1")
    assert [t.kind for t in toks] == ["IDENT", "+", "INT", "EOF"]
    assert toks[0].value == "x'"


def test_identifier_may_end_with_a_quote():
    toks = lex("x'")
    assert [t.kind for t in toks] == ["IDENT", "EOF"]
    assert toks[0].value == "x'"


# -- number vs field access ------------------------------------------------------


def test_float_is_one_token():
    toks = lex("1.5")
    assert [t.kind for t in toks] == ["FLOAT", "EOF"]
    assert toks[0].value == 1.5


def test_dot_not_followed_by_digit_is_a_separate_token():
    # Mid-stream form: the `.` / float distinction works correctly.
    assert kinds("(1.foo)") == ["(", "INT", ".", "IDENT", ")", "EOF"]


def test_dot_not_followed_by_digit_at_end_of_input():
    assert kinds("1.foo") == ["INT", ".", "IDENT", "EOF"]


# -- `?` and `do` (delta 46) -----------------------------------------------------


def test_question_is_one_token():
    assert kinds("a?") == ["IDENT", "?", "EOF"]


def test_question_ends_a_statement():
    """`?` is postfix, so a statement's last token can be one. Without `?` in
    `CAN_END` the newline would vanish and the next line would read as a
    continuation of this one."""
    assert kinds("let x = a?\nlet y = b") == [
        "let", "IDENT", "=", "IDENT", "?", "NEWLINE",
        "let", "IDENT", "=", "IDENT", "EOF",
    ]


def test_a_newline_before_a_question_does_not_survive():
    """`?` cannot begin a production, so it is absent from `CAN_START` and a
    line break in front of one simply disappears."""
    assert kinds("a\n?") == ["IDENT", "?", "EOF"]


def test_do_starts_a_statement():
    assert kinds("f()\ndo { }") == [
        "IDENT", "(", ")", "NEWLINE", "do", "{", "}", "EOF",
    ]


def test_do_is_a_keyword_and_no_longer_an_identifier():
    assert kinds("do") == ["do", "EOF"]
