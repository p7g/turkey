# The standard library: what it needs, and how it is laid out

Status: **draft for iteration.** Measured and prototyped at `d2b7a71`, and
re-checked at `prim-types-rebased` (`5f09d4b`), where source files are `.gob`;
facts that changed between the two say so.
Section 6 is the current recommendation; section 7 lists what is still open.
Read those first for the proposed API. Section 4 records the survey and
experiments, including superseded designs explicitly marked as historical.

Two questions, which turn out to constrain each other:

1. What does `lib/` need before Turkey is a reasonable language for Advent of
   Code 2026 (starting 2026-12-01)?
2. How should the library be named and divided -- the proposal on the table is
   `Collections.*`, `Algorithm.*`, `Text`, `Time`, `IO.*`, `System.*`, and
   whether `Control` survives.

The first is answered by measurement (section 2), the second by a survey
(section 4), and the answer to the first is the test for the second: a layout
is good if the modules AoC needs land somewhere a reader would look.

---

## 1. What exists today

2,192 lines in 21 files:

| Module | What it has |
|---|---|
| `Std.Classes` | `Eq` `Ord` `Add`..`Neg` `Show` `Hash` `Hasher` `Functor` `Applicative` `Monad` `Iterator` `Index` `Length` `Semigroup` `Monoid` `Foldable` |
| `Data.{Bool,Byte,Char,Int,Float,Ordering,Option,Either,Tuple}` | the primitive and sum types, their instances, conversions |
| `Data.Array` | growable array; `map` `filter` `fold` `reverse` `append` `slice` `indexOf` `sort` `sortBy` |
| `Data.Map`, `Data.Set` | insertion-ordered open-addressed hash map; set over `Map k Unit` |
| `Data.String` | UTF-8 views, `split` `splitOnce` `lines` `words` `trim` `find` `replace`, `Builder` |
| `Algorithm.Hash` | FNV-1a |
| `System.IO` | `readFile` `readBytes` `writeFile` `stderr` `print` `write` |
| `System.Env` | `args` `exit` `get` |
| `Unsafe.Ptr`, `Unsafe.Libc` | raw memory, and the C symbols declared through it (SPEC-DELTAS 70, 71). Not part of the surface this document is about: they exist so that `System.*` has something to be a wrapper over, and a module that imports one says so in its import list |

Prefix use across `boot/`, `lib/` and `tests/programs/`: 112 imports of
`Data.*`, 17 of `Std.*`, 8 of `System.*`, 2 of `Algorithm.*`, in 49 files; 42
mentions in `turkey/*.py`. A rename is a sed and a golden regeneration, not a
design problem. **But** the Prelude already does `import Data.Map as Map` for
all eleven of its re-exports, so every program already spells the library
without the prefix. Whatever the prefix is, programs do not pay for it (6.1).

---

## 2. What Advent of Code needs: measured

Source: [p7g/advent-of-code](https://github.com/p7g/advent-of-code), all seven
year branches, 273 solution files (helpers and tests excluded). 2019 and 2020
split days into per-part files and used numpy and a local `lib/`; 2021-2025
(105 files) is one file per day with a growing `aoc.py` prelude, and is the
better predictor of 2026. Percentages are *files that use the idiom at least
once*.

| Idiom | all | 2021-25 range | Turkey today |
|---|---|---|---|
| `print` | 69% | 100% | yes |
| `.splitlines()` | 39% | 79-96% | `String.lines` |
| comprehension | 38% | 60-84% | `Array.map`/`filter`, no lazy chains |
| `.split(` | 34% | 56-71% | `String.split` |
| `range(` | 31% | 40-64% | **no** (C-style `for` only) |
| `int(` | 24% | 40-47% | `Int.parse` |
| `sum(` | 20% | 28-44% | **no** |
| `enumerate` | 15% | 20-33% | **no** |
| `set(` | 15% | 20-36% | `Data.Set` (not in Prelude, FINDINGS 24) |
| `dict` / `{}` | 10% | 10-38% | `Data.Map` |
| bit operations | 10% | 12-30% | `Int.and` etc. |
| `count()` (unbounded) | 8% | 8-29% | **no** |
| `sorted` / `.sort` | 8% | 8-30% | `Array.sort`, `sortBy` |
| `abs(` | 8% | 8-30% | `Int.abs` |
| `any` / `all` | 8% | 4-20% | **no** |
| grid point (`Pt`, `nbrs4/8`, `pts`) | 8% | 20-40% (2023-25) | **no** |
| `defaultdict` / `Counter` | 7% / 5% | up to 20% | `Map.getOr` / `Map.update` |
| `zip` | 6% | 8-30% | **no** |
| `product` / `pairwise` / `combinations` | 5% / 4% / 2% | up to 20% | **no** |
| networkx | 8% | 8-20% | **no** |
| `deque` / `heapq` | 4% / 1% | up to 12% | **no** |
| `@cache` / `lru_cache` | 1% | up to 8% | hand-rolled `Map` |
| `sqrt` / `ceil` / `floor` | 3% | up to 10% | `Float.floor`/`ceil`, **no `sqrt`** |
| `gcd` / `lcm` | 1% | up to 8% | **no** |
| `Fraction` | <1% | 2025/10 | **no** |
| `re.*` | 9 calls in 7 years (4 `fullmatch`, 3 `compile`) | | none (FINDINGS 33) |
| numpy | 3% | 2019 only | -- |
| z3 | 1 file | | -- |

Aggregates over 2021-2025:

* **Search is the dominant algorithmic need.** 15 of 105 files do BFS or
  Dijkstra (`heappop`, `popleft`, `deque(`, `shortest_path`, `dijkstra`).
  networkx calls across all years: `shortest_path*` 8, `has_path` 5,
  `descendants` 2, `connected_components` 3, `single_source_dijkstra` 2,
  `all_shortest_paths` / `find_cycle` / `is_connected` / `minimum_edge_cut` /
  `enumerate_all_cliques` 1 each. The rest (`Graph`, `DiGraph`, `grid_*`) is
  constructing a graph *in order to* call one of those.
* **Grids are the dominant data shape.** 31 of 105 files index a 2D grid or ask
  for neighbours; the `aoc.py` helpers are called `wh` 27 times, `inbound` 17,
  `sign` 14, `pts` 13, `nbrs4` 10, `nbrs8` 5.
* **Parsing is split-and-int, not regex.** 47 of 105 files call `int(`; the
  total regex use in seven years is nine calls.
* **Laziness matters in a few places that matter a lot.** `count()` with
  `break` (29% of 2021), `islice` over a sorted stream (2025/08), `peekable`.

And what the harness (`aoc.py`) needs, separately from solutions: read a cached
input file, `mkdir`, an environment variable (`AOC_DAY`, `AOC_YEAR`), today's
date in `America/New_York`, and an HTTPS GET with a cookie.

### 2.1 Two examples that should shape the API

**2024/16** builds a `DiGraph` with four nodes per cell and 1000-weight edges
between them, purely so it can call `nx.shortest_path_length`. The graph is
never used for anything else. With a successor function, there is no graph:
the state is `(Point, Direction)` and its successors are "step forward, cost 1"
and "turn, cost 1000".

**2023/17** does the same search by hand -- 60 lines, a heap of lists with a
`removed` flag and a side table to fake decrease-key -- because the state
`(point, direction, run length)` is awkward to materialise as a networkx graph.
Same algorithm, implicit graph, and the hand-rolled version is where the bugs
live.

Both argue that search must work over an *implicit* graph: whatever the
abstraction is, it cannot require materialising nodes and edges first (4.2).

---

## 3. Constraints Turkey brings

These are not in the survey; they are why a peer's answer may not transfer.

* **Library code is written once.** `lib/` is Turkey source, compiled by both
  `turkey/` and `boot/`, so a new module is one implementation, not two
  (contrast a Core pass, CLAUDE.md). The expensive part of the library is
  **primitives**: `stdin`, environment variables, a clock, `sqrt`, directory
  creation each need `Prim.*` in the Python backend, the C runtime, and the
  native backend (FINDINGS 51). design.md 8.4 already says `System.*` is small
  for this reason. Budget new primitives, not new modules.
* **No generator syntax, no exceptions.** A lazy adapter is a type plus an
  instance. A generator can still be written with `yield` inside a loop,
  because a generator can be a monad and `?` lifts loops (4.9).
* **A family application cannot appear in a record field type.** `inner :
  Iter (Array a)` is rejected with "unknown type 'Iterable.Iter'" (prototype,
  4.3.1), so a wrapper iterator names its inner iterator's concrete type or
  holds a closure.
* **No multi-parameter classes, and never fundeps** (design.md 8.2). A graph
  abstraction has to be `class Graph g { type Node g ... }` with associated
  families, not `Graph g n`. Checked on 2026-09-14 with a scratch program: a
  `[Graph g, Hash (Node g)]` context -- a class constraint on a family
  application -- is accepted, and BFS over it runs, both for a record instance
  and for `instance Graph (FnGraph n)` wrapping a closure.
* **FINDINGS 5**: an instance may not define a family as a family applied to a
  concrete type, so a wrapper leaks its inner cursor type. Every adapter or
  wrapper container pays this.
* **No package manager.** The search path is the entry file's directory, then
  `lib/` (`turkey/modules.py:96`). A library that is not in `lib/` is a library
  the user copies. Peers that keep their stdlib small *and* have a registry
  (Rust, Roc, Gleam, Swift) are not a direct precedent until Turkey has one.
* **Module names are global and the user's directory wins.** A program with a
  `Map.tl` beside it shadows the library's `Map` today if the library's module
  is called `Map`. A flat namespace makes this a real hazard (6.1).
* **`Int` is 64-bit and traps.** No solution in the corpus has a literal of 12
  or more digits, but literal sizes do not bound computed counts, products,
  LCMs or rational intermediates. Bignums are deferred, pending checks of
  results and intermediate bounds in representative solutions.

---

## 4. Survey

### 4.1 Namespacing: prefixes versus flat

| Peer | Layout | Notes |
|---|---|---|
| Haskell `base` | `Data.*`, `Control.*`, `System.*`, `Text.*` | From the 2002 hierarchical-modules addendum. Current guidance ([Haskell for all, 2021](https://www.haskellforall.com/2021/05/module-organization-guidelines-for.html)) is that a package's default module is named after the package (`foo-bar-baz` -> `Foo.Bar.Baz`), which rules out `Data.`/`Control.` for third-party packages (4.11); `prettyprinter` renamed `Data.Text.Prettyprint.Doc` to `Prettyprinter`. `Data.Graph` lives in `containers`; `Data.List.Split` is a separate package (`split`) -- the most famous gap in `base`. |
| Rust `std` | `std::collections::{HashMap, VecDeque, BinaryHeap, BTreeMap...}`, `std::io`, `std::fs`, `std::env`, `std::process`, `std::time`; primitives as `std::primitive`/inherent methods | Category modules for *containers only*; the integer and string APIs are methods on the type. No graph, regex, itertools or datetime-with-zones in std. |
| Go | flat by topic: `container/heap`, `container/list`, `slices`, `maps`, `sort`, `strings`, `unicode`, `time`, `os`, `io`, `bufio` | `container/` is a category, but everything else is flat. Go 1.21 added generic `slices`/`maps`/`cmp` as *algorithms over* built-in types rather than new types. |
| OCaml `Stdlib` | flat: `List`, `Array`, `Hashtbl`, `Map`, `Set`, `Seq`, `Queue`, `Stack`, `String`, `Buffer` | No priority queue in the stdlib; Jane Street's `Core` adds `Pairing_heap`, `Fheap`. |
| Elm core / Gleam stdlib / Roc builtins | flat: `List`, `Dict`, `Set`, `String`, `Char` (Elm); `gleam/list`, `gleam/dict`, `gleam/set`, `gleam/string` (Gleam, package-rooted); `List`, `Dict`, `Set`, `Str`, `Num` (Roc) | The ML-family languages closest to Turkey all name a module after its type, with no category. Gleam's single `gleam/` root is a package name, not a category. |
| Zig | `std.ArrayList`, `std.AutoHashMap`, `std.PriorityQueue`, `std.fs`, `std.time` | One root (`std`), flat below. |

**Reading.** Nobody who started after Haskell chose a `Data.`-style category
for types. The split is between *flat, named after the type* (OCaml, Elm, Roc,
Go) and *one root, flat below* (Rust `std::`, Zig `std.`, Gleam `gleam/`). The
single root exists to stop user modules and library modules colliding, which is
section 3's shadowing hazard. Category modules survive where they group things
that are genuinely alike and not types: Rust's `collections`, Go's `container/`.

**The counterexample worth taking seriously** is Haskell's own: `Data.Map` and
`Control.Monad` are twenty years old and nobody is confused by them. `Data` is
a real category -- declarations of and operations on data. What went wrong in
Haskell is the *boundary* with `Control`: `Data.Functor` and
`Control.Applicative` could each be in the other hierarchy. Section 6.2 removes
the boundary rather than the category.

### 4.2 Algorithms decoupled from data types

The proposal's `Algorithm.*` has a long precedent. The question is the
decoupling *mechanism*, and there are two, with a clear record on which one
wins for AoC-shaped problems.

**By concept / trait / class.**

* **C++ STL** (Stepanov): containers expose iterators, algorithms are written
  against iterator categories. The original case for the split.
* **Boost Graph Library**: `dijkstra_shortest_paths` over `IncidenceGraph` and
  property-map concepts. Maximally general, famously hard to call.
* **petgraph** (Rust): `dijkstra(graph: G, start, goal, edge_cost) where G:
  IntoEdges + Visitable`, with `NodeId` an associated type. Works on
  petgraph's own graph types, or a user type implementing five traits.

**By function argument.**

* **`pathfinding`** (Rust, ~51k downloads/month,
  [lib.rs](https://lib.rs/crates/pathfinding)): `dijkstra(start: &N,
  successors: FN, success: FS) -> Option<(Vec<N>, C)>`, where `successors`
  returns `IntoIterator<Item = (N, C)>`. No graph type at all; `N: Eq + Hash +
  Clone`. The de facto AoC crate in Rust, precisely because AoC graphs are
  implicit.
* **`search-algorithms`** (Haskell): `dijkstra :: (state -> f state) -> (state
  -> state -> cost) -> (state -> Bool) -> state -> Maybe (cost, [state])`.
  Same shape.
* **Python `heapq`**: functions over a plain `list`, not a heap type.
  `bisect` likewise. Decoupled by being about a protocol (sequence) rather than
  a class.
* **Go `container/heap`**: `heap.Push(h, x)` over any `heap.Interface`
  (`sort.Interface` + `Push`/`Pop`). A concept, but a small one, and the
  generic-era replacement proposals all move to a type with a `less`
  function.

**Reading.** The two are not exclusive, and the mature designs use both: BGL
and petgraph take the *structure* through a concept (successors, node identity)
and the *per-query* information through arguments (A*'s heuristic, the goal
test, a visitor). The function-only crates win for AoC because their graphs
are implicit, not because classes are wrong.

Two things make classes cheaper in Turkey than in Rust:

* A method receives the graph *value*, so an instance's record can carry the
  problem's parameters. 2023/17's part 1 and part 2 (run length 1-3 versus
  4-10) are two values of one `Crucible { min, max }` type, not two types.
* The closure form is one instance away: `type FnGraph n = FnGraph { step :
  fun(n) -> Array (n, Int) }` with `type Node = n`. The family is applied to a
  variable of the head, so FINDINGS 5 does not bite.

And specialization (design.md 5.5) turns a known instance's method into a
direct call, where a closure field stays an indirect one (FINDINGS 78).
This motivates a candidate:
**a class for what the graph is, arguments for what this query asks.**
The final choice remains subject to the program comparison in 7.9, including
a function-only baseline. Once an
algorithm needs more than one fact about the structure -- successors, cost,
node identity -- the class is what keeps those facts consistent across the
algorithms that use the same graph.

It does not mean *every* algorithm moves out of its type. Rust keeps
`slice::sort`, Go put `slices.Sort` beside `slices`, and Swift's
`swift-algorithms` is extensions *on* `Sequence`/`Collection`. The test that
fits all of them: **an algorithm lives in `Algorithm.*` if it is parametric
over a class or a function, and with its type if it needs the representation.**
`Array.sort` (in place, needs storage) stays; `dijkstra` (needs a successor
function) goes.

### 4.3 Iteration

The largest single gap in section 2 is not a container. It is `range`, `sum`,
`enumerate`, `zip`, `any`/`all`, `count`, `product`, `pairwise`,
`combinations` -- 38-84% of recent files.

* **Rust**: adapters are default methods on `Iterator`, so every iterator gets
  all of them; `itertools` adds the combinatorial ones.
* **Go 1.23**: `iter.Seq[V]` is a push function, and `slices`/`maps` gained
  `All`, `Values`, `Collect`. Chosen *because* pull iterators needed a type per
  adapter.
* **Gleam**: `gleam/iterator` was in the stdlib, then deprecated in v0.44 and
  moved to the separate `gleam_yielder` package
  ([changelog](https://github.com/gleam-lang/stdlib/blob/main/CHANGELOG.md));
  the stdlib now wants eager `list` functions to be the default.
* **Swift**: `swift-algorithms` (`combinations`, `permutations`, `chunked`,
  `windows`, `product`) was deliberately kept out of the standard library "to
  incubate ... for eventual inclusion"
  ([swift.org](https://www.swift.org/blog/swift-algorithms/)).

Gleam is the counterexample to adding lazy iterators, and its situation
differs in a way that matters: Gleam has no mutation and lists are its only
sequence, so eager list functions cover the space. Turkey has in-place arrays
and `for` already runs on `Iterator`, so the lazy protocol is already paid for
-- what is missing is only the functions over it.

Under the original protocol proposal, each lazy adapter needs a type and an
instance, as in Rust; 4.13 removes that cost through one concrete type. The
library should support the code people want to write, but representation
affects which optimizations are feasible. Adapter performance is an acceptance
criterion for the design, measured against direct loops (6.5.2).

#### 4.3.1 `Iterable` and `Iterator`

**Historical design:** the two-class protocol below is superseded by 4.13.
Its resumable-consumer experiments remain requirements for the current design.

The case to support: one stateful iterator consumed by several loops, for
example a recursive parser that hands the rest of its input to a recursive call.

* **Python**: iterables have `__iter__`; iterators have `__next__`, and the
  glossary says they are "required to have an `__iter__()` method that returns
  the iterator object itself". So every iterator can go in a `for`, and a
  `for` over a half-consumed iterator resumes it.
* **Rust**: `IntoIterator` and `Iterator`, joined by a blanket
  `impl<I: Iterator> IntoIterator for I`; `impl Iterator for &mut I` (and
  `by_ref()`) lets a loop borrow an iterator without consuming it.
* **Swift**: `Sequence.makeIterator()` and `IteratorProtocol.next()`.
* **Java** `Iterable`/`Iterator` and **C#** `IEnumerable`/`IEnumerator`: an
  iterator is *not* iterable, so a half-consumed one cannot go in a for-each.
  This is the counterexample, and the pain point it is known for is exactly the
  case above.
* **Turkey today**: one class. `iter(c)` makes a cursor and `next(c, cursor)`
  needs the container too, so resuming means passing both.

Turkey rejects overlapping instance heads, so Rust's blanket instance is not
available. Python's rule is: each iterator type writes a three-line `Iterable`
instance answering itself.

**Prototype** (2026-09-14, `python3 -m turkey run`), with placeholder class
names:

```
class Iterable c { type Iter c; fun iterate(c) -> Iter c }
class Stream i   { type Elem i; fun advance(i) -> Option (Elem i) }

instance Iterable (ArrayIt a) { type Iter = ArrayIt a; fun iterate(it) = it }
```

Everything below ran and printed the expected answers:

* `sum` over `[Iterable c, Stream (Iter c), Elem (Iter c) ~ Int]` for an
  `Array`, `Some(41)`, `None`, and a wrapper container.
* **One iterator, three consumers**: over `[3, 4, 0, 5, 6, 0, 7]`, "sum to the
  next zero" twice then "sum the rest" answered 7, 11, 7.
* **`Option` is iterable**, with an `OptionIt` record.
* **Nested iterables**: `rows[Iterable rs, Stream (Iter rs), Iterable (Elem
  (Iter rs)), Stream (Iter (Elem (Iter rs)))]` applied to
  `Array.map(String.lines("ab\ncd"), String.codePoints)` answered
  `[[a, b], [c, d]]`, which is what a grid constructor needs.
* `String.CodePoints` adapted to the new protocol, via a closure over the old
  cursor.

And one thing did not: a family application in a record field (section 3).

#### 4.3.2 Does the split need a blanket instance?

*Moot if 4.13 is adopted: with one concrete iterator type, an iterator is
iterable through one ordinary instance.*

The split has to deliver two things:

1. A concrete iterator can be used in a `for` as itself.
2. Generic code given `Iterator i` can `for` over `i`.

Rust gets both from one blanket impl, `impl<I: Iterator> IntoIterator for I`.

**In Turkey that is not a small change.** The docstring of `turkey/classes.py`
names the restrictions the class system rests on. One is that an instance head
is "a constructor applied to distinct type variables". That is why matching
"cannot fail to terminate", and why overlap "is a dictionary lookup rather than
a unification test". A blanket head is a bare variable, and breaks it:

* **Lookup** is keyed by the head's constructor. A variable head has no key, so
  every lookup of the class must also consider the blanket.
* **Overlap becomes a question about contexts.** `Iterable (Array a)` and the
  blanket overlap only if some `Iterator (Array a)` exists. That is negative
  reasoning. Rust gets it from orphan rules; Turkey's owner rule (an instance
  lives with its class or its head type) and whole-program instance table would
  make it a global check. Feasible.
* **Family reduction is the trap.** `reduce_fam` matches families against the
  same table. Inside a generic function given only `Iterable c`, the skolem `c`
  has no specific instance, so the blanket would match and reduce `Iter c` to
  `c` -- wrongly, since nothing says `c` is an iterator. Reduction would have to
  check the blanket's context against the givens and stay stuck when it cannot.
  Today "failing to match is *stuck*, never false" holds by structure alone.
* **Termination** no longer follows from structure. `Iterable i` reduces to
  `Iterator i`, which is the same size, and that is safe only because the
  classes differ.

**Peers' experience.** Rust's plain blanket impls are stable and everywhere, but
adding one to an existing trait breaks downstream crates (RFC 1023). The
general form, overlapping impls chosen by specificity, is `specialization`
(RFC 1210, 2015): still unstable, and its tracking issue calls the current
implementation unsound. GHC's `OverlappingInstances` is the older cautionary
tale. The principled version is Morris and Jones's *instance chains* (ICFP
2010), where an instance lists explicit `else` alternatives so the order is
declared rather than inferred.

**Two ways to get both properties while keeping Haskell 98 heads.**

* **A superclass with an equality, plus defaults.**
  `class Iterator i : Iterable i, Iter i ~ i`. The superclass gives property
  2: a function given `Iterator i` also has `Iterable i` and `Iter i ~ i`, and a
  given equality on a family is already a rewrite rule (SPEC-DELTAS.md 39).
  Property 1 costs one line per iterator type, `instance Iterable ArrayIt {}`,
  if `Iterable` may declare a default family equation (`type Iter c = c`) and a
  default method that is valid under it. Those are GHC's associated type
  defaults and `DefaultSignatures`. New work: superclass equalities, and default
  family equations.
* **Synthesize the instance.** When an `Iterator T` instance is declared and
  its module has no `Iterable T`, the compiler adds `instance Iterable T { type
  Iter = T; fun iterate(x) = x }`. With the same superclass, that is the blanket
  instance materialised per type, checked by the ordinary head-key overlap test.
  New work: superclass equalities, and a special case in instance collection,
  as `for` already is.

Neither needs overlap. Both still ask the author of an iterator for something,
though: an empty instance in the first, and in the second a special case that
only `Iterator` gets.

**A third way asks the author for nothing: a default superclass instance.** The
class declaration contains an instance of its own superclass, and the compiler
adds that instance whenever an instance of the class is declared.

* **Haskell proposed it and never adopted it.** Superclass defaults appeared on
  the Haskell wiki in 2007. Conor McBride proposed *default superclass
  instances* for GHC in 2011, and revised them as *intrinsic superclasses* in
  2014, with `Functor` as an intrinsic superclass of `Applicative`. What exists
  today are the Template Haskell package `intrinsic-superclasses`, the
  *instance templates* proposal, and Martínez, Jaskelioff and De Luca's *class
  morphisms* (Haskell 2018), which relate classes without editing them and come
  with a GHC prototype.
* **The known difficulty is the diamond.** If `Traversable` and `Monad` both
  default `Functor`, a type that is both has two candidate `Functor` instances.
  The 2007 proposal makes that an error.
* **Swift ships the Iterator case.** `extension Sequence where Self:
  IteratorProtocol` supplies `makeIterator()` returning `self`, although the
  type still names `Sequence` among its conformances.

In Turkey the diamond does not arise: `Iterator` is the only class that
defaults `Iterable`. And with the superclass equality `Iter i ~ i` there is only
one valid `Iterable` instance for an iterator anyway, so the rule can be as
simple as "always generated; a hand-written one is an error". The generated
instance has a constructor head, so lookup, overlap and termination keep their
current structure. This was the earlier recommendation; the concrete iterator
in 4.13 supersedes it.

### 4.4 Text and Unicode

* **Rust** had `str::graphemes()` before 1.0 and removed it to the
  `unicode-segmentation` crate; std keeps only code points.
* **Roc** keeps grapheme functions out of `Str` in the separate
  `roc-lang/unicode` package, because "grapheme-related rules can change with
  new Unicode releases" and a library can follow them without a language
  release.
* **Go** has `unicode` (categories, simple case) and `unicode/utf8` in std;
  segmentation is third-party (`rivo/uniseg`).
* **Swift** is the counterexample: `String` iterates grapheme clusters by
  default, and pays for it in `count` being O(n) and in bundled data. Its
  situation differs because Apple ships ICU on every platform it targets.

PRIMITIVES.md 4 already put Turkey on the Rust/Roc side: bytes and code points
in `String`, no indexing, case conversion deferred (4.5). A `Text` module
beyond `String` is the natural home for what needs Unicode *tables* --
segmentation, normalization, full case mapping -- and the survey says keep it
separate from `String` so the tables can move on their own. AoC needs none of
it: the inputs are ASCII.

What AoC does need that sounds like text is **parsing**, and it is split plus
`Int.parse` and ordinary string operations. A helper extracting every integer
is not supported by the measured corpus and is not proposed (section 5).

### 4.5 Time

* **Temporal** reached TC39 Stage 4 on 2026-03-11 and is in ECMAScript 2026;
  shipped in Firefox 139, Chrome 144 and Node 26
  ([Igalia](https://www.igalia.com/2026/03/13/Temporal-Reaches-Stage-4.html)).
  It is a stable thing to subset now, which it was not a year ago. Its types:
  `Instant`, `ZonedDateTime`, `PlainDate`, `PlainTime`, `PlainDateTime`,
  `PlainYearMonth`, `PlainMonthDay`, `Duration`, `Now`.
* **jiff** (Rust) is Temporal's design ported to a typed language, and the
  closest precedent. It needs the IANA database and embeds a copy where the
  platform has none (Windows) --
  [PLATFORM.md](https://github.com/BurntSushi/jiff/blob/master/PLATFORM.md).
* **Go `time`** is one `Time` type with a location; zones come from the system
  database or an embeddable `time/tzdata`.

**Scope.** With input fetched by a wrapper, AoC needs no `Time` at all, so
this section is not an AoC requirement; it is here because the name was
proposed. The language is not only for AoC, so the design should be full
Temporal including zones, and it wants its own document and survey before any
code. Two numbers for that document: on Unix, zones are a TZif reader over
`/usr/share/zoneinfo`, which is library code over `readFile` and no new
primitive -- Go's is `time/zoneinfo_read.go`, 599 lines, plus 710 in
`zoneinfo.go` including POSIX TZ rule strings; the embedded fallback for
platforms with no database is Go's `lib/time/zoneinfo.zip`, 408,467 bytes.

**Reading.** The expensive half of Temporal is `ZonedDateTime`: the tz
database, and DST-aware arithmetic. The cheap half -- `Instant`, `Duration`,
the `Plain*` calendar types, fixed offsets -- is pure arithmetic and one clock
primitive.

The only time-zone use in the corpus is `aoc.py` asking for today's date in
`America/New_York`. AoC unlocks at midnight UTC-5 and New York does not change
offset in December, so a fixed offset gives the same answer. AoC's need is
satisfied entirely by the cheap half.

### 4.6 IO and System

* **Haskell**: `System.IO` (handles, files, stdin), `System.Environment`
  (args, env), `System.Exit`.
* **Rust**: `std::io` (streams, stdin/stdout), `std::fs` (files), `std::env`
  (args, vars, current dir), `std::process` (exit, spawn).
* **Go**: `os` (files, env, args, exit) with `io` and `bufio` as stream
  abstractions.

Everyone separates *byte streams* from *the process's environment*, and nobody
agrees where files go. The one consistent line is Rust's and Haskell's:
**streams and files** in one place, **args, env, exit, clock, subprocess** in
another.

### 4.7 Points and grids

An earlier draft of this document said no peer's standard library has one.
That was wrong:

* **Go** `image.Point` has `Add`, `Sub`, `Mul`, `Div`, `Eq`, `In(Rectangle)`
  and `Mod(Rectangle)`; `image.Rectangle` has `Dx`, `Dy`, `Size`, `Intersect`,
  `Union`, `Overlaps`, `Inset` ([pkg.go.dev](https://pkg.go.dev/image#Point)).
  `p.In(r)` is `aoc.py`'s `inbound`.
* **Julia** Base: `CartesianIndex` supports `+`/`-`, and
  `CartesianIndices(A)` iterates every index of an N-dimensional array -- which
  is `aoc.py`'s `pts`.
* **Haskell 2010 Report**: `Data.Ix` (`range`, `inRange`, `index`, with tuple
  instances) and `Data.Array` indexed by any `Ix`. `Array (Int, Int) Char` is a
  grid, in the language report.
* **Racket** `math/array`: N-dimensional arrays indexed by index vectors,
  `array-ref` raising on out-of-bounds.
* **Java** `java.awt.Point`, **.NET** `System.Drawing.Point`: GUI-motivated,
  but there.

Two shapes: a **point type with arithmetic** (Go, Java, .NET) and an **index
class over multi-dimensional arrays** (Haskell, Julia, Racket). None ships
4- or 8-neighbours; that part is AoC's own. The index-class shape fits Turkey
directly: `Index` already has a `Key` family, so `Grid a` with `Key = Point`
makes `grid[p]` work with no new syntax.

### 4.8 How much goes in the standard library at all

Python is batteries-included, and AoC in Python is pleasant largely because of
`collections`, `itertools`, `heapq` and `functools` -- 2.1 is the measurement.
Rust, Roc, Gleam and Swift keep std small and push the rest to packages, and
that works because each has a registry. Swift's incubation packages are the
middle path: ship it, but separately, until its API has been used.

**Decision: batteries included.** Pushing modules out to packages moves
maintenance to other people, and Turkey has one maintainer, so it would move
nothing. Turkey also has no registry to push to. Python's model is the one that
fits.

### 4.9 Generators

A generator is a function that yields values lazily. Three ways peers get one:

**Compiled into a state machine.** Python, JavaScript, C#. Rust has reserved
`gen` for `gen` blocks.

**A library over a general language feature.**

* Kotlin's `sequence { yield(x) }` is a library builder over `suspend`
  functions, which the compiler turns into CPS.
* OCaml 5 builds generators from effect handlers, and the manual uses this as
  its example.
* Go 1.23's push iterator is just a callback, `func(yield func(V) bool)`.
  `iter.Pull` turns it into a pull iterator on runtime coroutines. Russ Cox
  measured 190 ns per switch with channels, 118 ns with the compiler fusing
  the channel operations, and 20 ns with a direct coroutine switch -- 40 ns per
  pulled value ([research.swtch.com/coro](https://research.swtch.com/coro)).

**A monad.** Haskell's `pipes`, `conduit` and `streaming` are all free-monad
shaped. Free has two known problems:

* **Left-nested bind is quadratic.** Voigtländer, *Asymptotic Improvement of
  Computations over Free Monads* (MPC 2008), fixes it with the codensity
  transformation: build the computation in continuation-passing style so bind
  always associates to the right. van der Ploeg and Kiselyov, *Reflection
  without Remorse* (Haskell 2014), fix it with type-aligned sequences when a
  program alternates building and observing. Kiselyov and Ishii, *Freer
  Monads, More Extensible Effects* (2015), is the same idea applied to
  effects.
* **Strictness.** A free-monad generator in a strict language has to delay its
  continuation, or an infinite generator never returns.

And **stream fusion** (Coutts, Leshchinskiy and Stewart, ICFP 2007) represents a
stream as a step function over hidden state, which is Turkey's cursor protocol.
It is the representation compilers optimize well. The two fit together:
write generators monadically, consume them as iterators.

**Why this works in Turkey.** design.md 6.9: a `?` inside `while` or `for`
lifts the loop into a recursive local function, and closures capture `var`
cells (design.md 11, row 36). So a generator reads like Python:

```
fun between(lo : Int, hi : Int) -> Gen Int Unit {
    var i = lo
    while i < hi {
        yield(i)?
        i = i + 1
    }
    pure(())
}
```

The prototype's representation is codensity over a lazy step:

```
type Step a = Done | Yield(a, fun() -> Step a)
type Gen a r = Gen(fun(fun(r) -> Step a) -> Step a)

fun yield(x : a) -> Gen a Unit = Gen(fun(k) = Yield(x, fun() = k(())))
instance Monad (Gen a) {
    fun bind(g, f) = Gen(fun(k) = runGen(g, fun(x) = runGen(f(x), k)))
}
```

Each `yield` hands a thunk back to whoever is pulling, so the stack unwinds at
every element and infinite generators work. Bind is right-associated however
the generator is written. `Gen a` is iterable through a record holding the
pending thunk.

Prototype results:

* `sum(between(1, 5))` answered 10.
* The third element of the infinite `naturals()` was `Some(2)`.
* A recursive in-order tree walk -- `yield from` twice per node -- produced
  1, 2, 3, then `None`.
* `sum(between(0, 1000000))` answered 499999500000, and a left-nested
  generator 10,000 deep answered 50005000, both in 0.95 s wall time for the
  whole `turkey run`, with no stack overflow.

Caveats:

* `Gen a` is a monad in its *return* value, like `Either l`. `map` on it maps
  the result; mapping the *yielded* values is an iterator adapter.
* Mutating a captured `var` is sound because a generator's continuation runs
  once. It would not be for `Array`'s `bind`.
* The syntax warts are in 7.10.

#### 4.9.1 Generators and the one iterator type

With 4.13, a generator is `Iterable`: `iterate(g)` answers an `Iterator a` whose
`pull` advances the pending step thunk, which is the prototype's cursor record
turned into a closure. `Gen a r` stays a separate type from `Iterator a`. They
are the two monads on the same data: `Iterator`'s `bind` is the list monad
(every combination), and `Gen`'s is sequencing emissions. `yield` cannot be
written in the list monad.

**`yield from` and `yield*`.** There are two cases, and both already work.

* **Delegating to another generator is `?`.** `let n = counted(xs)?` yields
  everything `counted` yields, then binds its return value `r`. That is the
  full meaning of PEP 380's `n = yield from sub()` and of JavaScript's
  `yield*`, which both evaluate to the sub-generator's return value.
* **Yielding from any `Iterable`** is a library function,
  `yieldFrom[Iterable c](c) -> Gen (Elem c) Unit`. It is written directly
  against the step type, so it pulls nothing until it is run. An iterator has
  no return value, so this one answers `Unit`.

The prototype's `outer()` -- `counted([7, 8, 9])?`, then `yield(n * 100)`, then
`yieldFrom([1, 2])`, then `yieldFrom(Some(5))` -- produced
`[7, 8, 9, 300, 1, 2, 5]`.

**No per-element traversal of the delegation chain in this benchmark.**
CPython resumes every
delegating frame for every value, so a chain of `yield from` costs time per
level per element. With the codensity representation, binds associate to the
right, and an inner `yield` returns straight to the consumer. Measured on
2026-09-14 with the same program in each language -- a generator delegating
`d` levels down to a range, summed:

| | elements | depth 0 | depth 500 |
|---|---|---|---|
| CPython 3.13.2 | 1,000,000 | 0.018 s | 2.639 s |
| Turkey, `turkey run` wall time | 200,000 | 0.92 s | 0.95 s |
| Turkey, `turkey run` wall time | 5,000,000 | 1.22-1.24 s | 1.32-1.33 s |

Turkey's times include compiling the program; the 200,000-element row is
roughly that fixed cost. Depth 500 adds about 0.1 s over five million elements,
where Python's depth 500 is 146 times its depth 0. These results do not
establish that arbitrary delegation is free: compilation and execution must
be measured separately, alongside allocation and memory use (6.5.2).

**When the body runs: a factory separates construction from execution.**
Written as a plain function returning `Gen`, a generator's body runs up to its
first `?` when the function is *called*. Its `var`s are allocated once per call,
while each run replays the continuation chain. The first prototype showed both
consequences:

* `noisy()` printed "body started" before the caller's "called".
* Summing one `between(1, 4)` twice gave `(6, 1)`.

Hiding `Gen`'s constructor alone does not help, because `yield`, `bind` and
`pure` build `Gen` values without it. What does help is making the monad and the
iterable **different types**, with neither constructor exported:

```
type Body a r        -- a Monad: yield, yieldFrom, delegate, done; not Iterable
type Gen a r         -- Iterable; not a Monad

fun generator(body : fun() -> Body a r) -> Gen a r
fun delegate(g : Gen a r) -> Body a r               -- r = yield from g
fun yieldFrom[Iterable c](c : c) -> Body (Elem c) Unit
```

The only way to get a `Gen` is `generator`, and its argument is a *function*,
so the body runs at the first pull and every run allocates fresh state. This
was tested as a separate module with an abstract export list (2026-09-14):

* **The correct program works.** It printed "called" before "body started",
  summed one generator twice as `(6, 6)`, and `outer()` --
  `delegate(counted([7, 8, 9]))?`, `yield(n * 100)?`, a `Body` bound directly,
  `yieldFrom(Some(5))` -- gave `[7, 8, 9, 300, 1, 2, 5]`.
* **Iterating a `Body` directly is rejected:** "no instance for 'Iterable (Body
  Int Unit)'".
* **Building a `Gen` by hand is rejected:** "unknown constructor 'Gen'".

Fresh body-local state does not guarantee independent runs. A `Body` built
*outside* a run and
captured -- `let b = between(1, 4); generator(fun() = b)` -- shares its state
across runs. Types without linearity cannot see that. The natural spelling,
`generator(fun() = between(1, 4))`, allocates fresh local state. Capturing an
external iterator or mutable cell can still couple runs; the factory does not
clone captured values or make effects repeatable.

Kotlin's `sequence { yield(x) }` is the precedent: the builder takes a block,
`yield` exists only inside the block's scope, and the block runs when the
sequence is iterated.

**Discarding.** The `let _ = yield(x)?` in earlier prototypes was defensive:
`yield(x)?` is `Unit`, so a bare statement is fine. The rule that makes this
matter has landed. At `d2b7a71` a discarded non-`Unit` value was accepted
(`Int.parse("3")` as a statement compiled and ran). Since `f794a62` ("A
discarded value is an error") it is rejected: "this expression's value has type
'Option Int' and is discarded; use it, or write 'let _ = ...'".

Generators are exactly the case the rule is for. `yield(x)` without its `?` is
a discarded `Body a Unit` -- once a silent no-op, now an error. Peers guard the
same typo:

* Haskell's `-Wunused-do-bind` warns when a `do` statement discards a non-`()`
  result; `_ <- e` or `void e` silences it.
* Rust marks `Result` and every iterator adapter `#[must_use]`, the second
  because a lazy value that is never consumed does nothing.
* Swift warns on every unused result (SE-0047) unless the function is marked
  `@discardableResult`.

A discarding spelling matters only where a bind answers something other than
`Unit`: `delegate(g)?` when the generator's return value is not wanted, and
`yield` if it ever answered a sent value. `let _ =` covers both; whether a
named discard (`void`, or a `yield_`-style suffix) is worth adding is 7.10.

**Not covered: `send`.** Python's `send()` and JavaScript's `next(v)` make a
generator a coroutine that receives values. The representation generalizes to
`Yield(a, fun(i) -> Step i a)`, which is the shape of Haskell's pipes and
coroutine libraries. Nothing in AoC asks for it.

### 4.10 Classes shared by several algorithms

* **petgraph** splits its traits (`petgraph::visit`: `IntoNeighbors`,
  `IntoEdges`, `Visitable`, `NodeIndexable`) from its algorithms
  (`petgraph::algo`) and its representations (`graph`, `graphmap`, `csr`).
* **Python** puts the abstract classes in `collections.abc` (`Iterable`,
  `Mapping`, `Sequence`), separately from the concrete types in `collections`.
* **C++20** has `<concepts>` and the iterator concepts in `<iterator>`, with the
  algorithms in `<algorithm>` and `<ranges>`.
* **BGL** documents its concepts separately, in `graph_concepts.hpp`.
* **Swift** puts protocols (`Sequence`, `Collection`, `Hashable`) at the top
  level of the stdlib.
* **Haskell** puts classes beside their topic (`Data.Foldable`); fgl's `Graph`
  class is `Data.Graph.Inductive.Graph`, with algorithms under
  `Data.Graph.Inductive.Query`.

Where classes serve many algorithms and many types, peers give them a place of
their own. But each of those libraries draws the line *within one domain*
(graphs, or collections), where every class is on one side of it. 6.2 is about
whether that line survives in a whole standard library.

### 4.11 Why Haskell moved away from `Data.` and `Control.`

The 2021 guidance is about packages, not about the words. A package's default
module should be named after the package, so that an import tells a reader
which package to install. `prettyprinter` moved from
`Data.Text.Prettyprint.Doc` to `Prettyprinter` for that reason. It is not an
argument that `Data` is meaningless, and it does not apply to a single standard
library.

**Both halves of GHC are `GHC.*`, and they coexist through packages.**

* Since GHC 9.0 every module of the compiler is in a hierarchy (`GHC.Tc.*`,
  `GHC.Hs`, `GHC.Core.*`): Sylvain Henry's renaming, written up in *Modularizing
  GHC* (2022).
* The libraries are `GHC.*` too: `base` has `GHC.Base` and `GHC.Generics`, and
  `ghc-prim` has `GHC.Classes` and `GHC.Types`.
* Since GHC 9.10 (base 4.20) the implementation of `base` lives in a
  `ghc-internal` package as `GHC.Internal.*`, and `base` re-exports it.

Haskell resolves module names per package. An ordinary program does not depend
on the `ghc` library, so its modules are not in scope; an ambiguity is reported
only between two *exposed* packages; and `PackageImports` (`import "base"
GHC.Base`) is the escape hatch. Inside the GHC tree the leaves simply differ.
Turkey has no packages, so it has only that last defence -- disjoint leaves
(6.3).

### 4.12 `Foldable`, `Functor` over iterators, and collecting

**Historical design:** the restartable `Seq` below is superseded by 4.13.
The collector experiments remain relevant; 6.5 defines the current API.

**Where `sum` lives in Haskell.** With `Foldable`. `sum :: (Foldable t, Num a)
=> t a -> a` has been a method of `Foldable` since GHC 7.10 (the
Foldable/Traversable in Prelude change). It is defined in `Data.Foldable` and
exported by the Prelude.

But `Foldable`'s variable has kind `* -> *`, so it cannot describe a container
whose element type is fixed: `Text` and `ByteString` in Haskell, `String`,
`Bytes` and `CodePoints` here. The `mono-traversable` package exists to fix
that, with `MonoFoldable` over an `Element` family. Turkey's `Iterable` -- one
parameter of kind `*`, with an element family -- is already `MonoFoldable`'s
shape.

In `lib/` today `Foldable` has one instance (`Array`) and no users outside the
`dicts` test. So "`sum` belongs with `Foldable`" and "`sum` belongs with
`Iterable`" are the same answer once `Iterable` is Turkey's `Foldable`, with
`foldMap` as a function over it.

**Why remove `Foldable`, rather than keep it beside `Iterable`?** Two classes
for "can be walked" would mean:

* two instances for every container;
* a choice for every consumer, where one written against `Foldable` excludes
  `String`, `Bytes` and `CodePoints`, and one written against `Iterable` covers
  everything `Foldable` covers (any `f a` can have `Elem (f a) = a`).

What only a `* -> *` class can say is "rebuild the same shape with new
elements". That is `Traversable`'s job, not `Foldable`'s, and Haskell's
`Traversable` can supply its folds itself (`foldMapDefault`). Haskell keeps
both only because `Foldable` was entrenched long before `mono-traversable`;
Turkey's has one instance. And in a strict language with `for`, the
operational class is the native one.

**Functor over iterators: two designs.**

* **One type per adapter.** Rust's `Map<I, F>`, Swift's
  `LazyMapSequence<Base, Element>`, C++ ranges. They are zero-cost through
  monomorphization, but `map` changes the type constructor (`ArrayIt a` becomes
  `MapIt (ArrayIt a) b`), so no adapter is a `Functor`. Rust has no `Functor`
  to miss.
* **One erased type.** Haskell's lazy list; stream fusion's `data Stream a =
  forall s. Stream (s -> Step s a) s`; Java's `Stream<T>`; Kotlin's
  `Sequence<T>`; C#'s `IEnumerable<T>`. `map` answers the same constructor, so
  the type is a `Functor`, and like the list a `Monad`. Turkey has no
  existential types, but a closure hides state just as well:
  `type Seq a = Seq { start : fun() -> fun() -> Option a }`.

Two properties decide whether the second design is lawful.
4.13 revisits the first of them, because a description cannot represent a
stream.

* **A description, not a cursor.** Java streams are single-use: operating on
  one twice throws `IllegalStateException`. C#'s `IEnumerable` can be
  enumerated again, and so can most Kotlin sequences. `Functor`'s laws only
  mean something when mapping does not consume shared state, so the `Functor`
  is the *restartable* type: `start` makes fresh state each time. That answers
  "every adapter would need to implement `Functor`": no adapter does. There is
  one `Seq`, and iterators stay the stateful thing underneath.
* **Laziness belongs to the type.** `map` on a `Seq` is lazy, and `map` on an
  `Array` stays eager, because each type has its own `Functor` instance. That
  is Haskell's split between lists and `Vector`.

**Comprehensions come free.** Because `Array` and `Seq` are monads, `?` is a
comprehension:

```
fun pairs() -> Array (Int, Int) {
    let x = xs?
    let y = ys?
    [(x, y)]
}
```

That is `[(x, y) for x in xs for y in ys]`, eager for `Array` and lazy for
`Seq`. Comprehensions are in 60-84% of recent solution files (section 2).

**`traverse` over a lazy sequence is a collector.** Deciding `Some` or `None` for
the whole sequence means consuming all of it. Rust expresses exactly this as
`collect::<Option<Vec<_>>>()`: `FromIterator` has an instance for `Option<V>`
whenever `V: FromIterator`. So `collect` subsumes `traverse` and `sequence` for
`Option` and `Either`, and `traverse(xs, f)` is `collect(map(xs, f))`. A
`Traversable` class still means something for eager containers.

**Collectors.**

* **Rust**: `FromIterator` with `collect()`, polymorphic in its return type, so
  it needs an annotation or a turbofish.
* **Haskell** `IsList` (for `OverloadedLists`):
  `class IsList l where type Item l; fromList :: [Item l] -> l`. That is one
  parameter plus a family -- Turkey's shape exactly.
* **Scala 2.13**: `xs.to(Set)` passes the companion object as a factory
  *value*, so no annotation is needed.
* **Python** `list(it)` and `set(it)`, **Swift** `Array(seq)` and `Set(seq)`:
  the target's constructor. No class, no annotation, and no generic collector.
* **Java**: `collect(Collectors.toList())` takes a collector *value* (supplier,
  accumulator, combiner, finisher), and collectors compose:
  `groupingBy(f, counting())`.
* **Haskell's `foldl` package** (Gabriella Gonzalez): `Fold a b` is an
  `Applicative`, so `(,) <$> sum <*> length` computes both in one pass.

Two ideas beyond Rust's:

* **Per-type one-liners.** `Set.from(xs)` is `collect(xs)` with the result type
  pinned, which is Python's ergonomics with the generic class underneath. The
  annotation is needed only when collecting generically.
* **Collectors as values** (Java, `foldl`) for grouping and one-pass
  aggregates. AoC's `Counter` and `defaultdict(list)` are Java's
  `groupingBy(f, counting())` and `groupingBy(f, toList())`.

**Prototype** (2026-09-14): `Seq` as `Functor`, `Applicative` and `Monad`; a
`Collect` class; instances for `Array`, `Set` and `Option c`. All of these ran
correctly:

* `collect(map(seq([1, 2, 3]), fun(x) = x * 2))` gave `[2, 4, 6]`, annotated
  `Array Int`.
* The same `collect` into `Set Int` gave `{3, 1, 2}`.
* `collect(map(seq(String.lines("1\n2\n3")), Int.parse))` into
  `Option (Array Int)` gave `Some([1, 2, 3])`, and `None` when a line was `x`.
  That is `traverse`.
* `take(map(naturals(), square), 5)` over an infinite `Seq` gave
  `[0, 1, 4, 9, 16]`.
* The `?` comprehension gave `[(1, 10), (1, 20), (2, 10), (2, 20)]` over both
  `Array` and `Seq`.
* The same `Seq` collected twice gave the same array twice (restartable).

**A checker gap the prototype hit, since fixed.** The first `Collect` put the
whole constraint on the method,
`fun collect[Iterable s, Stream (Iter s), Elem (Iter s) ~ Part c](s) -> c`. At
`d2b7a71` it failed in every instance that constrains its elements:

* The `Set a : Hash a` instance: "cannot determine a type satisfying
  'Hash (Stream.Elem (Iterable.Iter s))'".
* The `Option c` instance: "cannot reduce ... to 'Option a'".

The workaround was a method taking `Seq (Part c)` and a top-level `collect`
carrying the equality. `a64d792` ("Let a method's own equality be a given to
discharge") fixed it: at `prim-types-rebased` the method-level form checks and
prints `{3, 1}`, so `Collect` may carry the equality on its own method.

### 4.13 One concrete iterator type

The proposal from review: every iterator is one concrete type whose state is
hidden and user-defined -- eventually an existential,
`exists s. Iterator { state : s, next : fun(s) -> Option a }`.

**A closure is already that existential.** Typed closure conversion (Minamide,
Morrisett and Harper, POPL 1996) represents a closure as exactly that: a package
of a hidden environment type and code over it. So the design can be built today
as `type Iterator a = Iterator { pull : fun() -> Option a }`. When existentials
arrive, the representation can change behind the same public type, provided
the constructor is not exported and iterators are always made through
functions.

**Peers.**

* **One concrete type** is the common choice: Java `Iterator<T>` and
  `Stream<T>`, C# `IEnumerator<T>`, Kotlin `Iterator<T>` and `Sequence<T>`,
  Scala `Iterator[A]`, Go 1.23's `iter.Seq[V]` (a function type), OCaml's
  `Seq.t` (a function returning a node), and stream fusion's `Stream`.
* **One type per iterator** is Rust's and Swift's choice, made for static
  dispatch without allocation. Swift still ships `AnyIterator` as the erased
  form.

**Streams.** The restartable `Seq` of 4.12 cannot represent a one-shot source,
such as standard input or a half-consumed iterator, without buffering it or
giving up restartability.

* OCaml's `Seq` names the distinction: a sequence is *persistent* or
  *ephemeral*. `Seq.once` makes one that raises `Forced_twice` if it is queried
  twice, and `Seq.memoize` makes any sequence persistent (both OCaml 4.14,
  [manual](https://ocaml.org/manual/5.3/api/Seq.html)).
* Haskell's lazy lists are memoized, which is why `getContents` can be a list.

Under this proposal an `Iterator` is ephemeral: it is a stream. Restartability
belongs to `Iterable` containers, and to functions that return a fresh
iterator on each call, as Python's generator functions do. Replay requires
a separate `Memoized a` iterable with a shared cache and
fresh cursors; an ordinary self-iterating `Iterator a` cannot provide it
merely by caching values (6.5).

**What it removes**, compared with 4.3.1, 4.3.2 and 4.12:

* **The iterator class and its `Iter` family.** `Iterable c` has one family,
  `Elem c`, and `iterate : c -> Iterator (Elem c)`.
* **The blanket-instance problem.** "An iterator is iterable as itself" becomes
  `instance Iterable (Iterator a)`, an ordinary instance with a constructor
  head. No default superclass instances, no superclass equalities.
* **Leaking inner cursor types** (FINDINGS 5, and the record-field family gap
  in section 3). `Set`'s iterator is just `Iterator a`.
* **`seq()`.** `iterate` is the one conversion, and adapters and consumers take
  any `Iterable` and call it themselves. Only `map` and `?` -- `Functor` and
  `Monad` methods on the `Iterator` type -- need an iterator in hand:
  `map(iterate(lines), Int.parse)`.
* **Half of every generic consumer's context.** `[Iterable c, Elem c ~ Int]`
  instead of `[Iterable c, Iterator (Iter c), Item (Iter c) ~ Int]`.

**Laws and effects on a one-shot type.** The intended list-like laws assume
pure callbacks and no observable aliasing of consumed state. Turkey enforces
neither restriction. Two `map`s over one iterator interleave, and callbacks
can mutate shared state. These instances provide useful comprehension syntax;
they do not justify unrestricted algebraic rewrites of effectful programs.

* Java throws on reuse.
* Rust's `map` takes `self` by value, so reuse does not compile.
* OCaml offers `Seq.once` to detect it.

Turkey has neither ownership nor a check, so this is the same contract as
Python's iterators.

**What it costs.**

* **Per-type capabilities have no type to hang on.** Rust's
  `ExactSizeIterator`, `DoubleEndedIterator` and `Peekable::peek` become
  optional fields (a size hint) or wrapper records that are themselves
  `Iterable` (`Peekable a` holding an `Iterator a` and a one-element buffer).
* **An indirect call per element**, unless the backend inlines a known closure.
  Known lambdas in literal records are already reduced (commit 944e1ba),
  but that does not establish fusion through general mutable closure chains.
  Measure execution, allocations and memory before accepting the cost (6.5.2).
* **The existential version adds a choice that the closure hides.** Either
  mutable state -- a shared cursor, which the resumable-loop requirement of
  4.3.1 needs -- or immutable state with `next : fun(s) -> Option (a, s)`,
  stream fusion's form, restartable by copying. The requirement decides for
  mutable.

**Prototype** (2026-09-14): `type Iter a = Iter { pull : fun() -> Option a }`;
`Iterable` with one family; `Functor`, `Applicative` and `Monad` on `Iter`;
`Collect` with `fromIter(Iter (Part c))`. All of these gave the correct answer:

* `sum` over an `Array`, over `Some(41)`, and over a user-defined
  `countdown(4)` (a record plus a step function): 6, 41, 10.
* One one-shot stream, `map(iterate(lines), parse)`, shared by "sum to zero",
  "sum to zero" and "sum the rest": 7, 11, 7.
* `collect(take(map(naturals(), square), 5))`: `[0, 1, 4, 9, 16]`. Into `Set`:
  `{3, 1, 2}`. Into `Option (Array Int)`: `Some([1, 2, 3])`, and `None` for a
  bad line.
* A comprehension over `iterate(...)?`: `[(1, 10), (1, 20), (2, 10), (2, 20)]`.
* **Python's comprehension gotcha, reproduced.** With `let ys = iterate([10,
  20])` bound *outside* the comprehension, `let y = ys?` is exhausted after the
  first outer element, giving `[(1, 10), (1, 20)]`.

This supersedes the iterator class of 4.3.1-4.3.2 and the `Seq` type of 4.12
(6.5).

---

## 5. What AoC needs, by priority

Evidence is the 2021-2025 column of section 2.

**P0 -- establish the everyday iteration and collection API.**

These are ergonomic gaps, not all expressiveness blockers: loops and eager
arrays already express many of the programs. The first milestone is complete
solutions using a small, specified API.

* **Reading input.** `stdin` (a primitive) or `readFile` (exists). Reading a
  whole stream is enough; line-at-a-time is not measured.
* **An iteration module** over `Iterator`: `range`, `count`, `enumerate`,
  `zip`, `map`, `filter`, `takeWhile`, `sum`, `product`, `min`/`max`/
  `minBy`/`maxBy`, `any`/`all`, `find`, `fold`, `collect` to `Array`/`Map`/`Set`.
  Add `take` and `flatMap` with explicit short-circuit and consumption contracts.
  Combinatorial adapters follow in P2; numeric `product` and
  `cartesianProduct` have distinct names.
* **Parsing** is already covered by `lines`, `split`, `splitOnce`, `trim`
  and `Int.parse`, once iteration can map over the pieces. That is how the
  corpus parses. (An earlier draft proposed a `String.ints`, a community AoC
  helper that pulls every integer out of a line. The corpus does not use it,
  and it is withdrawn.) A parser monad is 7.11.
* **`Set` in the Prelude** (FINDINGS 24), and a counter idiom on `Map`
  using the existing `Map.update(counts, key, 0, fun(n) = n + 1)`.
  `lib/Data/Map.gob` already documents and implements this idiom.
* **`Int`**: `gcd`, `lcm`, `pow`, `sign`, `min`, `max`. **`Float.sqrt`** (a
  primitive).

**P1 -- one day in seven (search) to one in three (grids).**

* **`Deque`** and **`Heap`** (priority queue, min by `Ord` or by key).
* **Search over successor functions**: `bfs`, `dijkstra`, `reachable`, with
  distances, one path, and predecessor information. Built on `Deque`, `Heap`,
  `Map`. A* and path enumeration follow only with explicit contracts (6.4).
* **A grid**: a point with arithmetic and neighbours, and a rectangular grid
  built from any iterable of iterables. That keeps the `list[str]`-as-2D-array
  ergonomics without making strings an axis (4.3.1). This is what the `aoc.py`
  prelude grew into over three years -- the strongest signal in the corpus.
  Its names were chosen for typing speed and are not a constraint.
* **Memoization** works today, two ways, both run on 2026-09-14:
  * Python's mechanism. `@cache` works because recursive calls go through a
    late-bound global. A module-level `var fib` holding a closure, reassigned
    to `memo(slowFib)`, does the same: closures capture the cell (design.md
    11, row 36). `fib(80)` made 81 calls.
  * No globals: `memoFix(fun(rec, n) = ... rec(n - 1) ...)`, where the function
    receives its memoized self.

  Both are ten lines of library. They belong in `Algorithm.Memo`. Writing the
  second with a *local* `fun go(x)` inside the generic `memoFix` hit an
  internal error at `d2b7a71` ("the variable 'x' should be Int but is k");
  `c1db094` fixed it, and it runs at `prim-types-rebased`.

**P2 -- expand when complete solutions demonstrate the need.**

* `pairwise`, `windows`, `chunks`, `cartesianProduct`, `combinations`,
  `permutations`; specify buffering and repeated traversal before shipping.
* **Generators** (4.9), replay through `Memoized`, and A*.
* Connected components and union-find; topological sort; cycle detection.
* `Rational` over `Int` (2025/10). Homogeneous operators make it an ordinary
  type with `Add`..`Div` instances. Intermediate overflow remains possible
  even when a reduced final answer fits; cancellation and checked arithmetic
  need explicit design and tests.
* An ordered map (`bisect`/`insort` at 8% of 2024).

**Harness, not solutions.** An environment variable read (a primitive), or
just `args`. HTTPS is not worth a primitive: fetch with `curl` in a shell
wrapper and read the cached file.

**Deferred.** Regex (nine calls in seven years), bignums (bounds unverified),
numpy, z3, time zones, Unicode segmentation.

---

## 6. Recommendation on layout

Revised after five reviews.

### 6.1 `Data` holds concrete types

`Data` holds declarations of data and operations on data: every type declared
with `type`, with its functions and instances. `Std.*` is withdrawn.
`Collections` is still out, because `Int`, `Option` and `Ordering` are not
collections.

### 6.2 `Protocol` holds every class

`Data` for concrete things, `Protocol` for abstract ones. The line is
syntactic: a `class` declaration goes in `Protocol`, a `type` declaration in
`Data`, and a reader never has to judge which side something falls on.

**Precedent.**

* **Scala's cats**: `cats.data` holds the concrete types (`NonEmptyList`,
  `Validated`, `Kleisli`); the classes are `cats.Functor` and `cats.Monad`, with
  `Eq`, `Order`, `Semigroup` and `Monoid` in `cats.kernel`.
* **petgraph** splits traits, representations and algorithms (`visit`, `graph`,
  `algo`).
* **Python**: `collections.abc` apart from `collections`.
* **Elixir**: the `Enumerable` protocol apart from the `Enum` functions.

The counterexamples are Haskell and PureScript, which put classes in `Data`
beside their topic, and Rust, which places traits by topic.

**The category-theory objection.** A class is a structure on a type, and under
dictionary passing (design.md 5.4) its evidence *is* a record, so in the
implementation a class is data. But the tree is for readers, and the question a
reader brings is "do I build one of these, or make my type satisfy it?". `type`
against `class` answers exactly that.

**By construct rather than by domain, and why that is less wrong here than in
an application.** The package-by-feature argument is about *change*. In an
application a feature touches its model, its view and its storage, so grouping
by layer scatters every change across the tree. A standard library changes
differently:

* **The unit of change is one module, and modules are already domains.**
  `Data.Map` is the type, its functions and its instances; `Protocol.Iterable`
  is the class and what is derived from it. The root is a second index on top
  of domain-shaped modules, not a replacement for them.
* **Acyclicity is a module-level constraint.** Turkey has no mutually recursive
  modules (FINDINGS 31). The category roots are not dependency strata:
  protocols need types and types need protocols. Low-level declaration
  modules, including `Turkey.Internal.*`, make the actual graph acyclic.
  Validate that graph before implementing the namespace migration (6.3).
* **Cross-cutting classes have no domain.** `Eq`, `Hash` and `Add` belong to
  everything. Rust's answer is to invent a domain per trait (`cmp`, `hash`,
  `ops`).

The cost is that "everything about graphs" lives in three roots. The mitigation
is a domain-shaped name under each root -- `Protocol.Graph`, `Data.Graph.*`,
`Algorithm.Search` -- and documentation that links them. Rust's `std::iter` is
the counterexample: the `Iterator` trait, its adapter types and free functions
are one domain module. It works because Rust's modules within a crate may refer
to each other freely, so a domain module needs no internal layering. Turkey's
may not.

**Functions derived from a class.** "With their dependencies" is ambiguous as
soon as there are two: `sum` walks an `Iterable` and adds its elements. Haskell
and Rust both put it with the container. `sum` is a `Foldable` method in
`Data.Foldable`; `Iterator::sum` is a method, and its helper trait `Sum` lives
in `std::iter`. The tie-breaker that fits both: **a function lives with the
protocol whose structure it walks**, and classes it uses only on the elements
are incidental. Consumers such as `sum`, `min` and `foldMap` therefore live
in `Protocol.Iterable`, and `hash` in `Protocol.Hash`. Adapters such as `zip`
and `filter` construct the concrete iterator type and live in `Data.Iterator`
(6.5). This is a placement rule with an explicit adapter distinction. When a
function's
substance is a procedure rather than a traversal -- search, sorting,
components -- it goes in `Algorithm.*` (4.2).

`sum` also exposes a gap: it needs a zero. `Add` has none, and `Monoid Int`
cannot choose between `+` and `*`. Rust's answer is a `Sum` trait. Haskell's is
`Num`, and Turkey split `Num` into per-operator classes on purpose (7.2).
The preference is an additive-identity class if generic numeric reductions
are retained. An explicitly seeded reduction is already `fold`; multiplication
needs its own identity, rather than an ambiguous `Monoid Int` instance.

**What it settles.**

* `Monad` is `Protocol.Monad`, beside `Protocol.Functor` and
  `Protocol.Applicative`. `Control` is not needed.
* If the graph experiment supports a class, it lives in `Protocol.Graph`,
  representations in `Data.Graph.*`, and algorithms in `Algorithm.Search`.
  The class remains provisional; successor-function entry points are required.
* Where a type can do an algorithm in place or better, it keeps its own API
  (`Array.sort`); the generic version stays in `Algorithm.*`.
* Instances stay where the owner rule puts them: `Iterable (Option a)` is in
  `Data.Option`.

### 6.3 What the compiler integrates with: `Turkey.Internal.*`

The declarations the compiler desugars to are `Eq`, `Ord`, `Add`..`Neg`
(operators), `Index` (brackets), `Iterable` and the `Iterator`
type (`for`), `Monad` (`?`),
and `Bool`, `Option`, `Array`. They are defined in one internal namespace, and
`Protocol.*`, `Data.*` and the Prelude re-export them -- the shape of GHC 9.10's
`ghc-internal` behind `base` (4.11).

Every one of them gets a user-facing module, the operator classes included:
`Protocol.Add`, `.Sub`, `.Mul`, `.Div`, `.Rem`, `.Neg`, one per class like every
other class. The classes are split per operator so that a type can add without
dividing (design.md 8.2), and the modules follow that split.

**The name can be `Turkey.Internal.*`, despite `boot/`.** A module name is its
whole dotted path, so `Turkey.Internal.Classes` (`lib/Turkey/Internal/
Classes.tl`) and boot's `Turkey.Classes` (`boot/Turkey/Classes.gob`) are
different modules. That is GHC's defence, disjoint leaves, without the package
system behind it. The hazard is future: boot adding a `Turkey/Internal/`
directory would shadow the library while boot compiles itself, because the
entry directory is searched first. A check that a program's own module may not share
a name with a `lib/` module turns that into a compile error. The
alternative is renaming boot to `Turkey.Compiler.*`, as GHC 9.0 renamed its
compiler: a sed over 40 files and 287 import lines.

**Proposed declaration dependencies** (arrows mean "imports"; names below
are proposed modules, not existing files):

```text
Turkey.Internal.Iteration -> Turkey.Internal.Option.Type,
                             Turkey.Internal.Functor,
                             Turkey.Internal.Applicative,
                             Turkey.Internal.Monad
Protocol.Iterable -> Turkey.Internal.Iteration
Data.Iterator     -> Protocol.Iterable, Turkey.Internal.Iteration
Data.Array        -> Protocol.Iterable, Turkey.Internal.Array.Type
```

`Turkey.Internal.Iteration` declares both `Iterator` and `Iterable`, along
with the iterator's instances and low-level pull operations. Keeping the two
declarations together avoids a cycle while respecting instance ownership.
Consumers in `Protocol.Iterable` use low-level operations without importing
`Data.Iterator` back. Public protocol and type facades re-export the respective
declarations; a facade re-export does not transfer ownership.

This graph is a design sketch, not a verified module layout. Compile a minimal
multi-module example with the real owner rule, export lists, instance loading,
and both compilers before renaming the library. Public facade modules may
re-export declarations, but must not introduce return dependencies.

### 6.4 `Algorithm.*`

Implicit successors are required; classes for structure remain provisional
(4.2). `Algorithm.Search`
re-exports `Algorithm.Search.Bfs`, `.Dijkstra` and `.AStar`. The graph class's
shape, and whether it earns its place, are found by experiment (7.9).
Keep a convenient successor-function entry point even if a class supports it.

Before shipping, specify nonnegative costs for Dijkstra, cost-overflow behavior,
A* heuristic requirements and reopening, and deterministic heap ties without
an `Ord` constraint on nodes. Separate distance-only queries, one reconstructed
path, and predecessor sets. Enumerating all shortest paths can be exponential;
zero-cost cycles require a definition of simple paths versus walks. Enumeration
is deferred until its termination and resource behavior are defined.

### 6.5 Iteration

One concrete iterator type (4.13).

* **`Data.Iterator`**: `type Iterator a`, with its state hidden -- a closure
  now, an existential later, behind the same public type. It is a `Functor`,
  `Applicative` and `Monad`. The adapters (`take`, `filter`, `zip`,
  `enumerate`) are its functions: they accept any `Iterable` and
  answer an `Iterator`. An iterator is ephemeral -- a stream. Its constructor
  stays private; a public factory accepts a pull closure for custom sources.
* **`Data.Memoized`** (P2): `memoize` accepts an iterable and returns a
  `Memoized a`, sharing one source and cache. Each `iterate` creates a fresh
  cursor starting at the cache beginning and extends it on demand. This
  replays results without rerunning source effects. Retaining the memoized
  value retains its cached prefix, potentially without bound.
* **`Protocol.Iterable`**: `class Iterable c { type Elem c; fun iterate(c) ->
  Iterator (Elem c) }`, with `instance Iterable (Iterator a)` answering itself.
  The consumers (`sum`, `min`, `any`, `foldMap`) live here. `Foldable` is
  removed (4.12).
* **`Protocol.Collect`**: `fromIterator(Iterator (Part c)) -> c`, and `collect`
  over any `Iterable` -- a method or a top-level function, now that the checker
  accepts either (4.12). Instances for `Array`, `Set`, `Map` (over pairs) and `String`;
  the instances for `Option c` and `Either e c` are `traverse`. Each type has a
  `from` that pins the result type.
* **`for x in e`** desugars to `iterate(e)` and repeated `next` on the one
  type.
* **Laziness follows the type.** `map` on an `Array` stays eager;
  `map(iterate(xs), f)` is lazy. `?` over either is a comprehension, with
  Python's gotcha for an iterator bound outside it.

Generators are `Data.Generator` (4.9.1): a monadic `Body a r` with `yield`,
`yieldFrom` and `delegate`, and an iterable `Gen a r` reachable only through
`generator(fun() { ... })`, with neither constructor exported. Each iteration
reruns the body with fresh local state, while captured values remain shared.
The monadic laws assume pure callbacks and unaliased consumed state; the
language does not enforce those conditions. Performance must satisfy 6.5.2.

#### 6.5.1 Proposed consumption contract

These are proposed API requirements, not claims about the prototypes:

* **Permanent exhaustion:** after `next` returns `None`, subsequent calls
  return `None` without calling the source again. The public pull factory
  enforces this for custom sources.
* **Lazy construction:** building an adapter pulls no elements and invokes no
  element callback. Specify when it calls `iterate`; the default is on the
  adapter's first pull, once per input.
* **Short-circuiting:** `take(0)` never pulls; `take(n)` pulls at most `n`.
  `any`, `all` and `find` consume the decisive element and stop immediately.
  Collecting into `Option`/`Either` consumes the first failure and stops there.
* **`takeWhile`:** consumes the first failing element. Callers needing that
  delimiter afterward must use a peeking wrapper.
* **`zip`:** pulls left first, then right, stopping at the first exhausted
  input. If right ends first, one unmatched left element has been consumed.
  Permanent exhaustion prevents further advancement after that point.
* **Shared cursors:** `iterate(it)` returns `it`; several consumers advance
  the same position. Breaking a loop preserves the remaining cursor.
* **Mutable containers:** mutation during iteration is outside the supported
  contract initially. The implementation must still preserve memory safety;
  snapshot or mutation-detection semantics can be added explicitly later.
* **Combinatorics:** each adapter must document which inputs it buffers or
  re-iterates, its finite-input requirements, and retained memory. Accepting
  `Iterable` does not imply that an input supports independent traversals.

Specify boundary cases too: empty `min`/`max` return `Option`, range endpoints
are exclusive, zero range steps are errors, and negative counts or nonpositive
window/chunk sizes are rejected. Numeric overflow follows `Int`'s trapping
contract. Tests must observe residual input and callback effects, not only the
values returned by the adapter.

#### 6.5.2 Acceptance evidence

Before accepting the iteration representation, compare direct loops against
`map`/`filter`/`fold`, nested `flatMap`, early termination, and graph-successor
iteration on the native backend. Report execution separately from compilation,
plus allocations and peak memory where instrumentation permits; explicitly
label unavailable measurements. Include generator delegation depth and long
left-associated binds. Establish acceptable costs from representative solution
runtimes before adding optimization work; a prototype result alone is not a
fusion or complexity guarantee.

The delivery milestone is several complete AoC solutions on both compiler
implementations, including ordinary parsing/aggregation, a grid search and a
stateful weighted search. Check known answers, failure diagnostics, residual
iterator state, runtime and memory. Preserve source programs and exact commands
so these checks can be repeated after compiler changes.

### 6.6 Batteries included

Decided (4.8).

### 6.7 Points and grids

`Data.Point` and `Data.Grid`, a grid built from any iterable of iterables
(4.3.1). Names are chosen for the library, not taken from `aoc.py`.

### 6.8 `System.IO` and `System.Env` stay

Haskell and .NET both nest IO under `System`, and those are the current names.

`System.Env.get` arrived with the FFI (SPEC-DELTAS 71) and is the shape the
rest of this layer should take: `Unsafe.Libc` declares `getenv`, `System.Env`
copies the name into a NUL-terminated block, calls it, reads the answer back
and frees what it owns, and what it exports is an `Option String`. The unsafe
module's name is in the wrapper's import list and in nothing above it.

### 6.9 `Time` and `Text`

Planned, not scheduled. `Time` gets its own document (full Temporal, with
zones); `Text` is for Unicode-table operations. Neither is needed for AoC.

### 6.10 The resulting tree

```
Prelude
Turkey.Internal.*               -- compiler-integrated declarations (6.3)
Protocol.{Eq,Ord,Hash,Show}
Protocol.{Add,Sub,Mul,Div,Rem,Neg}
Protocol.{Semigroup,Monoid,Index,Length}
Protocol.Iterable               -- plus sum, min, any, foldMap (P0)
Protocol.Collect                -- collect; Option/Either instances are traverse (P0)
Protocol.{Functor,Applicative,Monad}
Protocol.Graph                  -- provisional, P1 experiment
Data.{Bool,Byte,Char,Int,Float,Option,Either,Ordering,Tuple}
Data.{String,Array,Map,Set}
Data.Iterator                   -- the one iterator type; Functor/Monad; adapters (P0)
Data.Generator                  -- P2
Data.Memoized                   -- replayable cache with fresh cursors (P2)
Data.{Deque,Heap}               -- P1
Data.{Point,Grid}               -- P1
Data.Graph.*                    -- representations, when needed
Data.Rational                   -- P2
Algorithm.Hash
Algorithm.Search                -- .Bfs .Dijkstra (P1), .AStar (P2)
Algorithm.Memo                  -- P1
Algorithm.Components            -- P2
System.IO                       -- readFile suffices for P0; stdin when needed
System.Env                      -- plus environment variables
Time, Text                      -- planned
```

---

## 7. Open questions

1. **The derived-function rule.** "With the protocol whose structure it walks"
   (6.2) is a proposal, not a decision.
2. **Numeric identities.** Prefer an additive-identity class for generic
   `sum`; settle its laws and the multiplicative identity for `product`.
   Explicit initial values are served by `fold`.
3. **Collectors as values.** Java's `Collectors` or Haskell's `foldl` `Fold` as
   a `Data` type beside `collect`, for `groupBy`, `counts` and one-pass
   aggregates.
4. **The iterator's representation.** A closure now, with a private constructor
   and public pull factory. Future existentials must preserve shared-cursor
   semantics, even if the hidden step function uses immutable state.
5. **Capabilities on one type.** Size hints, peeking, reverse iteration: fields
   or wrappers (4.13).
6. **The comprehension gotcha.** Shared-cursor behavior is intentional. Can
   optional diagnostics catch accidental reuse without rejecting valid resumption?
7. **`for` over `Iterable`**, in both implementations.
8. **The checker gap left.** A family application in a record field type is
   still rejected at `prim-types-rebased` ("unknown type 'Iterable.Iter'").
   The method-own-equality gap (4.12) and the local generic `fun` crash
   (section 5) are fixed there.
9. **The graph class.** Write 2023/17, 2024/16 and 2025/08 against two or three
   candidate shapes -- `Int` costs; a `Cost g` family; separate `Graph` and
   `Weighted` classes; successors as an `Array` or an `Iterator` -- and compare
   the programs and their run time on the native backend. Include a function-only
   API as a baseline; a class is an outcome to justify, not a prerequisite.
10. **Generator syntax.** The trailing `done()` after a loop, which is the
    no-auto-`pure` rule (design.md 6.9). With discarded values an error (`f794a62`),
    a discarding spelling for binds that answer a value (`let _ =`, `void`, or a
    `_` suffix). And `send`, if ever.
11. **Parsing beyond `split`.** A parser monad (`base` ships
    `Text.ParserCombinators.ReadP`; OCaml ships `Scanf`): where, and when.
12. **Grid indexing.** One `Index` instance per type: `grid[point]` or
    `grid[y][x]`.
13. **What the Prelude re-exports.** FINDINGS 24 asks for a rule.
14. **Primitive budget.** `stdin` and `Float.sqrt`; an environment read.
15. **Integration evidence.** Preserve the corpus-analysis script, input revision,
    prototype sources and benchmark commands alongside the document. Recheck
    numeric intermediate bounds, not only literals. Complete the dependency
    experiment in 6.3 and acceptance measurements in 6.5.2.
