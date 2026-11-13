# How the compiler is written

House style for `src/`, the Turkey compiler written in Turkey. It exists
because the first drafts got several of these wrong, in ways that are cheap to
fix once and expensive to fix in ten thousand lines.

Where a rule is about the *language* rather than about taste, it says so.

## Imports

**Rely on the automatic Prelude.** Every module that does not say otherwise
imports it, and it already provides:

- every class -- `Eq`, `Ord`, `Show`, `Iterator`, `Index`, `Length`, `Monad`, …
- `Bool(..)`, `Option(..)`, `Either(..)`, `Ordering(..)`, `Array`, `Map`
- `print`, `write`, `show`, `len`, `error`, `hash`
- thirteen `Data.*` modules and `Iter` under their own
  qualification: `Vec.push`, `String.slice`, `Int.toString`, `Char.isDigit`,
  `Map.get`, `Option.map`, `Iter.any`

So a module that lexes text needs **no imports at all**. Write `String.push`
and `Vec.new` and be done.

`import Prelude ()` is for the standard library, which the Prelude itself is
built from. A compiler module that writes it, and then re-imports `Data.Eq`
and `Data.Option` and `Data.Bool` by hand, has reconstructed the Prelude by
hand for no reason. Don't.

What still needs importing:

- `System.IO`, `System.Env`, `Data.Set` -- not among the Prelude's qualifiers.
- other `Turkey.*` modules.
- `Iter`, as `import Iter`: the compiler in `bootstrap/`, which builds `src/`,
  does not qualify it.

`import Turkey.Ast` gives `Turkey.Ast.Expr` and no bare names; a module that
works on the tree writes `import Turkey.Ast (..)`, and one whose own `Pred`
would collide writes `import Turkey.Ast (Pred as _, ..)`.

**Qualified names work, including for types.** `String.Index` is a type and
`String.slice` is a function, and neither needs an import beyond the Prelude's.
Prefer the qualified spelling: it says where a name comes from, and it cannot
collide with the `Index` *class* the way a bare `Index` does.

## Exports

`T(..)` exports the type *and* its constructors. `T` alone exports the type
opaquely. So write one or the other:

```
export (Expr(..), ExprKind(..), Lit(..), litKind)
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

Modules are `Turkey.*` and live in `src/Turkey/`.

Reserved words cannot be field or variable names, and the list is longer than
it looks: `type class instance fun let var match if else while for in loop
return break continue module import export as do`. `export` is the one that
bites.

## Taking a record apart

A record pattern names every field or ends in `..`, and which of the two a site
writes is a statement about the site.

A function whose contract is *every field of this node* -- a child enumerator,
a map or rebuild, a substitution, a free-variable scan, a dump -- takes the node
apart with a full pattern and no `..`, writing `field = _` for a field it has
considered and does not need. Then a field added to the declaration is a
compile error in exactly the functions that have to decide about it. Reading
the same node by projection (`arm.body`) compiles on regardless, which is how a
walker comes to skip a new child silently.

A function that reads a few fields on purpose -- an accessor, a diagnostic, a
one-field test -- projects, or writes `..`.

## Diagnostics

A message has up to three parts, in this order, and the reader must be able to
tell which part is which:

1. **What is wrong**, stated about the code at the span and naming the user's
   own identifiers and types: `'x' is bound twice in this pattern`. Every
   message has this part.
2. **The rule it breaks**, only when the first part leaves it unclear. Phrase
   it modally -- *cannot*, *must*, *can only* -- never as a plain-present
   description of how things are. `a function made by '_' uses its parameter
   once` reads as neither the problem nor the requirement; `'_' can stand for
   only one parameter` is the rule.
3. **The fix**, when there is a clear one, as an imperative after a semicolon:
   `; write 'Some(x)'`.

Leave out the compiler's reasons ("so that reduction terminates") and its
vocabulary (Core, binders, giblet, opens, lowering). Say *associated type*, as
the reference does, not *type family*. A message starts lowercase, has no
trailing period, and quotes code in single quotes. A runtime panic names the
operation and the values it was given: `Byte.getLE: width 9 is outside 1..8`.

The stage prefix says who is complaining. A check that only a compiler bug can
fail uses the stage `internal error`, never a user-facing stage such as
`type error`, so the reader knows the program is not at fault.

## Comments

The standard is in the root `CLAUDE.md`: the code as it is now, no references
to documents or tickets, no history, no narration. What is worth a comment here
in particular is an ordering the passes depend on, a representation invariant
the backend relies on, and any place where the obvious simplification is wrong.

## When something is awkward

The language pinching -- a missing library function, a rule that forces a
clumsy shape, a papercut in the compiler's own source -- is worth recording
while it is fresh, because the interesting part is what was being written when
it bit. That goes in a tix ticket, not in a comment and not in a file.
