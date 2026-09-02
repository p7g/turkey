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

### 30. `Array` could not be emptied
**library, fixed.** M22. `Data.Array` had `push` and `pop` and no way to drop
every element, so a reused buffer had to be reallocated. `Array.clear` keeps the
capacity, which is the point of reusing it.

### 36. Node ids were unique per file, and one table is per program
**bug, fixed.** M23. Entry 10 records the decision: Turkey has no identity, so
the parser stamps a monotone `Int` on every node and the tables key on that. The
counter was the *parser's*, and restarted for each file -- which is fine while
every table keyed by it is per-module, and wrong the moment one is not.
`TypeTable` holds a whole program by design, so `Data.Array`'s node 57 read
whatever module had got there first.

It surfaced as an absurdity rather than a crash: the `next` of a `for ... in`
over an array came back typed `fun(String) -> Int`, which is `len`. Two facts
made that a one-step diagnosis instead of a hunt. The type was *printable*, so
the wrong answer named itself. And `TypeTable.of_` had just been changed to
report a miss rather than answer `TBottom`, which is what turned "some later
pass is confused" into "this node has no type".

The counter is program-wide now, and lives in `Turkey.Ast` with the node kinds.
Python keys those tables by object identity, which is unique by construction --
so the Python never had to say that program-wide uniqueness was load-bearing,
and there was nothing in it to port wrongly. This is the first bug in the
bootstrap that is *only* a bug in the port, and it is one the language's absence
of identity made available.

### 38. A generated node kept the id zero, and the checker found it
**bug, fixed.** M23, and entry 36's other half. `Resolve` writes an `EVar` of
its own for the `get` and `set` a bracket means, and stamped it `id = 0` --
written when node ids came only from the parser and resolution had no counter to
draw on. Every bracket in the program therefore shared one id, so every `set`
read whatever the first one had recorded.

What makes it worth its own entry is *how* it was found. The `core` dump matched
the reference byte for byte and all three `.core` goldens still passed, because
the printer does not show a node's type: the wrong type sat on a `CField` where
nothing printed it. It was the Core checker, on its first run, that said `the
field 'set' should be fun(Array a, Int) -> a but is fun(Array a, Int, a) ->
Unit`.

That is `plan.txt` item 5's claim, demonstrated on the port rather than argued
for: a differential test compares what two implementations *print*, and a
checker compares what a term *is*. The two catch different things, and this is
one only the second could catch.

### 39. The Core dump printed literals raw, and one control character showed it
**bug, fixed.** M23. Entry 2 records the token and tree dumps being made to
print the *language's* spelling rather than the host's. `core.show_expr` was
never given the same treatment: a string literal was `f'"{e.value}"'`, so a
`\r` or a `\n` inside one went into the dump as itself.

That makes the dump not read back -- a literal ends the line it is printed on --
and it makes the text sensitive to how it is captured, which is how it surfaced.
The harness read `boot`'s output with `text=True`, whose universal-newline
translation rewrites a `\r` as a `\n`; the reference side was built in-process
and did no such thing. The result was a mismatch four thousand lines from
anything wrong, in the one corpus program that writes `"a\r\nb\r\n"`.

Two fixes, and the second is the one that matters. The harness now decodes bytes
itself rather than letting the capture rewrite what it is comparing. And both
dumps now share one spelling -- `lexer.literal_text` in the Python,
`Ast.literalText` in the port -- so a float gets PRIMITIVES.md 3.3's form and a
string or char gets design.md 2.1's escapes, wherever it is printed.

No golden moved, which is exactly why this lasted: not one of the three `.core`
goldens contains a control character in a literal. A dump nobody has printed a
hard case through is a dump whose escaping has not been tested.

### 40. The optimizer was quadratic in call sites, and only `boot` was big enough to show it
**performance, fixed.** M23. Compiling `boot` takes about ninety-seven seconds,
and a profile said that ninety-five of them are `turkey/opt.py` -- the *Python*
optimizer working on `boot`'s own Core, before a single line of `boot` runs.
Two costs, both invisible at the scale of `tests/programs`:

**`dataclasses.fields` was called fifty-five million times.** It builds a fresh
tuple per call, and six generic walks in that module ask it once per node per
traversal. The answer depends only on the class, so it is now asked once per
class. About a sixth of the compile.

**The inliner asked per call site what it should have asked per callee.**
`body_of` already memoizes each binding's *reduced body*, and then every site
that mentioned that binding recomputed `_transfers(body)` and `_size(body)` over
it -- and, worse, ran `_rebase_spans` (a full copy) and `_apply` (a full
substitution) *before* discovering the body was too large to inline at all. Both
questions are about the callee, so both are now answered once per binding, and
answered before anything is copied.

Together: ninety-seven seconds to forty-six, with every golden and all 1,083
Python tests unchanged.

`plan.txt` item 9 calls the bootstrap "the scale test for both the language and
its new execution path". This is the second half of that sentence collecting.
Nothing in `tests/programs` is large enough for a per-call-site copy of a
too-large body to cost anything measurable; `boot` is twelve thousand lines with
a standard library behind it, and it made a constant factor into a wall.

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

### 31. No mutually recursive modules, so two files became one
**design.** M22. `FromInstance` holds the instance whose dictionary it is; an
instance holds the plan for building that dictionary; a plan is made of
evidence. Python is two files and breaks the cycle by typing one field `object`.
Here they are one module.

The language's own answer, stated in design.md 9 -- "two modules that need each
other are one module" -- and this is the first place the compiler has had to
take it. It is a *good* answer for genuine mutual recursion, and the cost is
that it applies to a cycle of one field as much as to a real one: what wanted
merging was three small record types, and what got merged was an eight-hundred
line class table with the elaborator's data. Splitting the algorithm out kept it
to the data, which is the mitigation available.

### 32. A type constructor's level had to become a mutable cell
**design.** M22, and the third consequence of entry 10. A *skolem* is a
constructor whose level is the rank of the binder that made it, and the solver
stamps that rank on after generation has already built every type mentioning it.
Python's constructors are objects shared by reference, so `con.level = rank` is
seen everywhere at once. A level carried in the immutable variant would have to
be rebuilt into every type that holds one, so `TCon` carries a record like a
variable does.

Nothing is wrong with this -- it is the same "record wrapping a variant" shape
the AST uses. It is recorded because it was *not* obvious from the Python, where
mutating a field of a shared object reads like an assignment rather than like a
design decision, and because it had to be found by asking "what does the solver
write to, and who else can see it".

### 33. There is no regular expression, anywhere
**library.** M22. `errors.short` strips the module qualification out of every
diagnostic with one expression, `[A-Za-z_][A-Za-z0-9_.]*#`. Written as a scan it
is twenty lines and a helper, and the twenty lines are the part a reader has to
check against the intent. Not an argument for a regex engine in the language --
one pattern does not pay for one -- but worth noting that the first real program
wanted one within its first ten thousand lines.

### 34. An exhaustiveness witness was spelled with the host's `repr`
**bug, latent.** M22. `exhaustive.render` renders a literal witness with
Python's `repr`, which is the same host dependency the token and tree dumps had
to be rid of (entry 2). It is unreachable in practice -- a literal key never
appears in a type's signature, so a witness is never rebuilt from one -- so
nothing has ever printed it. The port spells it the language's way; the Python
still does not, and the branch stays dead in both.

### 35. A panic trace golden pins a *library* line number
**design.** M22. `err_out_of_bounds.expected` names `Data/Array.tl:78:9`, so
adding `Array.clear` above `bounds` broke two conformance tests that have
nothing to do with either. The frame is the right thing to print -- a panic
trace naming only the user's file would be much worse -- but it couples every
golden that panics inside the library to the library's layout, and the failure
reads as "output mismatch" rather than "a line moved".

Regenerating is correct and was correct here: the message, the frame names and
the user-file locations were all unchanged and only the line moved. Recorded
because the *next* such break will look exactly like a real one.

### 37. `instance` and `loop` cannot name a function
**design.** M23, and the fifth and sixth reserved word to take an obvious name
(entries 7, 23, 28). `instance` is what the function that lowers one instance
wants to be called, and `loop` is what the function that lowers a loop wants to
be called; they are `instanceBind` and `lowerLoop`. Eight common words are now
unavailable to every binder in every program.

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
