# Spec Deltas

`design.md` is the spec. Building the prototype against it surfaced internal
contradictions, unwritable-in-the-grammar gaps, and places where a rule
("type-directed", "implicitly universally quantified") does not by itself
determine an algorithm. This document records each place the implementation
knowingly departs from `design.md` as written, the section it amends, and
why. It exists so the spec can be corrected or extended later without
re-deriving these decisions from the code.

## A. Corrections (design.md contradicts itself)

### 1. `stmt` gains the three assignment forms from §3.7

§3.4 defines `stmt ::= "let" ... | "var" ... | "fun" ... | expr`, with no
assignment alternative. Yet §10's worked example, which the spec presents as
correct code, assigns inside a block body: `s.top = s.top + 1` inside
`fun push(...) -> Unit { ... }`. Assignment (`IDENT "=" expr`,
`expr-postfix "." IDENT "=" expr`, `expr-postfix "[" expr "]" "=" expr`, §3.7)
is otherwise only reachable via `stmt-no-block` (§3.5), which itself is only
reachable from the C-style `for` header — so as written, no block can ever
contain an assignment statement. The implementation adds the three §3.7
assignment forms directly to `stmt`, so blocks can assign.

### 2. `type-expr` loses the arrow production

§3.2 gives `type-expr ::= btype ("->" btype)*`, i.e. `->` is a general
type-expr operator. This conflicts with `fun-ret ::= "->" type-expr` (§3.3):
under the §3.2 rule, `fun f(x) -> Int -> Bool { ... }` is ambiguous between a
return type of `Int -> Bool` and a return type of `Int` followed by a
dangling `-> Bool`. It also conflicts with decision 16 (§11) and the type
syntax row in §1's decision table, both of which say function types are
written `fun(...) -> τ`, not with a bare infix arrow. The implementation
drops the arrow production from `type-expr`; the only place `->` appears in
a type is inside `"fun" "(" type-list? ")" "->" type-expr` and in `fun-ret`.
Consequently, §10's stated inferred types are rewritten in `fun(...)` form:
`new : fun(Int) -> Stack a`, `push : fun(Stack a, a) -> Unit`,
`pop : fun(Stack a) -> a`, `drain : fun(Stack a) -> Array a` (§10 as written
gives `new : Int -> Stack a`, etc.).

### 3. `expr-atom` gains `()` as the Unit value

§8.1 lists `Unit` with values written `()`. But the tuple/grouping
production in §3.5, `"(" expr ("," expr)* ")"`, requires at least one
`expr`, so `()` cannot be parsed by the stated grammar — the value the spec
itself uses is unwritable. The implementation adds `"(" ")"` to `expr-atom`
as the literal Unit value.

### 4. Type application collapses to one production

§3.2 gives both `btype ::= atype+` and `atype ::= ... | CONID atype+`. A
type like `Array a b` can then parse two ways: as a single `atype`
(`CONID atype+` matching `Array a b` directly), or as a `btype` of three
atypes (`Array`, `a`, `b`). The implementation keeps `btype ::= atype+` as
the sole place application happens and drops `CONID atype+` from `atype`,
so `atype` is only `IDENT | CONID | "(" ... ")" | "fun" "(" ... ")" "->" ...`.

### 5. `error : fun(String) -> a` becomes a real builtin

§10 uses `error("empty stack")` and its Notes section states
`error : String -> a is a polymorphic primitive (always diverges/panics)`,
but this is only a note attached to the worked example — §8 (Built-in Types
and Modules) never lists `error` among the language's builtins, so a
conforming reader of §8 alone would not know it exists. The implementation
seeds `error` into the initial environment with type `fun(String) -> a`
(arrow form per delta 2), matching what §10 actually needs.

### 6. `[]` lexes as two tokens, not one

§3.5's `expr-atom` lists `"[]"` as its own alternative alongside
`"[" expr ("," expr)* "]"`, which reads as a single `"[]"` token. But §2.1's
punctuation list only defines `[` and `]` as separate tokens — there is no
`"[]"` token in the lexical grammar. The implementation lexes `[` and `]`
separately in all cases; the parser recognizes the empty-array literal as
the two-token sequence `"[" "]"` with no elements between.

## B. Design decisions taken

These resolve places where §4.5 and other sections state a rule without an
algorithm, or where the project owner chose one reading over another.

### 7. Field access is a `HasField` predicate

§4.5 says `r.f` is well-typed only when the static type of `r` is a
single-variant record or `Array a`, and decision 27 (§11) calls field access
"type-directed". Under ordinary Hindley-Milner inference, though, the
expression `r.f` is typically visited before `r`'s type is fully resolved
(`r` may still be an unbound unification variable at that point), so
"type-directed" does not by itself say what to do.

This entry originally recorded the obvious answer -- prune `typeof(r)` through
the substitution and demand a concrete record type on the spot -- along with
its documented consequence, that inference became order-sensitive:

```
fun f(a) { let n = a.length   let x = a[0]   n + x }   -- rejected
fun f(a) { let x = a[0]       let n = a.length   n + x }   -- accepted
```

**That is no longer the implementation.** `r.f` now emits the qualified-type
predicate `HasField "f" typeof(r) a` and decides nothing; the solver settles it
whenever the receiver becomes known, which may be long after the access was
visited. Both orderings above are accepted, and a predicate that is still
unresolved when its binding generalizes travels in the scheme:

```
fun get(r) = r.n        -- get : [HasField "n" a b] fun(a) -> b
```

so a function may be polymorphic in the record it reads from. `HasField` is
the `r \ l` predicate of Gaster & Jones, *A Polymorphic Type System for
Extensible Records and Variants* (NOTTCS-TR-96-3, 1996), taken without the
rows: records stay nominal, so entailment is a declaration lookup. It is a
built-in predicate, not a class -- there is no way to write an instance of it.

Two consequences worth stating. A function like `bf.tl`'s `inc` becomes
structurally polymorphic over any record carrying the fields it reads, in a
language whose records are otherwise nominal. And field names still need not
be globally unique: the receiver, not the field name, is what resolves the
access -- it is simply allowed to arrive later. What remains an error is a
demand that no scheme can carry, because nothing else mentions its receiver:

```
fun main() {
    var box = []
    print(Int.toString(box[0].n))   -- add a type annotation
}
```

### 8. Array bounds are checked against `length`, not `capacity`

§8.3 says `arr[i]` and `arr[i] = e` perform a "runtime bounds check" without
saying what the bound is, and separately exposes both `.length` (current
element count) and `.capacity` (allocated storage) as compiler-known fields.
The implementation checks both reads and writes against `.length`; indices
in `[capacity, length)` still panic even though storage exists.

### 9. No modules in v0

§9 describes a full Haskell-style module system (headers, qualified/
selective/hiding imports, cross-module name resolution). The prototype
implements a single file only: no `module`/`import` semantics. `Data.Array`
(`new`, `push`, `pop`) and `error` are seeded directly into the initial
global environment as if always in scope. `module` and `import` are still
lexed and parsed per §3.1/§9.2 so the surface syntax isn't lost, but both are
rejected at a later stage with a "not supported in v0" error.

### 10. Record update is dropped

Decision 29 (§11) says `r { f = e }` is a functional record update. It never
appears anywhere in the §3 grammar (`con-arg`, `field-init`, and
`expr-postfix` have no update production), adding it would collide with
block syntax in condition position (`if r { ... }` — see delta 12), and
since single-variant records are already mutable (§4.5), in-place field
assignment (`r.f = e`, §3.7) covers the use case. The implementation does
not support `r { f = e }`.

## C. Judgment calls (flagged for review)

### 11. Newline rule implemented as a concrete post-lexing filter

§2.4's own text is circular: it says a `NEWLINE` inside `{...}` is "dropped
except where the inner grammar uses it as a separator (e.g., between `match`
arms, between block statements)" — but "where the inner grammar uses it as a
separator" is exactly the thing a context-free lexer cannot decide by
looking at tokens alone. Implemented as: keep a `NEWLINE` iff the preceding
token is in §2.4's accepting-token set **and** the next non-`NEWLINE` token
is in §2.4's starting-token set; additionally, drop all `NEWLINE`s whenever
the innermost enclosing bracket is `(` or `[` (irrespective of the two-sided
test). Brace (`{`/`}`) depth does **not** suppress newlines by itself — it's
the accepting/starting-token test that does the work inside blocks.

Bracket nesting has to be tracked as a **stack, not a counter**. `(` and `{`
disagree about newlines and either can contain the other, so only the
innermost one may decide. A counter gets this wrong in one direction:
`Array.push(ops, match c { ... })` is inside a `(`, so every newline in the
match — including the ones separating its arms — was dropped, and the arms
ran together into a parse error. Found by the brainfuck conformance program,
which is the first code to nest a multi-arm `match` inside a call. Also
note: `else` and `|` are correctly absent from §2.4's starting-token list, so
`} else` and match-arm continuations (`pat | pat -> expr`) work under the
two-sided test without any special-casing.

### 12. Record-literal ambiguity in condition position

Independent of record update (dropped, delta 10), plain record construction
(`CONID "{" field-init ... "}"`, §3.5) already collides with block syntax:
`if Foo { x = 1 }` could be a nullary-constructor condition `Foo` followed
by a block `{ x = 1 }` (an assignment statement, per delta 1), or a record
construction `Foo { x = 1 }` used as the condition. The parser carries a
"no record literal" flag while parsing the scrutinee of `if`/`while`/`for`-in
(Rust-style); a record literal in condition position must be parenthesized.

### 13. Annotation type variables are lexically scoped to the enclosing `fun`

§4.2 says annotation type variables are "implicitly universally quantified
at the enclosing let/fun/top-level binding." Read as "each annotation gets
its own fresh quantification," this breaks §10's own worked example: inside
`fun drain(s : Stack a) -> Array a { let out = [] : Array a; ... }`, the `a`
in the parameter annotation, the `a` in the return annotation, and the `a`
in `[] : Array a` must all be the *same* type variable for the example to
type-check. The implementation treats a bare lowercase identifier occurring
in an annotation as an ordinary unification variable, scoped to (shared
across) the whole enclosing `fun`, not as a skolem constant. Consequence: an
over-general signature is not caught in v0 — e.g. `fun f(x) -> a { 5 }`
wrongly type-checks, since `a` is just unified with `Int` rather than being
rigid. Skolemization is left as a follow-up.

### 14. The §10 worked example does not run as written

Two independent reasons, both recorded here rather than silently patched:

First, under delta 8 (bounds checked against `length`), `Stack.push`'s body
`s.data[s.top] = x` writes to an array obtained from `Array.new(capacity)`,
whose `.length` starts at 0 — so the very first `push` call panics, since
index `0` is out of bounds against `length = 0` even though `capacity` may
be nonzero. The conformance test for this example uses `Array.push` (which
grows `.length`) instead of raw indexed assignment for the "push" step.

Second, under §9.3's name-resolution order (a module's own top-level
declarations, item 2, outrank unqualified imports, item 3), the calls to
`new`, `push`, and `pop` written inside the `Stack` module (which itself
defines top-level `new`/`push`/`pop`, and does `import Data.Array (new,
push, pop)`) resolve to `Stack`'s own functions, not the imported
`Data.Array` ones. `Stack.new` calling `new(capacity)` therefore resolves to
itself and recurses infinitely. This is correct shadowing behavior per §9.3,
not a language bug — but since v0 has no modules at all (delta 9), it's moot
for now; the conformance test is written with distinct names (no
same-named local defs shadowing the seeded `Data.Array` builtins) so the
example's intent is preserved.

### 15. Exhaustiveness checking is warning-only

§5.1 lists "pattern matching exhaustiveness checking (warnings; non-
exhaustive matches are a runtime error if reached)" as an extension to
standard HM — i.e. the spec already says this is a warning, not a rejection.
It's called out here because it's the last algorithm-level piece
implemented (the checker undercounts constructors conservatively until then,
degrading to "no warning" rather than a false one): a `match` that is not
exhaustive is accepted at compile time and panics at runtime only if an
unhandled case is actually reached, per spec.

---

## D. Further decisions surfaced while implementing

These were not visible from reading `design.md`; each one is a question the
spec never asks, discovered only when something had to actually run.

### 16. A `match` arm may begin with a leading `|`

§3.5 gives `match-arm ::= pat ("|" pat)* "->" expr` with arms separated by
newlines. But §2.4's starting-token list does not contain `|`, so the newline
before a line beginning with `|` is always dropped — meaning the alternatives
of one arm may be split across lines. That leaves `|` as the only separator the
parser sees, and it is unambiguous: a `|` encountered *before* an arm's `->`
continues that arm's pattern list, while a `|` encountered *after* a completed
arm begins the next one. The implementation therefore accepts an optional
leading `|` on each arm. Both of these parse, and mean different things:

```
match x { A | B -> 1 }        -- one arm, two patterns
match x { A -> 1
        | B -> 2 }            -- two arms, one pattern each
```

### 17. Unary `-` is `fun(Int) -> Int`

§3.5 admits `"-" expr-unary` but §8.2's operator table lists no unary operator
except `!`. Since operators are monomorphic in v1 (decision 34), unary minus is
given the Int type. Negating a Float requires `0.0 -. x`. When typeclasses
arrive this becomes a `Num` method.

### 18. Integer division truncates toward zero; division by zero panics

§8.2 types `/` and `%` as `fun(Int, Int) -> Int` but does not say how they
round or what happens at zero. The implementation truncates toward zero and
takes the remainder's sign from the dividend, matching C and Rust rather than
Python's flooring `//`. `1 / 0` and `1 % 0` panic, as does `1.0 /. 0.0`.

### 19. `continue` still advances the loop

§6.5 desugars `for x in arr { body }` into a `while` whose index increment
follows `body`. Read literally, a `continue` in `body` would skip the increment
and spin forever. The implementation advances the index (and, for the C-style
`for`, runs the step) before the next iteration, which is what every language
with `continue` does and what the desugaring plainly intends.

### 20. Top-level items are evaluated in dependency order

§5.2 specifies SCC-grouped inference for top-level `fun` declarations but says
nothing about top-level `let`/`var`, nor about the order initializers run in.
The implementation puts every top-level item — functions and bindings alike —
into one dependency graph, and both checks and evaluates them in topological
order, so a binding is always initialized before anything that reads it and a
`fun` may freely reference a `let` declared later in the file. A cycle among
items that are not all functions is rejected: only functions may be mutually
recursive.

### 21. `main` is the entry point

`design.md` never says how a program starts. After the top-level bindings are
evaluated, a zero-argument `main`, if one is defined, is called.

### 22. Output and conversion primitives

§8.4 sketches `Data.Int`, `Data.String` and `Data.Bool` without contents, and
`design.md` has no I/O at all — but a prototype that cannot print cannot be
tested. The initial environment adds `print : fun(String) -> Unit`,
`Int.toString`, `Float.toString`, `Bool.toString`, `Char.toString`,
`String.eq`, `String.lt`, `String.length`, `Bool.eq`, `Char.eq` and
`Float.lt`. The last few are the named comparison functions §8.2 promises for
non-Int types. Every name is also registered under its `Data.`-prefixed
spelling, so `Data.Array.push` and `Array.push` both resolve.

Added later, when the brainfuck program needed them:

- `write : fun(String) -> Unit` — `print` without the trailing newline. Both
  `write` and `print` flush, so the two cannot interleave out of order on a
  terminal. `tests/programs/chars.expected` deliberately ends without a
  newline, which is what pins `write`'s contract.
- `String.chars : fun(String) -> Array Char` — a placeholder for a real
  string API. The result is sized exactly, so a program reading `.capacity`
  is not misled.
- `Char.fromInt : fun(Int) -> Char` and `Char.toInt : fun(Char) -> Int`.
  `Char.fromInt` panics on a value outside `0..0x10FFFF` rather than letting
  Python's `chr` raise through the interpreter. Only `fromInt` was needed;
  `toInt` is there so the pair is symmetric.

### 23. A tuple of non-expansive expressions is non-expansive

§4.4's table covers literals, variables, lambdas, and constructor applications,
but omits tuples entirely. A tuple is a constructor application in all but
syntax, so it is treated as non-expansive when all of its elements are.

### 24. Writing `.length` and `.capacity`

§8.3 makes both fields mutable "at the user's own risk" without saying what the
risk is. The implementation defines it: writing `.capacity` reallocates
(shrinking also clamps `.length`); writing `.length` is permitted only within
`0 ... capacity`, and anything outside that range panics. Raising `.length`
exposes slots that were never written — reading one is undefined behaviour in
the surface language, and the prototype panics rather than inventing a value.

### 25. Value constructors parenthesize their payload; type application does not

`design.md` writes both applications the ML way, juxtaposed: `Some a` declares
a payload, `Some x` matches one, `Array a` applies a type constructor. The
value side moves to parens in declarations, construction and patterns:

```
type Option a = None | Some(a)
type Tree a   = Leaf | Node(Tree a, a, Tree a)

let opt = Some(123)
match opt { Some(n) -> n
            None    -> 0 }
```

Type application stays juxtaposed (`Array a`, `Option Int`, and the `Array Op`
inside `Loop(Array Op)`).

Three reasons:

1. **It matches the representation.** `decls.py` already stores every
   constructor -- arity 0 included -- as a `Scheme` over
   `TFun(argtypes) -> TCon(tycon, params)`. A value constructor *is* an
   uncurried function, so it should declare its payload the way a function
   declares parameters. Construction was paren-only from the start (`Some 5`
   has never parsed); this brings declarations and patterns in line with it
   rather than introducing anything new.
2. **Arity becomes visible.** `Node (Tree a) a (Tree a)` uses parens for
   *grouping*, and the arity is hard to see. `Node(Tree a, a, Tree a)` uses
   them for delimiting, and grouping comes free.
3. **Each language keeps one meaning for parens.** Parens are an argument
   list; juxtaposition is type application. If types also parenthesized, then
   `(...)` in type position would mean both argument list (`fun(A, B) -> C`)
   and grouping -- exactly the double meaning being removed from the value
   side.

Consequences:

- Nullary constructors stay bare: `None`, not `None()`, in every position.
- The juxtaposed *pattern* form (`Cons x xs`), previously accepted alongside
  the paren form, is **removed**. Both the pattern and the declaration site
  detect the old syntax and name the replacement, because a juxtaposed
  constructor otherwise parses as a nullary one and the error lands on
  whatever token follows.
- Exhaustiveness witnesses print `Some(Some(_))`. The paren form is
  self-delimiting, so `exhaustive.py`'s `render` no longer tracks nesting to
  decide where to add grouping parens.

The cost, acknowledged: `type Option a = None | Some(a)` puts both notations on
one line, which needs the explanation above rather than being self-evident.

