"""Recursive-descent parser for turkey-lite (design.md section 3).

A few decisions worth knowing before reading:

* Types and expressions have separate entry points. That is what disambiguates
  `fun(Int) -> Int` (a type) from `fun(x) -> Int = e` (a lambda): they are never
  reached from the same place.
* `self.no_record` is set while parsing the scrutinee of `if`/`while`/`match`
  and the iterable of `for ... in`, so `if Foo { ... }` reads as a condition
  followed by a block rather than a record construction (SPEC-DELTAS.md 12).
* Type declarations are disambiguated per section 7 against a set of type
  constructor names collected in a pre-pass, so a type may refer to one declared
  later in the file.
"""

from __future__ import annotations

from . import ast
from .errors import ParseError, Span
from .lexer import Token, tokenize

LITERAL_KINDS = ("INT", "FLOAT", "STRING", "CHAR")
LITERAL_TYPE = {"INT": "Int", "FLOAT": "Float", "STRING": "String", "CHAR": "Char"}

BUILTIN_TYCONS = frozenset({"Int", "Float", "String", "Char", "Bool", "Unit", "Array"})

# Tokens that can begin an expression. Used to decide whether `return`, `break`
# and the like carry a value.
EXPR_START = frozenset(
    set(LITERAL_KINDS)
    | {
        "IDENT", "CONID", "true", "false", "(", "[", "{", "-", "!",
        "fun", "if", "match", "while", "for", "loop", "return", "break", "continue",
    }
)

PATTERN_START = frozenset(
    set(LITERAL_KINDS) | {"IDENT", "CONID", "true", "false", "("}
)

# Binary operator precedence, loosest first (section 3.5).
PRECEDENCE: list[tuple[str, ...]] = [
    ("||",),
    ("&&",),
    ("==", "!="),
    ("<", "<=", ">", ">="),
    ("+", "-", "++", "+.", "-."),
    ("*", "/", "%", "*.", "/."),
]


class Parser:
    def __init__(self, tokens: list[Token], tycons: frozenset[str]):
        self.toks = tokens
        self.i = 0
        self.tycons = tycons
        self.no_record = False

    # -- token helpers ----------------------------------------------------

    @property
    def cur(self) -> Token:
        return self.toks[self.i]

    def peek(self, offset: int = 0) -> Token:
        i = min(self.i + offset, len(self.toks) - 1)
        return self.toks[i]

    def at(self, *kinds: str) -> bool:
        return self.cur.kind in kinds

    def advance(self) -> Token:
        tok = self.cur
        if tok.kind != "EOF":
            self.i += 1
        return tok

    def eat(self, kind: str) -> Token | None:
        return self.advance() if self.at(kind) else None

    def expect(self, kind: str, what: str | None = None) -> Token:
        if not self.at(kind):
            want = what or f"'{kind}'"
            raise ParseError(f"expected {want}, found {self._describe(self.cur)}", self.cur.span)
        return self.advance()

    @staticmethod
    def _describe(tok: Token) -> str:
        if tok.kind == "EOF":
            return "end of input"
        if tok.kind == "NEWLINE":
            return "end of line"
        if tok.kind in LITERAL_KINDS or tok.kind in ("IDENT", "CONID"):
            return f"{tok.kind} '{tok.text}'"
        return f"'{tok.kind}'"

    def skip_newlines(self) -> None:
        while self.at("NEWLINE"):
            self.advance()

    def _with_no_record(self, flag: bool):
        parser = self

        class _Scope:
            def __enter__(inner):
                inner.saved = parser.no_record
                parser.no_record = flag

            def __exit__(inner, *exc):
                parser.no_record = inner.saved
                return False

        return _Scope()

    # -- program ----------------------------------------------------------

    def parse_program(self) -> ast.Program:
        span = self.cur.span
        self.skip_newlines()
        header = self.parse_module_header() if self.at("module") else None
        imports: list[ast.ImportDecl] = []
        decls: list[ast.Stmt | ast.TypeDecl | ast.ClassDecl | ast.InstanceDecl] = []

        self.skip_newlines()
        while not self.at("EOF"):
            if self.at("import"):
                imports.append(self.parse_import())
            elif self.at("type"):
                decls.append(self.parse_type_decl())
            elif self.at("class"):
                decls.append(self.parse_class_decl())
            elif self.at("instance"):
                decls.append(self.parse_instance_decl())
            elif self.at("fun"):
                decls.append(ast.SFun(self.cur.span, self.parse_fun_decl()))
            elif self.at("let", "var"):
                decls.append(self.parse_binding_stmt())
            else:
                raise ParseError(
                    f"expected a top-level declaration, found {self._describe(self.cur)}",
                    self.cur.span,
                )
            self.end_of_statement()

        return ast.Program(span, header, imports, decls)

    def end_of_statement(self) -> None:
        """Consume the separator after a statement, or confirm we are at a closer."""
        if self.at("NEWLINE"):
            self.skip_newlines()
        elif not self.at("EOF", "}"):
            raise ParseError(
                f"expected end of statement, found {self._describe(self.cur)}",
                self.cur.span,
            )

    def parse_module_header(self) -> ast.ModuleHeader:
        span = self.expect("module").span
        name = self.parse_modname()
        exports: list[str] | None = None
        if self.at("("):
            exports = []
            self.advance()
            while not self.at(")"):
                exports.append(self.parse_export_item())
                if not self.eat(","):
                    break
            self.expect(")")
        self.expect("where")
        return ast.ModuleHeader(span, name, exports)

    def parse_export_item(self) -> str:
        tok = self.advance()
        if tok.kind not in ("IDENT", "CONID"):
            raise ParseError(f"expected an export name, found {self._describe(tok)}", tok.span)
        name = tok.text
        if tok.kind == "CONID" and self.at("("):
            self.advance()
            inner: list[str] = []
            while not self.at(")"):
                inner.append(self.advance().text)
                if not self.eat(","):
                    break
            self.expect(")")
            name += "(" + ",".join(inner) + ")"
        return name

    def parse_modname(self) -> str:
        parts = [self.expect("CONID", "a module name").text]
        while self.at(".") and self.peek(1).kind == "CONID":
            self.advance()
            parts.append(self.advance().text)
        return ".".join(parts)

    def parse_import(self) -> ast.ImportDecl:
        span = self.expect("import").span
        qualified = self.eat("qualified") is not None
        name = self.parse_modname()
        alias = items = hiding = None
        if self.eat("as"):
            alias = self.expect("CONID", "a module alias").text
        elif self.at("hiding"):
            self.advance()
            hiding = self.parse_paren_name_list()
        elif self.at("("):
            items = self.parse_paren_name_list()
        return ast.ImportDecl(span, name, alias, items, hiding, qualified)

    def parse_paren_name_list(self) -> list[str]:
        self.expect("(")
        names: list[str] = []
        while not self.at(")"):
            names.append(self.parse_export_item())
            if not self.eat(","):
                break
        self.expect(")")
        return names

    # -- type declarations -------------------------------------------------

    def parse_type_decl(self) -> ast.TypeDecl:
        span = self.expect("type").span
        name = self.expect("CONID", "a type name").text
        params: list[str] = []
        while self.at("IDENT"):
            params.append(self.advance().text)
        self.expect("=")

        # Section 7. A `|` or a `{` payload means data type outright. A bare
        # `CONID args*` is a data type when the head does not name an existing
        # type constructor -- or when it names the type being declared, which is
        # the newtype case, not a recursive alias.
        if self.at("CONID"):
            head = self.cur.text
            if self.peek(1).kind == "{" or self._rhs_has_alternatives():
                return self._parse_data_rhs(span, name, params)
            if head not in self.tycons or head == name:
                return self._parse_data_rhs(span, name, params)

        alias = self.parse_type_expr()
        return ast.TypeDecl(span, name, params, None, alias)

    def _rhs_has_alternatives(self) -> bool:
        """Look ahead for a top-level `|` before the declaration ends."""
        depth = 0
        j = self.i
        while j < len(self.toks):
            kind = self.toks[j].kind
            if kind in ("(", "[", "{"):
                depth += 1
            elif kind in (")", "]", "}"):
                if depth == 0:
                    return False
                depth -= 1
            elif depth == 0:
                if kind == "|":
                    return True
                if kind in ("NEWLINE", "EOF"):
                    return False
            j += 1
        return False

    def _parse_data_rhs(self, span: Span, name: str, params: list[str]) -> ast.TypeDecl:
        variants = [self.parse_con_decl()]
        while self.eat("|"):
            self.skip_newlines()
            variants.append(self.parse_con_decl())
        return ast.TypeDecl(span, name, params, variants, None)

    def parse_con_decl(self) -> ast.ConDecl:
        tok = self.expect("CONID", "a constructor name")
        if self.at("{"):
            return ast.ConDecl(tok.span, tok.text, [], self.parse_record_payload())
        args: list[ast.TypeExpr] = []
        if self.eat("("):
            # A value constructor is an uncurried function, so it declares its
            # payload the way a function declares parameters. Type application
            # inside stays juxtaposed -- `Loop(Array Op)` -- which is why the
            # grouping parens the old syntax needed here are now free.
            while not self.at(")"):
                args.append(self.parse_type_expr())
                if not self.eat(","):
                    break
            self.expect(")")
        elif self.at("IDENT", "CONID", "fun"):
            raise ParseError(
                f"constructor '{tok.text}' must parenthesize its payload: write "
                f"'{tok.text}(...)'",
                self.cur.span,
            )
        return ast.ConDecl(tok.span, tok.text, args, None)

    def parse_record_payload(self) -> list[tuple[str, ast.TypeExpr]]:
        self.expect("{")
        self.skip_newlines()
        fields: list[tuple[str, ast.TypeExpr]] = []
        seen: set[str] = set()
        while not self.at("}"):
            tok = self.expect("IDENT", "a field name")
            if tok.text in seen:
                raise ParseError(f"duplicate field '{tok.text}'", tok.span)
            seen.add(tok.text)
            self.expect(":")
            fields.append((tok.text, self.parse_type_expr()))
            self.skip_newlines()
            if not self.eat(","):
                break
            self.skip_newlines()
        self.expect("}")
        return fields

    # -- type expressions --------------------------------------------------

    def parse_type_expr(self) -> ast.TypeExpr:
        """`btype ::= atype+`. There is no arrow form -- see SPEC-DELTAS.md 2."""
        head = self.parse_atype()
        args: list[ast.TypeExpr] = []
        while self.at("IDENT", "CONID", "("):
            args.append(self.parse_atype())
        if not args:
            return head
        if isinstance(head, ast.TECon) and not head.args:
            return ast.TECon(head.span, head.name, args)
        if isinstance(head, ast.TEVar):
            # A variable in head position: `f a`. Legal since M4, and what a
            # higher-kinded parameter looks like at the surface.
            return ast.TEApp(head.span, head, args)
        raise ParseError("only a type constructor or a type variable can be "
                         "applied to arguments", head.span)

    def parse_atype(self) -> ast.TypeExpr:
        tok = self.cur
        if tok.kind == "IDENT":
            self.advance()
            return ast.TEVar(tok.span, tok.text)
        if tok.kind == "CONID":
            self.advance()
            return ast.TECon(tok.span, tok.text, [])
        if tok.kind == "fun":
            self.advance()
            self.expect("(")
            params: list[ast.TypeExpr] = []
            while not self.at(")"):
                params.append(self.parse_type_expr())
                if not self.eat(","):
                    break
            self.expect(")")
            self.expect("->", "'->' and a return type")
            return ast.TEFun(tok.span, params, self.parse_type_expr())
        if tok.kind == "(":
            self.advance()
            elems = [self.parse_type_expr()]
            while self.eat(","):
                elems.append(self.parse_type_expr())
            self.expect(")")
            return elems[0] if len(elems) == 1 else ast.TETuple(tok.span, elems)
        raise ParseError(f"expected a type, found {self._describe(tok)}", tok.span)

    # -- declarations and statements ---------------------------------------

    def parse_fun_decl(self, allow_signature: bool = False) -> ast.FunDecl:
        """`fun name [context] (params) -> ret body`.

        Inside a `class` a method may have no body, and then its parameters are
        *types* rather than binders. That is the one genuinely ambiguous
        production in the language: a bare identifier is both a legal parameter
        name and a legal type expression, so `fun combine(a, a) -> a` is two
        occurrences of one type variable while `fun combine(a, b) = a` is two
        binders. Nothing local decides it -- what follows the return type does.

        So the parameter list is parsed twice at worst: once as types, and, if
        that either fails or turns out to be followed by a body, again as
        patterns. The readings never mix. No body means every parameter is a
        type; a body means every parameter is a binder. A signature therefore
        cannot name its parameters and a definition cannot omit them, which is
        what makes the classification total rather than per-parameter.
        """
        span = self.expect("fun").span
        name = self.expect("IDENT", "a function name").text
        context = self.parse_context()
        if allow_signature:
            sig = self._try_signature(span, name, context)
            if sig is not None:
                return sig
        params = self.parse_param_list()
        ret = self.parse_type_expr() if self.eat("->") else None
        body = self.parse_fun_body()
        return ast.FunDecl(span, name, params, ret, body, context)

    def parse_context(self) -> list[ast.ClassPred]:
        """`[C a, D b]` -- a context, not a binder.

        The variables it mentions are the enclosing declaration's annotation
        variables (SPEC-DELTAS.md 13), which is why this constrains rather than
        introduces. It sits after the name so that a bare `fun[...]` stays free
        for a constrained lambda later.
        """
        if not self.at("["):
            return []
        self.advance()
        preds: list[ast.ClassPred] = []
        while True:
            tok = self.expect("CONID", "a class name")
            preds.append(ast.ClassPred(tok.span, tok.text, self.parse_atype()))
            if not self.eat(","):
                break
        self.expect("]")
        return preds

    def _try_signature(
        self, span: Span, name: str, context: list[ast.ClassPred]
    ) -> ast.FunDecl | None:
        """Read the parameter list as types. None means "this has a body"."""
        start = self.i
        try:
            params = self._signature_params()
        except ParseError:
            self.i = start
            return None
        if self.at("=", "{"):
            self.i = start
            return None
        if not self.eat("->"):
            raise ParseError(
                f"method '{name}' has no body, so it is a signature and must "
                f"state a return type",
                self.cur.span,
            )
        ret = self.parse_type_expr()
        if self.at("=", "{"):
            self.i = start
            return None
        # A parameter of a stated type with no name is an anonymous binder.
        return ast.FunDecl(
            span, name,
            [ast.PAnnot(t.span, ast.PWild(t.span), t) for t in params],
            ret, None, context,
        )

    def _signature_params(self) -> list[ast.TypeExpr]:
        self.expect("(")
        params: list[ast.TypeExpr] = []
        while not self.at(")"):
            params.append(self.parse_type_expr())
            if not self.eat(","):
                break
        self.expect(")")
        return params

    # -- classes and instances ---------------------------------------------

    def parse_class_decl(self) -> ast.ClassDecl:
        span = self.expect("class").span
        name = self.expect("CONID", "a class name").text
        param = self.expect("IDENT", "the class parameter").text
        supers: list[ast.ClassPred] = []
        if self.eat(":"):
            while True:
                tok = self.expect("CONID", "a superclass name")
                supers.append(ast.ClassPred(tok.span, tok.text, self.parse_atype()))
                if not self.eat(","):
                    break
        methods, families = self.parse_class_body(True, param)
        return ast.ClassDecl(span, name, param, supers, methods, families)

    def parse_instance_decl(self) -> ast.InstanceDecl:
        span = self.expect("instance").span
        context = self.parse_context()
        name = self.expect("CONID", "a class name").text
        # An `atype`, so a partially applied head parenthesizes:
        # `instance Functor (Either l)`.
        head = self.parse_atype()
        methods, families = self.parse_class_body(False, None)
        return ast.InstanceDecl(span, name, head, context, methods, families)

    def parse_class_body(
        self, is_class: bool, param: str | None
    ) -> tuple[list[ast.FunDecl], list]:
        """The `{ ... }` of a class or an instance: methods, and families.

        `type` inside the braces is an associated type family -- its
        declaration in a class, its definition in an instance. The two forms
        cannot be confused with each other or with a top-level `type`, since a
        declaration names the class parameter and a definition writes `=`.
        """
        self.expect("{")
        self.skip_newlines()
        methods: list[ast.FunDecl] = []
        families: list = []
        while not self.at("}"):
            if self.at("type"):
                families.append(self.parse_family(is_class, param))
            elif self.at("fun"):
                methods.append(self.parse_fun_decl(is_class))
            else:
                raise ParseError(
                    f"expected a method or a 'type', found {self._describe(self.cur)}",
                    self.cur.span,
                )
            self.end_of_statement()
        self.expect("}")
        return methods, families

    def parse_family(self, is_class: bool, param: str | None):
        span = self.expect("type").span
        name = self.expect("CONID", "a type family name").text
        if is_class:
            written = self.expect("IDENT", f"'{param}', the class parameter").text
            return ast.FamDecl(span, name, written)
        self.expect("=")
        return ast.FamBind(span, name, self.parse_type_expr())

    def parse_param_list(self) -> list[ast.Pattern]:
        self.expect("(")
        with self._with_no_record(False):
            params: list[ast.Pattern] = []
            while not self.at(")"):
                params.append(self.parse_pattern())
                if not self.eat(","):
                    break
            self.expect(")")
        return params

    def parse_fun_body(self) -> ast.Expr:
        if self.eat("="):
            with self._with_no_record(False):
                return self.parse_expr()
        if self.at("{"):
            return self.parse_block()
        raise ParseError(
            f"expected '=' or a block to begin a function body, found {self._describe(self.cur)}",
            self.cur.span,
        )

    def parse_binding_stmt(self) -> ast.Stmt:
        tok = self.advance()  # `let` or `var`
        with self._with_no_record(False):
            pat = self.parse_pattern()
            self.expect("=")
            value = self.parse_expr()
        cls = ast.SLet if tok.kind == "let" else ast.SVar
        return cls(tok.span, pat, value)

    def parse_stmt(self) -> ast.Stmt:
        if self.at("let", "var"):
            return self.parse_binding_stmt()
        if self.at("fun") and self.peek(1).kind == "IDENT":
            return ast.SFun(self.cur.span, self.parse_fun_decl())

        span = self.cur.span
        with self._with_no_record(False):
            expr = self.parse_expr()
            # Assignment (SPEC-DELTAS.md 1): the grammar puts these only in the
            # C-style `for` header, but blocks plainly need them too.
            if self.at("="):
                self.advance()
                if not isinstance(expr, (ast.EVar, ast.EField, ast.EIndex)):
                    raise ParseError(
                        "left side of an assignment must be a variable, "
                        "a record field, or an array element",
                        span,
                    )
                return ast.SAssign(span, expr, self.parse_expr())
        return ast.SExpr(span, expr)

    def parse_block(self) -> ast.EBlock:
        span = self.expect("{").span
        with self._with_no_record(False):
            stmts: list[ast.Stmt] = []
            self.skip_newlines()
            while not self.at("}"):
                if self.at("EOF"):
                    raise ParseError("unterminated block", span)
                stmts.append(self.parse_stmt())
                self.end_of_statement()
            self.expect("}")
        return ast.EBlock(span, stmts)

    # -- patterns -----------------------------------------------------------

    def parse_pattern(self) -> ast.Pattern:
        pat = self.parse_pattern_atom()
        if self.at(":"):
            self.advance()
            return ast.PAnnot(pat.span, pat, self.parse_type_expr())
        return pat

    def parse_pattern_atom(self) -> ast.Pattern:
        tok = self.cur
        if tok.kind == "IDENT":
            self.advance()
            return ast.PWild(tok.span) if tok.text == "_" else ast.PVar(tok.span, tok.text)
        if tok.kind in LITERAL_KINDS:
            self.advance()
            return ast.PLit(tok.span, LITERAL_TYPE[tok.kind], tok.value)
        if tok.kind in ("true", "false"):
            self.advance()
            return ast.PLit(tok.span, "Bool", tok.kind == "true")
        if tok.kind == "(":
            self.advance()
            elems = [self.parse_pattern()]
            while self.eat(","):
                elems.append(self.parse_pattern())
            self.expect(")")
            return elems[0] if len(elems) == 1 else ast.PTuple(tok.span, elems)
        if tok.kind == "CONID":
            return self.parse_con_pattern()
        raise ParseError(f"expected a pattern, found {self._describe(tok)}", tok.span)

    def parse_con_pattern(self) -> ast.Pattern:
        tok = self.expect("CONID")
        if self.at("{"):
            self.advance()
            self.skip_newlines()
            fields: list[tuple[str, ast.Pattern]] = []
            while not self.at("}"):
                name = self.expect("IDENT", "a field name")
                # Punning: `C { x }` binds `x` from field `x`.
                sub = self.parse_pattern() if self.eat("=") else ast.PVar(name.span, name.text)
                fields.append((name.text, sub))
                self.skip_newlines()
                if not self.eat(","):
                    break
                self.skip_newlines()
            self.expect("}")
            return ast.PRecord(tok.span, tok.text, fields)

        args: list[ast.Pattern] = []
        if self.at("("):
            self.advance()
            while not self.at(")"):
                args.append(self.parse_pattern())
                if not self.eat(","):
                    break
            self.expect(")")
        elif self.at(*PATTERN_START):
            # The juxtaposed form `Cons x xs` used to be accepted here. It is
            # not any more, and it fails as a pattern that simply ends early,
            # so say what happened rather than reporting the next token.
            raise ParseError(
                f"constructor pattern '{tok.text}' must parenthesize its "
                f"arguments: write '{tok.text}(...)'",
                self.cur.span,
            )
        return ast.PCon(tok.span, tok.text, args)

    # -- expressions --------------------------------------------------------

    def parse_expr(self) -> ast.Expr:
        expr = self.parse_binary(0)
        if self.at(":"):
            self.advance()
            return ast.EAnnot(expr.span, expr, self.parse_type_expr())
        return expr

    def parse_binary(self, level: int) -> ast.Expr:
        if level >= len(PRECEDENCE):
            return self.parse_unary()
        left = self.parse_binary(level + 1)
        while self.cur.kind in PRECEDENCE[level]:
            op = self.advance().kind
            right = self.parse_binary(level + 1)
            left = ast.EBinary(left.span, op, left, right)
        return left

    def parse_unary(self) -> ast.Expr:
        if self.at("!", "-"):
            tok = self.advance()
            return ast.EUnary(tok.span, tok.kind, self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self) -> ast.Expr:
        expr = self.parse_atom()
        while True:
            if self.at("("):
                self.advance()
                with self._with_no_record(False):
                    args: list[ast.Expr] = []
                    while not self.at(")"):
                        args.append(self.parse_expr())
                        if not self.eat(","):
                            break
                    self.expect(")")
                expr = ast.ECall(expr.span, expr, args)
            elif self.at("["):
                self.advance()
                with self._with_no_record(False):
                    index = self.parse_expr()
                self.expect("]")
                expr = ast.EIndex(expr.span, expr, index)
            elif self.at(".") and self.peek(1).kind == "IDENT":
                self.advance()
                expr = ast.EField(expr.span, expr, self.advance().text)
            else:
                return expr

    def parse_atom(self) -> ast.Expr:
        tok = self.cur
        kind = tok.kind

        if kind in LITERAL_KINDS:
            self.advance()
            return ast.ELit(tok.span, LITERAL_TYPE[kind], tok.value)
        if kind in ("true", "false"):
            self.advance()
            return ast.ELit(tok.span, "Bool", kind == "true")
        if kind == "IDENT":
            self.advance()
            return ast.EVar(tok.span, tok.text)
        if kind == "CONID":
            return self.parse_conid_atom()
        if kind == "(":
            self.advance()
            if self.at(")"):
                self.advance()
                return ast.EUnit(tok.span)
            with self._with_no_record(False):
                elems = [self.parse_expr()]
                while self.eat(","):
                    elems.append(self.parse_expr())
                self.expect(")")
            return elems[0] if len(elems) == 1 else ast.ETuple(tok.span, elems)
        if kind == "[":
            self.advance()
            with self._with_no_record(False):
                elems: list[ast.Expr] = []
                while not self.at("]"):
                    elems.append(self.parse_expr())
                    if not self.eat(","):
                        break
                self.expect("]")
            return ast.EArray(tok.span, elems)
        if kind == "{":
            return self.parse_block()
        if kind == "fun":
            self.advance()
            params = self.parse_param_list()
            ret = self.parse_type_expr() if self.eat("->") else None
            return ast.ELambda(tok.span, params, ret, self.parse_fun_body())
        if kind == "if":
            return self.parse_if()
        if kind == "while":
            self.advance()
            cond = self.parse_scrutinee()
            return ast.EWhile(tok.span, cond, self.parse_block())
        if kind == "for":
            return self.parse_for()
        if kind == "loop":
            self.advance()
            return ast.ELoop(tok.span, self.parse_block())
        if kind == "match":
            return self.parse_match()
        if kind == "return":
            self.advance()
            return ast.EReturn(tok.span, self.parse_optional_value())
        if kind == "break":
            self.advance()
            return ast.EBreak(tok.span, self.parse_optional_value())
        if kind == "continue":
            self.advance()
            return ast.EContinue(tok.span)

        raise ParseError(f"expected an expression, found {self._describe(tok)}", tok.span)

    def parse_conid_atom(self) -> ast.Expr:
        """A CONID atom is a constructor, a record construction, or -- when
        followed by `.` and a lowercase name -- a qualified variable."""
        tok = self.expect("CONID")
        parts = [tok.text]
        while self.at(".") and self.peek(1).kind == "CONID":
            self.advance()
            parts.append(self.advance().text)
        if self.at(".") and self.peek(1).kind == "IDENT":
            self.advance()
            parts.append(self.advance().text)
            return ast.EVar(tok.span, ".".join(parts))

        name = ".".join(parts)
        if self.at("{") and not self.no_record:
            self.advance()
            self.skip_newlines()
            fields: list[tuple[str, ast.Expr]] = []
            with self._with_no_record(False):
                while not self.at("}"):
                    fname = self.expect("IDENT", "a field name")
                    self.expect("=")
                    fields.append((fname.text, self.parse_expr()))
                    self.skip_newlines()
                    if not self.eat(","):
                        break
                    self.skip_newlines()
            self.expect("}")
            return ast.ERecord(tok.span, name, fields)
        return ast.ECon(tok.span, name)

    def parse_optional_value(self) -> ast.Expr | None:
        if self.at(*EXPR_START):
            return self.parse_expr()
        return None

    def parse_scrutinee(self) -> ast.Expr:
        with self._with_no_record(True):
            return self.parse_expr()

    def parse_if(self) -> ast.Expr:
        span = self.expect("if").span
        cond = self.parse_scrutinee()
        then = self.parse_block()
        otherwise: ast.Expr | None = None
        if self.eat("else"):
            otherwise = self.parse_if() if self.at("if") else self.parse_block()
        return ast.EIf(span, cond, then, otherwise)

    def parse_for(self) -> ast.Expr:
        span = self.expect("for").span
        # `for pat in expr` and the C-style header are told apart by trying the
        # first and backtracking. Only `in` distinguishes them.
        saved = self.i
        try:
            with self._with_no_record(False):
                pat = self.parse_pattern()
            if self.at("in"):
                self.advance()
                iterable = self.parse_scrutinee()
                return ast.EForIn(span, pat, iterable, self.parse_block())
        except ParseError:
            pass
        self.i = saved

        with self._with_no_record(False):
            init = None if self.at("NEWLINE") else self.parse_stmt()
            self.expect("NEWLINE", "';' after the loop initializer")
            cond = self.parse_expr()
            self.expect("NEWLINE", "';' after the loop condition")
            step = None if self.at("{") else self.parse_stmt()
        return ast.EForC(span, init, cond, step, self.parse_block())

    def parse_match(self) -> ast.Expr:
        span = self.expect("match").span
        scrutinee = self.parse_scrutinee()
        self.expect("{")
        with self._with_no_record(False):
            arms: list[ast.MatchArm] = []
            self.skip_newlines()
            while not self.at("}"):
                if self.at("EOF"):
                    raise ParseError("unterminated match expression", span)
                arms.append(self.parse_match_arm())
                # A `|` here begins the next arm: the newline before it was
                # dropped by section 2.4, so it is the only separator left.
                if self.at("NEWLINE"):
                    self.skip_newlines()
                elif not self.at("}", "|"):
                    raise ParseError(
                        f"expected the next match arm, found {self._describe(self.cur)}",
                        self.cur.span,
                    )
            self.expect("}")
        if not arms:
            raise ParseError("a match expression needs at least one arm", span)
        return ast.EMatch(span, scrutinee, arms)

    def parse_match_arm(self) -> ast.MatchArm:
        # A leading `|` is allowed. Because a newline before `|` is always
        # dropped (section 2.4), a `|` before the arm's `->` continues the
        # pattern list while a `|` after a finished arm begins the next one --
        # so the two readings never collide.
        self.eat("|")
        span = self.cur.span
        patterns = [self.parse_pattern()]
        while self.eat("|"):
            self.skip_newlines()
            patterns.append(self.parse_pattern())
        self.expect("->", "'->' after the arm's patterns")
        return ast.MatchArm(span, patterns, self.parse_expr())


def collect_tycons(tokens: list[Token]) -> frozenset[str]:
    """Pre-pass for section 7: every `type CONID` name declared in the file."""
    names = set(BUILTIN_TYCONS)
    for i, tok in enumerate(tokens):
        if tok.kind == "type" and tokens[i + 1].kind == "CONID":
            names.add(tokens[i + 1].text)
    return frozenset(names)


def parse(src: str) -> ast.Program:
    tokens = tokenize(src)
    return Parser(tokens, collect_tycons(tokens)).parse_program()
