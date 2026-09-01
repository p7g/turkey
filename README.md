# turkey-lite

A Python prototype of the language specified in [`design.md`](design.md): a
small procedural language with an ML-style type system — Hindley-Milner
inference with the value restriction, strict call-by-value evaluation,
uncurried functions, and mutation through single-variant records and arrays.

The point of the prototype is to make the design *executable*, so it can be
argued with. Reading a spec does not tell you that its worked example calls the
wrong `push`, or that its grammar cannot express an assignment inside a block.
Running one does. Every disagreement found along the way is recorded in
[`SPEC-DELTAS.md`](SPEC-DELTAS.md), keyed to the section it amends.

## Using it

Install the project with Python 3.11 or newer; native execution requires a C
compiler for the small runtime and uses `llvmlite` for code generation. From
the repo root:

```
python3 -m turkey run    program.tl    # type-check and execute
python3 -m turkey run    program.tl -- a b   # ... passing it arguments
python3 -m turkey types  program.tl    # print each top-level binding's type
python3 -m turkey tokens program.tl    # dump the token stream
python3 -m turkey ast    program.tl    # dump the parse tree
python3 -m turkey core   program.tl    # dump the typed Core the elaboration produces
python3 -m turkey mono   program.tl    # dump that Core specialized -- what actually runs
python3 -m turkey opt    program.tl    # dump the optimized Core
python3 -m turkey python program.tl    # print generated Python without running it
python3 -m turkey llvm   program.tl    # print verified LLVM IR
python3 -m turkey run --backend python program.tl  # compatibility backend
```

A program is a single file. Execution lowers optimized typed Core to a checked
control-flow IR, compiles it with LLVM, initializes top-level bindings, and
calls `main` if one is defined. `--backend python` retains the previous
generated-Python implementation as a compatibility and differential-testing
backend. Everything after `--` is the program's own command line, reached
through `System.Env.args`; under either backend the program runs with a large
stack, because the host's default recursion limit is a fact about the host
rather than about the language. The `llvm` and `python` commands expose their
generated forms for inspection; neither is a standalone-file interface.

```
type Stack a = Stack {
    data : Array a
    top  : Int
}

fun newStack(capacity : Int) -> Stack a {
    Stack { data = Array.new(capacity), top = 0 }
}

fun push(s : Stack a, x : a) -> Unit {
    Array.push(s.data, x)
    s.top = s.top + 1
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

fun main() {
    let s = newStack(4)
    push(s, 10)
    push(s, 20)
    for x in drain(s) { print(Int.toString(x)) }
}
```

`python3 -m turkey types` on that file reports:

```
newStack : fun(Int) -> Stack a
push : fun(Stack a, a) -> Unit
drain : fun(Stack a) -> Array a
main : fun() -> Unit
```

## Layout

| File | What it does |
|---|---|
| `turkey/lexer.py` | Tokens, comments, and §2.4's newline rule as a separate filter pass |
| `turkey/ast.py` | Syntax tree |
| `turkey/parser.py` | Recursive descent; §7 type-declaration disambiguation |
| `turkey/types.py` | Semantic types, unification, the bottom type, generalization |
| `turkey/decls.py` | Type and constructor declarations; alias expansion |
| `turkey/deps.py` | Free variables and Tarjan SCC, for §5.2's grouped inference |
| `turkey/infer.py` | Constraint generation: the value restriction, control-flow typing |
| `turkey/constraints.py` | The constraint language and its solver; ranks, predicates |
| `turkey/exhaustive.py` | Maranget's usefulness algorithm, for match warnings |
| `turkey/values.py` | Runtime values, including the hidden primitive array storage |
| `turkey/backend_ir.py` | Checked, layout-aware control-flow IR shared by native lowering |
| `turkey/backend_lower.py` | Core-to-backend-IR lowering, closure conversion, and ABI bridges |
| `turkey/llvmgen.py` | llvmlite emission, verification, JIT execution, and runtime loading |
| `runtime/` | Native strings, arrays, closures, panic frames, and exact-root collector |
| `turkey/pygen.py` | Retained generated-Python compatibility backend |
| `turkey/eval.py` | Tree-walking differential-test oracle |
| `turkey/builtins.py` | The machine primitives, and nothing else |
| `turkey/modules.py` | The import graph, and what each module can see |
| `turkey/resolve.py` | Rewrites a module's names so the program shares one namespace |
| `turkey/lib/` | The library, written in the language: classes, Prelude, `Data.*` and `System.*` modules |

Three things are worth knowing before reading the code, because they are where
this language departs from a textbook implementation.

**Bottom.** `return`, `break` and `continue` have type `⊥`, which unification
absorbs (§4.3). Absorption alone is not enough: in
`if c { return 1 } else { 2 }` the arms are `⊥` and `Int`, and unifying them is
a no-op that leaves the caller no wiser. So `types.join` is used wherever two
branches must agree, and it returns the surviving type.

**Field access is a predicate.** `r.f` emits `HasField "f" typeof(r) a` and
decides nothing; the solver settles it once the receiver is known, however much
later that is. A demand still unresolved when its binding generalizes travels
in the scheme, so `fun get(r) = r.n` is `[HasField "n" a b] fun(a) -> b` and
reads from any record with an `n`. Records stay nominal and there are no rows:
this is Gaster & Jones's `r \ l` without them, which is decidable here because
entailment is a declaration lookup (SPEC-DELTAS.md 7).

**Numeric projection is also a predicate.** `x.0` emits
`HasProjection 0 typeof(x) a`. It works on tuples and immutable types with one
positional constructor, can travel in an inferred scheme, and is read-only.
The solver checks the index once the receiver shape is known.

**The newline rule.** §2.4 as written is circular — newlines inside braces are
"dropped except where the inner grammar uses it as a separator", which a lexer
cannot decide. It is implemented as a concrete two-sided filter over the token
stream (SPEC-DELTAS.md 11), which is why it lives in its own pass and has its
own tests. Bracket nesting is tracked as a stack rather than a counter: `(` and
`{` disagree about whether a line break matters, either can contain the other,
and only the innermost one gets to decide.

## Tests

```
python3 -m pytest tests -q
```

The generated-Python compatibility backend has a manual, non-CI comparison
against the tree-walking evaluator. It reports generation, Python compilation,
and median warm execution separately:

```
python3 -m benchmarks.python_backend --rounds 3
```

`tests/programs/` holds golden-file conformance programs: each `NAME.tl` is
paired with a `NAME.expected` holding the exact combined output of running it.
Programs whose names begin with `err_` are expected to fail. To add a case,
write the two files — or write the `.tl` and run
`python3 tests/regenerate_expected.py`, then read the diff to confirm the
output is what you meant.

`tests/programs/` also holds *directories*: one whose entry module is `Main.tl`
is a multi-file program, run from inside that directory, with its golden in
`Main.expected` beside it.

## Current boundaries

Modules (§9) work for values, types, constructors, classes, methods, and
associated families. A plain `import M` provides bare and `M.`-qualified names;
`import M as A` is qualified-only. Classes and their members belong to their
declaring module, while globally coherent instances are protected by the
orphan and overlap rules. Any explicit Prelude import replaces the automatic
one, and `import Prelude ()` removes its dependency edge entirely for low-level
modules. Exhaustiveness remains a warning, per §5.1.

A program can reach outside itself: `System.Env` has `args` and `exit`, and
`System.IO` has `readFile`, `writeFile` and `stderr`. Files are read as bytes
and turned into a `String` by the checked constructor, so `readFile` answers
`Option String` -- a file is not guaranteed to be well-formed UTF-8 and a
`String` is.

`Data.String.Index` is an opaque position in a string. It is obtainable only
from `start`, `end`, `step` or `find`, and no arithmetic on one is exposed, so
every index names a character boundary and `slice` has nothing to validate. A
raw byte offset never reaches the surface language.

`Array` is an ordinary opaque growable library type backed by fixed-length
`Prim.Array` storage. Indexing and `len` are the `Index` and `Length` class
methods, so user-defined containers can support the same syntax. Storage
capacity and the primitive backing value are not part of the surface API.
