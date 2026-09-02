# What writing the compiler in the language has turned up

`plan.txt` item 9 says the bootstrap compiler is "the only forcing function that
finds papercuts at a scale test.tl cannot". This is the list of what it has
found. It is kept as work proceeds rather than written up afterwards, because
the interesting part of a papercut is the moment it bites and what was being
written at the time.

Entries are numbered in the order found. Each says what kind of thing it is, and
where it stands:

- **bug** -- the implementation does not do what the spec says.
- **design** -- the language does what it meant to, and that has a cost.
- **ergonomics** -- neither wrong nor costly, just friction.
- **library** -- something missing, or present and not findable.

`SPEC-DELTAS.md` carries the long reasoning for anything that amends the spec;
this file is the index and the things too small to amend anything.

---

## Fixed

### 1. A module's own declaration lost to an import, in its own export list
**bug.** M19. `modules._exports` asked "is this name a class anywhere in scope?"
before "does this module declare a type by it?", so `Data.String` -- which
imports `Std.Classes` -- exported that module's `Index` *class* under the name
`Index`, and its own `Index` *type* was not exported at all. Own declarations
shadow imports everywhere else. Nothing in the suite could have caught it:
`boot` is the first program to name that type from another module.
SPEC-DELTAS.md 59.

### 2. `turkey tokens` and `turkey ast` printed Python
**design, fixed.** M19, M20. Both dumped a Python `repr`: the quote character
depends on the contents, the escapes are Python's, the floats are `repr`'s.
Nothing else can reproduce that, so a second implementation could only have
matched by imitating CPython -- the opposite of what a differential test is for.
Both now print a canonical form whose float spelling is PRIMITIVES.md 3.3's and
whose escapes are design.md 2.1's. SPEC-DELTAS.md 59, 60.

### 3. A compiler could not read its own source
**library, fixed.** M18. `Prim.print`, `Prim.write` and `Prim.error` were the
whole of the language's contact with anything outside itself. Six primitives,
`System.IO` and `System.Env` now close it. Everything else M18 added --
`Int.parse`, ASCII classification on `Data.Char`, `Array.at`/`last`/`swap`/
`slice`/`indexOf`/`contains`/`sort`, `Map.contains`/`getOr`/`update`/`keys`/
`values`/`entries`, `Data.Set`, `String.lines`/`words` -- was found the same
way: by trying to write the thing that needed it. SPEC-DELTAS.md 58.

### 4. Recursion was capped at a thousand frames
**bug, fixed.** M18. Nothing called `setrecursionlimit`, so an ordinary walk
over a few hundred AST nodes overflowed. The generated program now runs on a
thread with a 512 MiB stack. CPython's default is a fact about the host, not
about the language, and the C backend's answer is the same shape.

### 18. The inliner captured a caller's variable in a callee's parameter
**bug, fixed.** M21. `opt._apply_names` substitutes a *value* argument into the
body and let-binds the rest. It checked the substitution against the binders of
the callee's body -- but not against the `let`s it was itself about to wrap
around that body. So

```
fun internal(owner : String, name : String) -> String = owner + "#" + name
...
internal(name, c.name)          -- a caller's local spelled like a parameter
```

inlined to `let name = c.name in name + "#" + name`: the argument substituted
for `owner` was captured by the binding made for `name`, and the call answered
`Eq#Eq` where it meant `Std.Classes#Eq`. A parameter that becomes a `let` now
disqualifies substituting any argument mentioning it, and disqualifying one
makes it a `let` in turn, so the decision is a fixpoint rather than one pass.

Nothing in the suite could have caught it: it needs a caller's local and a
callee's parameter to share a spelling, and the argument for the *other*
parameter to be a value. `Turkey.Modules` is the first code to have written
that, and it did so twice.

### 19. A `do` block as an entire function body was never lowered
**bug, fixed.** M21. `Desugarer.walk` *replaces* a `do` node rather than
rewriting it in place, so its answer has to be taken. Two callers dropped it:
`context`, which then returned the untouched `do`, and `_if`'s unlifted case.
`fun f(o) = do { let x = o?; Some(x) }` therefore kept its `?` all the way to
`deps.free_names`, which crashed with an internal assertion rather than a
diagnostic. Found by reading the pass closely enough to port it, which is a
different kind of reading from using it.

### 20. `Data.Char` had no `isAsciiUpper` / `isAsciiLower`
**library, fixed.** M21. `Turkey.Lexer` had already written the first inline to
tell a `CONID` from an `IDENT`; `Turkey.Modules` needed the second to tell a
value export from a type export. Both now sit in `Data.Char`, and
`isAsciiAlpha` is their disjunction. The Python asks `str.islower()`, which is
Unicode-wide -- the same deliberate ASCII narrowing the lexer already carries.

### 21. `System.IO` could not ask whether a file exists
**library, fixed.** M21. A module search path has to try several candidates and
read only the one that is there, and `readFile` answering `None` conflates "no
such file" with "not UTF-8". `System.IO.canRead` is the predicate; the
primitive behind it was already there for `readFile` to use.

### 27. A module-level `let` is process state, and the compiler is one process
**bug, fixed.** M22. `types.QUALIFY` is a program-wide set of the names that
must print qualified because two modules claimed one short name, and
`DeclTable.__init__` clears it. The port made it a top-level `let` in
`Turkey.Types`, which is per-*process* -- and `boot` checks thirty-two programs
in one process where `python3 -m turkey` gets a fresh interpreter for each. So
a clash in one program made `Either` print qualified in the next thirty-one.

Nothing about this is specific to that set. It is the shape of every global the
Python has: correct in a script, wrong in a compiler that is asked to compile
more than once. `Turkey.Decls.newDeclTable` now clears it, which is what
`DeclTable.__init__` was doing all along -- the Python was already written for
the case its host never exercised.

---

## Open, and accepted

### 5. An associated family cannot be defined as a family of a concrete type
**design.** M18, writing `Data.Set` over `Map k Unit`. An instance may define
its associated family as a family applied to a *variable of the instance head*,
but not to a concrete type, so a wrapper type cannot reuse its inner type's
cursor abstractly:

```
instance Iterator (Set a) {
    type Cursor = Cursor (Map a Unit)     -- rejected
    type Cursor = MapCursor a Unit        -- so `Data.Map` must export this
}
```

The restriction is what makes family reduction terminate, so the export is much
the cheaper side of the trade -- but it means every container that wraps another
container leaks the inner one's cursor type into its public API. Found by the
second program that ever wrapped a container.

### 6. No exceptions, so speculation cannot be "try and undo"
**design.** M20. `turkey/parser.py` backtracks by catching a `ParseError` twice:
deciding whether a method is a signature, and whether a `for` is the `in` form.
Neither can be written that way here. Both became token scans that look for the
one thing separating the readings, before either is attempted.

This turned out well -- a scan says what it is deciding, where a caught failure
says only that something went wrong -- and `_rhs_has_alternatives` in the Python
was already such a scan. But it is worth being honest that the shape was forced,
and that a parser needing three-token lookahead somewhere would have no such
escape.

### 7. `hiding` and `export` are reserved words
**design.** M20. `ImportDecl` has a `hidden` field because `hiding` cannot be a
field name. `export` is reserved as well and the grammar never uses it.
Reserving a common word takes it from every record in every program, not just
from the production that wanted it. `export` in particular is currently a pure
cost.

### 8. Discarding a result has to be said
**ergonomics.** M20. A block answers with its last statement, so a branch ending
in `expect(...)` answers a `Token` while its sibling ending in a `while` answers
`Unit`, and the two do not unify. Thirty-eight sites in the parser wanted the
call as a statement; `Turkey.Parser.skipToken` is the wrapper that says so.

A statement form -- or a `Unit`-coercing discard -- would remove a wrapper per
side-effecting function. Not obviously worth a language change; recorded because
it recurs.

### 9. Mutable state means a record wrapping a variant
**design.** M20. Multi-variant ADTs are immutable and single-variant records are
the mutable ones (design.md 4.5), so a tree whose nodes are rewritten in place
-- which is what `turkey/desugar.py` does -- cannot be a plain ADT. Every
syntactic category in `Turkey.Ast` is therefore a record carrying a variant:

```
type Expr = Expr { id : Int, span : Span, kind : ExprKind }
type ExprKind = ELit(Lit) | EVar(String, Bool) | ...
```

This is not a complaint -- the split is the language's whole position on
mutation, and the wrapper is where the node identity wants to live anyway. It is
recorded because it is a *consequence* a porter meets immediately and has to
decide before writing any node.

### 10. There is no identity, so nodes carry an id
**design.** M20. `turkey/typed.py` keys every expression's inferred type by
Python object identity. Turkey records have reference semantics but no address a
program can observe and no identity hash, so the parser stamps a monotone `Int`
on every node and the tables key on that. Fine, and cheap -- but it has to be
decided at the parser, since retrofitting it touches every node.

### 11. Exhaustiveness is a warning
**design, pre-existing.** design.md 5.1. Every `match` in the compiler over a
`Kind` or an `ExprKind` is a place where a missing case is a runtime panic
rather than a compile error. In a program that is one enormous case analysis
this is the single most likely source of a late bug.

### 12. `Show String` is the identity
**design, pre-existing.** PRIMITIVES.md 7.1. `show(["a,b"])` and
`show(["a", "b"])` are indistinguishable, so `show` cannot be used to dump
anything a machine will read back -- which is why both canonical dumps have
their own quoting rather than using `show`. The `Display`/`Debug` split the
class hierarchy does not have.

### 22. An assignment cannot be a `match` arm's body
**ergonomics.** M21. An arm's body is an expression and an assignment is a
statement, so every arm of `Turkey.Resolve` that rewrites a node in place --
which is most of them, that being what the pass does -- is written
`PVar(name) -> { p.kind = ... }`. The braces carry no meaning; they say
"statement goes here". Related to entry 8, and the same shape: the block is
doing the work a statement position would.

### 23. `module` is a reserved word, so it cannot name a parameter
**design.** M21, and the same cost entry 7 records for `hiding`. `internal(module,
name)` is the natural spelling of "qualify this name by that module" and is a
parse error; the parameter is `owner`. Reserving a word takes it from every
binder in every program, not just from the production that wanted it.

### 28. `var` and `type` are reserved, so they cannot name a parameter or field
**design.** M22, and the third time entry 7's cost has been paid.
`occurs_and_adjust(var, t)` is the name the algorithm goes by in every
textbook, and `var` is a parse error in a parameter list; it is `slot` here. A
counter record wanted a field called `type`; it is `tyvar`. Together with
`hiding`, `export` and `module`, five common nouns are now unavailable to every
binder in every program, in exchange for keywords the grammar could mostly
disambiguate positionally.

### 24. `Data.Set` is not one of the modules the Prelude re-exports
**library.** M21. `Array`, `Map`, `Option` and eight others arrive
automatically; `Set` needs an import, for no reason a reader could guess. Either
it belongs in the list or the list needs a stated rule for what is in it.

### 25. No reflection, so four scanners share one hand-written child list
**design.** M21. `turkey/desugar.py` asks four questions of a subtree --
"is there a `?` or `do` under here", "is there one that unwinds to *this*
context", "does anything transfer control out of here", "is there a loop that
will be lifted" -- which differ only in where they stop. Each is a generic walk
over the dataclass fields, so a node kind added to `turkey/ast.py` cannot be
missed by one of them.

Turkey has no reflection, so `Turkey.Desugar.children` is that enumerator
written out once and the four scanners share it. That is the honest port and it
reads well, but the safety property is gone: a node kind added to `Turkey.Ast`
and forgotten in `children` is missed by all four at once, silently. The same
shape recurs three more times in the file -- the transparent walk, the bracket
lowering, and `Turkey.Resolve`'s walk -- each an explicit case per node kind
where the Python has none.

This is not an argument for reflection. It is the cost of not having it, and
the mitigation available is the one already in use: the corpus diff, which
notices a missed node the moment any file contains one.

### 26. No function identity, so a continuation is a variant
**design, and an improvement.** M21. The lowering threads a continuation
through every rule, and in one place asks *which* continuation it has:
`if k is not self.fall` is how `turkey/desugar.py` decides that a loop is in
statement position rather than value position. Turkey has closures but no
function identity -- nothing to compare `k` against.

So `Cont` here is a four-way variant: `KId`, `KFall`, `KPure` and `KFn`, the
first three being the ones the pass builds for itself and only the last a real
closure. The question becomes `isFall(k)`, which is a case analysis rather than
a pointer comparison.

Recorded as a finding because it was forced, but it is the better spelling:
`KFall` says what the continuation *is* where a bound method compared by
identity said only that it was that particular object. Worth remembering when
the same pressure comes up again -- an absent feature made the code say more.

---

## Library, still wanted

### 13. `Option.isSome` existed and was reimplemented anyway
**library, discoverability.** M20. `Turkey.Parser` grew its own `isSome` because
the Prelude's re-export of `module Option` was not where it was looked for. Now
removed in favour of `Option.isSome`. The lesson is not "read the library" so
much as: there is no way to *search* it, and the Prelude re-exports ten modules
whose contents are only discoverable by opening them.

### 29. `Option.isNone` and `String.rsplitOnce` were missing
**library, fixed.** M22. `isSome` was there and its negation was not, which is
the kind of gap that gets papered over with a `match` at every call site.
`rsplitOnce` is the more interesting one: `splitOnce` existed, `Prim.stringRfind`
existed, and only the four-line wrapper between them did not -- and taking an
internal name apart needs the *last* separator, since `M#C.method` has a `.` in
the half after the `#`.

### 14. No `Array.copy`
**library.** M20. Copying an array is `Array.slice(xs, 0, len(xs))`, which says
"take this range" where the intent is "take a copy". `Turkey.Parser` needs one
so that `collectTycons` does not push onto the shared `builtinTycons` table --
a bug that would have been silent and cross-file.

### 15. No iterator combinators
**library.** Every consumer of `String.codePoints` writes its own `for`. There
is no generic `map`, `filter`, `take`, `zip`, `enumerate`, `count` or `collect`
over `Iterator` -- only `Array`-specific versions. `Iterator` is a class with
`iter`/`next` and nothing built on it.

### 16. No `Map` construction from pairs, no `Set` literal
**library.** A keyword table is an `Array String` scanned linearly because
building a `Set` means `new()` and a loop. `Map.fromArray` and a set literal
would each be a line.

### 17. No way to catch a panic
**library/design.** `xs[i]` panics, which is why `Array.at` had to be added. The
same shape recurs: every total operation needs an `Option`-returning twin,
because there is no recovery. `Data.Int.addChecked` reconstructs the overflow
condition by hand for exactly this reason.
