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
**ergonomics, answered.** M20. A block answers with its last statement, so a
branch ending in `expect(...)` answers a `Token` while its sibling ending in a
`while` answers `Unit`, and the two do not unify. Thirty-eight sites in the
parser wanted the call as a statement; `Turkey.Parser.skipToken` is the wrapper
that says so.

This asked for "a statement form -- or a `Unit`-coercing discard", and said it
was not obviously worth a language change. It is not one: **`let _ = expr` is
already both.** A `let` is a statement, so a block ending in one answers `Unit`,
and a wildcard pattern binds nothing -- which is exactly the branch-unification
case above, and exactly what a hand-written `discard(x : a) -> Unit = {}` was
for. M27 wrote two such helpers before noticing.

What remains is that it has to be *said*, which is the design's choice and not
a defect: nothing is discarded silently. The wrapper per side-effecting
function was never necessary.

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

Speculation also restores its flag to what it was rather than to `False` --
clearing it on the way out of an inner speculation re-armed the outer one --
which was a real bug and not the cause of anything measured here. It is
unbounded rather than merely expensive: with the flag re-armed the traversal's
depth grows with whatever stack the process is given, which is how it was
finally seen (68k levels at 1 GiB, 457k at 3 GiB).

One thing is left. An instantiated key is still exempt from the second rule,
because `reduce_program` warms no key that names type arguments; warming those
through a request queue, as `mono` already has, would remove the exemption
rather than state it.

**And the port needed every one of them.** None of these three reached
`boot/Turkey/Opt.tl`, which still carried the comment stating the pre-fix
rationale -- so `boot opt boot/Main.tl` died the same death a milestone later,
and looked like a stack limit rather than a missing fix. What settled it was
one measurement rather than any amount of reading: the reference recurses
**106 levels** on that program and `boot` reached **481,650 frames**. A 1,500x
gap is not depth. The lesson is about the differential rather than the
inliner -- `test_boot` compares *output*, so a stage that crashes is a stage
the oracle says nothing about, and a fix to a shared algorithm has no test
that notices it was applied to only one of the two implementations.

The third candidate fix, a global tick limit, is rejected rather than
deferred. It makes the output depend on traversal order and on program size,
which would break self-hosting's claim outright (plan item 9, M29):
stage2 and stage3 are the same program compiled by two hosts, and a budget exhausted at a different point in
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
**bug, fixed.** M25, and a regression that arrived with entry 45 rather than
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
was buying, and writing it off as free (entry 45) was wrong.

**The fix is GHC's rule that a wanted equality rewrites other wanteds**, and
the reason it is not one line is the answer to "why does every place need
patching": because the domain's entailment was four relations rather than one.
`ClassTable.reduce_fam` knew instances; `Solver.reduce` knew instances and
givens; `coretc.Fams` knew instances and a binding's stated equations;
`typed._Reducer` knew instances alone. `types.normalize(t, fams)` has the right
seam -- one reducer, passed in -- and four different things were being passed
into it, so a fact learned by one was invisible to the others. Teaching one is
worse than teaching none: solving then accepts `Add (Field.pos a)` and
elaboration fails with "no evidence for 'Add (Field.pos _a)', which solving had
already accepted".

So the rules live on `ClassTable`, which all four consult, and they are
correct there because a rule names particular type *variables*: no other
binding mentions them, so one binding's rule cannot fire inside another and a
flat list needs no scope stack.

Three restrictions make it sound, and each was found by a test rather than by
thinking:

* **A rule must make progress.** `c.bucket = b.next` equates two *stuck*
  families, and rewriting one to the other is a step sideways that steps back.
  Only an equality whose other side is neither a family nor a bare variable
  becomes a rule.
* **A rule never decides what a type is.** `Solver.reduce` is what `unify`
  calls, and a unification is irreversible: reducing `Container.Elem a` to
  `Int` by an unproved equation *binds* a variable, and the family is then gone
  from the scheme that was supposed to carry it. `_class` asks with the rules;
  everything unification can reach asks without them. A scheme's own body and
  its printed form are read the same way, so `fun(a) -> Container.Elem a` stays
  what it is.
* **A rule never discharges the equation it came from.** Reducing
  `Container.Elem c` by the rule `Container.Elem c ~ Int` supplied makes that
  equation trivially true, so a program that should be told to state it in its
  context is accepted and fails in the Core checker instead --
  `err_stuck_family` caught it. `ClassTable.settled` is the reducer that
  refuses them, and both the retry in `_equals` and the reflexive-drop in
  `simplify` use it. The second matters as much as the first: a `simplify` that
  reduced with the rules dropped the equation as trivial, and a scheme that
  drops an equation stops making its callers prove it.

`bf.tl`'s `move` loses all three of the predicates it had picked up, and an
un-annotated `Data.Map#resize` no longer reports its `match` non-exhaustive.
`lib/Data/Map.tl` keeps its annotations, but they are now only worth what an
annotation is normally worth.

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

### 48. A one-armed `if` discards a value the backend tried to keep
**bug, fixed.** M25. Section 6.7 gives statement-style `if` the type `Unit`
whatever its branch answers, and `infer._gen_EIf` says so outright: with no
`else`, the branch's type is generated and then dropped on the floor. So
`if isClose(tok.kind) && len(stack) > 0 { Array.pop(stack) }` in
`Turkey.Lexer#applyNewlineRule` is a well-typed program whose branch produces an
`Option Kind` the `if` does not have.

`backend_lower` lowered the branch straight into the `if`'s own destination, so
that `Option` -- a `PTR` -- arrived at a join parameter of layout `UNIT` and the
lowering refused: `LLVM cannot convert ptr to unit`. The value was never wanted;
nothing had been told to drop it.

Nothing upstream could object, and `coretc._check_CIf` is explicit about why: on
`e.otherwise is None` it returns the node's own type without checking the
branch against it. That is the same rule as `_gen_EIf`, faithfully kept, and it
is the *lowering* that had not been told. The branch now gets a destination of
its own -- one whose parameter is the layout it really produces, and which
hands the real destination the unit the `if` really answers. When the branch
diverges the block is unreachable and `finish` drops it, so a `return` inside a
one-armed `if` costs nothing.

Worth noticing that this is the *third* consumer to need telling separately.
`pygen` never noticed because Python is untyped, `coretc` deliberately declines
to look, and only a backend with layouts had to care. A discard that the Core
stated -- the `CLet %seq` that `core.py` already documents as "how a statement
whose value is discarded is expressed" -- would have been one fact rather than
three agreements.

### 49. A constructor named rather than applied is not a function
**bug, fixed.** M25. `Array.map(xs, ChExpr)` in `Turkey.Ast#exprChildren` passes
a constructor as a function value, which the surface language allows and the
type checker types. `CCon` cannot express it: it *is* the allocation, and
`CApp(CCon, args)` is the only shape any backend reads as building a value. So
the LLVM backend reported `constructor 'ChExpr' must be saturated` -- correctly,
about a Core term that should never have reached it.

`_lower_ECon` now eta-expands through the `eta` helper the lowering already had
for deferring a method, and `_lower_ECall` names a constructor callee directly
so that a saturated call emits exactly the Core it always did. A nullary
constructor is untouched: it is already a value, and `eta` declines it for the
same reason its type gives -- it is not a function.

`pygen` had no trouble with a bare `CCon` because a Python constructor is a
callable, which is why this survived to the second backend.

### 50. Scattered record slots were keyed by a name that shadows
**bug, fixed.** M25, and found on the way to 49 rather than looked for.

`_flat_records` compares names bare and says so -- "one disqualified mention
rules out every binding sharing that name". `backend_lower.record_slots` then
keyed the scattered slots by that same bare Core name, on the lowerer rather
than in the environment, so it had no scope at all. In `Turkey.Classes#showClasses`
a flattened `b` with a field `parts` and an unrelated `b` elsewhere in the body
met in that one dictionary, and reading `b.value` raised `KeyError: 'value'`.

The `KeyError` was luck. Two records whose field *names* agree would have read
each other's slots silently, at whatever layout the other's field was written
at -- a wrong answer of exactly the kind `check_layouts` exists to prevent, in
a table it does not look at.

The fix is to stop keeping a second, worse scope beside the real one: the
flattened binding puts a stand-in in `env`, which the lowering already copies
per scope, and the slots are keyed by that. An inner `b` finds its own slot,
which is not a record's, and falls through to the ordinary `object_get`. The
name-based analysis stays name-based -- it only has to over-approximate escape,
which it still does, so it costs opportunities and not correctness.

### 51. The six outside-world primitives had no native implementation
**bug, fixed.** M26. `Prim.args`, `Prim.fileCanRead`, `Prim.readFileBytes`,
`Prim.writeFileBytes`, `Prim.stderrWrite` and `Prim.exit` existed for the
Python host and nowhere else, so every program that read a file or looked at
its arguments -- `boot` above all -- stopped at `is not implemented`. They are
in the runtime now, and `boot` compiles itself to machine code and runs.

Three of them needed more than a C function:

* **arguments** are copied out of the host and held *outside* the Turkey heap.
  A `TurkeyString` per argument would have to stay reachable for the whole
  program from a root the collector scans, and there is no such root; plain
  bytes need none, and the strings are built on demand.
* **`Data.Array`** is what two of them answer, and the runtime cannot build
  one: the record and constructor around the flat storage carry tags that live
  in the code generator's tables. So the runtime allocates the storage and
  `backend_lower.wrap_array` puts the `Array` around it, which is the shape
  `Prim.stringToBytes` already had. Reading one back needs no tags -- the
  runtime does that structurally in `array_parts` -- and only building one
  does.
* **`exit`** unwinds on the panic flag rather than through a mechanism of its
  own. Generated code already tests one flag after every call that can fail,
  and a second would be a second thing to get right at every one of those
  sites; `turkey_exiting` is what tells the two apart at the boundary, since
  an exit carries a status and no message.

### 52. A fault in generated code said nothing at all
**ergonomics, fixed.** M26. `boot` running natively segfaulted, and what that
gave was `exited -11` and no output. The JIT registers no symbols, so the
operating system's crash report is a list of unnamed addresses; and lldb
cannot control an interpreter built with a hardened runtime -- it attaches and
then reports "could not pause execution" -- so the usual next step is not
available either.

Both shadow stacks needed to answer this already existed for other reasons:
`panic_calls` carries the source position of every call that can fail, and the
collector's root frames carry function names. Printing them on `SIGSEGV` turned
a silent `-11` into the exact Turkey call stack and the exact line, first try.
Opt-in through `TURKEY_SEGV_FRAMES`, the way `TURKEY_GC_STRESS` is: taking
`SIGSEGV` over for a process that is mostly not this runtime is a debugging
choice, not a default.

### 53. A record field is written bare and read boxed when only some copies specialize
**bug, fixed.** M26, and the reason six `test_boot` cases still fail. Found by
entry 52 in one run, having been invisible before `boot` could run natively at
all.

`Data.Map#findSlot` compares `bucket.key == key` through the `Eq k` dictionary.
`mono` specializes it for the key/value pairs it can see, and those copies are
right: `findSlot@Int,String` reads `bucket.key` at `i64` and *boxes* it before
the closure call. The generic copy is also right on its own terms: it reads the
same field at `boxed` and passes it straight to the closure, because a field of
an abstracted type is supposed to already hold a box.

They cannot both be right about the same `Bucket`. `Set Int` in
`Turkey.Types#generalizeWalk` has its buckets built by specialized code, which
stores a bare `i64`, and read by the generic copy, which hands that integer to
a closure that unboxes it. The raw value 1 becomes a pointer, and
`turkey_unbox` computes `header_of(1)->kind` -- address -7, which is the
fault.

This is the hazard `mono.check_layouts` exists to prevent, and the reason it
did not is now known and fixed. `transparent_parameters` read the parameters of
a binding's *outermost* lambda. Elaboration gives a constrained function two:
the dictionaries in one, and the value parameters in another inside it. So the
outermost lambda of `findSlot[Eq k]` takes `%Dict.Eq a` and nothing else -- and
a dictionary is deliberately exempt, because passing polymorphic data to the
closures inside one is the mechanism the check exists to leave intact. Every
*constrained* generic function was therefore exempt along with its dictionary,
which is a hole the width of the language's main abstraction mechanism. Reading
the whole abstraction spine closes it, and across `tests/programs`, `lib` and
`boot` it flags exactly one thing: this bug.

`layout.share` is right and is not the problem: `_key` refuses to key a copy on
`BOXED`, precisely because that is "the answer `layout_of` gives a variable it
has no layout for". What is missing is that nothing then stops the *unshared*
original from staying reachable, and the original assumes `BOXED` for the very
variable no copy would accept it for.

The fix was the first of the two candidates weighed here: close the sharing
gap rather than give up an unboxed key in `Map Int v`. `layout.share` is right
that `_key` must refuse a copy it cannot key, and the unshared original stays
in the program -- so the answer is that nothing must still *call* it, and the
pass now drops an original once every call site has gone to a copy. Leaving it
was not harmless: an original names the other originals, so one that survived
kept the rest alive and `check_layouts` refused a program for a body nothing
would have compiled.

The second candidate is written down because it is the floor the first sits
on, and it is still true that `MAX_SPECIALIZATIONS` guarantees a generic
fallback for some program. What makes the floor unnecessary is that layouts
are a *finite* lattice where types are not: sharing by layout terminates on
any program, so the copy the fallback would have needed always exists. See
entry 54 for the three further places a layout was being forgotten and the
guess in `held_at` that kept all of them quiet.

---

### 54. Four ways for a layout to be forgotten, and one guess that hid them all
**bug, fixed.** M26. `boot` compiled itself to a binary and then segfaulted in
`Turkey.Opt#specializeOn`, at `group.known[i]`, with `check_layouts` passing.
The fault was `turkey_unbox` handed the raw value `1`: a reader unboxing what
its writer had stored bare, which is entry 53's shape again in three new
places at once.

`known : Array Bool` is an array of `i1`, one byte to an element. It was
allocated at that width and pushed to at that width -- and read through
`%inst.Index (Array a)`, compiled with `a` unknown, at eight. What made the
three causes one bug is that each was survivable alone: `held_at` answered
`BOXED` wherever `layout_of` had no answer, so every one of them produced a
program that ran, right up until an element was not pointer-sized.

**An instance dictionary is not a lambda.** `layout.transparent` asks
`core.abstraction_parameters` what a binding's abstraction takes, and that
read the *spine*: lambdas, one inside the next. A dictionary is a `CRecord` of
lambdas under the class's type binder, so the spine reader found no parameters
in `instance Index (Array a)` and concluded it destructured nothing, while the
`get` inside it does `Prim.arrayGet` on an `Array a`. Its earlier argument --
that a lambda deeper in the body is a closure the body makes for itself --
does not survive a body that hands the closure out, and a dictionary is
nothing but that. So: every lambda in the value.

**A method's own `forall` went missing.** A method quantifies over its own
variables as well as its class's, and `lower.method_abstraction` states those
in a `CTyLam` *under* the dictionary lambda. `mono` supplies the evidence,
which consumes that lambda and leaves the abstraction outermost -- and the
copy it wrote had empty `binders` and a still-generic body. `mono.instantiate`
needs binders to match type arguments against and `layout.share` keys copies
on them, so `%default.Foldable.fold@Array` was invisible to both and reached
the generic `Index (Array a)` dictionary. The copy now states what it
abstracts over, and the use site restates it as the eta-expansion it is.

**A global was lowered under the initializer's layouts.** `layout.share` gives
a copy its `layouts`, and the copy of a *dictionary* is a record built in the
module initializer -- which lowered every global's value under its own empty
map. So `%inst.Index (Array a)@[i1]` existed, was correctly selected, and had
its methods lifted with nothing known.

**And a family that never reduced.** `layout_of` cannot answer
`Index.Value (Array Bool)`, which is the `Bool` the instance says it is and is
written as one. `_Rewriter.ty` reduces as it substitutes and says, correctly,
that with no substitution there is nothing to do -- which leaves the binding
nothing specialized carrying the types `lower` wrote, and a dictionary's
method is typed in its class's families there. One pass over the program
before the layouts are decided, rather than a rule that each of the places a
family is introduced has to remember.

The last of these is why the entry is one entry. `held_at` was total: the
uniform representation wherever `layout_of` had no answer, documented as the
right question for a value a body only *holds*. It is -- but the type that
says a body can only hold a value is a bare *variable*. `Array a` is not that,
and neither is `Index.Value (Array Bool)`, and neither is a field of a
dictionary compiled with its layouts mislaid. Answering `BOXED` for those is
not a convention two sides keep, it is one side guessing, and each of the four
bugs above is a case where the guess was wrong and nothing said so.

`held_at` now falls back only for a `TVar` and refuses anything else. Three of
the four would have been a compile error the day they were written, and the
whole suite passes with the guess gone -- so nothing was relying on it.

---

### 55. Every live pointer is rooted, in one flat array, for the whole function
**performance, open.** M26, found while measuring why a stack overflow in
`boot` looked like a depth problem.

`Turkey.Opt#expr` compiles to a frame carrying `[481 x ptr]` of GC roots --
nearly 4KB before anything else the frame holds. The collector is precise and
non-moving, and `turkey_root_enter` takes a frame of live pointers, so the
backend roots every pointer-shaped value that is live anywhere in the
function, in one array sized to the whole function's worst case, and stores to
it on every write.

Both halves of that are more than the collector asks for:

* **Every pointer, rather than the ones live across a collection.** A root is
  only needed if a collection can happen while the value is live, and a
  collection can only happen at an allocation. The IR does not say which
  instructions allocate, so the backend cannot ask, and roots everything.
* **One array for the function, rather than per-region.** Two values with
  disjoint live ranges need one slot between them; the frame is the sum
  instead of the maximum.

The cost is paid three times: the stores themselves, the frame size, and the
cache line that frame no longer fits in. It is also what turned a divergence
into a stack exhaustion -- with ordinary frames `boot` would have hung
visibly instead of dying at 481,650 of them, and the cause would have been the
first thing looked at rather than the last.

The fix is an effects bit per opcode -- does this instruction allocate, and
therefore collect -- which `backend_ir` has no room for today: an instruction
is an opcode *string* and a tuple of operands, and every rule about what an
opcode means lives in `llvmgen`. That is the same missing table that
instruction selection will want, so it is one piece of work and not two.

---

### 56. Core names are not unique, and an environment that only grows captures

**compiler, fixed.** M27 phase 1. `Turkey.SsaLower` kept one mutable `env` from
name to SSA value and never took anything out of it. That is right for a Core
where every binder is distinct and wrong for the Core this compiler actually
produces: monomorphization and inlining *copy bodies*, so two unrelated locals
called `i` in one function are ordinary. The second binding wrote over the
first, and every use after the first one's scope ended read the second's value
-- or, in the case that showed it, the reverse.

What makes this worth recording is how it surfaced. The lowering was silent.
The dump looked plausible. What said something was `Ssa.verify`, and what it
said was not "a name was captured" but:

```
Data.Array#push@Int: value 37 is used outside the blocks its definition dominates
```

-- because a captured name is a *dominance* violation once the two bindings are
in different blocks. The verifier had no idea about scopes and caught a scoping
bug anyway, which is the argument for running it after every pass rather than
at the end: it is checking a property that a whole class of unrelated mistakes
violates.

The fix is to save the affected names and put them back (`SsaLower.saving` /
`restore`), rather than copy the map per scope the way `backend_lower.py` does
with `dict(env)`. A scope binds a handful of names and the map holds every
local in the function.

### 57. A diverging call still has to answer a representation

**compiler, fixed.** M27 phase 1. `Data.Array#bounds` is declared `-> unit` and
one of its arms is a call to `outOfBounds`, which never returns. The lowering
produced `ret %11:ptr*` from a function whose result is `unit`, and the
verifier refused it.

Two different causes wearing one face, which is why it took two goes:

* a call typed `!` does not come back, so what follows it is *unreachable*
  rather than merely dead. Emitting `Unreachable` and moving on is both more
  honest and what keeps a `ret` from being built out of a value that never
  arrives;
* and separately, `outOfBounds` is typed `fun(...) -> a`, so the call's result
  type is a *variable* instantiated at the arm's expected type. It is held at
  the uniform representation and the context wants `unit`. That is not
  divergence at all -- it is an ordinary representation boundary, and it needed
  `coerce`.

The first fix hid the second: with `Unreachable` in place the complaint moved
rather than disappeared, and only the second made it go. Worth remembering that
"the verifier still says the same thing about a different function" is evidence
of a *second* cause and not of a bad fix.

### 58. The reason a lowering stopped is worth more than the count

**practice.** M27 phase 1. `boot ssa` reported `lowered 9 of 50 bindings` and
the plan for the next slice, made by reading Core, was `CMatch` and `CLam` --
closure conversion, the expensive one. Then the dump was changed to print
*what* stopped each binding, and the answer was:

```
--   15  not a function
--   10  an unbound name 'Prim.intLt'
--    4  an unbound name 'Prim.intEq'
```

Not a lambda in sight. A primitive reaches Core as a `CVar` as often as a
`CPrim`, because the module scope binds every `Prim.` name to itself, and the
lowering only knew the `CPrim` spelling. Six lines fixed it and coverage went
from 9 of 50 to 35 of 50; on the three sample programs every remaining skip is
a module-level global, which is a separate phase.

The finding is not about primitives. It is that an incomplete pass should
report the *shape* of what it cannot do and not merely how much, because the
histogram is the plan for the next slice and reading the input to guess is
worse than measuring. Same lesson as the measurements in `CORE-OPT.md`,
reached from the other direction.


### 59. The same capture bug, in the other map

**compiler, fixed.** M27 phase 1. FINDINGS 56 was `env` growing without ever
being scoped, so two inlined copies of a body shared one name. The fix scoped
`env` and left `joins` exactly as it was -- and `joins` has the same property
for the same reason: a function inlined into itself has two joins called
`loop`, and `Map.put(l.joins, name, target)` overwrote the outer one.

What that produces is not a wrong value but a wrong *edge* -- the outer copy's
jump lands in the inner copy's block -- and it surfaced identically:

```
Main#depth@Pair(Int): value 24 is used outside the blocks its definition dominates
```

Eleven of those, all in `polyrec.tl`, which is the program that inlines a
function into itself thirteen deep. The lesson is not "scope your maps". It is
that **fixing one instance of a bug is not fixing the bug**: `env` and `joins`
are the two scoped things in that record, the reasoning that condemned one
condemned the other verbatim, and I fixed one and moved on. A finding is worth
re-reading against the rest of the file that produced it.

The verifier caught it twice, which is the argument for a check that knows
nothing about the mistake it is catching.

### 60. Two kinds of thing have named fields, and only one is declared

**compiler, fixed.** M27 phase 1. `fieldIndex` asked `Decls.recordFields`,
which answers for a mutable record -- the surface language's record. A
*dictionary* is also a thing with named fields, and it is not one: its type and
its constructor are synthesized when evidence becomes a value, so nothing
registers them and `decls.tycons` has never heard of `%Dict.Std.Classes#Length`.

Every method call in the language is a field read off a dictionary, so this was
19 stopped bindings across the corpus and the single largest remaining gap. It
was invisible for two rounds because the message said "a field of a type with
no known layout", which named neither the field nor the type; changing it to
print both answered it immediately:

```
--   1  the field 'len' of (a c%Dict.Std.Classes#Length@-1 (a cData.Array#Array@-1 v116501))
```

The fix is the Python backend's: collect record shapes by *walking the program*
for `CRecord` nodes, not by asking the declaration table. Worth having the
diagnostic say what it saw before guessing at what it meant -- two rounds of
three-minute compiles bought nothing that one better message did.

The second half is that the same table now decides both how a dictionary is
*built* and how it is *read*. They were two lookups agreeing by convention,
which is how a field ends up written at one offset and read at another.

### 61. A 2:42 startup, paid thirty times

**tooling, mine.** M27 phase 1. To check the corpus I wrote a shell loop
running `boot ssa` once per program, and estimated an hour. Measured:

* one program -- **2:42**
* all twenty-eight in one invocation -- **2:56**

So ~2:40 is fixed and the actual work is about half a second per program. The
fixed part is `boot` itself: running it means the Python implementation
typechecks and then *interprets* the whole bootstrap compiler before it looks
at the target at all.

`boot/Main.tl` already takes any number of files, and its header already said
why -- "not a convenience for the test -- it is what keeps the milestone's diff
to one process, since starting this program currently means compiling it". The
design note was there, in the file I was invoking, and I wrote the loop anyway.
`tests/test_ssa_lower.py` had the same shape and now makes one invocation and
splits the dump.

The general form: when a tool documents how it wants to be called, the cost of
ignoring it is not proportional to the mistake.


### 65. The same startup cost, ignored a second time -- by the finding's author

**tooling, mine.** M27 phase 2. FINDINGS 61 measured that starting `boot`
costs 2:42 and compiling a program costs half a second, and concluded that a
tool documenting how it wants to be called should be called that way. Then I
added `boot llvm`, which emits one module, and wrote a corpus harness running
one process per program: 28 x 2:50 of work, twenty minutes wall even at eight
in parallel.

The user asked why it was twenty minutes. It did not have to be. Two modules
cannot share a `.ll` *file* -- the symbols collide -- but they can share a
*run*, and `boot llvm` now prints `; === <path>` before each module so a
caller splits them afterwards. That is the same trick every other dump in
`Main.tl` already uses, described in its header, which is where FINDINGS 61
found it the first time.

**3:34** for the whole corpus, against twenty minutes. The lesson is not the
one already written down; it is that writing a finding down does not install
it. The next command I add will have the same shape, and the check is whether
a *batch* of work can be one process -- asked when the command is designed,
not when the harness is slow.

### 62. A signed one-bit integer, and what `False < True` compiles to

**compiler, fixed.** M27 phase 2. The LLVM emitter chose `icmp slt` for every
integer comparison, because Turkey's `Int` is signed and that seemed to be the
rule. It is not: `Int` is the *only* signed type the language has. `Bool` is
`i1`, `Byte` is `i8`, `Char` is `i32`, and all three are ordered as magnitudes.

A signed one-bit integer holds 0 and -1, so `False < True` became
`icmp slt i1 0, -1` and answered **false**. One line of `operators.tl`'s output
was wrong and the other twenty-five programs were right, which is exactly the
shape of bug that a spot check misses and a corpus catches.

The rule is now stated by width -- `i64` is signed, everything else is not --
rather than by listing which primitives are unsigned, which is how
`turkey/llvmgen.py` says the same thing. Worth noticing that the two
implementations reached it from opposite ends and only the one that reasoned
from the *type* got it right first: the one reasoning from the primitive names
had to enumerate `byteLt`, `charLt`, `boolLt` and remember not to add `intLt`.

### 63. An unimplemented case that lies about its type

**compiler, fixed.** M27 phase 2. A primitive with no emission rule produced
`add i8 0, 0` and a comment saying so. That is wrong twice over. `cc` rejected
the module at whatever unrelated instruction first consumed the value --
`br i1 %v1` where `%v1` was `i8`, several hundred lines from the cause -- and
where the types *happened* to line up it would have compiled a wrong program
instead.

The placeholder now has the type the value's `Rep` promised, and the emitter
collects the missing names and refuses the module:

```
boot: cannot emit LLVM: no rule for Prim.args, Prim.readFileBytes
```

Which is the same discipline the lowering already had -- report what is not
handled, do not improvise -- applied one layer down. The general form: a
placeholder that is *well typed* is more dangerous than one that is not,
because the compiler stops catching it.

### 64. Compiling a program is not running it, and `.expected` knows the difference

**testing.** M27 phase 2. The first corpus check diffed each native binary's
output against `tests/programs/*.expected` and reported `exhaustive.tl` as a
failure. It was not: that file contains three *compile-time warnings*, which
`turkey run` prints because it compiles and runs in one process, and which a
compiled binary cannot print because its compile time was hours ago.

The oracle for this phase is differential *execution*, which
`NATIVE-BACKEND.md` says plainly -- so the check now runs
`python3 -m turkey run` on the same program and diffs against that. It is also
the stronger comparison: `.expected` is a file someone can update, and the
reference implementation is not.


### 69. Four kinds of root, and the collector needs all four

**compiler, fixed.** M27 phase 2. `TURKEY_GC_STRESS=1` collects at every
allocation, and under it the native corpus went 0 of 28. Emitting stack root
frames -- liveness at each safepoint, a slot per value live across one, a live
mask per program point -- moved it to **6**. Three more kinds of root existed,
and two of them are not on a stack at all:

* **The pointer globals.** Every instance dictionary is a global, computed once
  by the module initializer, and nothing refers to it from a frame afterwards.
  They now live *in* a permanent root array, so storing to a global and rooting
  it are the same store -- which is also why there is nothing to keep in step.
  0 to 6.
* **The interned string literals.** Each is a `TurkeyString` the runtime
  allocated, cached in a module global so that `ConstString` is a load rather
  than an allocation. The cache was invisible to the collector, so the first
  collection freed every literal in the program. 6 to 11.

Two more, and then it was done:

* **A safepoint's *arguments*.** The set rooted was what is live *after* the
  call, and an argument whose last use is that very call is dead afterwards --
  while the callee is still holding it and the collector may run. So
  `Int.toString`'s result was freed while `turkey_string_concat` read it, and
  "circle of 5" printed as "circle of c". 11 to 22. The rule is stated exactly
  in `turkey/llvmgen._safepoint_live`'s docstring, which is where it should
  have been read from rather than reasoned out.
* **The slots past 64.** The live mask is 64 bits and the runtime scans every
  slot from 64 up unconditionally, so those have to start null rather than
  holding whatever the stack left. `Main#main` in `question.tl` read a `0x1`
  out of slot 64. The runtime's own comment prescribes the fix, one paragraph
  above the field. 22 to 28.

**0 of 28 → 6 → 11 → 22 → 27 → 28.**

The finding is the shape of the search. "GC roots" sounded like one feature and
is four, and only the first was on the list -- the other three were found by a
test that reports a *number*, one fix at a time. A gap that produces a count
can be walked down; a gap that produces "broken" cannot, and no amount of
staring at the emitter would have suggested "the arguments" or "slot 64".

Two of the four were already written down in the code being ported. The Python
backend's docstring says which values are live at a safepoint and the runtime's
struct comment says what happens past 64 slots -- both were read *after* the
failure, to confirm a diagnosis, when reading them first would have prevented
it. That is the same lesson as FINDINGS 61 and 65 and it is now three for
three: this project's expensive mistakes are consistently things somebody had
already written down.

### 66. Wrapping and checked arithmetic were the same opcode

**compiler, fixed.** M27 phase 2. `Prim.intAdd` panics on overflow and
`Prim.intAddWrapping` does not -- that is the whole reason the second exists --
and both lowered to `Bin(Add, x, y)`. The IR comment even said so, cheerfully:
"the wrapping forms are the same machine instruction; what differs is the
overflow check the checked ones carry, and that is `Effects.traps`". It is not,
because `Effects` is derived from the constructor and the constructor was the
same.

Invisible until the emitter started *emitting* the check. Then `Data.Map`'s
hashing, which subtracts with wrapping on purpose, panicked with
`integer overflow in -` and two programs stopped halfway.

`BinOp` now has `AddWrap`, `SubWrap` and `MulWrap`. Which is the version this
project's design already argued for: a constructor where a pass must match, so
that a pass which has not been taught the difference fails to compile -- and
the printer did exactly that, one warning, before anything ran. A flag on `Bin`
would have been something to forget to read.

It also makes `effectsOf` true rather than approximately true, which is what an
optimizer will read: a wrapping add is pure and deletable when unused; a
checked one is not.

### 67. Three primitives answer storage, not an array

**compiler, fixed.** M27 phase 2. `Prim.args`, `Prim.readFileBytes` and
`Prim.stringToBytes` are typed `Array a`, and the runtime hands back only the
flat contents -- an `Array` is a record carrying that storage and a length, and
its tag belongs to the compilation rather than to the runtime. The Python
backend wraps them; this one did not.

Two of the three refused to compile and said so, which is how they were found.
The third, `Prim.stringToBytes`, I had mapped straight through to
`turkey_string_to_byte_storage` -- so it *compiled*, and returned raw storage
where an `Array Byte` was expected. No corpus program calls `String.toBytes`,
so nothing caught it; it went in with 27 of 28 passing and would have stayed
until something used it.

Worth stating plainly: the two that failed loudly cost twenty minutes and the
one that failed quietly was a miscompile shipped in a green commit. The
asymmetry is the whole argument for the refusal in FINDINGS 63.

### 68. Nothing survives a collection, and nothing had noticed

**compiler, open.** M27 phase 2. The native backend emits no GC root frames.
Every corpus program passes anyway, because collection triggers at 1024
allocations and none of them reaches it -- so the entire root-tracking
obligation was untested and, until it was measured, unquantified.

`TURKEY_GC_STRESS=1` collects at every allocation. Under it:

```
=== 0 survive GC stress, 28 do not
```

Twenty-seven `panic: invalid object field` and one bus error. Not "mostly
works" -- nothing works, which is the honest state of the feature and a much
better place to start from than a suspicion.

The test is written and marked `xfail(strict=True)`, so the day roots are
emitted these stop being expected failures and the marker has to come off.
That is the shape worth reusing: a gap that has a *test* is a gap with a
finish line, and a gap that has only a note in a design document is a gap
that gets rediscovered.


### 70. The low IR does not contain the language's semantics; the emitter does

**compiler, open.** M28 phase 4. Writing the arm64 instruction type was the
first thing to consume the low IR from somewhere other than `Turkey.Llvm`, and
that immediately showed what is *not* in it.

Overflow checks, division-by-zero checks, panic propagation after every call,
and the whole GC root apparatus -- liveness at safepoints, slot assignment, the
live mask, `turkey_root_enter`/`leave` -- are all in the **LLVM emitter**, about
300 lines of `Llvm.tl`'s 1,388. None of it is in the IR. `boot ssa` prints a
`Bin(Add, x, y)` that does not overflow-check and a `Call` that does not
propagate a panic, and those are not the semantics of the language.

Which means an arm64 backend would write all of it a second time, in a second
language, and the two would have to agree about which values are live at a
safepoint. This project has spent this session finding out what happens when
one rule lives in two places: FINDINGS 56 and 59 (the same capture bug, twice,
in two maps), 60 (a dictionary built at one field order and read at another),
66 (wrapping and checked arithmetic sharing an opcode). A GC root set computed
twice would be the same shape of mistake with the worst possible failure --
silent, rare, and only under collection.

The fix looked like a low-IR **expansion pass** covering all of it -- rewrite
checked arithmetic into a compare and a branch, insert the panic-flag test
after each call, materialize the root frame -- and one question killed a third
of that. Expanding the arithmetic would give `Bin(Add, ...)` two meanings, one
per phase, with nothing enforcing which; and the overflow *test* is exactly
what each target does differently, LLVM with `llvm.sadd.with.overflow` and
arm64 with `adds` and a flag the IR cannot name. A neutral expansion would
pessimize both.

So the line is not early-or-late, it is **neutral or not**. Panic propagation
and root frames are the same instructions on every target and are hoisted;
overflow and division checks stay in each emitter, about eighty lines apiece,
because each writes a different and better sequence. **Two implementations of
one rule is the hazard; two encodings of one check is the job**, and the
difference between those had to be asked for rather than noticed.

Two things make this fit better than it had any right to. `SlotLoad` and
`SlotStore` already exist in the low IR, unemitted, with a comment saying a
genuine stack slot appears only when something genuinely needs one -- and a
root array is exactly that, so the pass needs no new opcode. And the pass's
output is checked by machinery that already exists: `Ssa.verify` runs on it,
`LowIr.checkCalls` runs on it, and differential execution still runs the
program. The version living inside the emitter is checked by none of those,
because it never exists as IR.

The finding is not "extract a pass". It is that **a backend with one consumer
cannot tell which of its facts are in its IR and which are in its emitter**,
and adding the second consumer is what asks the question. The low IR looked
finished after M27 phase 2 and 28 corpus programs agreed with the reference;
it was finished as *input to LLVM*, which is a weaker claim than it appeared.

### 71. The optimizer captured a name, and only a type disagreement said so
**compiler bug, capture, shared algorithm.** M28. Extracting the runtime's
constants into `Turkey.Runtime` made `boot` stop typechecking, at a line eighty
lines from anything the change touched:

    Llvm.tl:1127:22: internal error: the variable 'e' should be Emit
                     but is Turkey.Runtime.Entry

Line 1127 is inside `emitRuntimeCall`, whose parameter `e` is an `Emit`. The
new code was in `runtimeOf`, a different top-level function, whose match arm
bound `Some(e)` at the new `Entry`. Renaming that binder to `entry` made the
error go away, which is the shape of a compiler bug rather than a mistake in
the edit -- so the rename was reverted and the error chased instead.

It reduces to eighteen lines:

    type Emit  = Emit  { count : Int }
    type Entry = Entry { symbol : String }

    fun other(x : Option Entry) -> Option String = match x {
        Some(e) -> Some(e.symbol)
        None -> None
    }

    fun uses(e : Emit, x : Option Entry) -> Int = match other(x) {
        Some(_) -> e.count
        None -> 0
    }

`coretc` runs at four points and it was the *third* that failed -- after the
optimizations, not after the lowering. `opt` inlines `other` into `uses`, which
makes a match of a match, and `case_of_case` pushes the outer alternatives into
the inner branches. The outer alternative mentions a free `e`, the inner branch
binds one, and after the push they are the same `e`. Case-of-known-constructor
then collapses `match Some(e.symbol) { Some(_) -> e.count }` to `e.count`,
reading `symbol` where `count` was meant.

Three things are worth keeping from it.

**The guard existed everywhere else.** `opt.py`'s header states the discipline
in its own words -- "substitution is capture-avoiding by refusing rather than by
renaming" -- and `trivial_let`, `beta`, `let_to_match` and
`fuse_recursive_join_result` all implement it, the last with a comment
explaining precisely this hazard for precisely this reason. `case_of_case` is
the one rule that moves a term under a *pattern* binder rather than under a
`let` or a join parameter, and it is the one rule that did not check. A stated
invariant is not an enforced one, which is the same shape as FINDINGS 65: a
rule written down in the place it applies is still applied by hand at each
site.

**The types are what caught it, and they caught it by accident.** The two `e`s
had to differ for `coretc` to complain. Two same-typed `e`s -- far more likely,
since a name collides most often with itself -- would have produced a
well-typed program that computes the wrong value, and the corpus would have
stayed green. `coretc`'s existence is why this was a compile error rather than
a bug report from a user of the compiler; its per-stage repetition
(`driver.py` runs it four times, deliberately) is why the *stage* was
identifiable in one experiment rather than by bisection.

**Third capture bug, and the first in a shared algorithm.** FINDINGS 56 and 59
were both `SsaLower` failing to scope an environment, and both were mine. This
one is older than either, lives in `turkey/opt.py` and `boot/Turkey/Opt.tl`
both, and had to be fixed twice -- the tax CLAUDE.md's "two implementations"
section describes, paid in full. The corpus never produced the shape, so
`test_boot` was never going to find it; what found it was writing a module
whose record fields happened to collide with an emitter's.

The fix declines rather than renames, in both implementations: if the outer
alternatives' free names meet the inner alternatives' pattern binders, the
rewrite does not fire. A `CIf` scrutinee binds nothing and is unaffected.


### 72. A pattern cannot name a qualified constructor
**language, papercut.** M28 phase 5. `import Turkey.Arm64 as A` brings `A.Gp`
into scope as an expression and *not* as a pattern: `match bankOf(r) { A.Gp ->
... }` is `parse error: expected '->' after the arm's patterns, found '.'`. The
only way to match on a constructor is to import it unqualified, so a module
that matches on another module's type must import that type twice --
`import Turkey.Arm64 as A` for the functions and
`import Turkey.Arm64 (Bank(..))` for the patterns.

The cost is not the second import line, it is that **the qualification the
first import bought is then gone**. `Turkey.Select` already carries a paragraph
explaining which of its two neighbours got the unqualified names and why, and
the answer -- the side being matched wins -- is a rule about parsing leaking
into how modules are imported. `Add`, `Eq` and `Ret` each name a constructor in
two of these modules, so the choice is forced rather than stylistic.

It bit twice, which is what makes it a finding rather than a preference: once
writing `Turkey.Select`, where it produced that paragraph, and again writing
`Turkey.Regalloc`, whose three-line `countOf` needed the same second import for
one two-armed `match`.

Nothing about the type system requires this. A qualified name in a pattern is
unambiguous by construction -- more so than an unqualified one, which is why
the workaround is strictly worse than the thing it works around. It is a
grammar that stops at `.`, and the fix is to let a constructor pattern take a
dotted path. Recorded rather than fixed because the parser is shared with the
Python implementation and changing it is two implementations plus goldens.


### 73. The build cache was keyed on the session, and the build is a function of the files
**tooling.** M28 phase 5, and the fourth turn of FINDINGS 61 and 65. `boot`
takes three minutes to compile and `tests/bootc.py` already built it once and
shared it -- across a *session*. Three one-off scripts in a row, none of which
changed a line of `boot`, each paid the full three minutes to ask a question
the compiled binary answers in ten seconds.

The previous three findings were all "someone pays a startup cost per unit of
work". This one is a different mistake with the same symptom: the cache had the
**wrong key**. `boot`'s output is a pure function of `boot/`, `lib/`, `turkey/`
and `runtime/`, and a session is not any of those. Keyed on a hash of those
files instead, the build is shared between test runs, scratch scripts,
concurrent jobs and future sessions, and it invalidates exactly when it should.
Concurrent builders are safe *because* the key is a content hash -- two of them
are producing the same bytes -- so the only care needed is that the file never
be observed half-written, which is one `os.replace`.

    binary() on an unchanged tree:  ~180s  ->  0.02s

The general form, worth having stated: **when the thing being cached is
deterministic in its inputs, any key that is not those inputs is both too
coarse and too fine at once** -- it rebuilds when nothing changed, and it
would happily serve a stale artifact if the lifetime were longer than the
session. Hashing the inputs is the only key that is neither.


### 74. A metric that reads exactly zero is a bug, not a result
**process.** M28 phase 5. The colourer hints each function parameter towards
its argument register, and the hit rate was added purely as a code-quality
number -- every miss is one `mov` the emitter keeps. It read **0 of 3714**.

Zero is not a bad hint rate. A hint that never lands is not a preference being
outvoted, it is a preference not being consulted, and the cause was worse than
the metric: `Ssa.Func.params` and `Ssa.Block.params` are different fields, the
walk coloured only the second, and **every function parameter was therefore
assigned no register at all**.

Nothing had noticed, and the reason is the part worth keeping. `verifyColouring`
checked two things -- two live values sharing a register, and a value holding a
register clobbered while live -- and an *unassigned* value took part in
neither, because both loops skipped it. A checker that says nothing about the
absence of an answer will happily bless a function where nothing was answered.
It now complains about a live value with no register, which is the check that
would have caught this on the first run.

Two lessons, and the second is the general one:

* **A metric added for quality found a correctness bug**, which is an argument
  for adding them earlier than they seem worth it. This one cost four lines.
* **Round numbers deserve suspicion in proportion to how round they are.** 38%
  and 69% invite interpretation; 0.0% of 3714 is a sentence about the
  measurement rather than about the thing measured. The instinct to explain a
  zero is the instinct to be resisted.

A third, smaller: the same run reported 4,471 verifier complaints that were all
about *unreachable* blocks, and 414,664 more about functions whose colouring
had already stopped. A checker has to agree with the pass it checks about which
code exists -- the colourer walks reachable blocks and does not finish stopped
functions, so the checker must do both too, or its output is noise that hides
the four real complaints it might one day have.


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
