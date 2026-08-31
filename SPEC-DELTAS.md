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
rows: records stay nominal, so entailment is a declaration lookup. In that
three-argument shape it is GHC's `HasField x r a | x r -> a` (Gundry's
`OverloadedRecordFields`, in `GHC.Records` since 8.2), and the solver's
`improve` rule is that functional dependency -- see `constraints.py` for why
dropping the rows is what makes the rule necessary rather than free. It is a
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

**Amended at delta 37.** `pop` no longer panics on an empty array: it answers
`Option a`. Indexing still panics, and that distinction is the point -- see
delta 37.

### 9. No modules in v0 — **amended at delta 41**

§9 describes a full Haskell-style module system (headers, qualified/
selective/hiding imports, cross-module name resolution). The prototype
implements a single file only: no `module`/`import` semantics. `Data.Array`
(`new`, `push`, `pop`) and `error` are seeded directly into the initial
global environment as if always in scope. `module` and `import` are still
lexed and parsed per §3.1/§9.2 so the surface syntax isn't lost, but both are
rejected at a later stage with a "not supported in v0" error.

**Amended at delta 41.** A program is a graph of modules now. `module` and
`import` mean what §9 says they mean, and this entry survives only as the
record of what came before.

**Amended at delta 36.** `Bool` was on the list of types a program may not
redefine. It is not built in any more -- the prelude declares it -- so the
list is `Int`, `Float`, `String`, `Char`, `Unit` and `Array`. Redeclaring
`Bool` is now the ordinary "declared more than once" collision with the
prelude's declaration.

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

**Amended at delta 41.** Modules exist, and the shadowing this entry
describes is exactly what `tests/programs/modules/Stack.tl` now relies on:
it defines `push` and `pop` of its own and calls `Array.push` qualified. The
infinite recursion the spec's example would have had is a consequence of
writing `import Data.Array (push)` *and* a local `push` and then calling the
bare name, which is a mistake the author can see, not one the language can
prevent.

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

### 17. Unary `-` is `fun(Int) -> Int` -- **retired at delta 32**

§3.5 admits `"-" expr-unary` but §8.2's operator table lists no unary operator
except `!`. Since operators are monomorphic in v1 (decision 34), unary minus is
given the Int type. Negating a Float requires `0.0 -. x`. When typeclasses
arrive this becomes a `Num` method.

**They arrived.** Unary `-` is `Neg.neg`, with instances for `Int` and `Float`,
and `-x` on a float is written `-x`. `0.0 -. x` no longer parses: `-.` is
gone.

### 18. Integer division truncates toward zero; division by zero panics

§8.2 types `/` and `%` as `fun(Int, Int) -> Int` but does not say how they
round or what happens at zero. The implementation truncates toward zero and
takes the remainder's sign from the dividend, matching C and Rust rather than
Python's flooring `//`. `1 / 0` and `1 % 0` panic, as does `1.0 / 0.0`.

**Amended at delta 32.** That behaviour is now what `instance Div Int` and
`instance Rem Int` do, rather than what the operator is; it is a property of
`Int`, which is where it belongs. `Float` has `Div` and no `Rem`, so `1.5 % 2.0`
is a missing instance rather than a silent choice about how floats take a
remainder.

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

### 22. Output and conversion primitives — **amended at deltas 33, 42**

§8.4 sketches `Data.Int`, `Data.String` and `Data.Bool` without contents, and
`design.md` has no I/O at all — but a prototype that cannot print cannot be
tested. The initial environment adds `print : fun(String) -> Unit`
(`[Show a] fun(a) -> Unit` since delta 33),
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

**Amended at delta 42.** None of these is an entry in the initial environment
any more: `§8.4`'s modules are written in the language, under `turkey/lib/Data`,
over `Prim.*`. The named comparisons (`String.eq`, `Char.eq`, `Float.lt` and
the rest) are gone entirely — they were delta 32's debt and it was paid there.
And the `Data.`-prefixed spelling is no longer a second registration of the
same name: `Data.Array.push` is what the function *is*, and `Array.push` is
what the Prelude re-exports it as.

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

### 26. Scope errors are reported before type errors

Inference is HM(X): generation walks the AST and builds a constraint, and a
separate solver settles it. Some failures are found while *generating* -- an
undefined name, an unknown constructor, a field a record literal does not
declare, a write to a `let` binding -- and the rest while *solving*.

Because generation now finishes before solving begins, a program containing
both kinds reports the generation one first, even when the type error comes
earlier in the source:

```
fun main() {
    let a : Int = "wrong"       -- line 2: a type error
    let b = Nope { n = 1 }      -- line 3: an unknown constructor
}
```

reports line 3. Previously, when generation and solving were interleaved, it
reported line 2. Compilation stops at the first error either way, so this only
changes *which* of several errors is shown, and the resulting order -- names
and declarations first, types second -- is the conventional one. Within each
kind, source order is preserved.

### 27. Numeric literals are polymorphic over a closed set

A numeric literal does not have a type. It has the *set* of types it could
have, carried as a `OneOf` predicate until something decides:

```
let x = 1        -- x : [OneOf a {Int, Float}] a
let y = 1.5      -- y : Float
```

`1` denotes a numeral, not an `Int`, so `1 +. 2.0` is well typed. Only the
reverse is unsafe: a decimal literal's set is the float types alone, so
`f(1.5)` where `f` wants an `Int` is still rejected. This is the
`Num`/`Fractional` split, without the classes.

Membership is decided by one rule for the whole tower -- can the type hold this
value exactly? -- so it is **value-dependent**: `1` is `{Int, Float}`, while
`9007199254740993` (past an f64 mantissa) is `{Int}` and will not silently
round. The set is **closed**: no program can add a member, so a `OneOf` needs
no runtime evidence, only a decision.

Three rules settle one. A singleton set is an equation and is discharged as
one on the spot. Two sets over the same variable intersect, and an empty
intersection is an error rather than a deferral, since nothing can widen a
closed set. A set that reaches a point where nothing can ever narrow it
further -- its variable appears in no type being generalized, so no use site
can pin it -- is **defaulted** to the first member in tower order: `Int` for an
integral literal, and for a decimal one the leading float type, which today is
`Float` and after the tower lands will be `Double`.

The cost, acknowledged: a mismatch is now reported against the literal rather
than against what disagreed with it, because the literal is what carries the
open set.

```
fun main() {
    let a = Array.new(4)
    Array.push(a, 1)
    Array.push(a, "two")        -- line 4
}
```

reports line 3, `a numeric literal cannot have type 'String'`, where a
monomorphic `1` would have reported line 4. Carrying an origin span into a
predicate would recover the better message; that is deferred to the milestone
that adds classes, which needs the same machinery.

A literal's openness can also surface in an inferred signature. `bf.tl`'s
`move` pushes `0` onto `t.data` and nothing else pins the element type, so it
generalizes to `[..., HasField "data" a (Array b), OneOf b {Int, Float}]`
rather than `Array Int` -- correct, and more general than intended, which is
the noise Haskell's monomorphism restriction exists to suppress. Turkey has no
such restriction and accepts the noise.

### 28. Types have kinds, and application is curried

`design.md` §4.1 writes a type constructor application as `TyCon τ ... τ` --
saturated, with the arguments held by the constructor itself, and arity checked
by counting them against the declaration. That representation has no sub-term
for a *constructor* to be, so nothing can abstract over one: `Functor f` has no
`f` to quantify.

Application is now curried. `Array Int` is `TApp(TCon("Array"), Int)`, a head
applied to one argument at a time, and a type variable may stand at the head:

```
type Wrap f a = Wrap(f a)          -- f :: * -> *, discovered from the body

fun unwrap(w) = match w { Wrap(inner) -> inner }
                                   -- unwrap : fun(Wrap a b) -> a b
```

`fun(τ₁, ..., τₙ) -> τ` stays a separate, uncurried node. It is the language's
fixed-arity function type (delta 2), not a constructor that happens to take two
arguments, and currying it would reintroduce partial application at the value
level through the back door.

**Kinds** are what keep the two apart and what replaces the arity check. `Int ::
*`, `Array :: * -> *`, `Wrap :: (* -> *) -> * -> *`. Over-application is no
longer counted, it is a kind error, and the same rule rejects `Int Bool`:

```
fun f(x : Array Int Bool) = x   -- 'Array Int' has kind *, so it cannot be
                                -- applied to 'Bool'
fun g(x : Array) = x            -- 'Array' has kind * -> *, but a type of kind
                                -- * is needed here
```

A declaration's parameter kinds are not written down, so they are inferred: a
kind *skeleton* -- one arrow per parameter, over kind variables -- is assigned
to every declaration before any body is read, and the bodies then constrain it.
Arity is syntactic, so the skeleton is exact and mutual recursion needs no
dependency ordering, unlike the value level. Kinds are first-order: nothing is
kind-polymorphic, and whatever is still undecided once the declarations have
been read is defaulted to `*`, as in Haskell 98.

Two consequences worth stating:

- **Decomposing an application is sound only because every head is rigid.**
  `f a ~ g b` is solved pointwise, which would be wrong if a head could be a
  function on types. There are no type-level lambdas, and a **type alias must
  be saturated** where it is used -- an alias is the one head that is not
  rigid, so a partially applied one is rejected rather than expanded later.
- An alias body must classify values, so `type Boxed f = f Int` is fine but
  `type Alias = Array` is not. Since an alias cannot be partially applied, an
  alias standing for an unapplied constructor could never be used anyway.

### 29. Single-parameter type classes

design.md has no classes at all -- §8.2 says only that `==`/`<` "can be unified
under `Eq`/`Ord` classes" later. This adds them, with one parameter, in the
syntax `Prelude.tl` already assumes:

```
class Semigroup a {
    fun combine(a, a) -> a
}

class Monoid a : Semigroup a {
    fun empty() -> a
    fun concat(xs : Array a) -> a = fold(xs)
}

instance Semigroup (Array a) { ... }
instance [Eq a] Eq (Array a) { ... }

fun largest[Ord a](xs : Array a) -> a { ... }
```

A class predicate `C t` is the fourth form of the constraint domain X, beside
`t1 ~ t2`, `HasField` and `OneOf`. Entailment is Jones's, from *Typing Haskell
in Haskell* (Haskell Workshop 1999): `by_super` walks the superclass closure,
`by_inst` matches an instance head, and the two together decide `entail`. All
of it lives in `turkey/classes.py`; `unify` learns nothing.

**A `fun` with no body is a signature, and its parameters are types.** This is
the one genuinely ambiguous production in the grammar: a bare identifier is
both a legal parameter name and a legal type expression, so `fun combine(a, a)
-> a` is two occurrences of one type variable while `fun combine(a, b) = a` is
two binders. Nothing local decides it -- what follows the return type does --
so the parameter list is read as types first and re-read as patterns if a body
turns up. **The two readings never mix**: no body means every parameter is a
type, a body means every parameter is a binder. A signature therefore cannot
name its parameters and a definition cannot omit them, which is what makes the
classification total rather than per-parameter. A body-less `fun` is legal only
inside a `class`.

**Superclasses use `:`, contexts use `[...]`.** Both mean "requires", but the
positions are disjoint, so there is no grammatical conflict and no reason to
force one spelling on the other. The split matches Rust (`trait A: B` versus
`fn f<T: Ord>`), and it keeps `[...]` meaning exactly one thing: a context on a
*value*'s type. A context is a constraint, not a binder -- the variables it
names are the enclosing declaration's annotation variables (delta 13).

Three restrictions, each load-bearing:

- **One class parameter, and no functional dependencies.** Ambiguity is then
  the plain free-variable test: a quantified variable a predicate mentions and
  the type does not can never be pinned by a use site. Multi-parameter classes
  need fundeps before that test says anything useful, and associated type
  families are meant to make the second parameter unnecessary.
- **An instance head is a constructor applied to distinct type variables**
  (Haskell 98's rule). `instance Functor (Either l)` yes, `instance Functor
  (Either Int)` no. Matching is then a one-way structural walk, and two
  instances overlap exactly when they name the same constructor -- so the
  overlap check is a lookup rather than a unification test.
- **A superclass constrains the class variable itself.** `class Monoid a :
  Semigroup a`, not `Semigroup (Array a)`, so carrying a predicate across the
  superclass edge is substitution-free.

**An instance method is checked, not inferred, and the variables the instance
does not fix are rigid.** Otherwise a body less general than its class's
signature would pass by narrowing the signature to fit -- `instance Functor
Option { fun map(opt, g) = match opt { Some(x) -> Some(x + 1) ... } }` would be
accepted with `a` silently `Int`. The quantified variables are replaced by
nullary `TCon`s named after them, which `unify` already treats as rigid, so the
error still reads in the author's own names.

Checking a method against a declared type is the one place a predicate is
*granted* rather than proved: the class's own predicate, the instance's context
and the method's context are facts inside the body. That is the whole of local
assumptions, deliberately -- an assumption introduced by a *pattern* is what
makes GADTs destroy principal types, and none is introduced anywhere.

A method shares the value namespace with ordinary functions, so `fun eq` and a
class method `eq` collide.

Nothing runs yet: a method call has no dictionary to resolve it until
dictionary passing lands (delta 30). `tests/programs/classes.tl` type-checks
and its `main` stays clear of methods.


### 30. Classes run by passing dictionaries

design.md §6 gives the evaluator no notion of a class, and it does not need
one: overloading is resolved before anything runs. A class predicate is turned
into a *value* -- a dictionary of that class's methods for that type -- which
is Wadler & Blott's translation ("How to make ad-hoc polymorphism less ad hoc",
POPL 1989) in the shape Jones gives it for qualified types ("A theory of
qualified types", ESOP 1992). `turkey/evidence.py` decides the evidence;
`turkey/eval.py` builds the dictionaries.

Three rules:

- **A binding that retains *n* class predicates takes *n* leading
  dictionaries.** `fun squash[Monoid a](xs : Array a) -> a` is a function of a
  `Monoid a` dictionary and then of `xs`. The order is the scheme's own
  predicate list, so nothing else in the pipeline has to agree about one.
- **A use site names its evidence.** Either a dictionary already in scope --
  a parameter, or the instance's own while its methods are being built -- or
  the dictionary of the instance that covers it, applied to evidence for that
  instance's context. `instance [Semigroup a] Semigroup (Array a)` is a
  function from dictionaries to a dictionary, and that is exactly how it runs.
- **A superclass is a selection, not a second obligation.** A `Monoid`
  dictionary carries its `Semigroup` one, so `[Monoid a]` alone is enough to
  call `combine`.

**Evidence is resolved after solving, and only after.** Which instance covers
`Semigroup a` is not a question that has an answer while `a` is still open,
which is the same reason generation does not decide it either. So generation
marks each use site, solving fills in the predicates and the scopes that were
open there, and elaboration is a third pass that reads the answers. Resolution
is `entail` again, made constructive: the same order -- assumptions first, then
the instance table -- returning the derivation instead of a boolean.

**A group's class predicates are shared across its bindings.** Per-name they
would not be: one member of a mutually recursive group may call another, and
would then need a dictionary its own signature never promised. Haskell 98
shares a group's context for the same reason. `HasField` and `OneOf` stay per
name, because both are erased and neither leaves anything to pass.

**A dictionary is registered before its methods are built.** An instance
method may need the very dictionary it belongs to -- by recursing, or through a
recursive type, where `Show (Array Rose)` needs `Show Rose` which needs it
back. Closing the cycle on the object under construction is what makes such a
dictionary finite; it is also what lets a default method call another method of
its own class without anything being passed.

A `let` can need dictionaries before it is a value at all -- `let f = combine`
generalizes to `[Semigroup a] fun(a, a) -> a` -- so such a binding stands for
itself until they arrive and is re-run per instantiation. Only a binding the
value restriction already calls non-expansive can get there, so re-running it
observes nothing.

Operators are still monomorphic and still route through the evaluator's
`BINARY` table: `+` becomes `Add.add` at the milestone that makes every
operator a class method, not here. Associated type families (delta 31) are
erased before any of this runs and leave it unchanged.


### 31. Associated type families, and unification's third outcome

design.md's type language has type constructors and type variables, and every
type in it is a *value*. A family is a **function** on types: `Elem c` is a
type the class computes from its own parameter, declared in the class and
defined by each instance.

```
class Container c {
    type Elem c

    fun first(c) -> Elem c
}

instance Container (Array a) {
    type Elem = a

    fun first(xs) = xs[0]
}
```

**Only associated families, never free-standing ones.** A family is declared
inside a class and takes that class's parameter, so its arity is one, its
coverage is anchored to an instance head, and there is exactly one place to
look for its definition. That is the setting of Schrijvers, Peyton Jones,
Chakravarty & Sulzmann ("Type Checking with Open Type Functions", ICFP 2008)
with the general case left out.

**This is what makes one class parameter enough.** `Container c` says what the
element type is without a second parameter and without a functional
dependency, which is the trade SPEC-DELTAS 29 named and deferred to here. A
context may then constrain a type nobody wrote down: `fun describe[Container
c, Show (Elem c)](xs : c) -> String` demands `Show` of the element, and the
demand reduces the moment the container is known.

**Unification gains a third outcome, and this is the milestone.** `Elem a ~
Int` with `a` unbound is neither solvable nor false -- it becomes one or the
other once `a` is decided. So `types.unify`, which could previously only bind
or raise, can now **defer**: the equation goes back on the solver's queue and
is retried as the solver learns more, alongside the deferred predicates it
already kept. An equation still stuck when the binding that owns its variables
generalizes can never be decided by anything outside, and is an error there.
Nothing but a family application can defer, so nothing else changes.

**Families are not injective.** `Elem i ~ Int` says nothing about `i`; two
containers with the same element type are not the same container. So two
family applications unify only when they are syntactically the same
application -- reflexivity, not decomposition -- and that is the one thing a
functional dependency would have given. Iteration only ever goes container to
element, so it is not missed.

A family application is its own type former (`types.TFam`) rather than a
constructor at the head of an application, for the reason delta 28 gives for
saturating type aliases: decomposing `f a ~ g b` pointwise is sound only
because every head is rigid, and a family head is precisely the one that is
not.

**Reduction terminates by a syntactic rule.** An instance head is a
constructor over distinct variables (delta 29), and a family definition may
apply a family only to a *variable of that head* -- a proper subterm of the
argument that selected the instance -- so every reduction step is a strict
decrease. `type Elem = Elem (Array a)` is rejected where it is written.

Families are **erased**: they are reduced away during solving and again before
evidence is resolved, so `turkey/eval.py` never sees one and delta 30 is
untouched. A family that no instance covers is reported as the missing
instance it is, at the equation that needed it.

### 32. Operators are class methods, and the library is written in the language

§8.2 gave every operator a monomorphic type, listed `+.` `-.` `*.` `/.` as a
second arithmetic for floats, and shipped `String.eq`, `String.lt`, `Bool.eq`,
`Float.lt` and `Char.eq` as named functions because there was no way to say
"equality, at whatever type this is". It closed by naming its own repayment:
"when typeclasses are added, these can be unified under `Eq`/`Ord` classes and
the operators overloaded." Deltas 29 through 31 built the typeclasses. This is
the repayment.

**Every arithmetic and comparison operator desugars to a method call.** `a + b`
is `add(a, b)` and nothing else; the parser records which method the operator
means and every stage after it sees an ordinary use of an ordinary name. There
is no operator table in the checker, no case in the evaluator, and no way for
`+` to be more privileged than a function a program writes itself. What is left
of §8.2's table is three entries: `&&` and `||`, which short-circuit and so
cannot be calls, and `++`, which is concatenation on `String` and has no class
to belong to until there is a `Semigroup`. (Delta 44 removes `++`: the class it
belongs to turned out to be `Add`.)

**Per-operator classes, not one `Num`.** `Add`, `Sub`, `Mul`, `Div`, `Rem` and
`Neg` are separate, as in Rust's `std::ops`, so a type that adds is not thereby
required to divide. `Money` in `tests/programs/operators.tl` has `Add`, `Neg`,
`Eq` and `Ord` and no arithmetic beyond them, and `a / a` on it is a missing
instance.

**Operators are homogeneous, and this is where declining MPTCs costs
something.** With a single class parameter there is no `Add a b`, so both
operands and the result share one type: `Vec * Scalar` is not expressible.
Delta 29 recorded the choice; this is the one place it is visible in the
surface language, and it is stated rather than left to be discovered.

**A numeric literal is still open, so `1 + 2` carries two predicates.** The
literal contributes `OneOf a {Int, Float}` (delta 27) and the operator
contributes `Add a`; at the outermost boundary the first defaults to `Int` and
the second is discharged against it. An *unannotated* function that adds keeps
both, which is Haskell's `Num a =>` in all but name:

```
fun inc(x) = x + 1      -- [OneOf a {Int, Float}, Add a] fun(a) -> a
```

That generality is correct and is more than was intended, in exactly the sense
delta 27 already accepted for `bf.tl`'s `move`. Two `.types` goldens changed
for it and no `.expected` did: `bf.tl`'s `inc` and `fields.tl`'s `bump` are
polymorphic in the numeric type they increment.

**`for x in xs` runs on a class.** `Iterator i` declares the associated family
`Item i` (delta 31), and the loop is a call to its methods; the loop variable's
type is `Item xs`, left to reduce like any other family application. `Array` is
the instance that ships, and it is no longer the only sequence a `for` can walk
-- `tests/programs/iter.tl` walks a `Range` record, a linked list and a `Pair`.
The cost is on the other side: a `String` is not an `Iterator`, so `for c in s`
is a missing instance and the characters are still reached through
`String.chars`. **Amended at delta 33:** the methods were `count` and `nth`,
because a cursor's `next` wants an `Option` and v0 had no such type. Delta 33
declares `Option` and makes the protocol a cursor.

**The classes are ordinary source.** They live in `turkey/prelude.py` as a
turkey program, and it is *checked* rather than trusted -- through the same
generator, solver and elaborator a user's program goes through. What is
privileged is not the classes but a small set of machine operations,
`Prim.intAdd` and its neighbours, which are in scope while the prelude is
checked and nowhere else; `Prim.intAdd` in a program is an undefined name. The
prelude declares no top-level binding, only classes and instances, so there is
nothing for the evaluator to run before the program: a dictionary is built from
its instance's plan on demand, as any other is. (**Amended at delta 33:** it
now declares two, `print` and `write`.)

One latent bug in delta 30 surfaced here and is fixed. A call from one member
of a binding group to another -- or to itself -- is solved against the
monomorphic placeholder the group binds, so it demands nothing, and at run time
it was handed the undischarged binding rather than a function. No golden had a
mutually recursive group with a class context until `adt.tl`'s `isEven`/`isOdd`
acquired one from `==` and `-`. Such a use now takes the group's own
dictionaries, which is what the group's context always meant.

### 33. `Show`, `Option`, and iteration as a cursor

Three changes with one cause: delta 32 left `print` taking a `String` and left
`for` walking an index, and both were compromises made for a missing type.

**`Option a` is declared in the prelude.** It is `None | Some(a)`, the same
type four test programs had each been declaring for themselves, and it is
there because `Iterator.next` needs it. A program that declares its own is now
told the type is declared more than once.

**Iteration is cursor-based.** `Iterator c` declares two families and two
methods:

```
class Iterator c {
    type Item c
    type Cursor c

    fun iter(c) -> Cursor c
    fun next(c, Cursor c) -> Option (Item c)
}
```

`iter` makes the mutable state that walks the container; `next` advances it and
ends the loop by answering `None`. Nothing asks the container how long it is.
That is the point: an indexed protocol can only describe containers that can
produce their *k*th element, so a linked list, a stream, a generator or a
file's lines could not be iterated at all, and a loop over a list that somehow
could would be quadratic. `tests/programs/iter.tl` now walks a linked list for
exactly this reason, and `tests/test_prelude.py` iterates a source with no end.

The cursor is a second associated family rather than a second class parameter,
and the container is passed to `next` alongside it rather than split across an
`Iterable`/`Iterator` pair as Rust does. The pair would need `Iterator (Iter c)`
as a superclass over its own family application; one class with two families
says the same thing and asks nothing new of delta 29's machinery.

**`print` is an ordinary function over `Show`.** `class Show a { fun show(a)
-> String }` ships with instances for the five primitives, and for `Array a`
and `Option a` given `Show a`. `print` and `write` are no longer builtins:

```
fun print(x) = Prim.print(show(x))
fun write(x) = Prim.write(show(x))
```

Both infer `[Show a] fun(a) -> Unit`. `Prim.print` and `Prim.write` are the
only things that reach stdout and are reachable only from the prelude, so the
one way to print a value is to say what it looks like as a `String` -- which
is what §8.2's `Show` was for. `print(Int.toString(n))` still works and every
golden's output is unchanged; `print(n)` now also does.

One consequence is worth stating: because `print` is constrained, it no longer
pins its argument's type. `print(default())`, where `default` is a method known
only by its result type, was previously resolved by `print` demanding a
`String` and is now correctly ambiguous.

**What this cost the prelude's seam.** Delta 32 could say the prelude declared
no top-level binding. It declares two now, so `driver.prelude` returns them
alongside the declaration and class tables, and the evaluator runs the
prelude's statements before the program's. Exported are exactly the names the
prelude's own statements bind -- which is how `print` crosses the seam and
`Prim.print` does not.

### 34. A record variant is symmetric with a positional one

design.md decision 28 says record construction may be positional (`C(v₁, ...)`)
or labeled (`C { f = v, ... }`), and §3.6's pattern grammar lists `CONID pat*`
for constructors with no restriction on which declaration form it applies to.
The implementation allowed positional *construction* and refused positional
*destructuring*, at one guard in `Generator.match_pattern`. Nothing in
design.md or in these deltas argued for the refusal; it was an omission.

The guard is gone. `Circle(r)` and `Circle { radius = r }` now match the same
value, and the arity check that already sat above the guard gives the error for
the wrong count. Nothing else moved, because nothing else was asymmetric:
`ConInfo` stores field *types* positionally with `field_names` a parallel list,
`ConstructorFn.build` zips positional arguments against declaration order for
both shapes, and `exhaustive._normalize` already reduced a record pattern to a
positional row. The evaluator learned only that a `PCon` may meet a
single-variant record's `RecordObj` as well as a `ConValue`.

**The two forms still differ in one respect, deliberately.** A record pattern
may name a subset of the fields; a positional one may not. `R(x)` for a
two-field `R` is an arity error. Names are self-describing and positions are
not, and this is the one place where that difference should have a consequence.

**A bug this fixed for free.** `exhaustive.render` prints a missing-case
witness positionally whatever form declared the constructor, so a missing
`Circle { center, radius }` was reported as `Circle(_, _)` -- a pattern the
checker then rejected. The suggestion is now legal, and a test pins that.

**Punning in construction,** the other half of the same asymmetry: patterns
have always punned (`C { x }` binds `x` from field `x`) and expressions
required `C { x = x }`. The `=` is optional now, exactly as in
`parse_con_pattern`; a literal may mix punned and written fields. Neither
generation nor evaluation can tell the difference -- both work from the
`(label, expr)` list -- and non-expansiveness is unaffected, an `EVar` being
non-expansive already. design.md §3.5's `field-init` and §3.6 are amended.

### 35. Parameters are reassignable

A parameter could not be assigned to: `cannot assign to 'x': it was bound with
'let'`. Working around it meant shadowing (`fun gcd(_a, _b) { var a = _a ... }`),
which is noise with no rule behind it. A parameter is bound monomorphically by
its function, so the value restriction -- which is about what may be
generalized -- has nothing to say about it, and there was no soundness question
being answered.

Every name a parameter pattern binds is now mutable. There is no keyword: an
opt-in `var` prefix would have cost a parser change, an AST field and a rule
about which parameter patterns may carry one, to state something the type
system does not care about. `fun` parameters and lambda parameters are treated
alike.

Nothing in the parser, the AST or the checker moved. The generator keeps a
name → mutable map, which is a scope rather than a type environment --
mutability is a property of the binding form, not of a type -- and
`gen_function` enters its parameters into it as mutable; the evaluator was
already storing them in ordinary environment slots.

**Reassignment rebinds the local name and does not write through.** For a
destructured parameter -- `fun f(Point(x, y)) { x = 1 }` -- this is the thing a
reader might assume wrongly, so it has a test: the caller's value is untouched,
patterns binding rather than aliasing, and a `ConValue` being immutable (§4.5)
in any case. `let` bindings inside a function body still refuse assignment.

### 36. `Bool` is a declared type, and its constructors are `True` and `False` — **amended at delta 43**

`true` and `false` were reserved words producing a `Bool` literal, and `Bool`
was a primitive type constructor seeded into the declaration table. That made
booleans the one data type in the language the language could not have
declared, and their spelling the one place where a value of a two-variant sum
was written in lower case.

`Bool` is now `type Bool = False | True`, in the prelude, checked like anything
else (delta 32's shape). `True` and `False` are ordinary nullary constructors:
they lex as `CONID`, they are looked up like any other constructor in both
expression and pattern position, and exhaustiveness gets their signature out of
the same table as every other type's -- so a one-armed `match b { True -> ... }`
now warns, which the literal path could not have been made to do without a
special case of its own.

**The cosmetic version would have been worse.** Renaming the reserved words to
`True`/`False` makes them lex as `CONID`, where `expr-atom ::= CONID` and
`pat ::= CONID` already mean "nullary constructor" -- so they would have needed
special-casing *ahead* of constructor lookup in both positions, plus a rule
forbidding a user constructor of either name. That is strictly more special
casing than declaring the type, for a worse end state.

**`types.BOOL` did not have to move.** A `TCon` is compared by name, so the
module constant and the head the prelude's declaration installs are the same
type, and every builtin written in terms of `BOOL` -- `Prim.boolEq`,
`Bool.toString`, every `if` condition -- meets the declared type without
knowing it. What did have to be separated is `PRIMITIVES`, which served as both
the seeded-heads table and the redefinition blocklist; `Bool` leaves both.

At run time a boolean is `ConValue("True", ())`, so every site that read a
Python `bool` -- `if`, `while`, the C-style `for` header, `&&`, `||`, `!` --
goes through one `truth` helper, and the comparison builtins answer with
`from_bool`. `ArrayObj._check`'s `isinstance(index, bool)` guard became dead
and was deleted. `Show Bool` prints `True`/`False`, which is the whole of the
golden churn: the `.expected` diff for this delta is exactly that
substitution.

### 37. `Array.pop` answers with `Option`

`pop` panicked on an empty array, which delta 8 recorded without arguing for.
Popping an empty array is not a program bug -- it is the ordinary end of a
loop, and the only case a caller has any business handling. `Option` is in the
prelude since delta 33, so `pop` can now say so:

```
fun pop(arr : Array a) -> Option a
```

The builtin checks the length and answers `None` without mutating;
`ArrayObj.pop` still raises, because every other caller of it is reading past
a length that a bug moved. Naming `Option` from `turkey/builtins.py` needs no
`DeclTable`, by the same name-equality that delta 36 relies on for `Bool`.

**Scope.** Only `pop` changed. `xs[i]` out of bounds, `Array.new`'s
uninitialized slots, `Int.div` by zero and `error` all still panic: those
report a program bug rather than an ordinary empty case, and turning them into
`Option` is a language-wide decision about partiality, not a repair to one
signature. The one golden that moved is `stack.tl`, whose `pop` now matches on
the answer; its `.expected` and `.types` are unchanged.

**Declined while here: an exponentiation operator.** Two reasons, neither of
them the parser. Operators are homogeneous (delta 32), so `class Pow a { fun
pow(a, a) -> a }` cannot express `Float ** Int`, which is the one people
actually want -- Haskell ships `^`, `^^` and `**` for exactly this reason, and
one class parameter can express only the least useful of the three. And `Int **
Int` is partial: `2 ** -1` has no `Int` answer, so it would be a second
operator that panics. It also wants a new right-associative precedence level
binding tighter than unary minus. If it arrives it belongs with the numeric
tower delta 27 was built for. `square(n) = n * n` costs one line.

### 38. A complete annotation is a signature, and a signature is checked

Delta 13 made an annotation's type variables ordinary unification variables and
recorded the consequence it could see: an over-general signature is not caught,
so `fun f(x) -> a { 5 }` wrongly type-checks. It deferred skolemization as a
follow-up. The hole runs the other way too, and there it is worse -- the
annotation is not merely unchecked but silently *overwritten*:

```
fun g[Iterator a](xs : a) -> Int {
    var n = 0
    for x in xs { n = n + 1; if n < 0 { n = n + g([1, 2]) } }
    n
}
```

reported `[OneOf a {Int, Float}] fun(Array a) -> Int`. The recursive call
unified the annotation's own `a` with `Array _`, the declared `[Iterator a]`
vanished, and nothing said so. An annotation that inference may rewrite cannot
document anything, because it is not a claim about the function -- it is a hint
the solver is free to discard.

**A `fun` whose annotation is complete now states its type.** Complete means
every parameter annotated and a return type written; anything less takes the
inference path exactly as before, unchanged. Annotations remain optional, and
the all-or-nothing rule is what keeps "checked or inferred" something a reader
settles by eye rather than per parameter.

Checking is `check_method`'s procedure applied to a `fun`, and reuses it whole:
the declared variables become rigid nullary constructors (`Skolems`, delta 29),
the declared context becomes a `CAssume` given rather than a wanted, and the
body is checked against the skolemized type. A method already had a type to
live up to; now a `fun` may have one too.

**A stated type is the whole of the type, contexts included.** This is the
visible behaviour change. `fun heads(xs : Array a, ys : Array a) -> Bool =
egal(xs, ys)` was accepted, with `[Egal a]` inferred and added; it is now an
error, and the signature must say `[Egal a]`. That is Haskell's rule and it
follows from the same place: if the body may quietly add to the declared
context, the declaration is again not a claim. Dropping the return type is
enough to ask for inference back.

**Polymorphic recursion, once the type is written.** Inferring it is
undecidable -- Milner-Mycroft typability reduces to semi-unification (Henglein
1993; Kfoury, Tiuryn and Urzyczyn 1993) -- which is why `build_fun_group` binds
an inferred group's placeholders monomorphically, and still does. But
*checking* a recursive use against a stated type is an ordinary instantiation,
so a signature is bound by a new `CBind` before its own body is solved, and a
recursive occurrence instantiates it. `g` above now keeps `[Iterator a] fun(a)
-> Int` and calls itself at `Array Int`, dictionary and all.

**A written context is checked for ambiguity where it is written.** `fun
f[Egal b](x : Int) -> Int` constrains a `b` the type never mentions, so no use
site could ever decide it. The inference path catches this at generalization,
via `split`; a signature has no generalization step, so the same `reach`
closure runs over the written signature instead. `[Container c, Show (Elem c)]`
still passes: `Elem c` reaches `c`, and `c` is in the type.

**Two solver rules that only a *given* could expose.** Both are fixes, not
accommodations. `_class` now consults assumptions *before* it looks at the
shape of the predicate's argument: a given may be about a family application,
as `Show (Elem c)` is, and `Elem c` over a skolem never reduces, so testing the
head first deferred such a predicate for ever and then reported it stranded --
a demand the declaration had already granted. And `defer` no longer reports a
family over a *skolem* as a missing instance: a skolem is a nullary constructor
(delta 29), so `Elem c` looks exactly like a family over a type whose instance
is absent, when in fact the declaration assumed that instance and which one it
will be is the caller's business. Such an equation is merely stuck, and is
reported as stuck -- now naming `c`, the variable the signature wrote, rather
than a letter `show` invented.

**Scope.** One golden moved: `err_stuck_family.expected`, for that better name.
`bf.tl` is unaffected here; it needs delta 39 as well. Erased predicates are
untouched -- a signature's dictionary parameters are its class predicates in
scheme order, which is the order a use site instantiates in.

**Still open, deliberately.** Delta 13's own example is *not* repaired: `fun
f(x) -> a { 5 }` has an unannotated parameter, so it is not a signature and
stays on the soft path. Closing that needs a body's inferred predicates
re-abstracted over the skolems a partial annotation fixed, which is a different
and larger change.

Two further gaps this delta left open -- an unchecked escape, and a mutually
recursive group mixing the two paths -- turned out to be one gap, and are
closed by delta 40.

### 39. A context may state an equality, and a scheme may carry one

Delta 31 gave unification a third outcome and then took half of it back. An
equation over a family can *defer*, and a deferred equation was retried
alongside the deferred predicates -- but where a predicate that survived to a
binder was **retained** into the scheme, an equation that survived to the same
binder was **rejected**. Delta 31 said why in as many words:

> An equation still stuck when the binding that owns its variables generalizes
> can never be decided by anything outside, and is an error there.

The first clause is true of the *variables* and false of the equation. Nothing
outside can decide `a`, but every caller decides it, which is what a qualified
type is for. The same argument would prove that `Container a` cannot be
quantified either -- and the asymmetry was visible in the code, since
`retained` and `take_stuck` applied the identical level test and one fed
`generalize` while the other fed a `raise`. It was visible in the language too:
`Show (Elem c)` -- a predicate *over* a stuck family -- was inferred and
carried without complaint. Only the equation was refused.

**An equation is now a predicate.** `~` is the fifth form of the domain,
`Pred("~", [left, right])`, and `unify` defers into the one queue the
predicates already use. `take_stuck`, `reject_stuck`, `deferred_eqs` and the
duplicate of `Pred.level` that served them are gone; `retained` handles both
kinds because there is only one kind. Within a round equalities are still
solved first, for the reason two queues used to encode: a family that has just
become reducible decides a type a predicate is waiting on.

The immediate effect is that programs which had no type acquire one:

```
fun countOps(ops) {
    var n = 0
    for op in ops { n = n + useOp(op) }
    n
}
```

was `cannot reduce 'Item a' to 'Op'` and is now `[Item a ~ Op, Iterator a]
fun(a) -> Int`. So is the version that *matches* on the element rather than
passing it: a constructor pattern fixes the scrutinee by ordinary unification,
which defers the equality, which is now kept.

**`~` costs nothing at runtime.** It is deliberately not a class name, so
`is_class` is false for it and the three filters that already erase `HasField`
erase it: it never reaches `Use.preds`, never becomes a dictionary parameter,
and still travels in the scheme. No evidence, no elaboration case, no change to
the evaluator.

**A written equality is a rewrite rule.** Discharging `Item s ~ Op` is not
enough on its own. In `bf.tl`'s `run` the scrutinee has type `Item s`, and
`match op { Inc(n) -> ... }` cannot look up a constructor until `Item s`
genuinely *becomes* `Op`. So a *given* equality is read as a reduction rule for
the family it names: `Solver.reduce` consults the assumptions before the
instance table, and `Item s` reduces everywhere in the body -- match,
exhaustiveness, field access alike.

Two syntactic restrictions keep that sound and terminating, both checked where
the equality is written. The left side must be a family application, so every
rule has one evident left-hand side; and the right side may not mention it, so
no rule feeds itself. A context may also give a family only one answer: `[Item
s ~ Op, Item s ~ Int]` is rejected there, because a written equality becomes an
assumption and never joins the deferred queue that `improve` inspects. For
equalities that *are* deferred, `improve_families` applies the same rule that
`improve` has always applied to `HasField`, and for the same reason -- a family
is a function of its argument.

**Syntax.** `~` is a new token, and `class-pred` becomes one of two forms.
`parse_context` reads each entry as a type expression and classifies it
afterwards, because nothing shorter can tell `Item c ~ Op` from `Show (Elem c)`
-- they begin identically and the `~` is two atypes away. Destructuring the
result also enforces the one-argument rule the old `CONID atype` production
enforced by shape. A carried equality is oriented family-first when it is
built, so an inferred context reads the way a written one must be written.

**This is what `bf.tl`'s `run` was missing**, and it needed both halves of M10:

```
fun run[Iterator s, Item s ~ Op](tape : Tape, ops : s) -> Unit
```

Delta 39 is what lets the type be written and the `match` reduce; delta 38 is
what stops the recursive `run(tape, loopOps)` from pinning `s` to `Array Op`.

**Scope.** `bf.tl` and its `.types` moved; no other golden did. Declined while
here: a general `σ ~ τ` between two arbitrary types. It is what would make this
a rewrite system needing confluence rather than a table of family definitions,
and it says nothing an annotation cannot. Equalities introduced by a *pattern*
are declined for the reason `CAssume` already gives -- that is what destroys
principal types -- and none is introduced.

### 40. A skolem carries the rank of the binder that made it

Delta 38 made a declared type a promise the body has to keep, and then left the
promise half-open: nothing stopped the constant standing for `a` from leaving
the body it belonged to. Delta 38 recorded that as one limitation and the
rejection of a mixed mutually-recursive group as another. They are the same
limitation, and this is the one line that states it:

> A signature's variable is rigid *inside* the body and quantified *outside*
> it. So it is a constructor only for the length of that body, and no type
> older than the body may be equal to it.

Rigidity was already modelled -- a skolem is a nullary `TCon` (delta 29), and
`unify` treats a constructor as rigid without further machinery. Scope was not.
A `TCon` was ground by construction, so `Int` and a skolem `a` were the same
sort of thing, and a variable born anywhere could be bound to either.

**What changed.** `TCon` gains a `level`, `NO_SCOPE` for every declared
constructor and the rank of its binder for a skolem. `escaping` walks the type
a variable is about to be bound to and answers with the first constant younger
than that variable; `unify` runs it before `occurs_and_adjust`, which lowers
levels and would erase the evidence. The test is one comparison because ranks
already say exactly this -- the same Remy ranks that decide generalization,
read in the other direction.

Ranks belong to the solver, so the solver stamps them: `CLet` carries the
skolems of the body it ranks, `Skolems` records what it made, and `solve_let`
stamps on the way in and unstamps on the way out. The unstamping is not
tidiness. `check_exhaustiveness` unifies constructor types against a solved
scrutinee long after solving, at no rank at all, and a constant still claiming
a rank there reads as an escape when nothing has escaped.

Two skolems that share a name are now distinguished by level as well, since
`Skolems` uniquifies names only within one scope and a nested signature may
write `a` too.

**What this rejects.** The unsoundness first:

```
fun main() {
    let cell = []
    fun f(x : a) -> Int { Array.push(cell, x); 1 }   -- error, delta 40
    print(Int.toString(f(3)))
}
```

`f` promises to work for whatever type a caller picks, so `cell : Array a`
records an equation between a real type and one that has no values yet. Before
this delta it was accepted, and the complaint arrived at some later, unrelated
use of `cell`.

And the mixed mutually-recursive group, which delta 38 listed as a separate
limitation and which is really this one seen from the other end:

```
fun size[Iterator a](xs : a) -> Int { ... other(xs) ... }
fun other(xs) = size(xs) + size(Two { fst = 1, snd = 2 })
```

`other` shares `size`'s SCC and is inferred, so it has one monomorphic
placeholder, which `size`'s skolem flows into. This is a real error and not a
gap: `other` is used at two element types, so it would have to be polymorphic,
and an inferred member of a group cannot be -- that is polymorphic recursion,
undecidable in inference (delta 38). The remedy is a signature on `other` too,
and with one the group checks. What changed is that the diagnostic now says so,
instead of reporting a missing `Iterator a` instance that was never the
problem. Haskell asks for the same signature here, and for the same reason.

**What this does not reject.** Rigidity is not a ban on the skolem *moving*.
Collecting it, handing it to a polymorphic function, and returning it where the
signature wrote `a` are all still fine -- only being equated with something
older is not, which is why the test is on the variable's rank and not on the
constant's presence.

**Scope.** No golden moved. Instance and default method bodies get the check
for free, since `check_method` builds its skolems the same way.

**Also here.** `for pat in e` and the C-style header are told apart by parsing
the first and backtracking, and `in` is the only token that distinguishes them
-- so `in` is now the point of no return. A parse error *after* it is reported
where it is, rather than sending the parser back to re-read the loop as a
C-style header and complain about a missing `;` in a loop whose source has no
`;` in it.

### 41. A program is a graph of modules

`plan.txt` puts modules ahead of everything else because they were already
blocking. The prelude claimed seventeen ordinary names — `add sub mul div rem
neg eq ne lt lte gt gte show iter next print write` — in one flat namespace,
and a program that defined any of them was told `'add' is already defined; a
class method shares the namespace of ordinary functions`. M9 hit this on `add`
and worked around it by renaming. This delta is the repayment.

A program is now a directory. `turkey run Main.tl` loads `Main.tl`, follows its
`import`s against that file's own directory and then the shipped library under
`turkey/lib`, and checks each module in dependency order. §9's surface syntax
was already lexed and parsed; what it means is here.

**Names are made unique rather than scoped.** Every stage after resolution keys
on a flat string — `Env`, `REnv`, `DeclTable`, the evaluator's globals are all
`dict[str, ...]`. A module system could thread *n* of each of those through
every stage, or it could give each top-level binding a name no other module can
collide with. This takes the second road: `turkey/modules.py` works out what
each module can see, `turkey/resolve.py` rewrites the module's AST so that a
top-level `f` in module `M` becomes `M#f`, and nothing downstream learns that
modules exist. The scope is built in §9.3's order — the Prelude, then each
import, then the module's own declarations, later winning — so a local
definition shadows an import and an import shadows the Prelude.

`#` is the separator because no surface name may contain one, so an internal
name can never collide with something a program could write, and a diagnostic
can strip the prefix back off without guessing where the module name ends (an
ordinary `.` would be ambiguous against `Array.push`). `errors.short` does that
stripping once, in `TurkeyError.__init__`, so a message added later cannot
forget: no diagnostic ever says `Main#f`.

**Three kinds of name stay unqualified.** Locals, because they never leave the
scope that binds them. Class methods, because classes and instances are global
(below) and a method means the same thing in every module — which is also what
lets a program define its own `add`: the ordinary binding becomes `Main#add`
and the method stays `add`, and the two coexist. And *operators*.

**The operator hazard.** `a + b` has desugared to a plain `EVar("add")` at
parse time since delta 32. That was safe only while no program could define
`add`; the moment shadowing an import became legal, a module's own `add` would
have silently captured every `+` in the file. The parser now marks the node it
writes (`EVar.method`), and resolution skips a marked node — so `+` still means
`Add.add` in a module whose own `add` concatenates strings. The same mark
covers the `iter`/`next` a `for` loop desugars to.

**Classes, instances and type constructors are global, not scoped.** They are
registered into one shared `ClassTable`/`DeclTable` as each module is checked,
and an import neither adds nor withholds them. Instance coherence is the
reason: a predicate is solved once, and it must not matter which module asked.
Concretely, `Solver._class` and `Elaborator.resolve` must agree about which
instances exist, and a disagreement between them surfaces as an *internal
error* rather than a diagnostic. Global instances is what keeps them in
agreement. The consequence is that an export list's type and class entries
(`Point(..)`, `Eq(..)`) parse and are accepted but withhold nothing; a
constructor is reachable from any module, qualified or not. Delta 43 is where
that is repaid.

**The import graph must be acyclic.** Two modules that need each other are one
module here: the checker solves a whole module's bindings as one
dependency-ordered pass, and nothing would interleave two. A cycle is reported
by name (`imports form a cycle: Odd -> Even -> Odd`).

**The prelude is a file.** `turkey/lib/Prelude.tl` is ordinary source, loaded
like any other module and implicitly imported by every one. It is checked, not
trusted — `class Add a` and `instance Add Int` go through exactly the machinery
a user's would. `turkey/prelude.py` keeps only what the *compiler* has to know:
which method an operator desugars to, and what a `for` loop is written in terms
of. `Prim.*` is in the shared environment so that the prelude can be checked
against it, and is kept out of the language one stage earlier: a module's scope
spells `Prim.intAdd` only if the module lives under `turkey/lib`.

**Two smaller fixes fall out.** §7's alias-vs-data question is decided by a
token pre-pass over `type CONID` in one file, so a type name declared in
*another* file was invisible to it and `type Foo = Bar` with an imported `Bar`
would have misparsed; the loader threads the known type names into `parse`. And
`import qualified M as S (f)` did not parse at all — the parser's `elif` chain
made `as` and a selective list mutually exclusive.

**A span knows its file.** `Span` gained `file`, set by the lexer, so a
diagnostic in an imported module names that module rather than the entry point.
It is `None` for the entry file, which keeps single-file diagnostics printing
the name the CLI was given.

**Scope.** No golden moved. `tests/programs/err_modules_unsupported` is gone,
replaced by three multi-file programs — a directory under `tests/programs/`
whose entry is `Main.tl` is now a program — and `tests/test_modules.py` covers
the scoping rules themselves. Three tests changed because what they asserted is
what this delta reverses: a method and a top-level function may now share a
name, and a program may define `add`.

### 42. The library is written in the language

Delta 32 moved the operators out of the checker and into the prelude, and named
the rest of the debt: `Data.Array`, `Data.String` and the other §8 modules were
still Python entries in the initial environment, registered under two spellings
each (`Array.push` and `Data.Array.push`) because there was no module system to
tell them apart. Delta 41 built the module system. This is the move.

`turkey/lib/Data/{Array,Bool,Char,Float,Int,Option,String}.tl` are ordinary
source, checked exactly as a program is. What is left in `turkey/builtins.py`
is the floor they stand on: `Prim.arrayPush`, `Prim.intToString`,
`Prim.stringChars` and the arithmetic, under names a module outside
`turkey/lib` cannot spell. `_CORE` and the `_ALIAS_PREFIXES` table are gone.

**`Bool` and `Option` moved out of the Prelude**, into `Data.Bool` and
`Data.Option`, because a type belongs in the module that is about it — and
under delta 43's orphan rule that is where an instance for it will have to be
able to live. `Bool.toString` is a `match` now rather than a Python attribute
read, and `Option` gained `isSome` and `unwrapOr`. Neither type is built in:
`if` demands `Bool` by name (delta 36) and `Prim.arrayPop` answers `Option` by
name, both of which work because a `TCon` is compared by name.

**A module re-export, not a name re-export.** The Prelude reads:

```
module Prelude (print, write, error, module Array, module Bool, ...) where

import qualified Data.Array as Array
import qualified Data.Bool  as Bool
...
```

`module M` is §3.1's export form, and here it means: everything in scope under
that qualification, passed on *still qualified*. So an importer of the Prelude
— which is every module — gets `Array.push` and `Int.toString` with no import,
and the bare names `push`, `new`, `pop` and `toString` are **not** claimed and
stay free for a program to define. That is the second half of `plan.txt` item
3: the first half (delta 41) freed the seventeen class-method names, and this
frees the library's.

Our `module M` is not quite Haskell's, which exports the entities in scope
*both* as `M.e` and as `e`. Ours re-exports the qualified spelling and only the
qualified spelling, which is the whole point — a re-export that claimed bare
names would put the papercut straight back.

The long spelling is no longer a free alias: `Data.Array.push` is what the
function is, and a program that wants to write it says `import qualified
Data.Array`. `error` stays a bare Prelude export, since it has no module to
belong to.

**`ExportItem`.** An export or import list entry was a string with the
sub-names glued back on (`"Point(..)"`). It is a node now, with a `kind` that
distinguishes `module M` from an entity, because the two are not the same shape
of thing. `module` is rejected inside an import's name list: a module
re-exports, it does not import.

**Scope.** No golden moved, which is the acceptance test for this delta: all 93
`Array.*` and `Int.toString` call sites in the conformance programs resolve
through the re-export unchanged. `turkey/lib` ships as package data.

### 43. A type constructor is qualified, and an instance is not an orphan

Delta 41 made a *value* belong to its module and delta 42 moved the library
into the language. What was left global was the type namespace: every
`TCon`, every data constructor, keyed on a bare name in one flat table. So two
libraries that each declared a `Node` could not be used in one program, which
is the thing a module system exists to make possible.

**A type and its constructors are qualified**, the same way and by the same
pass: `type Point` in module `Geometry` is `Geometry#Point`, and so is its
constructor. They are separate namespaces from values and from each other,
because `type Point = Point(Int, Int)` puts one spelling in two of them, so
`turkey/modules.py` carries three maps per module rather than one.
`type_key`, `match` and unification are untouched: all three already compared
`TCon.name`, and the name is simply longer now.

**What a reader sees does not change.** `TCon.display` is the short name, so
`fun() -> Point` is still what a signature prints — the module a type came from
is not news to the person who wrote the file. Two modules that declare the same
short name are the exception: neither may keep it, and both print qualified
(`Shape.Node`, `Graph.Node`), because a message saying `Node` twice says less
than one that says which is which. `DeclTable` notices the clash while
registering, and records it in `types.QUALIFY` rather than on the constructor —
the same constructor exists as more than one `TCon` object (`types.BOOL` and
the one `Data.Bool` registers), and both have to agree.

A skolem stays out of the qualified space: it is a constructor for the length
of one body, named after the type variable it stands for, and it belongs to no
module.

**A constructor may be qualified where it is written.** `G.Point(1, 2)` in
expression position, `G.Point(x, y)` in a pattern, and `S.Node` in type
position — the last needed a parser change, since a dotted CONID chain was only
read as one name in expression position. This is what §9.3's "the constructor
must be qualified, or an error is raised" needs, and it is what makes an
`import qualified` actually qualify: before this delta a constructor came into
scope bare no matter how it was imported.

**An export list finally withholds something.** `T` exports the type alone and
`T(..)` its constructors too, which is the difference between an abstract type
and a transparent one — `module Opaque (Token, make)` lets an importer hold a
`Token` and not take one apart. Class entries are still accepted and still
withhold nothing, because a class is global.

**Coherence: global instances, plus an orphan rule.** Instances remain global
— every module sees every one — because a predicate must mean the same thing
wherever it is solved. Concretely, `Solver._class` and `Elaborator.resolve` have
to agree about which instances exist, and a disagreement between them surfaces
as an *internal error* rather than a diagnostic; keeping them global is what
keeps them in agreement. The overlap check is unchanged and was already
whole-program, since the table is shared and accumulates as each module is
checked.

Global and *unrestricted* is what would make coherence a matter of luck: two
libraries could each write `instance Show Point` over someone else's `Show` and
someone else's `Point`, and whichever loaded second would be the error, in a
file neither author wrote. So an instance must be declared in the module that
declares its class or the module that declares its head constructor. A built-in
head (`Int`, `String`, `Array`) belongs to no module, so an instance over one is
legal only from the class's own module — Haskell's rule, and `plan.txt` flagged
it as cheap now and expensive later.

**Bool and Option may now be shadowed.** Delta 36 recorded that redeclaring
`Bool` was an ordinary "declared more than once" collision. It is not any more:
`Bool` belongs to `Data.Bool` (delta 42) and a type belongs to its module, so a
program's own `type Bool` shadows the import and is a different type. `if` still
demands the library's, which is what stops the shadow from being a way to break
the language — `if A { ... }` over a local `Bool` is now
`expected Main.Bool, found Data.Bool.Bool`. The same goes for `Option`.

**Scope.** No golden moved except `tests/programs/modules/Main.tl`, which now
writes `G.Point` where it wrote `Point` — the deliberate change. Two tests
changed because they pinned the redeclaration collision this delta removes.

### 44. Concatenation is `instance Add String`, and `++` is gone

Delta 32 made every arithmetic and comparison operator a class method and left
three entries behind in §8.2's table. Two of them, `&&` and `||`, are there for
a reason no class can express: they short-circuit, and a function call does not.
The third, `++`, was there for no reason at all — only because delta 32 looked
for a `Semigroup` to put it in and there wasn't one.

There did not need to be. `Add` is `class Add a { fun add(a, a) -> a }`, which
is exactly the shape of concatenation, so **`instance Add String` is the whole
change** and `"a" + "b"` is `add("a", "b")` like any other `+`. The primitive it
calls, `Prim.stringConcat`, joins the other `Prim.string*` operations that
delta 42 moved the library onto.

Nothing about `Add` had to bend to accept it. The class is per-operator rather
than one omnibus `Num` (delta 32), so `String` adding does not oblige it to
subtract or divide, and `"a" - "b"` is still a missing instance rather than a
runtime error. The instance is not an orphan under delta 43 either: `Add` is the
Prelude's own class, and it is declared in the Prelude.

**What this removes.** The token, its precedence row, the last entry in
`infer.BINARY_OPS` that is not short-circuiting, and `eval.BINARY` entirely —
the evaluator now has no binary-operator table at all, only the two
short-circuit cases and the method path. `String` gains nothing it did not have
and the language loses an operator that a program could not have written for
itself.

**What it costs.** A concatenation is a dictionary-mediated method call where it
used to be a builtin, which is the same trade delta 32 already made for `+` on
`Int`. And a use of `+` no longer pins its operands to `String` on sight: `fun
add(x, y) = x + y` was `fun(String, String) -> String` when written with `++`
and is `[Add a] fun(a, a) -> a` now, which is more general and more honest. One
test that relied on that pinning writes the annotation instead.

**Scope.** Every `.tl` that concatenated changed spelling; no `.expected` and no
`.types` golden moved.

### 45. `Functor`, `Applicative` and `Monad`, and the library grows an `Either`

`plan.txt` item 4 wants `?`: Rust's operator, generalized past `Result` to any
monad. Before any of that can be spelled there has to be something for it to
mean, and there was nothing — no `Monad`, no `bind`, no `pure`, no `Either`
anywhere in the language or its library. This entry is that groundwork and no
syntax at all; `?` itself is delta 46.

**The chain is the one everybody knows.** `class Functor f`, then
`class Applicative f : Functor f` adding `pure`, then `class Monad m :
Applicative m` adding `bind`. Only `bind` is needed to give `?` a meaning, and
the argument for the other two is not fidelity to Haskell: a `Monad` with no way
back in from a plain value is one nobody can write a generic function against,
and the lowering of `?` in delta 47 emits `pure` itself.

**Nothing in the checker moved.** A class whose variable has kind `* -> *` has
been expressible since M4, and `bind`'s own signature is the only evidence that
`m` is one — `m a` appears in it, so the kind is discovered exactly as
`type Wrap f a = Wrap(f a)` discovers it. `pure`'s class variable appears only
in its result, which is the shape `Monoid.empty` already had and the reason M6
passes dictionaries rather than selecting them from an argument. `instance Monad
(Either l)` is a partially applied head, which delta 29's Haskell 98 rule has
always allowed. Three classes and nine instances, all of it ordinary source in
`turkey/lib/Prelude.tl`, checked by the same machinery a program's own would be.

**`Either` is declared beside its functions**, in `turkey/lib/Data/Either.tl`,
the way delta 42 put `Option` in `Data.Option` — and re-exported by the Prelude
both qualified (`Either.isRight`) and unqualified (`Either(..)`), so `Left` and
`Right` need no import. `Option` earned its unqualified place by being named by
a rule of the language, since `next` answers one. `Either` has no such claim; it
is there because `?` is only worth having over more than one monad, and "failure
that says why" is the second one.

**The instances are right-biased because a class has one parameter.** `Monad
(Either l)` fixes `l` and varies `r`, so `map`, `pure` and `bind` can only work
on `Right` and pass a `Left` through. The bias every language's `Either` has is
that fact rather than a taste, and §8.2's homogeneity paragraph is the same
constraint seen from the other side.

**`Array` is a monad, and that is the point.** Its `bind` invokes the
continuation once per element, so `bind([1,2,3], fun(x) = bind([10,20], fun(y) =
pure(x*y)))` is the cross product. A monad whose continuation runs zero times
(`None`), once (`Some`), and many times (`Array`) is what settles the question
delta 46 has to answer: `?` means the instance's `bind`, and cannot mean an
early return, because for `Array` there is no single value to return early with.

**What it costs.** Three ordinary names. `map`, `pure` and `bind` are the
prelude's now, and a program cannot define its own — which takes `plan.txt`
item 3's count of names the prelude claims from seventeen to twenty, and `map`
is the one most likely to be wanted back. `?` did not require this: it reaches
`bind` the way `+` reaches `add`, through a node marked at parse time that
resolution leaves alone (`turkey/resolve.py`), so the methods could have stayed
qualified. Exporting them is a choice made for the reader, and the cost is
stated here rather than left to be discovered.

A second cost, smaller and worth knowing: **a class is a single global name**,
where delta 43 qualified types and instances by the module that declared them.
So a program that declares its own `Functor` now collides with the prelude's,
where a program declaring its own `Either` merely shadows and prints as
`Main.Either`. Two goldens declared a `Functor` and now consume the prelude's
instead. Whether classes should be qualified too is a real question and not this
entry's to answer.

**Scope.** `classes.tl` and `dicts.tl` lost their local `Functor` — the first
keeps its `instance Functor (Either l)` over its own `Either`, which is still
what shows a partially applied head — and both `.types` goldens lost their `map`
line. `tests/test_classes.py` renamed its fixture class to `Mappable`/`over` and
dropped the local `Either` its helpers prepended. New golden `monads.tl`, with
`.expected` and `.types`, writes the chains out by hand: it is what delta 46's
sugar has to agree with, and worth reading in longhand once before any of it is
hidden.

### 46. `?` is the instance's `bind`, and `do` says where it unwinds to

Delta 45 put `Monad` in the library and left it to be called by hand.
`plan.txt` item 4 wanted the operator: Rust's `?`, generalized past `Result` to
any monad. This is that, for everything except loops and the control transfers
that cross one -- which are delta 47.

**`?` is not an early return, and could not be.** Rust's `?` is a non-local
exit, and that reading works only because `Option` and `Result` are the
try-shaped monads: there is one value to leave with. `Array` is a monad too
(delta 45), its `bind` runs the continuation once per element, and "return
early" names nothing there. So `e?` is `bind(e, fun(x) { <the rest> })` and
nothing else, and every question below is answered by asking what the
instance's `bind` does rather than by a rule of the language.

**It cannot be a node, and that is the only structural novelty here.** Every
other sugar in the language is a node carrying the method it means: `a + b` is
an `EBinary` holding `add` (delta 32), `for x in xs` an `EForIn` holding `iter`
and `next` (delta 33), and `turkey/infer.py` and `turkey/eval.py` each read that
field. `?` cannot work that way, because what it binds is *the rest of the
enclosing statement sequence*, which no node annotated in place can name. So
there is a pass, `turkey/desugar.py`, and it is the first one in this
implementation that rewrites the tree rather than annotating it.

The upside of paying that once is that nothing else changes at all. `?` and `do`
are gone before the tree reaches the checker, so `turkey/infer.py`,
`turkey/eval.py`, `turkey/deps.py` and `turkey/exhaustive.py` know nothing about
this feature -- no cases, no fields, no `Monad` special case anywhere. The
`Monad m` obligation is raised by `bind`'s own scheme, the same way `Add a` is
raised by `add`'s, and the dictionary arrives by M6's ordinary path.

**It runs after resolution.** `turkey/driver.py` calls it between `Resolver` and
`Generator`. Both halves of that matter: the code the pass lifts into lambdas has
already had its names settled, so lifting it cannot change what one means; and
the code the pass writes may name internal constructors directly rather than
hoping a module exported the surface spelling. Generated binders are `%k1`,
`%k2`, and `%` is not a character an identifier may contain, so a generated name
cannot capture one the author wrote -- the guarantee `%sig.{name}` already
relies on.

**A do-context is what a `?` unwinds to, and there are two:** an explicit
`do { ... }`, and the body of a `fun` or lambda containing a `?`. Everything
else is transparent -- `if`, `match`, a bare block -- and a `?` inside one lifts
outward through it. A lambda is opaque, so a `?` in a callback belongs to the
callback and the enclosing function is not monadic at all.

Which settles the open question `plan.txt` left about an empty or `?`-free `do`:
**it emits nothing whatsoever.** No `bind`, so no `Monad` obligation, so nothing
for it to be ambiguous about. `question.tl`'s `plain` is `fun(Int) -> Int` in
the `.types` golden, and `do { }` is `Unit`. `do` is a scoping marker and not a
mode.

**Straight-line code comes out as the chain a person would write.** One `bind`
per `?`, the rest of the block inside the lambda, and statements before the
first `?` left exactly where they were -- a block splits only where it must.
`tests/test_desugar.py` asserts the shape directly rather than inferring it from
output, because "`?` is sugar for `bind` plus a lambda" is a claim about a tree.

**A control construct holding a `?` is lifted into the monad.** An `if` branch's
value is `Unit` by the language's own rule, not `m Unit`, so a `?` inside one
cannot be hoisted past it. Instead each branch is translated with `pure` as its
continuation and the `if` becomes the left argument of a `bind`; `match` is the
same, per arm. In *tail* position neither the `pure` nor the `bind` is emitted:
there the branches already are the do block's tail, and the tail of a do block
is the monadic value. So `if` costs nothing where it is the answer, and costs a
join where it is a statement.

`&&` and `||` are read as the `if` they already mean when a `?` is on the right.
§8.2 says these two are not class methods because they short-circuit and no
function call does -- and no argument to `bind` does either, so hoisting the
right operand out would have quietly evaluated it. Under a branch it stays
unevaluated: `guarded(0, None)` answers rather than failing.

**The `pure` the lowering inserts is not the auto-`pure` that was ruled out.**
That rule is about the tail of a do block, and the tail is still taken to be
already monadic -- `do { a? }` is a type error, from ordinary unification, with
no special case. The inserted `pure` appears only where the lowering lifts
something whose value was `Unit` by rule into a monad it never asked to be in.

**What it costs.** Two things, both stated rather than discovered.

An unwritten invariant became observable. Closure capture in this language is by
reference over a shared mutable scope chain, and `Generator.is_mutable` walks
scopes with no function barrier -- so a lambda that writes an enclosing `var`
writes through to it. No document said so, because until `?` no program could
easily notice. Now everything after a `?` is inside a lambda, and
`question_capture.tl` pins what follows: under `Some` the write happens once,
under `None` never, and under `Array` once per element *sharing one counter*, so
`each([7, 8, 9])` is `[107, 208, 309]`. That is not a decision about `?`. It is
the instance's `bind`, which is what `?` was defined to be.

And `do` is a reserved word now, so a program cannot use it as a name.

**Scope.** New `turkey/desugar.py`; `?` and `do` added to `turkey/lexer.py`'s
operator, keyword, `CAN_END` and `CAN_START` tables; `EQuestion` and `EDo` in
`turkey/ast.py`, consumed by the pass and deliberately not deleted, since a
lowering straight to a Core IR will want a sugared tree to lower and this pass to
check itself against; a suffix case in `parse_postfix` and an atom case in
`parse_atom`; two passthrough cases in `turkey/resolve.py`; one line in
`turkey/driver.py`. New goldens `question.tl` (with `.types`),
`question_capture.tl`, and three `err_question_*.tl`, two of which record what
delta 47 has yet to do. New `tests/test_desugar.py`. No existing golden moved.

### 47. What crosses a bind is a value

Delta 46 gave `?` its meaning and refused two things: a `?` inside a loop, and a
`return`, `break` or `continue` after one. Both refusals were the same gap seen
twice, and this closes it. `plan.txt` item 4 is now done except for the fast
lowering, which belongs after items 5 and 6.

**A control transfer cannot escape through a `bind`.** After a `?`, everything
that follows is inside a lambda, so a `return` there means "return from the
lambda" -- not what anyone wrote. The obvious repair is to let it escape: the
evaluator implements `return` as an exception, so it would propagate out through
`bind` for free, and for `Option` and `Either` that even gives Rust's semantics.

It is still wrong, and delta 45's `instance Monad Array` is why. That `bind`
runs its continuation once per element. An escape out of the third of five
branches is not "the function returned" -- there is no single answer to return
-- and any behaviour it had would be a fact about the interpreter rather than
about the program. Escaping is not something a `bind` does.

**So it travels as a value**, since a value is the only thing a `bind`
propagates. The Prelude gains `type Flow a b r = Fall(a) | Brk(b) | Cont |
Ret(r)`, declared and exported by nobody, exactly as `ArrayCursor` is: it exists
so that the lowering has something to say, and no program can name it. A context
that needs it runs in **flow mode** -- every statement answers with `m (Flow
...)`, the next runs only under `Fall`, and the other three are rebuilt and
passed along. Three parameters because the three values are three types: a
block's own, its loop's, and its function's.

**The do-context's boundary is where `Ret` becomes a result again**, with one
`match` at the end of the function body. `Brk` and `Cont` cannot arrive there --
a `break` outside a loop is refused before this pass -- but the type system has
no way to know that, so those arms exist and call `error`, which diverges and so
can claim any type (§10). An unreachable arm that says so is better than a
partial match.

**Flow mode is not switched on for a block that does not need it.** The test is
whether a transfer sits at or after the *first* statement holding a `?`:
everything before that stays in the prefix, at the nesting the author wrote it
at, where a `return` still is one. So `question.tl`'s `early` lowers exactly as
it did before this entry, and the machinery here shows up only where the
alternative was being wrong.

**A loop is the same problem twice over.** Its continuation is not known until
its body has run, so it cannot be a lambda written once, and it becomes a
recursive local `fun` that answers with a `Flow`. `Fall` and `Cont` both mean
"go round again" and differ only in where they came from; `Brk(v)` becomes the
loop's own `Fall(v)`, which is what lets `let v = loop { ... break x }` keep
working; `Ret` keeps travelling. A C-style `for`'s step runs on `Cont` as well as
on `Fall`, or a `continue` would not terminate.

`for x in xs` is expanded to its cursor form (§6.5) *here* rather than left to
`turkey/infer.py`, because only the expansion has a loop to lift. That is the
first time that desugaring exists as a tree rather than as an agreement between
the checker and the evaluator.

**What it costs, stated exactly.** `Flow` is not part of `Monad`. It appears in
no signature -- `bind` is still `fun(m a, fun(a) -> m b) -> m b` -- and an
instance never writes it, never sees it and could not match on it if it wanted
to, since the constructors are exported by nobody. All the lowering does is
instantiate the method's own type variable with it at the call sites it
generates.

What *is* observable is one step removed, and it is not special to `Flow`: **the
shape of the bind chain**. How many times the lowering invokes a continuation,
and in what order, is part of what a program means as soon as the instance is
free to run its continuation other than exactly once. That was already true
before this entry -- delta 46's tail-position rule, which drops a `pure` and a
`bind` where the branches are the do block's tail, changes a bind count.

So the constraint on a later lowering -- the join-point one `plan.txt` defers
behind items 5 and 6 -- is that it reproduce the *chain*, not that it reproduce
`Flow`. Anything answering with the same sequence of continuation invocations may
represent a transfer however it likes, join points and no sum type included.
That is why design.md §6.9 writes the translation out and
`tests/test_desugar.py` asserts it: the chain is the specification, and the
encoding is only this lowering's way of producing it.

The reading to avoid is that carrying a transfer as data gives it some meaning
of its own. It does not. `return` under `Array` is the plainest case: in a
five-element bind it does not stop the other four, it replaces one branch's
answer, so `fun(xs) { let x = xs?; if x == 2 { return [999] }; pure(x * 10) }`
over `[1, 2, 3]` is `[10, 999, 30]` -- and `bind(xs, fun(x) = if x == 2 { [999] }
else { pure(x * 10) })`, with no `?` and no `Flow` anywhere, is the same thing.
"Return" means "this branch's answer is that" because in a nondeterministic
monad there is nothing else it could mean.

The sharpest illustration is `question_control.tl`'s `spread`, which answers
`[90, 90, 90]`. Three, not four, because iteration is a cursor (delta 33) and a
cursor is one mutable object: the list monad explores depth-first, the branches
share the cursor, and the first to run exhausts it. All three are `90` because
the counter is one mutable cell, read after the traversal finished mutating it.
None of that is a decision `?` made, and the file proves it -- `spreadByHand`
beside it is the same loop with the recursion and the `bind` written out, no `?`
anywhere, and it prints the same thing. That is the differential test `plan.txt`
asked for, at the one point where the answer is surprising enough to want one.

**Scope.** `Flow` added to `turkey/lib/Prelude.tl`, unexported;
`turkey/desugar.py` gains flow mode and the loop lowering. `err_question_in_loop`
and `err_question_escape` are **deleted** -- they recorded delta 46's gap, and
the gap is closed. New golden `question_control.tl`, with `.expected` and
`.types`. No other golden moved, and `question.tl`'s lowering is unchanged.

### 48. Every expression knows the type it was given

`plan.txt` item 5 asks for a typed Core IR, on the grounds that the elaborator
(delta 30) is already a proto-Core that has no datatype. Before any of that can
be built there is a precondition, and this entry is only that: **inference stops
throwing away the types it computed.**

**Today it throws away nearly all of them.** `Generator` gives every expression
a type, uses it to build the constraint, and drops it. Afterwards the only
per-node record in the whole compiler is `match_sites`, kept since M7 so that
exhaustiveness can see a scrutinee's type. Per-*binding* schemes survive, in the
environment, which is what `turkey types` prints -- but a scheme is not what a
lowering reads. A lowering reads every node.

**The cheap part is cheap because `Type` is mutable union-find.** A `TVar`
stashed during generation is not a snapshot: solving fills in `ref`, and `prune`
reads the answer out later. So recording costs one dictionary write per
expression at generation and nothing at all at solving time -- no second pass,
no zonking, no copying back. `match_sites` has relied on exactly this since M7;
`turkey/typed.py` is the same trick applied to every expression rather than to
`match` scrutinees.

**The table is not a field on `ast.Expr`, deliberately.** Mechanically it could
not be: every expression node declares non-default fields, so a defaulted field
on the base class is a `TypeError` at class creation. But the real reason is
that the AST already carries three side channels -- `EVar.use`, `FunDecl.dicts`,
`SLet.dicts` -- typed `object` and trusted by the evaluator, and the point of
what follows this entry is to *delete* them. Adding a fourth on the way there
would be moving backwards.

**What a use site instantiated its scheme at is now recorded too**, beside the
predicates it already recorded and by the same line of the solver. The two are
halves of one story: the predicates say what evidence an occurrence costs, the
type arguments say what the occurrence instantiated the scheme *at*.
`instantiate_qual` built both and returned one, because until there was
something to lower to, nothing downstream asked. A type application in a
System-F-ish Core is precisely that argument list, in `scheme.quantified`'s
order -- which is the same order a type abstraction at the definition binds in,
so the two need no separate agreement to keep, exactly as `Abstraction.preds` is
already the order of the dictionary parameters.

**Reading a type back reduces families throughout, and `types.normalize` does
not.** That is not an oversight there; it says so, and gives the reason: "Only
the head is reduced. A family buried inside an argument is reduced when
something compares it, which is the only moment its value can matter."
Unification only ever compares heads, so head-only is exactly enough for it. A
table something reads *whole* is the case that module did not have. `iter(xs)`
answers `Cursor (Array Int)` and `next` answers `Option (Item (Array Int))`:
both families sit under a constructor, and a Core term annotated with either
would carry a type that was never reduced and would not check. So
`TypeTable.resolve` reduces at every level, and `adt.tl`'s loop records
`fun(Array Int, ArrayCursor) -> Option Int`.

**What must not survive, and what may.** Two things would make a recorded type
not a type. A `TSet` -- a numeric literal whose type is still a *decision*
(delta 32) -- must be gone, and is: across all 38 non-error golden programs,
11,470 recorded expressions, none unresolved. A `TFam` is different: 28 survive,
and all 28 should. `Elem c` where `c` is bound by the enclosing signature is
rigid, and as much a type as `Int` is -- a type abstraction binds the `c`. What
must not survive is a family still *waiting* on an instance, and that is
rejected during solving (delta 39), not here.

**A generalized binding records its own bound variable, and that is correct.**
In `let n = 1`, `n` is non-expansive and generalizes, so the literal's recorded
type is the variable the scheme quantifies, not `Int`. The `Int` appears where
it is decided, at the use site. This looks like a gap and is the opposite of
one: it is what a typed Core says out loud, since the definition becomes a type
abstraction and inside one the bound variable *is* the type. Recording anything
else would be recording a lie.

**What it costs.** One dictionary entry per expression, for the life of the
check -- roughly 11,000 on the largest golden, which is bounded by the program
and freed with it. Nothing observable changes: no output moved, no scheme
changed, no diagnostic changed, and the evaluator does not know the table
exists. `instantiate_qual` returns a triple instead of a pair, which has one
caller.

**Scope.** New `turkey/typed.py`; `Generator.gen_expr` records in one place, so
no case can forget; `Solver.solve_instance` fills in `Use.type_args`;
`Checked` carries the table. New `tests/test_typed_ast.py`. No golden moved --
this entry adds nothing a program can see, which is the point of doing it
separately.

### 49. The elaborator has a datatype, and evidence is checked

Delta 30 made classes run by passing dictionaries, and left the elaboration as
a set of mutable side objects hung on surface AST nodes: a `Use` per
occurrence, an `Abstraction` per binding group, an `InstancePlan` per instance.
`ast.py` types the first of them `object`. Nothing ever checked any of it. This
entry gives it a datatype and a typechecker, which is `plan.txt` item 5's
sentence exactly: it "makes evidence checkable rather than trusted".

**What was actually at stake.** A dictionary in the wrong position was not a
compile error. It was a wrong answer, or an `AttributeError` raised from inside
the interpreter at whatever later moment the missing method was reached. The
elaborator was correct, as far as anyone knew; "as far as anyone knew" was the
problem, and it is the same problem the whole prototype exists to avoid
elsewhere.

**A class becomes a record type.** `class C a` gives `%Dict.C a`, with a field
per method and a `%super.S` field per superclass. `%` cannot start an
identifier, so no program can name it. That is the whole trick and everything
else follows: a dictionary now has a type to be *wrong* about, so passing a
`Monoid Int` where a `Semigroup Int` belongs stops typechecking.

**An instance becomes a top-level binding.** `%inst.C.Con`, unique because
instances are coherent (delta 43). One with a context becomes a function from
dictionaries to a dictionary, which is what `instance [Show a] Show (Array a)`
always meant -- the evaluator built it on demand and memoised it on object
identity, and here it is an ordinary binding an ordinary application uses.

**Evidence stops being a language.** `FromDict(name, path)` is a variable and a
chain of field projections; `FromInstance(inst, args)` is a variable applied to
the evidence for its own context. `Absent` is a call to `error`. There is no
`Evidence` in Core, because there is nothing for it to be: it was a second,
unchecked term language that only the evaluator understood.

**A use site is a type application and then a value application**, in that
order, because that is the order the two were abstracted in:

    print(x)  ==>  Prelude#print[String](%inst.Show.String)(x)

**Core has no statements.** A block is nested `CLet`s and a `var` is a
reference cell -- `CRef` makes one, `CDeref` reads it, `CAssign` writes it.
That last is the one place this entry writes down something the language never
wrote down. Capture by reference was an accident of the evaluator's scope
representation (`eval.py`'s shared mutable `REnv` chain), invisible until
delta 46 put the rest of a block inside a lambda and delta 47's `spread` made
it decide an answer. In Core it is a cell, so capture is by value of a
reference and the reproduction is checkable rather than incidental.

**The checker runs on every compile.** Not behind a flag and not only in tests:
"evidence checkable rather than trusted" is not true of a check nobody runs, so
it runs where exhaustiveness runs, unconditionally, in `driver.check`.

**Three things it found immediately**, all of them real and none of them
visible in any program's output:

* A **default method** is elaborated once, against the *class* variable, and
  shared by every instance that does not override it. Copying that body into
  each dictionary put `a` where `Int` belonged. It is now one top-level
  `%default.C.m`, polymorphic in the class variable, applied at each head.
* A **method body is checked against skolems** (delta 40), so every type
  recorded inside it names rigid constants where the class's declaration names
  variables. So is the body of any `fun` with a complete signature (delta 38).
  Both now record which constant stands for which variable, because nothing
  else did and the two are the same types.
* A **recursive call** is monomorphic to inference -- correctly; there is
  nothing to record yet -- and still needs its type application in System-F, at
  the binding's own variables. `isEven` calling `isOdd` is the case.

**What the checker derives rather than reads.** A `CAlt` records no binding
types. The checker works them out from the scrutinee's type and the
constructor's declaration, so a pattern that does not fit its scrutinee is a
rejected term. That check did not exist before: in the surface language the
same fact was established by unification during inference and then forgotten.

**What it is slack about, deliberately.** `⊥` absorbs, because it does
(decision 12) -- a branch that returns is compatible with one that does not. A
free variable matches anything, so a term that is *more* general than expected
is not rejected. And an equality the binding's context states is a reduction
rule while that binding is checked, consulted before the instance table exactly
as `Solver.reduce` consults its assumptions first: `Item s` over a rigid `s`
never reduces through an instance, and `bf.tl`'s `run[Iterator s, Item s ~ Op]`
needs it to become `Op` for its `match` to typecheck at all.

**What it costs.** A second traversal of every program at every compile, and
three modules -- `core.py`, `lower.py`, `coretc.py` -- to keep in step with the
elaborator they re-express. The evaluator does not use any of it yet, so for
this entry the Core is a second opinion; delta 50 is what makes it the program.

**Scope.** New `turkey/core.py`, `turkey/lower.py`, `turkey/coretc.py`; a
`turkey core` subcommand printing the entry module's Core, as `turkey types`
prints its signatures. `evidence.MethodImpl` and `evidence.Abstraction` record
skolem maps and schemes the solver had been discarding. New
`tests/test_core.py`, including the negative cases -- a checker exercised only
on correct input is asserted to accept, which is the half that cannot fail.
New `.core` goldens for `dicts`, `monads` and `question_control`. **No
`.expected` and no `.types` moved**, which is the point: this entry adds a
check and changes nothing a program can see.
