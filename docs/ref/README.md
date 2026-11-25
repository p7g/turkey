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
3. [Declarations](declarations.md): functions, lambdas, placeholder
   functions, `let` and `var`, annotations, and signatures.
4. [Expressions](expressions.md): operators, calls, field access, indexing,
   records, tuples, array literals, and string interpolation.
5. [Patterns](patterns.md): the pattern forms, exhaustiveness, and which
   patterns may appear in bindings.
6. [Statements and control flow](statements.md): blocks, assignment, `if`,
   `match`, the loops, `break`, `continue`, and `return`.
7. [The `?` operator and `do`](do-notation.md): monadic sequencing.
8. [Type classes](classes.md): classes, instances, superclasses, associated
   types, and equality constraints.
9. [Type inference](inference.md): what the compiler infers, when it
   generalizes, and how numeric and array literals get their types.
10. [Runtime errors](runtime-errors.md): what makes a program panic.
11. [Built-in types and classes](builtins.md): the library the language itself
    relies on.
12. [Modules](modules.md): export declarations, imports, and the entry
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
compiled by the test suite (`sh scripts/test.sh docs/ref`), so none of them is
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

Source files use the extension `.gob`. The compiler, `turkey`, writes the
whole program as an executable, named after the file and placed in the working
directory, with nothing to assemble or link:

```sh
turkey build hello.gob
./hello
```

The executable is signed on a Mac, and on Linux it runs on glibc 2.34 or
later. It loads the shared libraries the program calls into, as
[Foreign functions](declarations.md#foreign-functions) describes. `-o PATH`
names it, and `-o -` writes it to standard output. `-S` writes the program's
assembly instead, to `hello.s` or wherever `-o` names. The system assembler
accepts it, but it is for reading the code the compiler made, not for
building programs.

The project README says how to build `turkey` itself.

## Warnings

`turkey build` and `turkey check` run *checks* on the program: advice about
code that compiles but probably does not say what its author meant. Each
finding is a warning, printed with the name of its check at the end, and the
compile ends with a count of them:

<!-- run -->
```kotlin
fun greet(name : String) -> Unit {
    if name == "" { return {} }
    print("hello, " + name)
}

fun main() {
    greet("world")
}
```

```text
hello, world
```

<!-- stderr -->
```text
Main.gob:2:28: warning: a bare 'return' already means 'return ()'; remove '{}' [empty-return-value]
1 warning
```

Every check has a *level*. At `allow` it is off, at `warn` its findings are
printed, and at `deny` they are printed as errors, which fail the compile.
Each check has a level of its own unless a flag sets one for the run:

* `--allow NAME`, `--warn NAME` and `--deny NAME` set the level of the check
  `NAME`. Each takes one name or a comma-separated list, and each may be given
  more than once. When several flags name one check, the last one wins.
* `--deny-warnings` makes every finding still at `warn` an error, which suits
  a build that must stay clean.

The checks run on the program's own modules only: the entry file and the
modules found beside it. Nothing is reported inside the standard library.

`turkey check --fix` writes the fixes that some checks know into the
program's own files, where the fix cannot change what the program does. It
checks the program again after each round of fixes, and then prints what is
left.

| Check | Level | What it finds |
|---|---|---|
| `empty-return-value` | warn | `return {}`, `return ()`, `break {}` and `break ()`, each of which means the bare `return` or `break`. `--fix` removes the value. |
| `ambiguous-underscore` | warn | a `fun` whose parameter is `_` and whose body uses `_` to make a function of its own ([Placeholder functions](declarations.md#placeholder-functions)), as in `fun(_) = lookup(_, table)`, where the second `_` is not the parameter |
| `unused-variable` | warn | a local binding nothing reads: a `let`, `var` or `let ... else`, a pattern of `for`, `match`, `if let`, `if var`, `while let` or `while var`, or a local `fun`. Reads in the binding's own assignments, as in `x = x + 1`, do not count. Parameters, and names that start with `_`, are not reported. `--fix` renames the binding `_name`, or writes `_` for a variable inside a pattern. |
| `unused-import` | warn | an import, or an entry of an import's list, that brings nothing the module reads or its export list passes on, such as a name a declaration of the module hides. `x as _` is not reported, and `import M ()` only when loading `M` changes nothing a program does. `--fix` removes the entry, writes an unused `x as y` beside `..` as `x as _`, and removes an import none of which is used, unless it is the last import of a module whose loading may run code, provide an instance or define a `foreign` symbol, which it writes as `import M ()`. |
| `var-never-reassigned` | warn | a local `var`, `if var`, `while var` or `var ... else` that no assignment targets, a lambda's included. Its fix writes `let` in place of `var`, except in a `for` initializer, and where another variable of the same pattern is assigned. `--fix` makes it only where the initializer is not a value a `let` could give another type, as it gives `1` a type that may be `Int` at one use and `Float` at another. |
| `loop-variable-captured` | warn | a function made in the body of a C-style `for` that captures a variable of the loop's initializer and is stored past the iteration: pushed with `Vec.push`, put with `Map.put`, spawned, assigned to an element or field, put in an array or record, or returned ([`for` with a counter](statements.md#for-with-a-counter)). The finding suggests beginning the body with `let i = i`, unless the body assigns `i`; `--fix` does not make that change, since the function then sees a different value. |
| `unread-assignment` | warn | an assignment to a local `var` whose value no path reads before the next assignment or the end of the variable's scope. A `var` that a nested function refers to, and a `var`'s initializer, are not reported. |
| `unreachable-code` | warn | a statement after one that cannot complete ([Blocks](statements.md#blocks)) |
| `repeated-condition` | warn | a test in an `if`/`else if` chain that repeats an earlier test of the chain, so its branch can never run. A test with a call in it is not compared, and neither is one after a test with a call. |
| `identical-operands` | warn | a comparison, `-`, `/`, `%`, `^`, `&&` or `\|\|` whose two operands are the same expression, with no call or indexing in it, as in `a.x == a.x`. `Float` operands are not reported. |
| `self-assignment` | warn | an assignment of a variable or a field path to itself, as in `c.count = c.count`, which changes nothing. `a[i] = a[i]` is not reported, since it can panic. |
| `impossible-comparison` | warn | two comparisons of one `Int` or `Char` with constants, joined by `&&`, that no value satisfies both of, as in `c >= 'a' && c <= 'Z'`, or joined by `\|\|`, that every value satisfies one of |
| `discarded-value` | warn | a statement that computes a value other than `()` and drops it ([Discarded values](statements.md#discarded-values)), except those `discarded-monadic-value` reports |
| `discarded-monadic-value` | warn | a statement that drops an `Option`, an `Either`, or a value of the monad of the `?` block it is in, which is most often a missing `?` ([Discarded values](statements.md#discarded-values)) |
| `unit-spelling` | warn | `{}` written where its value is used, as in `let done = {}`, and a `match` arm that does nothing spelled `()` in a statement or `{}` in a `match` whose value is used. `{}` is a body that does nothing and `()` the unit value. `--fix` writes the other spelling. |
| `detached-doc-comment` | warn | a `---` doc comment that documents nothing ([Doc comments](lexical.md#doc-comments)), such as one parted from its declaration by a blank line, one inside a body, or one after code on its line. `--fix` deletes a single blank line between a doc comment and a declaration. |
| `needless-equality` | warn | an equality in a function's context, such as `Item c ~ a`, whose variable is in no other entry and is written at most once in the signature and nowhere in the body, so that it only names `Item c`. `--fix` removes the entry and writes `Item c` in its place. |
| `ord-inconsistent-with-eq` | warn | an `instance Ord` written by hand for a type whose `Eq` is derived, which can make `<=` hold in neither direction ([Derived instances](classes.md#derived-instances)) |
| `unfulfilled-expect` | warn | a check that an `--@expect` names and that finds nothing in what the directive covers ([Expecting a finding](#expecting-a-finding)) |

### Expecting a finding

```ebnf
DIRECTIVE ::= "--@" NAME "(" ARGUMENTS ")" REASON?
EXPECT    ::= "--@expect(" SPACE* CHECK SPACE* ("," SPACE* CHECK SPACE*)* ")" REASON?
REASON    ::= SPACE TEXT
SPACE     ::= " " | "\t"
```

A line comment that begins `--@` is a *directive*: an instruction to `turkey`,
not prose. The directive's name follows `--@` directly, and `(` follows the
name directly. The arguments run to the first `)`. Anything after it must
start with a space, and is free text: the reason for the directive. A comment
that begins `--@` and is not a well-formed directive is an error, in any
module. The one directive is `expect`.

`--@expect(NAME, ...)` names one or more checks, separated by commas, with
spaces allowed around each. It says that each check reports a finding in the
code the directive covers, and those findings are neither printed nor fixed
by `--fix`:

* On a line of its own, a directive covers the whole of the import,
  statement, declaration or `match` arm that begins next, every line of it included,
  provided it begins inside the block, type, call or other construct the
  directive is in. If nothing begins there, as at the end of a block, the
  directive covers nothing, and warns. A doc comment can be directly above the
  directive and still document the declaration below it
  ([Doc comments](lexical.md#doc-comments)).
* After code, a directive covers the findings that begin on its line.

Each name must be a check's, and may be named once. A check that the level
flags set to `allow` still runs where an `expect` names it.

<!-- run -->
```kotlin
fun lookup(key : Int, table : Map Int String) -> String = Map.getOr(table, key, "?")

--@expect(ambiguous-underscore) each key becomes a function of the key to look up
fun lookups(keys : Vec Int, table : Map Int String) -> Vec (fun(Int) -> String) =
    Vec.map(keys, fun(_) = lookup(_, table))

fun visit(done : Bool) -> Unit {
    if done { return () } --@expect(empty-return-value) the value matches the signature
    print("visiting")
}

fun main() {
    let table = Map.new()
    Map.put(table, 1, "one")
    for f in lookups([7], table) { print(f(1)) }
    visit(False)
}
```

```text
one
visiting
```

An expectation that does not hold is a finding too. For each name that has no
finding of its check in what the directive covers, the check
`unfulfilled-expect` warns at the name, so a directive does not outlive the
code it excused. Where two directives cover one finding and both name its
check, only the inner one is met by it. An `expect` cannot name
`unfulfilled-expect`.

<!-- run -->
```kotlin
--@expect(unreachable-code) the early return for debugging was here
fun main() {
    print("all reachable")
}
```

```text
all reachable
```

<!-- stderr -->
```text
Main.gob:1:11: warning: 'unreachable-code' finds nothing in what this expect covers; remove it from the expect [unfulfilled-expect]
1 warning
```

<!-- error: a directive's name must follow '--@' directly; write '--@expect' -->
```kotlin
--@ expect(unreachable-code)
fun main() {
    print("never compiled")
}
```

## Formatting

`turkey format` rewrites each file it is given in the one style Turkey has,
which takes no options. `turkey format --check` writes nothing: it prints the
name of each file it would change, and exits with status 1 if there is one. A
file that cannot be read, parsed or written is reported, the other files are
done anyway, and the exit status is 3. A file the formatter refuses, because its
output would not be the same program, is reported as an internal error, with
status 2, and a command line `turkey format` cannot read exits with status 4.

```sh
turkey format hello.gob
turkey format --check *.gob
turkey format -- -odd-name.gob
```

As for the other commands, `--` ends the options, so every argument after it
is a file, even one whose name starts with `-`. The exception is `-` itself,
which means standard input there too, as below.

`turkey format -` formats the text on standard input instead and writes it to
standard output, for an editor to run on a buffer. It checks the text as it
checks a file, and exits with the same statuses: 2 if the formatter refuses it,
3 if it cannot be read or does not parse, and 4 for a command line it cannot
read. On any of those it writes nothing to standard output. With `--check` it
writes nothing either, and exits with status 1 if the text would change.
Diagnostics call the input `<stdin>`, or the name given with
`--stdin-name NAME`. `-` cannot be given with files, so a file named `-` is
written `./-`.

```sh
turkey format - < hello.gob > formatted.gob
turkey format --check --stdin-name hello.gob - < hello.gob
```

The formatter changes only what leaves the program as it is: line breaks,
indentation, spaces, blank lines, the commas of a list, `;`, and the `=` before
a block body, so `fun f() = { ... }` becomes `fun f() { ... }`. It never adds
or removes parentheses, never spells a literal another way, and never changes
a comment's text, except to drop trailing spaces. Comments keep their order:
one on a line of its own stays on one, one at the end of a line stays at the
end of one, and a block comment inside a line stays inside it. A doc comment
documents what it documented before. Comments do not count toward the width:
a line holding one may run past it, since moving code to the next line would
not make the comment fit.

A few choices are left to the author, because the source shows them:

* A line break right after a `(`, `[` or `{` keeps that list, record or block
  on one line per item, even when it would fit on one. Delete the line break
  to let it join up again.
* A line break before a data type's `=` keeps its constructors on one line
  each.
* A directive at the end of a line, such as `--@expect(...)`, covers that
  line, so the formatter keeps the code on that line exactly as it is. A
  statement written on one line stays on one line, however long. A statement
  written over several lines with such a directive on any of them, after a
  `{` or anywhere else, is kept as written, moved only by the change in its
  first line's indent. A function whose body opens with one keeps its
  signature as written, and its body is formatted.

## For maintainers

**Keeping it true.** Examples are checked mechanically. Each `kotlin` fence
must be preceded by an HTML comment that says how to check it:

| Directive | Meaning |
|---|---|
| `<!-- run -->` | A whole program. The next `text` fence is its exact standard output. |
| `<!-- check -->` | Must compile. |
| `<!-- error: TEXT -->` | Must be rejected, and the error message must contain `TEXT`. |
| `<!-- panic: TEXT -->` | Must compile and then panic with a message containing `TEXT`. An optional `text` fence gives the output printed before the panic. |
| `<!-- stderr -->` | The next `text` fence is the exact standard error of the `run` or `panic` example before it: the compile's warnings, then the panic and its trace. |
| `<!-- module: Name.gob -->` | Another source file for the next example, written beside it. The example itself is `Main.gob`. |

Every example is compiled and run by `turkey`, the way a reader who copies one
would. A `kotlin` fence with no directive fails the test. Grammar goes in `ebnf`
fences and shell commands in `sh` fences; the test ignores both.

**What goes in.** Only what the compiler accepts today. The reference does not
mention planned features, reserved syntax, or open proposals; those belong in
tix tickets, and so does a proposal that was rejected, closed with the reason.
When the language changes, change this reference in the same commit, and say
why in the commit message.

**Why it is shaped this way.** These are the references this one borrows
from, and the one that chose differently.

* The **Python Language Reference** is separate from both the tutorial and the
  Library Reference, and its "Data model" chapter covers the hooks that
  operators and `for` call into. That is the same scope as this reference, and
  [Built-in types and classes](builtins.md) plays the part of the data-model
  chapter.
* The **Haskell 2010 Report** gives the meaning of each piece of syntactic
  sugar by translating it into a smaller core language. This reference
  explains `for`, `if let`, `while let`, `let ... else`, array literals and
  `?` the same way, except that it translates into ordinary Turkey rather than
  a separate core language.
* The **Rust Reference** puts a small syntax block in each section, and it
  tests its examples, including examples that are meant to be rejected. This
  reference does both: the syntax sits next to the construct it describes, and
  [Grammar](grammar.md) collects all of it in one place.
* The **Go specification** is the counterexample: one normative file, with no
  chapters. It can be, because Go has no type classes, type families or
  inference to explain. Turkey has all three, and a reader looking up one of
  them should not have to scroll past the other two.
