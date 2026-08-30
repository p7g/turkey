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

### 22. Output and conversion primitives — **amended at delta 33**

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
to belong to until there is a `Semigroup`.

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
