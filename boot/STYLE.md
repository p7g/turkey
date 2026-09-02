# How the compiler is written

House style for `compiler/`, the Turkey compiler written in Turkey. It exists
because the first drafts got several of these wrong, in ways that are cheap to
fix once and expensive to fix in ten thousand lines.

Where a rule is about the *language* rather than about taste, it says so.

## Imports

**Rely on the automatic Prelude.** Every module that does not say otherwise
imports it, and it already provides:

- every class -- `Eq`, `Ord`, `Show`, `Iterator`, `Index`, `Length`, `Monad`, …
- `Bool(..)`, `Option(..)`, `Either(..)`, `Ordering(..)`, `Array`, `Map`
- `print`, `write`, `show`, `len`, `error`, `hash`
- every `Data.*` module under its own qualification: `Array.push`,
  `String.slice`, `Int.toString`, `Char.isDigit`, `Map.get`, `Option.map`

So a module that lexes text needs **no imports at all**. Write `String.push`
and `Array.new` and be done.

`import Prelude ()` is for the standard library, which the Prelude itself
depends on and therefore cannot import. A compiler module that writes it, and
then re-imports `Std.Classes` and `Data.Option` and `Data.Bool` by hand, has
reconstructed the Prelude by hand for no reason. Don't.

What still needs importing:

- `System.IO`, `System.Env`, `Data.Set` -- not re-exported by the Prelude.
- other `Turkey.*` modules.

**Qualified names work, including for types.** `String.Index` is a type and
`String.slice` is a function, and neither needs an import beyond the Prelude's.
Prefer the qualified spelling: it says where a name comes from, and it cannot
collide with the `Index` *class* the way a bare `Index` does.

## Exports

`T(..)` exports the type *and* its constructors. `T` alone exports the type
opaquely. So write one or the other:

```
module Turkey.Ast (Expr(..), ExprKind(..), Lit(..), litKind)
```

not `Expr, Expr(..)`, which asks for the same thing twice.

## Control flow

**`else if`, not `else { if`.** The parser reads an `else` followed by `if` as
a chain, so a three-way test is three lines and not three nesting levels:

```
if n == 0 {
    "zero"
} else if n == 1 {
    "one"
} else {
    "many"
}
```

**Prefer `match` to a chain of equality tests.** Patterns work on `String`,
`Char` and `Int` literals, and alternatives work with `|`:

```
fun binaryMethod(op : String) -> Option String = match op {
    "+" -> Some("add")
    "-" -> Some("sub")
    _ -> None
}
```

A `match` on a literal says "this is a table" where an `if` chain says "these
happen to be related". It is also the form that gets an exhaustiveness warning
when the scrutinee is an ADT.

**A block answers with its last statement.** So a branch ending in a call
answers that call's value, and two branches of one `if` must agree. Where a
call is a *statement* -- its result discarded -- and it ends a branch, say so.
`Turkey.Parser` has `skipToken` for exactly this, wrapping `expect`.

## Tables

**A table is a top-level `let`, not a function returning a literal.**

```
let keywords = ["type", "class", "instance", ...]
```

`fun keywords() -> Array String = [...]` rebuilds the array on *every call*,
which in a lexer means once per identifier. Top-level `let` is evaluated once,
before `main`.

A table looked up by key belongs in a `Map` built once at the top level, not in
a linear scan -- but a scan over a handful of entries is fine and simpler, and
the operator table is scanned in order on purpose, longest match first.

## Naming

Modules are `Turkey.*` and live in `compiler/Turkey/`. The compiler is the
Turkey compiler; `boot` was a name for the milestone, not for the program, and
the program outlives the milestone.

Reserved words cannot be field or variable names, and the list is longer than
it looks: `type class instance fun let var match if else while for in loop
return break continue module import export as hiding do`. `hiding` and `export`
are the two that bite -- `ImportDecl` has a `hidden` field for that reason.

## Comments

Say why, not what. A comment that restates the code is noise; a comment that
records a decision -- why the scan and not the speculation, why the field is
`hidden`, which spec section a rule comes from -- is why the next reader does
not have to rediscover it. Cite `design.md`, `PRIMITIVES.md` and
`SPEC-DELTAS.md` by section where a rule comes from one.

## Porting

`compiler/` is a port of `turkey/*.py`, and stays recognizably so: the same
stage boundaries, the same function names where the language allows, the same
order of decisions. A port that reorganizes cannot be diffed against the thing
it was ported from, and the diff is the whole test (`tests/test_boot.py`).

Where the language forces a difference -- no exceptions, so speculation is a
token scan rather than a caught failure -- the difference gets a comment saying
which language rule forced it.

## When something is awkward

Write it down. `FINDINGS.md` at the repo root is the running list of what
writing this compiler has turned up about the language -- bugs, design costs,
friction, and library pieces that were missing. `plan.txt` item 9 says the
bootstrap is the forcing function that finds papercuts at a scale `test.tl`
cannot; a papercut nobody recorded was not found.
