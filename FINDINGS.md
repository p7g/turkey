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

### 41. A control transfer cannot cross a destructuring binding
**design.** M24. This is refused:

```
let (fn, args) = match e.kind {
    CApp(f, a) -> (f, a)
    _ -> return None
}
```

with `a control transfer in a destructuring binding`. The lowering's rule wants
a plain name to hang the join's parameter on, and a tuple pattern has none --
`Turkey.Lower.convBindName` says so outright. The same statement with a single
binder is fine, and so is the tuple binding without the `return`.

The shape is not exotic; it is what a function does when it takes several things
apart at once and has a nothing-to-do case. It came up in `Turkey.Mono` within
an hour of the file existing, and the fix -- split the function so the `match`
dispatches to a second one that takes the pieces as parameters -- reads better,
which is the honest reason to leave it. What it costs is that the better
spelling was not a choice.

Related to entry 8: both are places where a form the language *has* is not
available in a position it obviously belongs.

### 42. A guard that could never fire, and cannot simply be made to
**design, open.** M24. `opt._mentions_alts` asks whether the `match` that
case-of-case pushed into a branch survived the reduction -- "did pushing it
there buy anything" -- and asks it as `n.alts is alts`. The answer is always
false. `_Reducer.expr` rebuilds every node it walks, the alternative list
included, before any rule fires, and no rule reuses the caller's list; so the
object being compared is gone by the time the reduction it is asking about has
happened. Counted over the whole suite to be sure: 364 calls, 364 answers of
false.

What that leaves switched off is case-of-case's join point. The `landed` guard
is always satisfied, no branch ever becomes a `jump`, and the continuation is
copied into every branch -- the code explosion `plan.txt` item 7 introduces join
points to prevent, described in the docstring immediately above the line.

The port could not translate `is`, so the question had to be asked properly, and
the fix looked easy: carry the *patterns*' identity, which is the one part of an
alternative that survives a rebuild (`CAlt` is rebuilt with its `pat` reused,
here and in `core.map_kind` alike). It works -- the guard then fires 206 times
and 18 rewrites that bought nothing are declined -- and it makes the compiler
worse. A branch whose pushed-in match has not collapsed *yet* becomes a jump
carrying the whole unreduced branch, so the join specialization downstream finds
no constructor tag to split on: `tests/test_opt.py`'s `clamp` goes from erasing
its `Flow` entirely to keeping a four-way match on it. Reverted on both sides.

So the entry is open rather than fixed, and it is two findings. The guard has
never fired, which is a bug. And "did this rewrite buy anything" is not
answerable by looking for the term afterwards, because the reductions are a
fixed point and a term that is still there may be one rewrite from gone --
which is a design question about where the protection belongs, not a spelling
mistake. All three `.opt` goldens are byte-identical either way, which is its
own finding about what the goldens cover.

`boot` states the answer as a constant with the reasoning attached, which is
the honest translation of a question whose answer is decided.

### 43. The new inliner does not finish on a program the size of the compiler
**performance, fixed.** M24, after rebasing onto the LLVM backend. `turkey`
cannot compile `boot` at all any more. Measured, with a 2 GB stack and no
recursion limit worth speaking of: 125,459 reductions in 472 seconds, at a
flat ~250 a second, and then a `RecursionError`. The same check under the
*previous* cost model takes 54 seconds.

Two scale failures, and neither is a bug in a rule:

**Four fifths of the compiler is now inlinable.** `_size` charging nothing for
variables, literals, `let`s and single-alternative matches is right about what
a call site grows by, and on `tests/programs` it is a clear win -- the
benchmark goes 3.22s to 2.51s. On `boot` it puts **1519 of 1896** lambda
bindings under the budget. It is not that any one body is secretly huge: the
worst understatement in the whole program is `Turkey.Desugar#sequence` at 32
against 112 real nodes. It is that nearly everything qualifies, so every
inlined body arrives full of calls that also qualify.

**Each reduction costs more than the last.** `_Reducer.expr` re-walks the whole
subtree after every rewrite -- `e = self.children(made)` -- so as the terms
grow the constant factor grows with them. Small programs run ~1500 reductions
a second; `boot` runs 250.

**And the stack is call-graph depth times term depth.** `inline` asks
`body_of` for a callee's reduced body *while inside* the caller's walk, so a
chain of first-time expansions stacks a full traversal per link. There is no
rewrite cycle -- no node reached even 400 rounds of its own fixpoint -- so this
is depth, not divergence.

**What it actually was.** The first two measurements above are true and were
not the cause; the third was, and it was worse than "depth". Reducing
top-level bindings in dependency order fixed the part it describes -- 472
seconds to 52, and the deepest chain of nested body reductions from the height
of the call graph down to nine -- and `boot` still could not be compiled.

The measurements that found the rest, each contradicting a plausible story:

* Not a rewrite cycle. No node reached 200 rounds of its own fixpoint.
* Not speculation. With `SPECULATIVE_INLINE_LIMIT` lowered to refuse it
  outright, the overflow is unchanged.
* Not the input. `mono` hands `opt` 2,123 bindings, 106,294 nodes, deepest
  term 96 levels. During reduction one term passes 440,000 levels -- deeper
  than the whole input program has nodes.
* Not recursion through the rules. In a 1.9-million-frame stack there is
  exactly one `inline` frame and one `step` frame. The rest is a single flat
  descent into one enormous term.

The term is `Turkey.Desugar#thread`: fifty nodes, in a twenty-one binding
cycle with `#expr`, the desugarer's whole expression walker. `inline` asked
`body_of` for each of the other twenty *while inside* `thread`'s own walk, and
then refused most of them on size -- 74,073 inlinings, every reduction in the
trace, and then the stack. Both halves of that are work done before the
question that would have made it unnecessary:

* the ceiling was asked of the *reduced* body, so a callee too large to inline
  was fully optimized at every call site that named it, and the answer thrown
  away. Asked of the body as written it costs nothing observable: across the
  3,393 inlines the conformance suite performs, the largest body inlined is
  fifty nodes as written, and reduction never shrinks one by more than a
  quarter, against a ceiling of 128. It is the size question GHC asks, whose
  unfolding guidance is computed from the term as written.
* a cold callee was reduced nested inside its caller. With bindings reduced in
  dependency order, a cold monomorphic callee is one in the caller's own cycle
  whose turn has not come. Declining it leaves a cycle's members inlined into
  each other in the order `deps.sccs` already sorts them into -- the same
  arbitrary-but-stable choice the loop breaker makes.

`turkey opt boot/Main.tl` finishes in 44 seconds, and no golden moves: the
whole corpus reaches neither declined case.

Two things are left. Speculation restores its flag to what it was rather than
to `False` -- clearing it on the way out of an inner speculation re-armed the
outer one -- which was a real bug and not the cause of anything measured here.
And an instantiated key is still exempt from the second rule, because
`reduce_program` warms no key that names type arguments; warming those through
a request queue, as `mono` already has, would remove the exemption rather than
state it.

The third candidate fix, a global tick limit, is rejected rather than
deferred. It makes the output depend on traversal order and on program size,
which would break M26's claim outright: `stage2.c` and `stage3.c` are the same
program compiled by two hosts, and a budget exhausted at a different point in
two different walks gives two different programs. It also answers "is this
reduction worth doing" with "was it early".

### 44. The layout invariant refuses the bootstrap compiler
**design, fixed.** M25's problem, arriving early. Once `opt` finishes (43),
`boot` reaches `mono.check_layouts` and is refused:

```
monomorphization left a generic body able to destructure polymorphic data,
whose layout it cannot know: push takes xs : Array a
```

One leak, down from the four the old cost model left (`grow`, `map` twice and
`push`) -- the stronger inliner removes three of them, and cannot remove the
last.

**Fixed by `turkey/layout.py`**: the same specialization keyed on the *layout*
of each type argument rather than on the type, with no cap, because the reason
for the cap does not apply. There are seven layouts and infinitely many types,
and `layout(Pair a)` is `ptr` whatever `a` is, so the chain that made item 6
partial -- `Pair Int`, `Pair (Pair Int)`, ... -- collapses to one key.

The copies stay *polymorphic*. Substituting a type would be a lie: `#push` at
layout `ptr` is called with `Array String` and `Array (Option Int)` alike and
no type checks against both. So a copy keeps the original's scheme, its call
sites type-check unchanged, `coretc` checks it as it checked the original, and
it carries one extra fact -- `CBind.layouts`, the layout each abstracted
variable stands for, which `backend_lower.layout_of` consults. A variable that
had no layout and was held `BOXED` now has one.

Nothing type-directed happens at run time that did not happen before: no
witness table, no address-only value, no reabstraction thunk for a closure
crossing the boundary, because `layout(fun(a) -> b)` is `ptr` and the closure's
own body is a binding this pass shares in its turn. That was the alternative
considered -- Swift passes a value-witness table and keeps unknown-typed
values address-only -- and it buys the same totality for a runtime indirection
on every access. The six keys also make the exact-root collector's job
computable: a body keyed `ptr` knows its slots are pointers and one keyed
`i64` knows they are not.

`tests/test_layout.py` exercises it by setting `MAX_SPECIALIZATIONS` to zero,
which is the same situation `boot` reaches by being large.

Nothing in `tests/programs` violates it, and the commit that added the check
says why: specialization stops the generic `Data.Array#grow` and `#push` from
being *reached*, and inlining removes what is left. Neither finishes the job on
a program where the specialization cap actually binds -- `boot` uses `Array` at
enough types, through enough layers, that generic bodies survive with live call
sites.

This is exactly the hole `plan.txt` item 10 describes and M25 was already
scheduled to fill: **one compiled body per distinct layout of the type
arguments**, which is total where specialization is partial, because there are
six layouts and infinitely many types. The refusal is the right thing to do
until that exists; it is not a thing `boot` can be written around.

### 45. `HasField` is erased, so a record-polymorphic body cannot be compiled
**design, fixed.** M25. `mono.transparent_parameters` holds that a
bare `a` parameter is always safe, "because that is parametricity": a generic
body may hold an abstracted value and pass it on, and nothing else.

`HasField` is the exception, and it is not a small one. A record-polymorphic
binding *does* take an `a` apart, and the predicate that licenses it is
discharged by the solver and **erased** -- nothing is passed for it. So the
body knows a field's type and not its position, and `coretc.record_field` says
so in as many words: "if that is still a variable the field was resolved by a
`HasField` the solver discharged and erased, and there is nothing left here to
check."

Specialization hides this whenever it reaches such a binding, because the
receiver becomes a known record. It stopped reaching `Data.Map#findSlot` on
`boot`, which was unannotated and so had inference give it
`HasField "cap" m Int`. What was left could not be compiled, and the failure
was the lucky one: the *field index* is unknown, so the backend refused. Had
the body only done `Prim.arrayGet(m.table, i)` there would have been no
refusal -- `layout_of` answers `BOXED` for everything read out of an unknown
receiver, so the elements would have been read at the boxed width whatever
they were written at. That is a silent wrong answer, and it is the exact
hazard `check_layouts` exists to prevent, in the one case it was not looking
at.

Layout sharing cannot fix it and is not asked to: a layout is a width and a
pointer bit, and what is missing is an offset and an element type.

**The fix is to stop erasing it.** A field access is a class method, and the
reason it did not look like one is that `HasField l r a` has three arguments
where a class has one. The label is a compile-time constant and folds into the
name; the field type is a *function* of the receiver, which is what an
associated type family is for. So the encoding is one generated
single-parameter class per label:

```
class %HasField.cap r { type %Field.cap
                        get : fun(r) -> %Field.cap r
                        set : fun(r, %Field.cap r) -> Unit }
```

with a generated instance per record type declaring that field. Nothing in the
dictionary machinery widens: `dict_parts` finds a real class, the dictionary is
an ordinary one-argument `%Dict.%HasField.cap r`, devirtualization hoists
`%inst.%HasField.cap.Map#get`, and the inliner collapses it back to a direct
field load wherever the receiver is known -- the path `Ord Int`'s `lt` already
takes. `transparent_parameters` then exempts the dictionary parameter by the
rule it already has, and a record-polymorphic body compiles because it is
handed accessors rather than being expected to guess an offset.

It **removes** machinery rather than adding it. `Solver.improve`'s
`HAS_FIELD`/`HAS_PROJECTION` branch exists to enforce the fundep `l r -> a` by
hand -- two stuck demands on the same label and receiver are made to agree, or
two reads of `m.cap` on an unknown `m` would generalize to unrelated variables.
With the family both reads have the type `%Field.cap r`, the same type
expression, so ordinary unification does it and the branch goes. That is the
position `classes.py` already states in its header -- "an associated type
family makes the second parameter unnecessary" -- and it keeps the fundep out
of the language, where it belongs.

The uniform version is the one to build: making only *retained* `HasField`
predicates carry dictionaries would leave two lowering paths for field access
and a class generated on demand, which is the kind of special case that rots.
The cost to watch is diagnostics -- `_has_field` produces "type 'Box' has no
field 'cap' (it has: ...)" and "'Int' is not a single-variant record type.
Multi-variant types are immutable and are taken apart with 'match'", which
ordinary instance resolution would render as "no instance for
`%HasField.cap Int`". Several `err_*` goldens cover those, so the generated
classes need their own diagnostic path, and that is part of the work rather
than a follow-up. The accessor bodies must also be built directly in Core:
written in the surface language, `fun get(r) = r.cap` demands its own
`HasField` and regresses.

**Done.** The class is generated per label, the instances per record type, and
both on demand -- a label's class has to exist before any signature mentioning
it is read, and no pass can know in advance which tuple arities a program
projects from. `mono.check_opaque_destructuring` is gone, because what it
guarded against cannot happen: there is no body that reads a field without
evidence for reading it.

Nothing ground pays for it, which was the thing to check rather than assume.
Every `.core`, `.mono` and `.opt` golden is byte-identical: the instance has no
context, so wherever specialization reaches, the dictionary is ground,
`mono`'s devirtualizer hoists the accessor, and the inliner takes it -- the
body is one node -- leaving exactly the `CField` that used to be emitted
directly.

What it cost is entry 47, which arrived with the fix rather than surviving it.
`lib/Data/Map.tl` keeps its two annotations because of that, but they are a
choice now rather than a workaround: the receiver is a `Map` at every call
site, so saying so costs nothing and keeps both the exhaustiveness check and
the specialization sharp.

### 46. A reflexive equation makes family reduction spin
**bug, fixed.** M25, found by `test_a_field_of_a_record_polymorphic_target_keeps_its_inferred_type`
-- a recursive record type accessed record-polymorphically, which is exactly
the program that has two demands for one field of two receivers.

`Field.tag a ~ Field.tag a` reaches a binding's `equations`. Two family
applications are deferred while their arguments differ, and the arguments can
become equal afterwards; nothing dropped the equation once they had. As a
*given* it is a rewrite rule from a family application to itself, so
`types.normalize` -- reduce at the head until the head is no longer a family --
never terminates. The compiler did not crash or report anything; it spun, in
`mono`, on a program the suite had been checking all along.

Three changes, and each is a different kind of guard. `classes.simplify` drops
an equation whose sides are already equal, since it states nothing.
`Fams.reduce` and `Solver.reduce` *skip* such a rule rather than returning it,
so the instance table still gets its turn -- returning it left `Field.tag Auto`
sitting next to the `Int` it is, and the comparison that needed them equal
failed. And `normalize` stops when a rule hands back what it was given, so a
reducer that fails to make progress costs an answer rather than the compiler.

It was reachable before this milestone: associated families have always been
able to produce one. Making field access a family is what found it.

### 47. A wanted equality on a family is not used as a rewrite
**bug, open.** M25, and a regression that arrived with entry 45 rather than
one it failed to fix.

`HasField "pos" a Int` put the field's type in the predicate's third argument,
so an assignment to the field unified a *variable* with `Int` and everything
downstream knew it. `Field.pos a ~ Int` says the same thing and does not
substitute, because a family application is not a variable. Two symptoms, one
cause:

* `bf.tl`'s `move` retains `Add (Field.pos a)`, `Ord (Field.pos a)` and
  `Length (Field.data a)`, which used to discharge. They are correct, and they
  are three dictionaries a caller now passes for nothing.
* an un-annotated `Data.Map#resize` has its `match` reported non-exhaustive.
  The scrutinee is *not* opaque: matching `Empty` is what contributes the
  equality that says which type it is. Exhaustiveness reads the recorded type
  without applying that equality and concludes it knows no constructors.

Neither rejects a valid program today -- the first costs speed in unspecialized
code, the second is a warning -- but the second is a false report, which is
worse than either.

This is the capability `Solver.improve`'s hand-written functional dependency
was buying, and writing it off as free (entry 45) was wrong. The fix is
GHC's rule that a wanted equality rewrites other wanteds. What makes it more
than a one-line change is that three places normalize independently:
`Solver._class` through the class table, exhaustiveness through the type the
inferencer recorded, and `Elaborator.resolve` through its own. Teaching only
the first is not a partial fix but a broken one -- solving then accepts
`Add (Field.pos a)` and elaboration fails with "no evidence for
'Add (Field.pos _a)', which solving had already accepted". It wants one
normalization that includes the equalities in scope, used by all three, which
also means the equality set has to reach elaboration: it is known per binding
at generalization, and the `Use` recorded at each site is where it would ride.

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
