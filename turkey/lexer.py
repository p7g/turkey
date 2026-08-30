"""Tokenizer for turkey-lite (design.md section 2).

Lexing happens in two passes. `tokenize_raw` produces tokens including a NEWLINE
for every line break; `apply_newline_rule` then implements section 2.4, deciding
which of those newlines actually terminate a production. Keeping the two apart
means the newline rule -- the subtlest part of the lexical structure -- can be
tested on its own. See SPEC-DELTAS.md entry 11 for how the circular wording in
section 2.4 was made concrete.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import LexError, Span

KEYWORDS = frozenset(
    """type class instance fun let var match if else while for in loop return
    break continue module import export as qualified hiding
    where""".split()
)

# Longest match first: `<=` must beat `<`, `->` must beat `-`, `++` must beat `+`.
OPERATORS = [
    "==", "!=", "<=", ">=", "->", "++", "&&", "||", "..",
    "=", "<", ">", "+", "-", "*", "/", "%", "!", ":", ",", ".", "|", "~",
    "{", "}", "(", ")", "[", "]",
]

LITERAL_KINDS = frozenset({"INT", "FLOAT", "STRING", "CHAR"})

# Section 2.4, preceding condition: tokens that can legally end a production.
# `break`, `continue` and bare `return` are added -- design.md omits them, but a
# statement ending in one of them must still be terminable by a newline.
CAN_END = (
    LITERAL_KINDS
    | {"IDENT", "CONID", ")", "]", "}", "break", "continue", "return"}
)

# Section 2.4, following condition: tokens that can legally start a sibling
# production. `else` and `|` are deliberately absent. That is what lets `} else`
# stay one expression, and what lets a match arm's alternative patterns be split
# across lines: the newline before the `|` simply disappears.
CAN_START = (
    LITERAL_KINDS
    | {
        "let", "var", "fun", "type", "class", "instance", "if", "match",
        "while", "for", "loop", "return", "break", "continue", "module",
        "import",
        "IDENT", "CONID", "(", "[", "{", "-", "!",
    }
)

STRING_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "0": "\0",
    "\\": "\\", '"': '"', "'": "'",
}


@dataclass(frozen=True)
class Token:
    kind: str
    value: object
    span: Span
    forced: bool = False  # NEWLINE that came from `;` and always survives filtering

    @property
    def text(self) -> str:
        return self.value if isinstance(self.value, str) else str(self.value)

    def __repr__(self) -> str:
        if self.kind in LITERAL_KINDS or self.kind in ("IDENT", "CONID"):
            return f"{self.kind}({self.value!r})@{self.span}"
        return f"{self.kind}@{self.span}"


class Lexer:
    def __init__(self, src: str):
        self.src = src
        self.pos = 0
        self.line = 1
        self.col = 1

    # -- character helpers ------------------------------------------------

    def _peek(self, offset: int = 0) -> str:
        i = self.pos + offset
        return self.src[i] if i < len(self.src) else ""

    def _advance(self, count: int = 1) -> str:
        taken = self.src[self.pos : self.pos + count]
        for ch in taken:
            if ch == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1
        self.pos += count
        return taken

    def _span(self) -> Span:
        return Span(self.line, self.col)

    # -- main loop --------------------------------------------------------

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while True:
            self._skip_inline_trivia()
            if self.pos >= len(self.src):
                break
            span = self._span()
            ch = self._peek()

            if ch == "\n":
                self._advance()
                tokens.append(Token("NEWLINE", "\n", span))
            elif ch == ";":
                self._advance()
                tokens.append(Token("NEWLINE", ";", span, forced=True))
            elif ch.isdigit():
                tokens.append(self._lex_number(span))
            elif ch == '"':
                tokens.append(self._lex_string(span))
            elif ch == "'":
                tokens.append(self._lex_char(span))
            elif ch.isalpha() or ch == "_":
                tokens.append(self._lex_word(span))
            else:
                tokens.append(self._lex_operator(span, ch))

        tokens.append(Token("EOF", "", self._span()))
        return tokens

    def _skip_inline_trivia(self) -> None:
        """Consume spaces, tabs, carriage returns and comments -- but not newlines."""
        while self.pos < len(self.src):
            ch = self._peek()
            if ch in " \t\r":
                self._advance()
            elif ch == "-" and self._peek(1) == "-":
                while self.pos < len(self.src) and self._peek() != "\n":
                    self._advance()
            elif ch == "{" and self._peek(1) == "-":
                self._skip_block_comment()
            else:
                return

    def _skip_block_comment(self) -> None:
        start = self._span()
        depth = 0
        while self.pos < len(self.src):
            if self._peek() == "{" and self._peek(1) == "-":
                self._advance(2)
                depth += 1
            elif self._peek() == "-" and self._peek(1) == "}":
                self._advance(2)
                depth -= 1
                if depth == 0:
                    return
            else:
                self._advance()
        raise LexError("unterminated block comment", start)

    # -- token kinds ------------------------------------------------------

    def _lex_number(self, span: Span) -> Token:
        start = self.pos
        while self._peek().isdigit():
            self._advance()
        # FLOAT is `[0-9]+.[0-9]+`: a `.` only continues the number when a digit
        # follows it, so `1.foo` stays an INT followed by a field access.
        if self._peek() == "." and self._peek(1).isdigit():
            self._advance()
            while self._peek().isdigit():
                self._advance()
            return Token("FLOAT", float(self.src[start : self.pos]), span)
        return Token("INT", int(self.src[start : self.pos]), span)

    def _lex_escape(self, quote: str) -> str:
        span = self._span()
        self._advance()  # the backslash
        ch = self._advance()
        if ch == "u":
            digits = self.src[self.pos : self.pos + 4]
            if len(digits) != 4 or any(d not in "0123456789abcdefABCDEF" for d in digits):
                raise LexError("\\u escape needs exactly four hex digits", span)
            self._advance(4)
            return chr(int(digits, 16))
        if ch in STRING_ESCAPES:
            return STRING_ESCAPES[ch]
        raise LexError(f"unknown escape sequence '\\{ch}'", span)

    def _lex_string(self, span: Span) -> Token:
        self._advance()  # opening quote
        out: list[str] = []
        while True:
            ch = self._peek()
            if ch == "":
                raise LexError("unterminated string literal", span)
            if ch == "\n":
                raise LexError("newline in string literal", span)
            if ch == '"':
                self._advance()
                return Token("STRING", "".join(out), span)
            out.append(self._lex_escape('"') if ch == "\\" else self._advance())

    def _lex_char(self, span: Span) -> Token:
        self._advance()  # opening quote
        ch = self._peek()
        if ch == "":
            raise LexError("unterminated character literal", span)
        value = self._lex_escape("'") if ch == "\\" else self._advance()
        if self._peek() != "'":
            raise LexError("character literal must contain exactly one character", span)
        self._advance()
        return Token("CHAR", value, span)

    def _lex_word(self, span: Span) -> Token:
        start = self.pos
        # `_peek` returns "" at end of input, and "" is a substring of every
        # string -- so the membership test must be guarded, or an identifier
        # that ends the file loops forever.
        while (ch := self._peek()) and (ch.isalnum() or ch in "_'"):
            self._advance()
        text = self.src[start : self.pos]
        if text in KEYWORDS:
            return Token(text, text, span)
        # Section 2.3: the leading character decides. `_` and lowercase are
        # variables; uppercase are type and value constructors.
        return Token("CONID" if text[0].isupper() else "IDENT", text, span)

    def _lex_operator(self, span: Span, ch: str) -> Token:
        for op in OPERATORS:
            if self.src.startswith(op, self.pos):
                self._advance(len(op))
                return Token(op, op, span)
        raise LexError(f"unexpected character {ch!r}", span)


def apply_newline_rule(tokens: list[Token]) -> list[Token]:
    """Decide which NEWLINE tokens terminate a production (section 2.4).

    A newline survives iff the previous token can end a production and the next
    one can start a sibling. A newline from `;` always survives.

    Bracket nesting is a stack, not a counter, because `(` and `{` disagree
    about newlines and either can contain the other. Inside `(` or `[` a line
    break is just wrapping, so it is dropped; inside `{` it separates block
    statements and match arms, so it is kept. Only the innermost bracket
    decides -- otherwise the `{ }` in `f(match x { ... })` would inherit the
    call's suppression and its arms would run together.
    """
    out: list[Token] = []
    stack: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        if tok.kind != "NEWLINE":
            if tok.kind in ("(", "[", "{"):
                stack.append(tok.kind)
            elif tok.kind in (")", "]", "}") and stack:
                stack.pop()
            out.append(tok)
            i += 1
            continue

        # Collapse a run of blank lines into a single decision.
        run_end = i
        forced = False
        while run_end < len(tokens) and tokens[run_end].kind == "NEWLINE":
            forced = forced or tokens[run_end].forced
            run_end += 1
        nxt = tokens[run_end] if run_end < len(tokens) else None
        prev = out[-1] if out else None

        if prev is None or nxt is None or nxt.kind == "EOF":
            keep = False  # never lead or trail the stream with a separator
        elif forced:
            keep = True
        elif stack and stack[-1] in ("(", "["):
            keep = False
        else:
            keep = prev.kind in CAN_END and nxt.kind in CAN_START

        if keep:
            out.append(Token("NEWLINE", tokens[i].value, tokens[i].span, forced))
        i = run_end

    return out


def tokenize(src: str) -> list[Token]:
    """Lex `src` all the way to a parser-ready token stream."""
    return apply_newline_rule(Lexer(src).tokenize())
