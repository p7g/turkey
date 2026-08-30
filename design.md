# Language Specification

## 1. Overview

A minimal procedural programming language with an ML-style type system and Hindley-Milner type inference. Strict call-by-value evaluation. Functions are uncurried. Mutable state is supported through mutable record fields and arrays. The runtime representation of values is opaque and left to the compiler.

### Key design decisions

| Aspect | Decision |
|---|---|
| Evaluation | strict, call-by-value |
| Functions | uncurried, fixed arity, first-class |
| Polymorphism | Hindley-Milner, `let`-generalization with value restriction |
| Recursion | explicit via `fun`; SCC-grouped inference |
| Mutation | `let`/`var` control binding mutability; record fields and arrays are mutable |
| Data types | single-variant records are mutable (reference semantics); multi-variant ADTs are immutable |
| Arrays | primitive mutable type, reference semantics, dynamic capacity |
| Modules | Haskell-style, explicit exports, qualified imports |
| Runtime | opaque; compiler chooses representation |
| Type syntax | `fun(Int) -> Int` for function types; `-> τ` for return annotations; `e : τ` for expression annotations |
| Naming | lowercase = variables; uppercase = type and value constructors |

---

## 2. Lexical Structure

### 2.1 Tokens

```
IDENT    ← [a-z_][A-Za-z0-9_']*
CONID    ← [A-Z][A-Za-z0-9_']*
INT      ← [0-9]+
FLOAT    ← [0-9]+\.[0-9]+
STRING   ← "..."   (escapes: \n \t \\ \" \uXXXX)
CHAR     ← '...'
```

**Reserved words:** `type fun let var match if else while for in loop return break continue module import export as qualified hiding where`

**Operators and punctuation:**
```
=  ==  !=  <  <=  >  >=
+  -  *  /  %
++ && ||  !
-> :  ;  ,  .  |  {  }  (  )  [  ]
```

### 2.2 Comments

- `--` to end of line.
- `{- ... -}` for nested block comments.

### 2.3 Naming convention

- **Lowercase** identifiers (`a`, `x`, `myFunc`, `count`) are **variables**: they bind values in patterns and refer to values in expressions. In type expressions, lowercase identifiers are **type variables**.
- **Uppercase** identifiers (`Int`, `Counter`, `Nil`, `Some`) are **type constructors** (in type positions) or **value constructors** (in pattern/expression positions).

This distinction resolves syntactic ambiguities: in a function literal's parameter list, an uppercase identifier is a constructor pattern (not a variable), so `fun(Int) -> Int = ...` is a type error (no value constructor named `Int`).

### 2.4 Newline rule

A `NEWLINE` token terminates the current production iff **both**:

1. **Preceding condition:** the previous token can legally end the current production. Accepting tokens: `IDENT`, `CONID`, literals, `)`, `]`, `}`, type annotations.
2. **Following condition:** the next non-`NEWLINE` token can legally start a new sibling production. Starting tokens: keywords (`let`, `var`, `fun`, `type`, `if`, `match`, `while`, `for`, `loop`, `return`, `break`, `continue`, `module`, `import`), `IDENT`, `CONID`, literals, `(`, `[`, `{`, unary `-`, unary `!`.

If only one condition holds, the `NEWLINE` is dropped (treated as whitespace). Inside `(...)`, `[...]`, or `{...}` of a brace-delimited block, `NEWLINE` is also dropped except where the inner grammar uses it as a separator (e.g., between `match` arms, between block statements).

A `;` may always be used in place of a significant `NEWLINE`.

---

## 3. Grammar

### 3.1 Programs and modules

```
program       ::= module-header? toplevel*
module-header ::= "module" modname export-list? "where"
modname       ::= CONID ("." CONID)*
export-list   ::= "(" export ("," export)* ")"
export        ::= IDENT
               | CONID
               | CONID "(" ".." ")"
               | CONID "(" CONID ("," CONID)* ")"
toplevel      ::= type-decl
               | class-decl                      -- (delta 29)
               | instance-decl                   -- (delta 29)
               | fun-decl
               | let-decl
               | var-decl
               | import-decl
import-decl   ::= "import" modname import-spec?
import-spec   ::= "as" CONID
               | "(" import-item ("," import-item)* ")"
               | "hiding" "(" IDENT ("," IDENT)* ")"
import-item   ::= IDENT | CONID | CONID "(" ".." ")"
```

### 3.2 Type declarations

```
type-decl    ::= "type" CONID typaram* "=" type-rhs
typaram      ::= IDENT
type-rhs     ::= con-decl ("|" con-decl)*     -- data type (multi or single variant)
               | con-decl                       -- ambiguous: resolved by name resolution
               | type-expr                      -- type alias
con-decl     ::= CONID con-arg*
con-arg      ::= atype
               | "{" field ("," field)* "}"    -- record payload (at most one per constructor)
field        ::= IDENT ":" type-expr
type-expr    ::= btype ("->" btype)*           -- right-assoc (function type uses fun(...) syntax, see below)
btype        ::= atype+
atype        ::= IDENT                           -- type variable
               | IDENT atype+                    -- variable in head position (delta 28)
               | CONID                           -- nullary type constructor
               | CONID atype+                    -- type constructor application
               | "(" type-expr ("," type-expr)* ")"
               | "fun" "(" type-list? ")" "->" type-expr
type-list    ::= type-expr ("," type-expr)*
```

### 3.3 Declarations

```
fun-decl     ::= "fun" CONID? IDENT "(" pat-list? ")" fun-ret? fun-body
               -- CONID? would be invalid per naming rules; so:
fun-decl     ::= "fun" IDENT context? "(" pat-list? ")" fun-ret? fun-body
fun-ret      ::= "->" type-expr
fun-body     ::= "=" expr
               | "{" stmt* "}"

-- Classes and instances (delta 29). A `fun` with no body is a signature, and
-- its parameters are then *types*, not binders; that form is legal only in a
-- class body.
class-decl   ::= "class" CONID IDENT (":" class-pred ("," class-pred)*)?
                 "{" (method | fam-decl)* "}"
instance-decl ::= "instance" context? CONID atype
                 "{" (fun-decl | fam-bind)* "}"
method       ::= fun-decl
               | "fun" IDENT context? "(" type-list? ")" "->" type-expr
-- Associated type families (delta 31). A family is declared over the class's
-- own parameter and defined by each instance; it is written and applied like
-- any other type constructor, so `type-expr` needs no production of its own.
fam-decl     ::= "type" CONID IDENT
fam-bind     ::= "type" CONID "=" type-expr
context      ::= "[" class-pred ("," class-pred)* "]"
class-pred   ::= CONID atype
type-list    ::= type-expr ("," type-expr)*
let-decl     ::= "let" pat "=" expr
var-decl     ::= "var" pat "=" expr
pat-list     ::= pat ("," pat)*
```

### 3.4 Statements

Inside a block, statements are declarations or expressions:

```
stmt         ::= "let" pat "=" expr
               | "var" pat "=" expr
               | "fun" IDENT "(" pat-list? ")" fun-ret? fun-body    -- local function
               | expr
```

### 3.5 Expressions

```
expr         ::= expr-annot
expr-annot   ::= expr-bin (":" type-expr)?       -- type annotation, lowest precedence
expr-bin     ::= expr-or (op-or expr-or)*         -- by precedence table
expr-or      ::= expr-and ("||" expr-and)*
expr-and     ::= expr-eq ("&&" expr-eq)*
expr-eq      ::= expr-rel (("==" | "!=") expr-rel)*
expr-rel     ::= expr-add (("<" | "<=" | ">" | ">=") expr-add)*
expr-add     ::= expr-mul (("+" | "-" | "++") expr-mul)*
expr-mul     ::= expr-unary (("*" | "/" | "%") expr-unary)*
expr-unary   ::= "!" expr-unary
               | "-" expr-unary
               | expr-postfix
expr-postfix ::= expr-atom (
                   "(" arg-list? ")"            -- function application
                 | "[" expr "]"                  -- array index
                 | "." IDENT                     -- field access
                 )*
expr-atom    ::= INT | FLOAT | STRING | CHAR
               | IDENT                           -- variable
               | CONID                           -- nullary constructor
               | "(" expr ("," expr)* ")"       -- tuple (if >1) or grouping
               | "[" expr ("," expr)* "]"       -- array literal
               | "[]"                            -- empty array literal
               | CONID "{" field-init ("," field-init)* "}"  -- record construction
               | "fun" "(" pat-list? ")" fun-ret? fun-body   -- anonymous function
               | "if" expr block ("else" (if-expr | block))?
               | "while" expr block
               | "for" for-header block
               | "loop" block
               | "match" expr "{" match-arm+ "}"
               | "return" expr?
               | "break" expr?
               | "continue"
               | block
block        ::= "{" stmt* "}"
field-init   ::= IDENT "=" expr
               | IDENT                           -- punning: `C { x }` is `C { x = x }`
for-header   ::= stmt-no-block ";" expr ";" stmt-no-block    -- C-style
               | pat "in" expr                                 -- iteration over an Iterator
match-arm    ::= pat ("|" pat)* "->" expr
arg-list     ::= arg ("," arg)*
arg          ::= expr
stmt-no-block ::= "let" pat "=" expr
               | "var" pat "=" expr
               | IDENT "=" expr            -- assignment to var
               | expr-postfix "=" expr     -- assignment to field/array element
               | expr
```

### 3.6 Patterns

```
pat          ::= IDENT                          -- variable binder
               | "_"                             -- wildcard
               | CONID pat*                      -- constructor (positional)
               | CONID "{" field-pat ("," field-pat)* "}"  -- constructor (record)
               | INT | FLOAT | STRING | CHAR    -- literal
               | "(" pat ("," pat)* ")"         -- tuple or grouping
               | pat ":" type-expr              -- annotated pattern
field-pat    ::= IDENT "=" pat
               | IDENT                           -- punning: binds same-name variable
```

**Either form matches either declaration.** A record variant is a positional
variant plus a list of field names, so `Circle(r)` and `Circle { radius = r }`
match the same value, exactly as `Circle(2)` and `Circle { radius = 2 }` both
construct one (decision 28). The two forms differ in one respect only: the
record form may name a subset of the fields, and the positional form may not --
`R(x)` for a two-field `R` is an arity error, because a position is not
self-describing the way a name is.

### 3.7 Assignment

Three forms of assignment, all written with `=`:

```
IDENT "=" expr              -- reassign a var binding, or a parameter
expr-postfix "." IDENT "=" expr   -- mutate a record field
expr-postfix "[" expr "]" "=" expr  -- mutate an array element
```

---

## 4. Type System

### 4.1 Types

```
τ ::= α                              -- type variable
    | TyCon                          -- type constructor (kinded; delta 28)
    | τ τ                            -- type application, curried (delta 28)
    | fun(τ₁, ..., τₙ) -> τ          -- function type (n ≥ 0, uncurried)
    | (τ₁, ..., τₙ)                  -- tuple type (n ≥ 2)
    | ⊥                              -- bottom type
```

`⊥` (bottom) is the type of expressions that never produce a value (divergence, control transfer).

### 4.2 Type schemes

```
σ ::= ∀ α₁ ... αₙ. π₁, ..., πₘ ⇒ τ   -- n, m ≥ 0; both 0 means monomorphic
π ::= C τ                            -- a class predicate (delta 29)
    | HasField l τ τ                 -- a field demand (delta 7)
    | OneOf τ {T₁, ..., Tₙ}          -- a numeric literal's set (delta 27)
```

A context is written in brackets, ahead of the type: `[Ord a] fun(Array a) -> a`.

Type variables in annotations are implicitly universally quantified at the enclosing `let`/`fun`/top-level binding. No explicit `forall` keyword in v1.

### 4.3 Unification with bottom

```
unify(⊥, T)   = T
unify(T,   ⊥) = T
unify(⊥,   ⊥) = ⊥
unify(T₁, T₂) = ...  -- standard otherwise
```

`⊥` is absorbed into any type it unifies with. This makes diverging arms transparent in `if`/`match` arm agreement.

### 4.4 Generalization and value restriction

A `let` binding (or top-level `let`/`fun`) is generalized iff its RHS is a **non-expansive** (syntactic value) expression. `var` bindings are **never** generalized.

**Parameters are reassignable**, with no `var` and no keyword of any kind. A
parameter is bound monomorphically by the enclosing function, so the value
restriction -- which is about what may be generalized -- has nothing to say
about it, and there is no soundness question to answer. Reassignment rebinds
the local name: it does not write through to the argument, and that holds for a
destructured parameter too, since a pattern binds rather than aliases.

An expression is **non-expansive** iff:

| Form | Non-expansive? |
|---|---|
| literal (`0`, `"x"`, `'c'`) | ✓ |
| variable | ✓ |
| lambda `fun(...) ...` | ✓ |
| `C(v₁, ..., vₙ)` positional, `C` immutable constructor, all `vᵢ` non-expansive | ✓ |
| `C { f₁ = v₁, ... }` record, `C` immutable (multi-variant) constructor, all `vᵢ` non-expansive | ✓ |
| `C { ... }` record, `C` mutable (single-variant record) | ✗ |
| array literal `[...]` or `[]` | ✗ |
| `Data.Array.new(...)` | ✗ |
| function application `f(args)` | ✗ |
| `if` / `match` / `while` / `for` / `loop` / `return` / `break` / `continue` | ✗ |
| block `{ ... }` | ✗ |
| `e : τ` | iff `e` is non-expansive |

### 4.5 Mutability of types

| Type | Mutable? | Semantics |
|---|---|---|
| Single-variant record (`type T = T { ... }`) | ✓ | reference type; fields mutable |
| Multi-variant ADT (`type T = A \| B ...`) | ✗ | immutable; value or immutable reference |
| Positional single-variant ADT (`type T = T a b`) | ✗ | immutable |
| `Array a` | ✓ | reference type; elements and fields mutable |
| Primitives (`Int`, `Float`, `String`, `Char`, `Unit`) | ✗ | immutable |

Field access `r.f` and field mutation `r.f = e` are only well-typed when the static type of `r` is a single-variant record type or `Array a`.

### 4.6 Bottom-typed expressions

| Expression | Type | Side effect |
|---|---|---|
| `return e` | `⊥` | unifies enclosing function's return type with `typeof(e)` |
| `return` | `⊥` | unifies enclosing function's return type with `Unit` |
| `break e` (in `loop`) | `⊥` | unifies loop's result type with `typeof(e)` |
| `break` (in `loop`/`while`/`for`) | `⊥` | unifies loop's result type with `Unit` |
| `continue` | `⊥` | — |
| `loop { body }` (no fall-through) | `α_loop` | unified with all break-value types; `⊥` if no break |

---

## 5. Type Inference

### 5.1 Algorithm

Standard Hindley-Milner (Algorithm W or constraint-based), extended with:

- Bottom type `⊥` in unification.
- Value restriction via non-expansiveness check.
- Pattern matching exhaustiveness checking (warnings; non-exhaustive matches are a runtime error if reached).

### 5.2 SCC-grouped inference for mutual recursion

1. Build dependency graph of top-level `fun` declarations. Edge `f → g` exists when `g` is referenced in `f`'s body.
2. Compute SCCs, topologically sorted (dependencies first).
3. For each SCC `S = {f₁, ..., fₖ}` in topological order:
   1. Allocate fresh type variable `αᵢ` for each `fᵢ`; bind `fᵢ : αᵢ` in Γ.
   2. Type-check each `fᵢ` body under Γ, producing constraints.
   3. Solve all constraints.
   4. For each `fᵢ`: principal type `τᵢ = unify(αᵢ)`. Generalize: `σᵢ = ∀ Qᵢ. τᵢ` where `Qᵢ = FV(τᵢ) \ FV(Γ_without_SCC)`, subject to value restriction.
   5. Replace `fᵢ : αᵢ` with `fᵢ : σᵢ` in Γ before processing the next SCC.

A `fun` that calls itself is an SCC of size 1.

### 5.3 Type annotation checking

Wherever a type annotation appears (expression `e : τ`, parameter pattern `pat : τ`, return type `-> τ`), the inferred type is unified with `τ` before generalization. Annotations never cause generalization themselves; they only constrain.

---

## 6. Operational Semantics

### 6.1 Evaluation strategy

Strict, call-by-value, left-to-right evaluation of arguments and record fields. Pattern matching is exhaustive-checked at compile time (warnings; runtime error on unhandled cases).

### 6.2 Runtime representation

The runtime representation of values is **opaque** — the surface language does not specify it. The compiler is free to choose any layout (boxed, unboxed, NaN-boxed, packed records, untagged enums, specialized closures, etc.). The type system never influences representation; representation is an implementation concern.

### 6.3 Reference semantics

**Mutable types** (single-variant records, arrays) have **reference semantics**:

- Construction allocates on the heap and yields a reference.
- Binding (via `let`, `var`, parameter, `for x in arr`, pattern match) binds the name to the reference.
- `let d = c` makes `d` and `c` aliases for the same object.
- Field/element writes through one reference are visible through all aliases.

**Immutable types** (multi-variant ADTs, primitives) are observationally indistinguishable under value or reference semantics. The compiler may choose either; the programmer cannot tell.

### 6.4 `let` vs `var`

- `let x = e`: the binding `x` is **immutable** — `x = e2` is a type error. The name `x` cannot be reassigned. If `e` is a mutable reference, the *data* is still mutable (`x.field = e2` works).
- `var x = e`: the binding `x` is **mutable** — `x = e2` is allowed and rebinds `x` to a new reference/value.

### 6.5 `for x in seq` elaboration

```
for x in seq { body }
```
desugars to:
```
{
    let __c = iter(seq)
    loop {
        match next(seq, __c) {
            None -> break
            Some(x) -> body
        }
    }
}
```

`iter` and `next` are the methods of the `Iterator` class (§8.2), so the loop
runs on anything with an instance and `x` has the type the associated family
`Item seq` reduces to. `Array` is the instance the language ships; it is no
longer the only sequence a `for` can walk.

Iteration is **cursor-based**: `iter` makes the mutable state that walks the
container and `next` advances it, ending the loop by answering `None`. Nothing
asks the container how long it is, which is what lets a linked list, a stream
or a generator be iterated at all -- an indexed protocol can only describe
containers that can produce their *k*th element, and would make a loop over a
list quadratic. `Cursor seq` is a second associated family, so the state a
container walks with is computed from the container, like its element type.

`x` is an immutable `let` binding, fresh each iteration. If the element type is a mutable reference type, `x` is a reference to the object stored in the array; mutations via `x.field = e` affect the array's contents. If the element type is immutable, `x` is the value.

### 6.6 Array literal desugaring

- `[]` desugars to `Data.Array.new(0)`. Element type is a fresh type variable `α`, monomorphic at the binding site.
- `[e₁, ..., eₙ]` (n ≥ 1) desugars to:
  ```
  {
      let tmp = Data.Array.new(n)
      Data.Array.push(tmp, e₁)
      ...
      Data.Array.push(tmp, eₙ)
      tmp
  }
  ```
  The element type is fixed by the first `push` (unifies `α` with `typeof(e₁)`); all elements must agree.

### 6.7 Control flow

| Construct | Returns | Notes |
|---|---|---|
| `if c { b1 } else { b2 }` | `unify(typeof(b1), typeof(b2))` | `else` required if result is used |
| `if c { b1 }` | `Unit` | statement-style if |
| `while c { b }` | `Unit` | `break` (no value) exits |
| `for init; c; step { b }` | `Unit` | C-style; `break`/`continue` allowed |
| `for x in arr { b }` | `Unit` | array iteration |
| `loop { b }` | `α_loop` | `break e` returns value; infinite if no break |
| `return e` | `⊥` | exits enclosing `fun` with value `e` |
| `break e` | `⊥` | exits enclosing `loop` with value `e` |
| `break` | `⊥` | exits enclosing `loop`/`while`/`for` |
| `continue` | `⊥` | skips to next iteration of `while`/`for`/`loop` |

`return` is only valid inside a `fun`. `break`/`continue` are only valid inside `loop`/`while`/`for`.

### 6.8 Block evaluation

A block `{ s₁; ...; sₙ }` evaluates each statement in order. Declarations (`let`, `var`, `fun`) are in scope for all subsequent statements. The value of the block is the value of the last statement. If the last statement is a declaration, the block's value is `Unit`.

---

## 7. Type Declaration Disambiguation

A `type Foo a = RHS` is resolved as follows:

1. If `RHS` contains `|` → **data type** (one variant per `|`-separated piece).
2. If `RHS` is a single `CONID { ... }` → **data type** (single-variant record).
3. If `RHS` is a single `CONID arg*` (no `|`, no `{}`):
   - Resolve the head `CONID` in the type-constructor environment.
   - **Resolved as a type constructor** → **type alias** (transparent, expanded during unification).
   - **Not resolved** → **data type** with one constructor named `CONID`. If `CONID == Foo`, the constructor shares the type's name (newtype case).
   - **Resolved but to a data constructor of an in-progress type** → error.
4. Type aliases may not be recursive.

Records (constructor payload using `{ ... }`) are only mutable when the data type has exactly one variant. Multi-variant data types are always immutable, even if their constructors use record syntax.

---

## 8. Built-in Types and Modules

### 8.1 Primitive types

| Type | Values | Notes |
|---|---|---|
| `Int` | `0`, `1`, `-5`, ... | machine integer |
| `Float` | `0.0`, `3.14`, ... | floating-point |
| `String` | `"hello"`, ... | UTF-8 |
| `Char` | `'a'`, `'\n'`, ... | single Unicode codepoint |
| `Bool` | `True`, `False` | declared in the prelude as `type Bool = False \| True`, not built in |
| `Unit` | `()` | singleton type |

### 8.2 Operators

Every arithmetic and comparison operator is a **class method**. There is no
operator table in the checker: `a + b` *is* `add(a, b)`, and what makes it
mean integer addition is `instance Add Int`.

| Operator | Desugars to | Class |
|---|---|---|
| `+` `-` `*` `/` `%` | `add` `sub` `mul` `div` `rem` | `Add` `Sub` `Mul` `Div` `Rem` |
| `-` (unary) | `neg` | `Neg` |
| `==` `!=` | `eq` `ne` | `Eq` |
| `<` `<=` `>` `>=` | `lt` `lte` `gt` `gte` | `Ord`, whose superclass is `Eq` |
| `++` | -- | `fun(String, String) -> String` |
| `&&` `\|\|` | -- | `fun(Bool, Bool) -> Bool`, short-circuit |
| `!` | -- | `fun(Bool) -> Bool` |

The last three are not methods. `&&` and `||` short-circuit, which no function
call does, and `++` is concatenation on `String`, which has no class to belong
to until there is a `Semigroup`.

The classes are per-operator and Rust-shaped rather than one omnibus `Num`, so
a type that adds is not thereby required to divide. They are ordinary source
(`turkey/prelude.py`), declared and checked exactly like a program's own, and a
program may write `instance Add` for its own type and use `+` on it.

**Operators are homogeneous.** With one class parameter there is no `Add a b`,
so both operands and the result have the same type; `Vec * Scalar` is not
expressible. This is the one place the decision to do without multi-parameter
classes is visible in the surface language.

The classes that ship, and their instances:

| Class | Method | Instances |
|---|---|---|
| `Eq` | `eq`, and `ne` by default | `Int` `Float` `String` `Char` `Bool` |
| `Ord : Eq` | `lt`, and `lte`/`gt`/`gte` by default | `Int` `Float` `String` `Char` `Bool` |
| `Add` `Sub` `Mul` `Div` | | `Int` `Float` |
| `Rem` | `rem` | `Int` |
| `Neg` | `neg` | `Int` `Float` |
| `Show` | `show` | `Int` `Float` `String` `Char` `Bool`, and `Array a` / `Option a` given `Show a` |
| `Iterator` | `iter`, `next`, and the families `Item` and `Cursor` | `Array a` |

`print` and `write` are not builtins. Both are `[Show a] fun(a) -> Unit`,
written in the prelude as `Prim.print(show(x))`, and `Prim.print` is the only
thing that reaches stdout and is reachable from nowhere else. So the only way
to print a value is to say what it looks like as a `String`.

`Option a` is declared in the prelude, because `Iterator.next` needs it.

`String.eq`, `String.lt`, `Bool.eq`, `Float.lt` and `Char.eq` are gone: they
were the per-type equality this section promised would be "unified under
`Eq`/`Ord` classes later", and this is later. So are `+.` `-.` `*.` `/.`, which
existed only because `+` could not be overloaded; `1.5 + 2.0` is now what it
reads as.

### 8.3 Array

`Array a` is a primitive mutable type with reference semantics.

**Compiler-known fields** (mutable, at user's own risk):

| Field | Type | Notes |
|---|---|---|
| `.length` | `Int` | current number of elements |
| `.capacity` | `Int` | allocated capacity |

**Compiler-known syntax:**

| Syntax | Type | Notes |
|---|---|---|
| `arr[i]` | `(Array a, Int) -> a` | index read; runtime bounds check |
| `arr[i] = e` | `(Array a, Int, a) -> Unit` | index write; runtime bounds check |
| `[]` | `Array α` | empty literal, α fresh |
| `[e₁, ..., eₙ]` | `Array τ` | literal with n elements |

These will become typeclass methods/instances when typeclasses are added; existing code remains valid.

**`Data.Array` module:**

```
module Data.Array where

fun new(capacity : Int) -> Array a             -- empty array, given capacity
fun push(arr : Array a, x : a) -> Unit          -- append, growing if needed
fun pop(arr : Array a) -> Option a              -- remove and return last, or None if empty
```

### 8.4 Other standard modules (suggested)

```
module Data.Int     -- arithmetic, conversion functions
module Data.String  -- string operations
module Data.Bool    -- logical functions
module Data.List    -- list operations (immutable lists via ADT)
```

---

## 9. Modules

### 9.1 Module declaration

Each file begins with an optional module header:

```
module MyMod.Sub (f, MyType(..), g) where
```

If omitted, the module name defaults to the file name. If no export list is given, all top-level `fun`, `let`, `var`, and `type` declarations (with all constructors) are exported.

### 9.2 Imports

```
import MyMod.Sub                       -- unqualified, all exports
import MyMod.Sub as S                  -- qualified as S
import MyMod.Sub (f, MyType(..))       -- selective
import qualified MyMod.Sub as S        -- qualified only
import MyMod.Sub hiding (f)            -- everything but f
```

### 9.3 Name resolution

1. Local scope (parameters, `let`/`var` bindings).
2. Module's own top-level declarations.
3. Imported names (unqualified imports).
4. Qualified names (`M.x`).

Constructor disambiguation in patterns: if two imported types share a constructor name, the constructor must be qualified, or an error is raised requiring qualification.

---

## 10. Complete Worked Example

```
module Stack (Stack, new, push, pop, drain) where

import Data.Array (new, push, pop)

type Stack a = Stack {
    data : Array a,
    top  : Int
}

fun new(capacity : Int) -> Stack a {
    Stack { data = new(capacity), top = 0 }
}

fun push(s : Stack a, x : a) -> Unit {
    s.data[s.top] = x
    s.top = s.top + 1
}

fun pop(s : Stack a) -> a {
    if s.top == 0 { return error("empty stack") }
    s.top = s.top - 1
    s.data[s.top]
}

fun drain(s : Stack a) -> Array a {
    let out = [] : Array a
    loop {
        if s.top == 0 { break out }
        push(out, pop(s))
    }
}
```

Inferred types:

```
new   : Int -> Stack a
push  : (Stack a, a) -> Unit
pop   : Stack a -> a
drain : Stack a -> Array a
```

Notes:
- `Stack a` is a single-variant record → mutable, reference semantics.
- `push` and `pop` mutate `s` in place; callers observe changes.
- `drain` uses `loop` + `break out` to return the accumulated array.
- `[] : Array a` annotates the empty literal's element type.
- `return error("...")` has type `⊥`; the `if` arm unifies `⊥` with `a` (the return type of `pop`), yielding `a`.
- `error : String -> a` is a polymorphic primitive (always diverges/panics).

---

## 11. Summary of All Design Decisions

| # | Decision |
|---|---|
| 1 | Strict, call-by-value evaluation |
| 2 | Uncurried functions with fixed arity |
| 3 | Hindley-Milner type inference with `let`-generalization |
| 4 | Value restriction via non-expansiveness (Wright 1995) |
| 5 | `let` = immutable binding; `var` = mutable binding; both can hold mutable data |
| 6 | `var` never generalized; `let`/`fun` generalized iff non-expansive |
| 7 | Single-variant records are mutable (reference semantics); multi-variant ADTs are immutable |
| 8 | Array is a primitive mutable type with `.length`/`.capacity` fields and `arr[i]` indexing |
| 9 | `Data.Array` module provides `new`/`push`/`pop` as ordinary functions |
| 10 | Array `.length`/`.capacity` are mutable (user's risk) |
| 11 | `Array.new` takes capacity only (no default) |
| 12 | Bottom type `⊥` unifies with anything (`⊥ ∪ T = T`) |
| 13 | `return`, `break`, `continue` have type `⊥` |
| 14 | `loop { }` with `break e` returns a value; `while`/`for` return `Unit` |
| 15 | `for x in arr` iterates over arrays; `x` is immutable, bound to reference |
| 16 | Function type syntax: `fun(Int, String) -> Bool` |
| 17 | Return type annotation: `-> τ`; parameter annotation: `pat : τ` |
| 18 | Type annotation expression: `e : τ` (low precedence) |
| 19 | No annotation slot on `let`/`var`; use `e : τ` on RHS |
| 20 | No `forall` keyword; implicit quantification |
| 21 | Lowercase = variables; uppercase = type/value constructors |
| 22 | No parens around `if`/`while`/`for` conditions; braces required for bodies |
| 23 | Newline terminates iff prev token ends production AND next token can start new one |
| 24 | Everything is an expression except `let`/`var`/named `fun`/`type`/`import` |
| 25 | Anonymous function expression: `fun(params) -> ret = body` or `fun(params) -> ret { body }` |
| 26 | Type declarations disambiguated by name resolution: `|` or `{}` → data type; otherwise resolve head |
| 27 | Records only as constructor payloads; field access via `r.f` (type-directed, single-variant only) |
| 28 | Record construction: positional `C(v₁, ...)` or labeled `C { f = v, ... }`; patterns are symmetric with it (delta 34) |
| 29 | Record update: `r { f = e }` (functional, returns new value) |
| 30 | SCC-grouped inference for mutual recursion |
| 31 | Haskell-style modules with explicit exports and qualified imports |
| 32 | Runtime representation is opaque (compiler's choice) |
| 33 | Array literals: `[]` = `Data.Array.new(0)`; `[e₁,...]` = new + pushes |
| 34 | ~~Operators are monomorphic (Int/Float variants); no typeclasses in v1~~ -- superseded: every arithmetic and comparison operator is a class method (SPEC-DELTAS.md 32) |
| 35 | `error : String -> a` is a polymorphic primitive (panics/diverges) |
