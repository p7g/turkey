# The Turkey Language Reference

This reference describes the syntax and meaning of Turkey programs. It is
written for people who write Turkey or are learning it, and it assumes you
already program in some other language. It is not a tutorial: each chapter
states the rules for one part of the language, with examples, and the chapters
can be read in any order.

Some things are deliberately left out:

* **How the compiler works.** The reference says what a program means, not how
  it is compiled, and nothing here depends on how a value is laid out in
  memory.
* **The standard library**, except the parts the language itself relies on:
  the types and classes behind literals, operators, `for` loops, indexing and
  `?`. Those are described in [Built-in types and classes](builtins.md).
  Examples also use a few library functions, such as `print`, without further
  comment.

## Contents

1. [Lexical structure](lexical.md): source files, comments, identifiers,
   keywords, literals, and how line breaks end statements.
2. [Types](types.md): primitive types, tuples, functions, data types, records,
   aliases, mutability, and existential types.
3. [Declarations](declarations.md): functions, lambdas, `let` and `var`,
   annotations, and signatures.
4. [Expressions](expressions.md): operators, calls, field access, indexing,
   records, tuples, and array literals.
5. [Patterns](patterns.md): the pattern forms, exhaustiveness, and which
   patterns may appear in bindings.
6. [Statements and control flow](statements.md): blocks, assignment, `if`,
   `match`, the loops, `break`, `continue`, and `return`.
7. [The `?` operator and `do`](do-notation.md): monadic sequencing.
8. [Type classes](classes.md): classes, instances, superclasses, associated
   types, and equality constraints.
9. [Type inference](inference.md): what the compiler infers, when it
   generalizes, and how numeric literals get their types.
10. [Runtime errors](runtime-errors.md): what makes a program panic.
11. [Built-in types and classes](builtins.md): the library the language itself
    relies on.
12. [Modules](modules.md): module headers, exports, imports, and the entry
    point.
13. [Grammar](grammar.md): the whole grammar in one place.

## Notation

Each construct has a **Syntax** block. The grammar notation is:

| Notation | Meaning |
|---|---|
| `"fun"` | the literal text `fun` |
| `IDENT`, `CONID`, `INT`, ... | a token of that class ([Lexical structure](lexical.md)) |
| `a b` | `a` followed by `b` |
| `a \| b` | `a` or `b` |
| `a?` | `a` or nothing |
| `a*` | zero or more `a` |
| `a+` | one or more `a` |
| `( ... )` | grouping |

Lists separated by commas also accept a trailing comma. The grammar leaves
this out to stay readable.

Some constructs are shorthand for longer code. The reference explains them by
showing the longer code, written as approximately equivalent Turkey. Names
that begin with `$` stand for hidden temporaries. You cannot write those names
in a program, so they never clash with yours.

A note that begins **For readers new to this** explains a term that is
standard in the literature on programming languages but may be unfamiliar,
such as *principal type* or *existential type*. A **Coming from Rust** or
**Coming from Haskell** note points out where Turkey differs from what a
reader of that language would expect.

## Examples

Every example is a whole program or a set of declarations. Each one is
compiled by the test suite (`tests/test_reference.py`), so none of them is
hypothetical. When an example prints something, its output follows it:

<!-- run -->
```kotlin
fun main() {
    print("hello")
}
```

```text
hello
```

Examples that the compiler rejects say so in the surrounding text, and the
test checks that the error message says what the text claims it says.

Source files use the extension `.gob`. To run one:

```sh
python3 -m turkey run hello.gob
```

## For maintainers

**Keeping it true.** Examples are checked mechanically. Each `kotlin` fence
must be preceded by an HTML comment that says how to check it:

| Directive | Meaning |
|---|---|
| `<!-- run -->` | A whole program. The next `text` fence is its exact standard output. |
| `<!-- check -->` | Must compile. |
| `<!-- error: TEXT -->` | Must be rejected, and the error message must contain `TEXT`. |
| `<!-- panic: TEXT -->` | Must compile and then panic with a message containing `TEXT`. An optional `text` fence gives the output printed before the panic. |
| `<!-- module: Name.gob -->` | Another source file for the next example, written beside it. The example itself is `Main.gob`. |

Examples that run are run twice: once in the test process, and once through
`python3 -m turkey run`, whose default backend is the native one, since that is
what a reader who copies an example will execute. A `kotlin` fence with no
directive fails the test. Grammar goes in `ebnf`
fences and shell commands in `sh` fences; the test ignores both.

**What goes in.** Only what the compiler accepts today. The reference does not
mention planned features, reserved syntax, or open proposals; those belong in
`PROPOSALS.md` and `SPEC-DELTAS.md`. When the language changes, change this
reference in the same commit.

**Why it is shaped this way.** These are the references this one borrows
from, and the one that chose differently.

* The **Python Language Reference** is separate from both the tutorial and the
  Library Reference, and its "Data model" chapter covers the hooks that
  operators and `for` call into. That is the same scope as this reference, and
  [Built-in types and classes](builtins.md) plays the part of the data-model
  chapter.
* The **Haskell 2010 Report** gives the meaning of each piece of syntactic
  sugar by translating it into a smaller core language. This reference
  explains `for`, `if let`, `while let`, array literals and `?` the same way,
  except that it translates into ordinary Turkey rather than a separate core
  language.
* The **Rust Reference** puts a small syntax block in each section, and it
  tests its examples, including examples that are meant to be rejected. This
  reference does both: the syntax sits next to the construct it describes, and
  [Grammar](grammar.md) collects all of it in one place.
* The **Go specification** is the counterexample: one normative file, with no
  chapters. It can be, because Go has no type classes, type families or
  inference to explain. Turkey has all three, and a reader looking up one of
  them should not have to scroll past the other two.
