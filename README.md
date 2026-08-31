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

No dependencies beyond the standard library. From the repo root:

```
python3 -m turkey run    program.tl    # type-check and execute
python3 -m turkey types  program.tl    # print each top-level binding's type
python3 -m turkey tokens program.tl    # dump the token stream
python3 -m turkey ast    program.tl    # dump the parse tree
```

A program is a single file. Execution evaluates the top-level bindings, then
calls `main` if one is defined.

```
type Stack a = Stack {
    data : Array a,
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
        if s.top == 0 { break out }
        Array.push(out, Array.pop(s.data))
        s.top = s.top - 1
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
| `turkey/values.py` | Runtime values; array length/capacity semantics |
| `turkey/eval.py` | Tree-walking evaluator |
| `turkey/builtins.py` | The machine primitives, and nothing else |
| `turkey/modules.py` | The import graph, and what each module can see |
| `turkey/resolve.py` | Rewrites a module's names so the program shares one namespace |
| `turkey/lib/` | The library, written in the language: `Prelude.tl`, `Data/*.tl` |

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

`tests/programs/` holds golden-file conformance programs: each `NAME.tl` is
paired with a `NAME.expected` holding the exact combined output of running it.
Programs whose names begin with `err_` are expected to fail. To add a case,
write the two files — or write the `.tl` and run
`python3 tests/regenerate_expected.py`, then read the diff to confirm the
output is what you meant.

`tests/programs/` also holds *directories*: one whose entry module is `Main.tl`
is a multi-file program, run from inside that directory, with its golden in
`Main.expected` beside it.

## Not in v0

Modules (§9) work — a program is a directory, `import` means what §9 says, the
prelude and the `Data.*` modules are ordinary source under `turkey/lib`, and a
type constructor belongs to the module that declared it, so two libraries that
each declare a `Node` can be used together (SPEC-DELTAS.md 41, 42, 43). What is
not there yet: classes are global, so an export list may name one but withholds
nothing, and two modules cannot each declare an `Eq`. Instances are global too,
which the orphan rule is what makes safe. Exhaustiveness is a warning, per
§5.1.
