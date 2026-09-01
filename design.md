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
| Arrays | opaque source-library type over hidden primitive storage; reference semantics |
| Modules | explicit exports; plain and qualified-only imports |
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
FLOAT    ← [0-9]+\.[0-9]+([eE][+-]?[0-9]+)?
STRING   ← "..."   (escapes: \n \t \r \0 \\ \" \' \u{H...H})
CHAR     ← '...'
```

**Reserved words:** `type class instance fun let var match if else while for in loop return break continue do module import export as hiding`

**Operators and punctuation:**
```
=  ==  !=  <  <=  >  >=
+  -  *  /  %
&& ||  !
?
-> :  ;  ,  .  |  {  }  (  )  [  ]
```

`?` is a postfix operator (§6.9), so it can end a statement — §2.4's preceding
condition includes it.

An exponent is only recognized after a fractional part, so `1e10` is still two
tokens and a numeral still needs a `.` to be a `Float`. What the exponent buys
is round-tripping: `show` on a large `Float` has to print one, and the string
it produces must lex back.

`\u{H...H}` takes one to six hex digits — four could not reach past the BMP,
so `"\u{1F600}"` was previously unwritable. An escape naming a surrogate
(`D800..DFFF`) or a value above `10FFFF` is a lex error, and there is no
`\xNN`; together those two rules make "a string literal is well-formed UTF-8"
true by construction.

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
module-header ::= "module" modname export-list?
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
import-decl   ::= "import" modname ("as" CONID)? import-spec?
import-spec   ::= "(" import-item ("," import-item)* ")"
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
               | "{" record-fields? "}"        -- record payload (at most one per constructor)
record-fields ::= field (record-sep field)* ","?
record-sep    ::= "," NEWLINE? | NEWLINE
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
instance-decl ::= "instance" qclass atype (":" class-pred ("," class-pred)*)?
                 "{" (fun-decl | fam-bind)* "}"
method       ::= fun-decl
               | "fun" IDENT context? "(" type-list? ")" "->" type-expr
-- Associated type families (delta 31). A family is declared over the class's
-- own parameter and defined by each instance; it is written and applied like
-- any other type constructor, so `type-expr` needs no production of its own.
fam-decl     ::= "type" CONID IDENT
fam-bind     ::= "type" CONID "=" type-expr
context      ::= "[" ctx-pred ("," ctx-pred)* "]"
ctx-pred     ::= class-pred | eq-pred                 -- (delta 39)
class-pred   ::= qclass atype
qclass       ::= CONID ("." CONID)*
-- An equality states what a family answers (delta 39). The left side must be
-- a family application and the right may not mention it, so a given is a
-- terminating rewrite rule rather than a general equation.
eq-pred      ::= type-expr "~" type-expr
type-list    ::= type-expr ("," type-expr)*
let-decl     ::= "let" pat "=" expr
var-decl     ::= "var" pat "=" expr
pat-list     ::= pat ("," pat)*
```

Comma-separated forms accept a trailing comma. In a multiline record payload,
a significant newline is also a field separator, so commas are optional.
Parenthesized singleton tuples do not exist: `(x)` is grouping and `(x,)` is
rejected.

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
expr-add     ::= expr-mul (("+" | "-") expr-mul)*
expr-mul     ::= expr-unary (("*" | "/" | "%") expr-unary)*
expr-unary   ::= "!" expr-unary
               | "-" expr-unary
               | expr-postfix
expr-postfix ::= expr-atom (
                   "(" arg-list? ")"            -- function application
                 | "[" expr "]"                  -- array index
                 | "." IDENT                     -- field access
                 | "." INT                       -- zero-based tuple/payload projection
                 | "?"                           -- monadic bind (§6.9)
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
               | "do" block                      -- monadic context (§6.9)
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
    | F τ ~ τ                        -- a family's answer (delta 39)
    | HasField l τ τ                 -- a field demand (delta 7)
    | HasProjection n τ τ            -- a numeric projection demand (delta 55)
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
| `Data.Array.new(...)` / `Data.Array.filled(...)` | ✗ |
| function application `f(args)` | ✗ |
| `if` / `match` / `while` / `for` / `loop` / `return` / `break` / `continue` | ✗ |
| `e?` and `do { ... }` | ✗ — `e?` is a call to `bind` (§6.9) |
| block `{ ... }` | ✗ |
| `e : τ` | iff `e` is non-expansive |

### 4.5 Mutability of types

| Type | Mutable? | Semantics |
|---|---|---|
| Single-variant record (`type T = T { ... }`) | ✓ | reference type; fields mutable |
| Multi-variant ADT (`type T = A \| B ...`) | ✗ | immutable; value or immutable reference |
| Positional single-variant ADT (`type T = T a b`) | ✗ | immutable |
| `Array a` | ✓ | reference type; elements and fields mutable |
| Primitives (`Int`, `Byte`, `Float`, `String`, `Char`, `Unit`) | ✗ | immutable |

Field access `r.f` and field mutation `r.f = e` are only well-typed when the static type of `r` is a single-variant record type or `Array a`.

Numeric projection `v.n` is zero-based and read-only. It applies to structural
tuples and to immutable types with exactly one positional constructor. A
projection whose receiver is not yet known is carried by `HasProjection n r a`,
so `fun first(x) = x.0` remains polymorphic. Records and multi-variant data
types are taken apart with named fields and `match`, respectively.

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

### 5.4 Elaboration: the typed Core (SPEC-DELTAS.md 48, 49)

Inference does not only decide types. It decides which instance every class predicate is discharged by, and a program cannot run without that answer. The result is a **typed Core**: the same term language with three things made explicit that the surface language leaves implicit.

**Every node carries its type.** Not a scheme: a type, with family applications reduced throughout and numeric literals decided.

**Dictionaries are ordinary values.** A class `C a` has a record type `%Dict.C a`, one field per method and a `%super.S` field per superclass. An instance is a top-level binding of that type; one with a context is a function from dictionaries to a dictionary. Selecting a method is a field projection, and so is selecting a superclass.

**Polymorphism is explicit.** A generalized binding is a type abstraction; every use of it is a type application, and then an application to its dictionaries, in that order:

```
print : forall a. fun(%Dict.Show a) -> fun(a) -> Unit

print(x)  ⇒  print[String](%inst.Show.String)(x)
```

Two further differences from the surface language:

- **Core has no statements.** A block is nested `let`s; a statement whose value is discarded binds a name nothing reads. `{ }` is `()`.
- **`var` is a reference cell** (see decision 36). `let` is an immutable binding, `var x = e` binds a cell, a mention of it is a read, and an assignment is a write.

Control constructs are unchanged: `if`, `match`, `while`, `loop`, `for`, and `return`/`break`/`continue` typed `⊥`. Turning those into join points is a separate layer and belongs below this one.

The Core is **checked**, on every compile, against the class table and the declarations. That is the point of having it: before it existed, a dictionary in the wrong position was not a compile error but a wrong answer at run time.

### 5.5 Specialization (SPEC-DELTAS.md 51, 52, 53, 54)

The Core is then **specialized**: a generalized binding gets one copy per type it is used at, and every use becomes a reference to the copy rather than a type application. An instance with a context is applied at ground types once, at the top level, so a dictionary is built once rather than per request.

Specialization is **partial**, because it cannot be total. Turkey admits polymorphic recursion (§5.3: a complete signature gives a recursive call a scheme to instantiate), so a binding may be used at unboundedly many types even though the program terminates. Past a cap the pass stops making copies, the call site keeps its type application, and the generic dictionary-passing binding — which is never removed — serves it. A program that hits the cap is told so.

Once every dictionary at a call site is a known top-level record, a method selection out of one is decided at compile time — coherence says no other value can be that dictionary — so each ground dictionary's methods become ordinary top-level bindings and the selection becomes a reference to one. A `for` loop's `iter` and `next` are two more of these. What is then unreachable is dropped, which is where specialization stops being an addition: the bindings a program keeps are the ones it names.

Specializing and devirtualizing each make bindings the other would have worked on — collapsing an instance produces a record, hoisting that record's methods produces bindings, and those bindings have ground call sites nothing has looked at — so the two run twice, in that order, and the drop runs once at the end. The cap is what makes the round count a question rather than a detail: a budget spent per round would be no bound at all, so there is one budget for the whole pass, charged to the original a copy descends from, and it holds however many rounds run. Two rounds and not a fixed point, deliberately: "iterate until nothing changes" is a promise about a number that polymorphic recursion does not let this pass make.

The specialized program is checked by the same checker, on every compile. It is not a different language: it is Core with fewer type abstractions in it. It is also the program that **runs**: the unspecialized Core is kept because it is what the specialization is read against, but nothing evaluates it.

---

## 6. Operational Semantics

What runs is the Core of section 5.4, not the surface tree (SPEC-DELTAS.md 50). The two agree — the semantics below are the language's either way — but where the two descriptions differ, the Core is the one being described: a dictionary is a record, a method call is a field projection, a `var` is a reference cell, and a block is a chain of `let`s. Type abstraction and application are erased.

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

### 6.9 `e?` elaboration

`e?` is the `Monad` instance's `bind`, with the rest of the enclosing statement
sequence as its continuation (SPEC-DELTAS.md 46). It is **not** an early return:
`Array` is a monad, its `bind` runs the continuation once per element, and there
is no single value to leave with.

```
do { let x = e?; rest }
```
desugars to:
```
bind(e, fun(__x) { let x = __x; rest })
```

A **do-context** is the block a `?` unwinds to, and there are two: an explicit
`do { ... }`, and the body of a `fun` or lambda that contains a `?`. `if`,
`match` and bare blocks are transparent — a `?` inside one lifts outward through
it. A lambda is opaque, so a `?` in a callback belongs to the callback.

A `do` containing no `?` emits nothing at all: no `bind`, hence no `Monad`
obligation, hence nothing for it to be ambiguous about. It means the block it
wraps, and `do { }` is `Unit`.

A `?` inside a control construct **lifts** the construct into the monad, since
an `if` branch's value is `Unit` by §6.7 rather than `m Unit`:

```
if c { A } else { B }; rest      -- with a `?` somewhere in A or B
```
becomes
```
bind(if c { A′ } else { B′ }, fun(__j) { rest })
```

where each branch is translated with `pure` as its continuation and a missing
`else` is `pure(())`. In **tail** position neither the `pure` nor the `bind` is
emitted: the branches already are the do block's tail, and the tail of a do block
is the monadic value. `match` lifts the same way, per arm.

`x && y` and `x || y` with a `?` in the right operand are read as the `if` they
already mean (§8.2), which keeps the operand unevaluated when the operator
short-circuits.

**No auto-`pure`.** The trailing expression of a do block is already the monadic
value — `Some(3)`, not `3` — so `do` never changes what an ordinary expression
means. This matters more here than in Haskell, since a function body becomes a
do block without anyone typing `do`. The `pure` the lowering inserts appears only
where it lifts something whose value was `Unit` by rule.

**What crosses a bind.** A `return`, `break` or `continue` after a `?` would land
inside a generated lambda, and it cannot escape through the `bind` either —
escaping is not something a `bind` does, and for `Array` there is nothing for an
escape to mean. So it travels as a *value*, in the prelude's unexported `Flow`
(SPEC-DELTAS.md 47), because a value is the only thing a `bind` propagates. Each
statement in such a block answers with `m (Flow …)`, the next runs only under
`Fall`, and the do-context's boundary is where `Ret` becomes a result again.

A `return` *before* every `?` in its block needs none of that: it stays in the
prefix, outside every lambda, and means exactly what it says.

A loop containing a `?` becomes a **recursive local function** answering with a
`Flow` — its continuation is not known until its body has run, so it cannot be a
lambda written once. `Brk` becomes the loop's own `Fall`, which is what lets
`let v = loop { … break x }` still work, and `Ret` keeps travelling outward.
`for x in xs` is expanded to its cursor form (§6.5) before being lifted.

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
| `Int` | `0`, `1`, `-5`, ... | two's-complement signed 64-bit; arithmetic **traps** on overflow |
| `Byte` | `Byte.fromInt(200)` | unsigned 8-bit; no literal syntax, and no arithmetic instances |
| `Float` | `0.0`, `3.14`, `1.0e16` | IEEE 754 binary64, roundTiesToEven |
| `String` | `"hello"`, ... | an immutable, well-formed **UTF-8 byte sequence**; no indexing, no `length` |
| `Char` | `'a'`, `'\n'`, ... | a Unicode **scalar value**: `0..10FFFF` excluding the surrogates `D800..DFFF` |
| `Bool` | `True`, `False` | declared in the prelude as `type Bool = False \| True`, not built in |
| `Unit` | `()` | singleton type |

`PRIMITIVES.md` is the full semantics; what follows is the part that changes
how ordinary code reads.

**`Int` traps.** `+`, `-`, `*` and unary `-` panic when the result leaves
`-2^63 .. 2^63-1`, the way `/` by zero already panicked. An integer literal
outside that range is a compile error rather than a wraparound. `Data.Int`
carries the escape hatches — `addWrapping`, `addChecked : ... -> Option Int`,
the bitwise functions, and `minValue()`, which exists because
`-9223372036854775808` cannot be *written* (it lexes as a negation of a
literal that is itself out of range). `%` is remainder and takes the sign of
the dividend; `Int.mod` is the floored one, and is what a bucket index wants.

**`Float` is IEEE, NaN included.** `1.0 / 0.0` is `Infinity`, not a panic.
`==`, `<`, `<=`, `>`, `>=` are the IEEE predicates, so every comparison
involving NaN is false and `NaN != NaN` is true. This makes `Eq Float` and
`Ord Float` the one pair of instances in the language that does not satisfy
its class's laws — `Eq` is not reflexive and `Ord` is not total — because
there is one `Ord` and no `PartialOrd` to put the distinction in. Sorting a
`Float` array containing NaN is therefore *safe but unspecified*: it
terminates and panics nowhere, and may produce an unsorted result. Code that
must not misbehave on NaN uses `Float.totalCompare`, which is IEEE 754
`totalOrder` and returns an `Ordering`. There is deliberately **no
`Hash Float`**, since a non-reflexive key can be inserted into a `Map` and
never found again.

**`String` is bytes.** No `s[i]`, no `Length String`, and no `String.length`
— "how long is this string" has three defensible answers, so the cheap one is
`String.byteLength` (O(1)) and the usual one is `String.codePointCount`
(O(n), and spelled out). `String.bytes` and `String.codePoints` are lazy
iterator views, leaving room for `String.graphemes`. `==` is byte equality
and performs **no Unicode normalization**, so `"\u{00E9}" != "\u{0065}\u{0301}"`
even though both render as é; `Ord` is byte-lexicographic, which is not a
collation. No byte offset reaches the surface language: strings are taken
apart with `split`, `splitOnce`, `stripPrefix`, `stripSuffix`, `startsWith`,
`endsWith`, `contains` and `trim`. `String.toBytes` / `String.fromBytes` (which
validates, returning `Option String`) are the only door in and out, and are
what `Byte` exists for.

**`Char` is a scalar value, not a code point.** The surrogates are excluded,
which is what makes the `String` invariant enforceable, so `Char.fromInt`
returns `Option Char`. A `Char` is *not* a user-perceived character — that is
a grapheme cluster, which may be several scalar values — so `codePoints` is
not "the characters". There is no `Char.toUpper`: case mapping is not a
per-scalar-value function (`ß` uppercases to two characters), so case will
live on `String` when there is a Unicode table to do it correctly.

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
| `&&` `\|\|` | -- | `fun(Bool, Bool) -> Bool`, short-circuit |
| `!` | -- | `fun(Bool) -> Bool` |

The last three are not methods, and for a reason no class can express: `&&` and
`||` short-circuit, which no function call does, and `!` is defined in terms of
them. There is no `++`: concatenation is `instance Add String`, so `+` on two
strings is the same method call as `+` on two integers (SPEC-DELTAS.md 44).

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
| `Eq` | `eq`, and `ne` by default | `Int` `Byte` `Float` `String` `Char` `Bool` `Ordering` |
| `Ord : Eq` | `lt`, and `lte`/`gt`/`gte` by default — except `Float`, which overrides all four | `Int` `Byte` `Float` `String` `Char` `Bool` `Ordering` |
| `Add` `Sub` `Mul` `Div` | | `Int` `Float` |
| `Rem` | `rem` | `Int` |
| `Neg` | `neg` | `Int` `Float` |
| `Show` | `show` | `Int` `Byte` `Float` `String` `Char` `Bool` `Ordering`, and `Array a` / `Option a` / `Either l r` given `Show` of the parts |
| `Hash : Eq` | `hashInto` | `Int` `Byte` `String` `Char` `Ordering`, and the composites — **not** `Float` |
| `Iterator` | `iter`, `next`, and the families `Item` and `Cursor` | `Array a` |
| `Functor` | `map` | `Option` `Either l` `Array` |
| `Applicative : Functor` | `pure` | `Option` `Either l` `Array` |
| `Monad : Applicative` | `bind` | `Option` `Either l` `Array` |

The last three are what `?` means (SPEC-DELTAS.md 45). Their class variable has
kind `* -> *`, discovered from `m a` in `bind`'s own signature and written down
nowhere. `Monad (Either l)` fixes the left parameter and varies the right, which
is why `Either` is right-biased — the same one-parameter constraint the
homogeneity paragraph above describes, seen from the other side. Unlike the
operator classes, these three claim their method names: `map`, `pure` and `bind`
are the prelude's, and a program cannot define its own.

`print` and `write` are not builtins. Both are `[Show a] fun(a) -> Unit`,
written in the prelude as `Prim.print(show(x))`, and `Prim.print` is the only
thing that reaches stdout and is reachable from nowhere else. So the only way
to print a value is to say what it looks like as a `String`.

`Option a` is declared in the prelude, because `Iterator.next` needs it. `Either
l r` is there too, and for a weaker reason: nothing in the language names it, but
`?` is only worth having over more than one monad, and "failure that says why" is
the second one (SPEC-DELTAS.md 45). Both live beside their functions, in
`Data.Option` and `Data.Either`, and are re-exported.

`String.eq`, `String.lt`, `Bool.eq`, `Float.lt` and `Char.eq` are gone: they
were the per-type equality this section promised would be "unified under
`Eq`/`Ord` classes later", and this is later. So are `+.` `-.` `*.` `/.`, which
existed only because `+` could not be overloaded; `1.5 + 2.0` is now what it
reads as.

### 8.3 Array

`Array a` is an opaque mutable type declared by `Data.Array`, with reference
semantics. Its representation holds hidden fixed-length `Prim.Array a` storage
plus a logical length; neither the storage nor capacity is accessible to source
programs.

Array literals are compiler-known syntax, but indexing and length are ordinary
class operations:

| Syntax | Type | Notes |
|---|---|---|
| `container[key]` | `[Index c] (c, Index.Key c) -> Index.Value c` | `get`; implementation may bounds-check |
| `container[key] = e` | `[Index c] (c, Index.Key c, Index.Value c) -> Unit` | `set` |
| `len(container)` | `[Length c] c -> Int` | logical length |
| `[]` | `Array α` | empty literal, α fresh |
| `[e₁, ..., eₙ]` | `Array τ` | literal with n elements |

`Index` has associated families `Key` and `Value`. User-defined
containers may implement `Index` and `Length`, and the same syntax then works
without compiler changes.

**`Data.Array` module:**

```
module Data.Array (Array, new, filled, push, pop, map, filter, fold, reverse, append)

fun new(capacity : Int) -> Array a             -- empty array, given capacity
fun filled(length : Int, value : a) -> Array a -- initialized logical elements
fun push(arr : Array a, x : a) -> Unit          -- append, growing if needed
fun pop(arr : Array a) -> Option a              -- remove and return last, or None if empty
```

The library-only primitive operations are fixed-length:

```
Prim.arrayNew(length, value)
Prim.arrayNewUninit(length)
Prim.arrayGet(storage, index)
Prim.arraySet(storage, index, value)
Prim.arrayLength(storage)
```

The unsafe constructor does not track initialized slots. Dynamic growth and
logical bounds are entirely `Data.Array` policy.

The standard library also provides `Functor`, `Applicative`, `Monad`,
`Iterator`, `Index`, `Length`, `Foldable`, `Semigroup`, `Monoid`, `Add`, and
`Show` instances for Array where their element constraints permit them.

### 8.4 Other standard modules (suggested)

```
module Data.Int      -- arithmetic, wrapping/checked forms, bitwise, `mod`
module Data.Byte     -- conversions and bitwise; no arithmetic instances
module Data.Float    -- IEEE 754 binary64: parsing, rounding, `totalCompare`
module Data.Ordering -- the three-way comparison result
module Data.String   -- UTF-8 views, search and split, `Builder`, interchange
module Data.Bool    -- logical functions
module Data.List    -- list operations (immutable lists via ADT)
module Data.Set      -- a set, over `Map k Unit`
module System.Env    -- `args` and `exit`
module System.IO     -- `readFile`, `writeFile`, `stderr`
```

`System.IO` and `System.Env` are the whole of a program's contact with anything
outside itself, and are deliberately small: each operation has to exist in
every backend's runtime. Files are read and written as bytes, so `readFile`
answers `Option String` -- the validating constructor of 8.1 is what stands
between a file and a `String`, rather than something hidden inside a read.
`main` keeps the type `fun() -> Unit`; a program chooses a status with
`System.Env.exit`, which diverges. `System.Env.args` excludes the program's own
name, so element zero is its first real argument (SPEC-DELTAS.md 58).

---

## 9. Modules

### 9.1 Module declaration

Each file begins with an optional module header:

```
module MyMod.Sub (f, MyType(..), MyClass(..), g)
```

If omitted, the module name defaults to the file name. If no export list is
given, top-level values, types, constructors, classes, methods, and associated
families are exported. `C(..)` exports a class with all its members.

### 9.2 Imports

```
import MyMod.Sub                       -- unqualified, all exports
                                       -- and also MyMod.Sub-qualified
import MyMod.Sub as S                  -- qualified-only as S
import MyMod.Sub (f, MyType(..))       -- selective
import MyMod.Sub hiding (f)            -- everything but f
```

Any explicit Prelude import replaces the automatic one. A selective import
therefore exposes only what it names and contributes one ordinary dependency
edge. The special spelling `import Prelude ()` exposes no names and removes
the implicit dependency edge entirely; for every other module, `import M ()`
remains an instance-only dependency.

### 9.3 Name resolution

1. Local scope (parameters, `let`/`var` bindings).
2. Module's own top-level declarations.
3. Bare names from plain imports.
4. Qualified names (`M.x` or an explicit alias).

Constructor disambiguation in patterns: if two imported types share a constructor name, the constructor must be qualified, or an error is raised requiring qualification.

Classes, methods, and associated families have stable qualified identities.
Two modules may each declare `Eq`, and two classes may each declare `map`.
Instances are visible globally for coherence, but an instance must be declared
in the module that owns its class or the module that owns its head type; tuple
instances may live with their class or in `Data.Tuple`. Overlapping heads are
rejected.

---

## 10. Complete Worked Example

```
module Stack (Stack, new, push, pop, drain)

import Data.Array as Array

type Stack a = Stack {
    data : Array a
}

fun new(capacity : Int) -> Stack a {
    Stack { data = Array.new(capacity) }
}

fun push(s : Stack a, x : a) -> Unit {
    Array.push(s.data, x)
}

fun pop(s : Stack a) -> a {
    match Array.pop(s.data) {
        Some(x) -> x
        None -> error("empty stack")
    }
}

fun drain(s : Stack a) -> Array a {
    let out = [] : Array a
    loop {
        match Array.pop(s.data) {
            Some(x) -> Array.push(out, x)
            None -> break out
        }
    }
}
```

Inferred types:

```
new   : fun(Int) -> Stack a
push  : fun(Stack a, a) -> Unit
pop   : fun(Stack a) -> a
drain : fun(Stack a) -> Array a
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
| 8 | `Array` is an opaque source-library type over hidden `Prim.Array` storage; indexing is the `Index` class |
| 9 | `Data.Array` provides collection functions and standard class instances |
| 10 | Logical length is `len` through `Length`; storage capacity is not a surface value |
| 11 | `Array.new` takes capacity; `Array.filled` takes length and a value |
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
| 31 | Modules have explicit exports; plain imports add bare and module-qualified names, while `as` is qualified-only |
| 32 | Runtime representation is opaque (compiler's choice) |
| 33 | Array literals construct the opaque `Data.Array.Array` wrapper around primitive storage |
| 34 | ~~Operators are monomorphic (Int/Float variants); no typeclasses in v1~~ -- superseded: every arithmetic and comparison operator is a class method (SPEC-DELTAS.md 32) |
| 35 | `error : String -> a` is a polymorphic primitive (panics/diverges) |
| 36 | A `var` is a mutable cell, and closures capture the cell, not its value -- so a lambda that writes a captured `var` writes through to it (SPEC-DELTAS.md 49; new prose for behaviour the evaluator always had) |
| 37 | Numeric projection `v.0` is zero-based, read-only, and polymorphic over tuples and immutable single-positional-variant values (SPEC-DELTAS.md 55) |
