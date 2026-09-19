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

from . import ast, prelude
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
        "IDENT", "CONID", "(", "[", "{", "-", "!",
        "fun", "if", "match", "while", "for", "loop", "return", "break", "continue",
        "do",
    }
)

PATTERN_START = frozenset(
    set(LITERAL_KINDS) | {"IDENT", "CONID", "("}
)

# Binary operator precedence, loosest first (section 3.5).
PRECEDENCE: list[tuple[str, ...]] = [
    ("||",),
    ("&&",),
    ("==", "!="),
    ("<", "<=", ">", ">="),
    ("+", "-"),
    ("*", "/", "%"),
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
            elif self.at("foreign"):
                decl = self.parse_foreign_decl()
                decls.append(ast.SFun(decl.span, decl)
                             if isinstance(decl, ast.FunDecl) else decl)
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
        exports: list[ast.ExportItem] | None = None
        if self.at("("):
            exports = []
            self.advance()
            while not self.at(")"):
                exports.append(self.parse_export_item())
                if not self.eat(","):
                    break
            self.expect(")")
        return ast.ModuleHeader(span, name, exports)

    def parse_export_item(self) -> ast.ExportItem:
        # `module M` re-exports everything in scope under that qualification
        # (section 3.1). It is the one export form that names no entity.
        if self.at("module"):
            span = self.advance().span
            return ast.ExportItem(span, self.parse_modname(), "module")
        tok = self.advance()
        if tok.kind not in ("IDENT", "CONID"):
            raise ParseError(f"expected an export name, found {self._describe(tok)}", tok.span)
        subs: list[str] | None = None
        if tok.kind == "CONID" and self.at("("):
            self.advance()
            subs = []
            while not self.at(")"):
                subs.append(self.advance().text)
                if not self.eat(","):
                    break
            self.expect(")")
        return ast.ExportItem(tok.span, tok.text, "name", subs)

    def parse_modname(self) -> str:
        parts = [self.expect("CONID", "a module name").text]
        while self.at(".") and self.peek(1).kind == "CONID":
            self.advance()
            parts.append(self.advance().text)
        return ".".join(parts)

    def parse_import(self) -> ast.ImportDecl:
        span = self.expect("import").span
        name = self.parse_modname()
        alias = items = hiding = None
        if self.eat("as"):
            alias = self.expect("CONID", "a module alias").text
        # An alias and a selective list are independent: `import M as S (f)`
        # names the module and narrows what it brings. Aliased imports are
        # qualified-only; unaliased imports bring both spellings.
        if self.at("hiding"):
            self.advance()
            hiding = self.parse_paren_name_list()
        elif self.at("("):
            items = self.parse_paren_name_list()
        return ast.ImportDecl(span, name, alias, items, hiding, alias is not None)

    def parse_paren_name_list(self) -> list[ast.ExportItem]:
        self.expect("(")
        names: list[ast.ExportItem] = []
        while not self.at(")"):
            item = self.parse_export_item()
            if item.kind == "module":
                raise ParseError(
                    "'module' may appear in an export list, not in an import's "
                    "name list: a module re-exports, it does not import",
                    item.span)
            names.append(item)
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

    def parse_con_context(self) -> tuple[list[str], list[ast.ClassPred]]:
        """`[s, Error e]` -- the variables an existential constructor hides.

        Read like a `fun`'s context, entry by entry as a type expression, with
        one more form and one fewer: a bare variable binds one unconstrained,
        and an equality is refused, since equality givens are what GADTs are
        made of (SPEC-DELTAS 68).
        """
        if not self.at("["):
            return [], []
        self.advance()
        binders: list[str] = []
        preds: list[ast.ClassPred] = []
        while True:
            start = self.cur.span
            written = self.parse_type_expr()
            if self.at("~"):
                raise ParseError(
                    "a constructor's bracket may not state an equality", start)
            if isinstance(written, ast.TEVar):
                binders.append(written.name)
            elif isinstance(written, ast.TECon) and len(written.args) == 1:
                preds.append(ast.ClassPred(start, written.name, written.args[0]))
            else:
                raise ParseError(
                    "a constructor's bracket holds type variables, as in 's', "
                    "and classes applied to one type, as in 'Error e'",
                    start,
                )
            if not self.eat(",") or self.at("]"):
                break
        self.expect("]")
        return binders, preds

    def parse_con_decl(self) -> ast.ConDecl:
        tok = self.expect("CONID", "a constructor name")
        binders, context = self.parse_con_context()
        if self.at("{"):
            return ast.ConDecl(tok.span, tok.text, [], self.parse_record_payload(),
                               binders, context)
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
        return ast.ConDecl(tok.span, tok.text, args, None, binders, context)

    def field_separator(self) -> bool:
        """Consume what separates two fields in braces, or answer False.

        A comma, or a significant newline, or both (SPEC-DELTAS 64) -- in a
        declaration, a construction and a pattern alike, since `record-sep` is
        one production. The newline rule has already dropped every line break
        that continues an expression, so one that survives to here ended the
        field. What follows it must then be a field or the closing brace, and
        the complaint says why the parser thought so: in `P { x = a` / `-b }`
        the line break made `-b` a new field, not a continuation of `a`.
        """
        if self.eat(","):
            self.skip_newlines()
            return True
        if not self.at("NEWLINE"):
            return False
        self.skip_newlines()
        if not self.at("IDENT", "..", "}"):
            raise ParseError(
                "a line break separates fields here, so this line must begin "
                f"a field; found {self._describe(self.cur)}",
                self.cur.span,
            )
        return True

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
            if not self.field_separator():
                break
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
            # A qualified type constructor: `S.Node`, `Data.Array.Foo`. The
            # dotted chain is one name, resolved as one (delta 43).
            parts = [tok.text]
            while self.at(".") and self.peek(1).kind == "CONID":
                self.advance()
                parts.append(self.advance().text)
            return ast.TECon(tok.span, ".".join(parts), [])
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
                if self.at(")"):
                    if len(elems) == 1:
                        raise ParseError("a singleton tuple '(x,)' is not supported", self.cur.span)
                    break
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

    def parse_foreign_decl(self) -> ast.ForeignDecl | ast.FunDecl:
        """`foreign "symbol" fun name(params) -> ret`, with or without a body.

        Without one it is a declaration: C defines the symbol and Turkey calls
        it. With one it is a *definition*: Turkey defines the symbol and C
        calls it (PROPOSALS.md item 9, SPEC-DELTAS 74). The definition is an
        ordinary `FunDecl` that also carries the symbol, because everything
        from here to the backend already knows what to do with a function, and
        what the symbol adds -- a C-callable entry point that nothing may drop
        -- is the backend's business alone.

        The C symbol is always written, even when it is the Turkey name spelled
        the same way. Which of the two names is which is then never something a
        reader has to work out, and the declaration that says
        `foreign "__error" fun errnoLocation() -> Ptr` reads no differently from
        the one that says `foreign "read" fun read(...)`.

        There is no context, and a body only for a definition. A return type
        is required either way -- a C
        function that returns nothing writes `-> Unit`, because "the signature
        is stated in full" is the only property that makes the declaration
        worth trusting.
        """
        span = self.expect("foreign").span
        tok = self.cur
        if tok.kind != "STRING":
            raise ParseError(
                f"expected the C symbol as a string, found {self._describe(tok)}",
                tok.span,
            )
        self.advance()
        assert isinstance(tok.value, str)
        symbol = tok.value
        if not symbol:
            raise ParseError("a foreign declaration needs a C symbol", tok.span)
        self.expect("fun")
        name = self.expect("IDENT", "a function name").text
        params = self.parse_foreign_params()
        if not self.eat("->"):
            raise ParseError(
                f"foreign '{name}' has no body, so it must state a return "
                f"type; a C function that returns nothing writes '-> Unit'",
                self.cur.span,
            )
        ret = self.parse_type_expr()
        if self.at("{", "="):
            # Every parameter of a definition is a binder, and a name is what
            # binds one; an unnamed parameter could never be read.
            for param in params:
                assert isinstance(param, ast.PAnnot)
                if not isinstance(param.pat, ast.PVar):
                    raise ParseError(
                        f"foreign '{name}' has a body, so each parameter needs "
                        f"a name: write 'x : T'",
                        param.span,
                    )
            body = self.parse_fun_body()
            return ast.FunDecl(span, name, params, ret, body, symbol=symbol,
                               monomorphic=True)
        return ast.ForeignDecl(span, name, symbol, params, ret)

    def parse_foreign_params(self) -> list[ast.Pattern]:
        """`(fd : Int, buf : Ptr)`, or `(Int, Ptr)`, or a mix.

        Stored as `FunDecl` stores a signature's parameters -- an annotated
        pattern -- so that everything downstream reads a parameter's type off
        one shape whether it was named or not.
        """
        self.expect("(")
        params: list[ast.Pattern] = []
        while not self.at(")"):
            start = self.cur
            if start.kind == "IDENT" and self.peek(1).kind == ":":
                self.advance()
                self.advance()
                ty = self.parse_type_expr()
                binder = (ast.PWild(start.span) if start.text == "_"
                          else ast.PVar(start.span, start.text))
                params.append(ast.PAnnot(start.span, binder, ty))
            else:
                ty = self.parse_type_expr()
                params.append(ast.PAnnot(ty.span, ast.PWild(ty.span), ty))
            if not self.eat(","):
                break
        self.expect(")")
        return params

    def parse_context(self) -> list[ast.ClassPred | ast.EqPred]:
        """`[C a, Item c ~ Op]` -- a context, not a binder.

        The variables it mentions are the enclosing declaration's annotation
        variables (SPEC-DELTAS.md 13), which is why this constrains rather than
        introduces. It sits after the name so that a bare `fun[...]` stays free
        for a constrained lambda later.

        Each entry is read as a *type expression* first and classified after,
        because nothing shorter can tell the two forms apart: `Item c ~ Op` and
        `Show (Elem c)` begin identically, and the `~` that separates them is
        two atypes away. So the class-predicate reading is recovered by
        destructuring what came back, which also enforces the arity rule a
        `CONID atype` production used to enforce by shape.
        """
        if not self.at("["):
            return []
        self.advance()
        preds: list[ast.ClassPred | ast.EqPred] = []
        while True:
            start = self.cur.span
            written = self.parse_type_expr()
            if self.eat("~"):
                preds.append(ast.EqPred(start, written, self.parse_type_expr()))
            elif isinstance(written, ast.TECon) and len(written.args) == 1:
                preds.append(ast.ClassPred(start, written.name, written.args[0]))
            else:
                raise ParseError(
                    "a context entry is a class applied to one type, as in "
                    "'Ord a', or an equality, as in 'Item c ~ Op'",
                    start,
                )
            if not self.eat(",") or self.at("]"):
                break
        self.expect("]")
        return preds

    def _try_signature(
        self, span: Span, name: str, context: list[ast.ClassPred | ast.EqPred]
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
                tok = self.cur
                class_name = self.parse_modname()
                supers.append(ast.ClassPred(tok.span, class_name, self.parse_atype()))
                if not self.eat(","):
                    break
                self.skip_newlines()
                if self.at("{"):
                    break
        self.skip_newlines()
        methods, families = self.parse_class_body(True, param)
        return ast.ClassDecl(span, name, param, supers, methods, families)

    def parse_instance_decl(self) -> ast.InstanceDecl:
        span = self.expect("instance").span
        name = self.parse_modname()
        # An `atype`, so a partially applied head parenthesizes:
        # `instance Functor (Either l)`.
        head = self.parse_atype()
        context = self.parse_instance_context()
        self.skip_newlines()
        methods, families = self.parse_class_body(False, None)
        return ast.InstanceDecl(span, name, head, context, methods, families)

    def parse_instance_context(self) -> list[ast.ClassPred | ast.EqPred]:
        """Parse the post-head instance context: ``C T : P a, Q b``.

        Unlike function contexts, instance constraints are attached after the
        instance head.  The old bracketed spelling is deliberately not
        accepted: after ``instance`` the parser expects the class name.
        """
        if not self.eat(":"):
            return []
        return self.parse_context_entries("{")

    def parse_context_entries(self, end: str) -> list[ast.ClassPred | ast.EqPred]:
        preds: list[ast.ClassPred | ast.EqPred] = []
        while True:
            start = self.cur.span
            written = self.parse_type_expr()
            if self.eat("~"):
                preds.append(ast.EqPred(start, written, self.parse_type_expr()))
            elif isinstance(written, ast.TECon) and len(written.args) == 1:
                preds.append(ast.ClassPred(start, written.name, written.args[0]))
            else:
                raise ParseError(
                    "a context entry is a class applied to one type, as in "
                    "'Ord a', or an equality, as in 'Item c ~ Op'",
                    start,
                )
            if not self.eat(","):
                break
            self.skip_newlines()
            if self.at(end):
                break
        return preds

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
                if isinstance(expr, ast.EProject):
                    raise ParseError("numeric projections are read-only", span)
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
        if tok.kind == "(":
            self.advance()
            elems = [self.parse_pattern()]
            while self.eat(","):
                if self.at(")"):
                    if len(elems) == 1:
                        raise ParseError("a singleton tuple '(x,)' is not supported", self.cur.span)
                    break
                elems.append(self.parse_pattern())
            self.expect(")")
            return elems[0] if len(elems) == 1 else ast.PTuple(tok.span, elems)
        if tok.kind == "CONID":
            return self.parse_con_pattern()
        raise ParseError(f"expected a pattern, found {self._describe(tok)}", tok.span)

    def parse_con_pattern(self) -> ast.Pattern:
        tok = self.expect("CONID")
        # Qualified as in an expression, `G.Point(x, y)`, and resolved the
        # same way (delta 43): a constructor imported only under a qualifier
        # could not otherwise be matched at all.
        parts = [tok.text]
        while self.at(".") and self.peek(1).kind == "CONID":
            self.advance()
            parts.append(self.advance().text)
        con = ".".join(parts)
        if self.at("{"):
            self.advance()
            self.skip_newlines()
            fields: list[tuple[str, ast.Pattern]] = []
            rest = False
            while not self.at("}"):
                if self.eat(".."):
                    # The rest, deliberately unnamed. Last, or it would not be
                    # the rest.
                    rest = True
                    self.field_separator()
                    if not self.at("}"):
                        raise ParseError(
                            "'..' must be the last thing in a record pattern",
                            self.cur.span,
                        )
                    break
                name = self.expect("IDENT", "a field name")
                # Punning: `C { x }` binds `x` from field `x`.
                sub = self.parse_pattern() if self.eat("=") else ast.PVar(name.span, name.text)
                fields.append((name.text, sub))
                if not self.field_separator():
                    break
            self.expect("}")
            return ast.PRecord(tok.span, con, fields, rest)

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
                f"constructor pattern '{con}' must parenthesize its "
                f"arguments: write '{con}(...)'",
                self.cur.span,
            )
        return ast.PCon(tok.span, con, args)

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
            method = prelude.BINARY_METHOD.get(op)
            fn = ast.EVar(left.span, method, method=True) if method else None
            left = ast.EBinary(left.span, op, left, right, fn)
        return left

    def parse_unary(self) -> ast.Expr:
        if self.at("!", "-"):
            tok = self.advance()
            method = prelude.UNARY_METHOD.get(tok.kind)
            fn = ast.EVar(tok.span, method, method=True) if method else None
            return ast.EUnary(tok.span, tok.kind, self.parse_unary(), fn)
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
            elif self.at(".") and self.peek(1).kind in ("IDENT", "INT"):
                self.advance()
                member = self.advance()
                if member.kind == "INT":
                    expr = ast.EProject(expr.span, expr, int(member.value))
                else:
                    expr = ast.EField(expr.span, expr, member.text)
            elif self.at("?"):
                # A suffix like the other three, so it binds tighter than any
                # operator and chains with them: `f(x)?.field?` reads as it
                # looks. `bind` is marked, so a program's own `bind` is not what
                # a `?` means -- see `turkey/resolve.py`.
                self.advance()
                fn = ast.EVar(expr.span, prelude.MONAD_BIND, method=True)
                expr = ast.EQuestion(expr.span, expr, fn)
            else:
                return expr

    def parse_atom(self) -> ast.Expr:
        tok = self.cur
        kind = tok.kind

        if kind in LITERAL_KINDS:
            self.advance()
            return ast.ELit(tok.span, LITERAL_TYPE[kind], tok.value)
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
                    if self.at(")"):
                        if len(elems) == 1:
                            raise ParseError("a singleton tuple '(x,)' is not supported", self.cur.span)
                        break
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
        if kind == "do":
            self.advance()
            return ast.EDo(tok.span, self.parse_block())
        if kind == "fun":
            self.advance()
            params = self.parse_param_list()
            ret = self.parse_type_expr() if self.eat("->") else None
            return ast.ELambda(tok.span, params, ret, self.parse_fun_body())
        if kind == "if":
            return self.parse_if()
        if kind == "while":
            self.advance()
            if self.at("let", "var"):
                # `while let p = e { A }` is `loop { match e { p -> A, _ ->
                # break } }`, so a `continue` in `A` evaluates `e` again.
                pat, value, body = self.parse_binding_condition()
                span = tok.span
                return ast.ELoop(span, ast.EBlock(span, [ast.SExpr(span, ast.EMatch(
                    span, value, [
                        ast.MatchArm(span, [pat], body),
                        ast.MatchArm(span, [ast.PWild(span)], ast.EBreak(span, None)),
                    ]))]))
            cond = self.parse_scrutinee()
            self.refuse_chained_condition()
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
                    # Punning: `C { x }` is `C { x = x }`, as in patterns.
                    value = (self.parse_expr() if self.eat("=")
                             else ast.EVar(fname.span, fname.text))
                    fields.append((fname.text, value))
                    if not self.field_separator():
                        break
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
        if self.at("let", "var"):
            return self.parse_if_let(span)
        cond = self.parse_scrutinee()
        self.refuse_chained_condition()
        then = self.parse_block()
        otherwise: ast.Expr | None = None
        if self.eat("else"):
            otherwise = self.parse_if() if self.at("if") else self.parse_block()
        return ast.EIf(span, cond, then, otherwise)

    def parse_if_let(self, span: Span) -> ast.Expr:
        """`if let p = e { A } else { B }` is `match e { p -> A, _ -> B }`.

        Desugared here rather than carried as a node (SPEC-DELTAS 63): a
        `match` with a wildcard arm is exactly its meaning, so resolution,
        inference, exhaustiveness and both lowerings need nothing new, and the
        binder scopes over the then-block and nothing else because that is what
        an arm's binder already does. Without an `else` the other arm is `()`.
        """
        pat, value, then = self.parse_binding_condition()
        otherwise: ast.Expr = ast.EUnit(span)
        if self.eat("else"):
            otherwise = self.parse_if() if self.at("if") else self.parse_block()
        return ast.EMatch(span, value, [
            ast.MatchArm(span, [pat], then),
            ast.MatchArm(span, [ast.PWild(span)], otherwise),
        ])

    def parse_binding_condition(
        self,
    ) -> tuple[ast.Pattern, ast.Expr, ast.Expr]:
        """`let p = e { body }` or `var p = e { body }`, after `if` or `while`.

        `var` makes the binders reassignable the way `var` does anywhere else:
        the block is prefixed with `var x = x` for each one, in the order the
        pattern binds them.
        """
        span = self.cur.span
        mutable = self.advance().kind == "var"
        with self._with_no_record(False):
            pat = self.parse_pattern()
            self.expect("=")
        value = self.parse_scrutinee()
        self.refuse_chained_condition()
        body = self.parse_block()
        if mutable:
            rebinds: list[ast.Stmt] = [
                ast.SVar(span, ast.PVar(span, name), ast.EVar(span, name))
                for name in _binders(pat)
            ]
            body = ast.EBlock(body.span, rebinds + body.stmts)
        return pat, value, body

    def refuse_chained_condition(self) -> None:
        """The condition of an `if` is a list in the grammar (SPEC-DELTAS 63),
        so that `if let Some(x) = a, x > 0` can be added without a retrofit.
        Only the one-element list is implemented."""
        if self.at(","):
            raise ParseError(
                "a condition cannot be chained with ',' yet; nest the 'if' instead",
                self.cur.span,
            )

    def parse_for(self) -> ast.Expr:
        span = self.expect("for").span
        # `for pat in expr` and the C-style header are told apart by trying the
        # first and backtracking. Only `in` distinguishes them.
        saved = self.i
        # `in` is the only thing that tells the two apart, so it is also the
        # point of no return. Backtracking past it would re-read the loop as
        # the C-style form and report whatever *that* reading fails on -- an
        # error about a missing `;` in a loop that never had one, hiding the
        # real complaint about the body. So a failure before `in` backtracks
        # and a failure after it is the answer.
        committed = False
        try:
            with self._with_no_record(False):
                pat = self.parse_pattern()
            if self.at("in"):
                self.advance()
                committed = True
                iterable = self.parse_scrutinee()
                body = self.parse_block()
                return ast.EForIn(
                    span, pat, iterable, body,
                    ast.EVar(iterable.span, prelude.ITER_ITER, method=True),
                    ast.EVar(iterable.span, prelude.ITER_NEXT, method=True),
                )
        except ParseError:
            if committed:
                raise
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


def _binders(pat: ast.Pattern) -> list[str]:
    """The names a pattern binds, in the order it binds them."""
    if isinstance(pat, ast.PVar):
        return [pat.name]
    if isinstance(pat, ast.PAnnot):
        return _binders(pat.pat)
    if isinstance(pat, ast.PCon):
        return [n for arg in pat.args for n in _binders(arg)]
    if isinstance(pat, ast.PRecord):
        return [n for _, sub in pat.fields for n in _binders(sub)]
    if isinstance(pat, ast.PTuple):
        return [n for elem in pat.elems for n in _binders(elem)]
    return []


def parse(src: str, known: frozenset[str] = frozenset(),
          file: str | None = None) -> ast.Program:
    """Parse one module. `known` is the type constructor names its imports
    already put in scope: section 7's alias-vs-data question is decided by a
    token pre-pass over this file, and a name declared in *another* file is
    invisible to it (M11a)."""
    tokens = tokenize(src, file)
    return Parser(tokens, collect_tycons(tokens) | known).parse_program()
