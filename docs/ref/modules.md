# Modules

A Turkey program is a set of modules, one per source file. A module decides
which of its names other modules may use, and imports the names it needs from
other modules. This chapter covers export declarations, imports,
how names are resolved, and the entry point.

## Modules and files

Each `.gob` file is one module, and a module's name is its path: the file
`Shapes/Circle.gob` is the module `Shapes.Circle`, and `import Shapes.Circle`
loads it. The compiler looks for the file first relative to the directory of
the program's entry file, then in the standard library: the directory
`$TURKEY_LIB` names, or without it the nearest `lib` directory at or above the
compiler's own executable. The working directory is not searched. Moving a file renames
the module, and every import of it has to change with it.

The path is spelled exactly as the name is, letter case included, even where
the filesystem ignores case, as macOS's usually does. A file whose name
differs only in case is not the module, so a program finds the same modules on
every system:

<!-- module: Shapes/circle.gob -->
```kotlin
export (area)

fun area(r : Int) -> Int = 3 * r * r
```

<!-- error: cannot find module 'Shapes.Circle': 'Shapes/circle.gob' differs from its name only in case -->
```kotlin
import Shapes.Circle (area)

fun main() {
    print(area(2))
}
```

The file the compiler is given is the **entry module**, and
it must define [`main`](#the-entry-point). The entry module is called `Main`,
whatever its file is called, and no module may import it: neither as `Main`
nor by the name its file would have as an ordinary module: beside an entry
`Tool.gob`, `import Tool` is an error. So each file of a program is exactly
one module, with one name.

<!-- error: 'Main' is the entry module, and the entry cannot be imported -->
```kotlin
import Main

fun main() {
    print("hello")
}
```

> **Coming from Haskell:** a file's path is its module's whole name, so there
> is no `module Shapes.Circle where` to keep in step with it, and the entry
> module is `Main` without saying so.

The standard library's `Turkey.*` modules are the implementation itself -- the
collector, the allocator, the program's startup -- and a program may not import
them. Only other standard library modules can. A program's own modules are
its own, whatever they are called: `Turkey/Shapes.gob` beside the entry file is
an ordinary module.

<!-- error: 'Turkey.Heap' is part of the implementation, and only the standard library may import it -->
```kotlin
import Turkey.Heap as Heap

fun main() {
    print(Heap.heapObjects())
}
```

Modules may import each other, directly or through other modules. Modules
that import each other in a cycle are checked together, so a function in one
may call a function in another, and a type in one may mention a type in
another, in either direction:

<!-- module: Even.gob -->
```kotlin
export (isEven)
import Odd (isOdd)

fun isEven(n : Int) -> Bool = if n == 0 { True } else { isOdd(n - 1) }
```

<!-- module: Odd.gob -->
```kotlin
export (isOdd)
import Even (isEven)

fun isOdd(n : Int) -> Bool = if n == 0 { False } else { isEven(n - 1) }
```

<!-- run -->
```kotlin
import Even (isEven)

fun main() {
    print(isEven(10))
}
```

```text
True
```

Each module in a cycle still sees only what it imports: a type imported without
its constructors is [abstract](#export-declarations) there, whichever module
declares it.

Top-level bindings are initialized in [dependency
order](declarations.md#top-level-bindings) across the whole cycle, the same way as
within one module. So values in different modules may not depend on each other
in a cycle either:

<!-- module: Width.gob -->
```kotlin
export (width)
import Height (height)

let width = height * 2
```

<!-- module: Height.gob -->
```kotlin
export (height)
import Width (width)

let height = width / 2
```

<!-- error: only functions can be mutually recursive -->
```kotlin
import Width (width)

fun main() {
    print(width)
}
```

A name a module exports must be declared somewhere. Two modules that each export
a name as the other's, without either declaring it, export nothing by that
name.

## Export declarations

**Syntax**

```ebnf
export-decl ::= "export" "(" ".." ")"
              | "export" "(" (export ("," export)*)? ")"
modname     ::= CONID ("." CONID)*
export      ::= IDENT                          -- a function or value
              | CONID                          -- a type without its constructors, or a class
              | CONID "(" ".." ")"             -- a type with all its constructors
              | CONID "(" CONID ("," CONID)* ")"
              | "(" OPERATOR ")"                -- a declared operator
```

A module that other modules import starts with an export declaration, which
lists what it **exports**. Only exported names can be used from other modules,
whether bare or [qualified](#qualified-names). `export (..)` exports everything
the module itself declares, though not what it imports, and `export ()` exports
nothing.

The declaration comes first in the file, before any import, and it does not
name the module: its [path](#modules-and-files) does.

<!-- module: Inventory.gob -->
```kotlin
export (Item, restock, describe)

type Item = Item { name : String, count : Int }

fun restock(name, count) = Item { name, count = clamp(count) }

fun describe(item : Item) = item.name + " x" + show(item.count)

fun clamp(n) = if n < 0 { 0 } else { n }
```

<!-- run -->
```kotlin
import Inventory (restock, describe)

fun main() {
    print(describe(restock("bolts", 12)))
}
```

```text
bolts x12
```

`clamp` is not exported, so `Main` cannot call it, neither as `clamp` nor as
`Inventory.clamp`:

<!-- module: Inventory.gob -->
```kotlin
export (Item, restock, describe)

type Item = Item { name : String, count : Int }

fun restock(name, count) = Item { name, count = clamp(count) }

fun describe(item : Item) = item.name + " x" + show(item.count)

fun clamp(n) = if n < 0 { 0 } else { n }
```

<!-- error: 'Inventory.clamp' is not defined -->
```kotlin
import Inventory

fun main() {
    print(Inventory.clamp(-1))
}
```

Only the [entry module](#modules-and-files) may leave the declaration out,
since nothing can import it. Importing a module that has none is an error:

<!-- module: Scratch.gob -->
```kotlin
fun helper() -> Int = 1
```

<!-- error: module 'Scratch' is imported but has no export declaration -->
```kotlin
import Scratch

fun main() {
    print(helper())
}
```

`export (..)` is the shortest declaration that makes it importable:

<!-- module: Scratch.gob -->
```kotlin
export (..)

fun helper() -> Int = 1
```

<!-- run -->
```kotlin
import Scratch

fun main() {
    print(Scratch.helper())
}
```

```text
1
```

> **Coming from Haskell:** a module with no export list exports everything
> in Haskell. Here every module that is imported says what it exports, and
> `export (..)` is how it says "everything".
>
> **Coming from Rust:** a declaration is private unless the export list names
> it, much as an item is private without `pub`; the list is in one place at
> the top of the file rather than on each item.

**Types.** Exporting `T` exports the type but not its constructors. Other
modules can then use `T` in types and receive and pass `T` values, but cannot
build one with a constructor, take one apart with a pattern, or read or assign
its record fields. Field access requires the constructor to be in scope,
including through a qualified import. This is how a
module makes a type *abstract*, so that only its own functions can create
values of it. `T(..)` exports the type and all its constructors.

<!-- module: Sealed.gob -->
```kotlin
export (Token, make)
type Token = Token { value : Int }
fun make(n : Int) -> Token = Token { value = n }
```

<!-- error: cannot access field 'value' of abstract type 'Token' -->
```kotlin
import Sealed
fun reveal() = Sealed.make(3).value
```

<!-- module: Sealed.gob -->
```kotlin
export (Token, make)
type Token = Token { value : Int }
fun make(n : Int) -> Token = Token { value = n }
```

<!-- error: does not export constructor 'Token' of type 'Token' -->
```kotlin
import Sealed (Token(..))
```

**Classes.** Exporting `C(..)` exports a class and its methods. Exporting `C`
alone exports the class without its methods, so other modules can name it in
constraints but cannot call its methods.

An export list names declarations, not modules: a module cannot pass on
another module's qualifier, and a module that wants `Shapes.Circle.area`
imports `Shapes.Circle` itself.

## Imports

**Syntax**

```ebnf
import ::= "import" modname ("as" CONID)? import-list?
import-list ::= "(" (entry ("," entry)*)? ")"
entry  ::= item ("as" (IDENT | CONID | "_"))? | ".."
item   ::= IDENT | CONID | CONID "(" ".." ")"
         | CONID "(" CONID ("," CONID)* ")"
         | "(" OPERATOR ")"
```

| Import | Brings into scope |
|---|---|
| `import Geometry` | every export, qualified only: `Geometry.area`, not `area` |
| `import Geometry as G` | every export, qualified as `G.area` |
| `import Geometry (area, Point(..))` | the listed names bare, and every export qualified |
| `import Geometry (..)` | every export, both bare and qualified |
| `import Geometry (area as size)` | `area` under the bare name `size`, and every export qualified |
| `import Geometry (area as _, ..)` | every export bare except `area`, and every export qualified |
| `import Geometry as G (area)` | `area` bare, and every export qualified as `G.area` |
| `import Geometry ()` | the same as `import Geometry`: no bare names, every export qualified |

An import always brings the module's qualified names, `Geometry.area`, or
`G.area` under an alias, and they are every export the module has. An alias
replaces the module's name as the qualifier rather than adding to it, so
after `import Geometry as G` there is no `Geometry.area`. The list chooses only
which bare names come too. Every import also loads the module, and its
instances with it.

`..` in a list stands for every export the other entries do not name, and may
appear once. Beside it an entry only renames (`x as y`) or withholds
(`x as _`): `import Geometry (area, ..)` is an error, since `..` already
brings `area`. A rename keeps a name's case, so a value is renamed to a
lowercase name and a type, class or constructor to a capitalized one.
`Point(..) as _` withholds the type and its constructors together, and
`C(..) as _` a class and its methods; `Point as _` withholds the type alone.

Renaming and withholding are for bare names that collide. Here two modules
both export `parse`, and the second import withholds its `parse`, which is
then written `Csv.parse`:

<!-- module: Json.gob -->
```kotlin
export (Value(..), parse)

type Value = Number(Int) | Text(String)

fun parse(s : String) -> Value = match Int.parse(s) {
    Some(n) -> Number(n)
    None -> Text(s)
}
```

<!-- module: Csv.gob -->
```kotlin
export (parse, width)

fun parse(line : String) -> Vec String = String.split(line, ",")

fun width(row : Vec String) -> Int = len(row)
```

<!-- run -->
```kotlin
import Json (..)
import Csv (parse as _, ..)

fun describe(v : Value) -> String = match v {
    Number(n) -> "number " + show(n)
    Text(s) -> "text " + s
}

fun main() {
    print(describe(parse("42")))
    print(width(Csv.parse("a,b,c")))
}
```

```text
number 42
3
```

`import Csv (parse as parseRow, ..)` would bring it bare as `parseRow`
instead.

In a selective import, `T` brings in the type alone, `T(..)` requests its
constructors too, and `T(A, B)` requests the named constructors. Each requested
constructor must be exported by the imported module and belong to `T`. A type
and a constructor can share a spelling; importing the type alone does not
import that constructor.

<!-- module: Temperature.gob -->
```kotlin
export (toFahrenheit, toCelsius)

fun toFahrenheit(c) = c * 9.0 / 5.0 + 32.0

fun toCelsius(f) = (f - 32.0) * 5.0 / 9.0
```

<!-- run -->
```kotlin
import Temperature as T

fun main() {
    print(T.toFahrenheit(100.0))
    print(T.toCelsius(212.0))
}
```

```text
212.0
100.0
```

Instances are not imported or exported. Every instance in the program is
available everywhere ([Coherence](classes.md#coherence)).

### Operators in imports

A [declared operator](expressions.md#declared-operators) is exported by
writing it in parentheses, `export ((</>))`, and `export (..)` exports every
operator the module declares. The operator's target does not have to be
exported too: a use of the operator calls it all the same.

An operator has no qualified spelling, so an import that does not name it
does not bring it. It comes in only through an import list, as `(</>)` or
with `..`, and `(</>) as _` beside `..` withholds it. It cannot be renamed; a
module that wants another spelling declares its own operator with `infix`.
The Prelude's operators, such as `<>` and `|>`, come in with the implicit
Prelude import like its other names.

<!-- module: Path.gob -->
```kotlin
export ((</>))

infix Combine (</>) = joinPath

fun joinPath(dir : String, name : String) -> String = dir + "/" + name
```

<!-- run -->
```kotlin
import Path ((</>))

fun main() {
    print("usr" </> "local" </> "bin")
}
```

```text
usr/local/bin
```

Without the list, `</>` is not in scope:

<!-- module: Path.gob -->
```kotlin
export ((</>))

infix Combine (</>) = joinPath

fun joinPath(dir : String, name : String) -> String = dir + "/" + name
```

<!-- error: the operator '</>' is not imported; 'Path' exports it -->
```kotlin
import Path

fun main() {
    print("usr" </> "bin")
}
```

An operator follows the [name resolution](#name-resolution) rules for bare
names: the module's own `infix` declaration shadows an imported one, and two
imports that bring distinct declarations of one operator make every use of it
an error. The compiler does not pick one by the operands' types, since that
would also pick a precedence group. An operator a module passes on with its
export list is the same declaration, and importing it by both routes is not
ambiguous.

### Qualified names

A name from another module can be written with that module's name in front:
`Temperature.toCelsius`, or with the alias from `as`: `T.toCelsius`. A module
name with dots is written in full: `Shapes.Circle.area`.

Qualified names work for functions, for types (`T.Token` in a type), and for
constructors in expressions and [patterns](patterns.md#constructor-patterns)
alike: `G.Point(1, 2)` builds a point and `G.Point(x, y)` takes one apart.

<!-- module: Geometry.gob -->
```kotlin
export (Point(..), origin)

type Point = Point(Int, Int)

fun origin() = Point(0, 0)
```

<!-- run -->
```kotlin
import Geometry as G

fun distanceFromOrigin(p) {
    let G.Point(x, y) = p
    x + y
}

fun main() {
    print(distanceFromOrigin(G.Point(3, 4)))
    print(distanceFromOrigin(G.origin()))
}
```

```text
7
0
```

### The Prelude

Every module imports the Prelude implicitly, with its bare names and the
library modules it brings qualified, such as `Vec` and `String`. An explicit
`import Prelude ...` replaces the implicit import of its bare names, and
`import Prelude ()` imports nothing from it at all, the qualified modules
included, where `()` after any other module still brings its qualifier. See [The Prelude](builtins.md#the-prelude) for what it provides.

<!-- run -->
```kotlin
import Prelude ()
import System.IO (print)

fun main() {
    print("only print is in scope")
}
```

```text
only print is in scope
```

### The Target module

The library module `Target` says which platform the program is being compiled
for. `Target.os` is the operating system, of type `OS`, and `Target.arch` the
processor architecture, of type `Arch`. Each type lists only the targets the
compiler supports, which today are arm64 macOS and arm64 Linux: `OS` has the
constructors `Darwin` and `Linux`, and `Arch` the single constructor `Arm64`.
The compiler's `--target` option chooses among them. Its value is spelled
`arch-os`, `arm64-darwin` or `arm64-linux`, and the default is the platform the
compiler itself runs on:

```sh
boot build --target arm64-linux -o main.s main.gob
```

Code that differs by platform is an ordinary `match`. The signal for a bus
error is 10 on macOS and 7 on Linux, so `sigbus()` is one or the other
depending on the target, and the program prints the same on both:

<!-- run -->
```kotlin
import Target (OS(..))
import Target as Target

fun sigbus() -> Int = match Target.os {
    Darwin -> 10
    Linux -> 7
}

fun describe(signal : Int) -> String =
    if signal == sigbus() { "bus error" } else { "signal " + Int.toString(signal) }

fun main() {
    print(describe(sigbus()))
    print(describe(2))
}
```

```text
bus error
signal 2
```

Two rules make this the way to write such code:

* **Every arm is checked, on every build.** The arms for other targets are
  resolved and type-checked like any others, and when a target is added to
  `OS`, every `match Target.os` that does not handle it becomes a
  [non-exhaustive match](patterns.md#exhaustiveness) error. The compiler lists
  every place a new target has to be taught about.
* **Only the chosen target's arm is compiled.** `Target.os` and `Target.arch`
  are known while compiling, so a `match` on either keeps only the arm that
  matches. An arm for another target may call a
  [`foreign`](declarations.md#foreign-functions) function that does not
  exist on this one, and the program still links.

Exhaustiveness treats `OS` and `Arch` like any other type, so a `match` that
forgets a target is an error whichever target is being compiled for:

<!-- error: this match is not exhaustive; 'Linux' is not handled -->
```kotlin
import Target (OS(..))
import Target as Target

fun sigbus() -> Int = match Target.os {
    Darwin -> 10
}

fun main() {
    print(sigbus())
}
```

> **Coming from Rust:** `#[cfg(target_os = "linux")]` removes the item before
> it is checked, so code for another platform can be broken without anyone on
> this one noticing. Nothing here is removed before checking.
>
> **Coming from Zig:** Zig skips analysis of the branch not taken on
> `builtin.os.tag`. Here the branch not taken is analyzed and then not
> compiled.

## Name resolution

Within a module, a name is looked up in this order, and the first match wins:

1. local variables, from the innermost scope outward;
2. the module's own top-level declarations;
3. names brought in by imports;
4. names from the Prelude.

So a module's own declaration **shadows** an imported name of the same
spelling, and an import shadows the Prelude. A module can define its own
`print` or `map`, and its own type called `Option`; the operators, `for`
loops, indexing and `?` still use the Prelude's classes and types.

<!-- run -->
```kotlin
fun print(message : String) -> Unit = write(">> " + message + "\n")

fun main() {
    print("shadowed")
}
```

```text
>> shadowed
```

When two explicit imports provide different declarations with the same bare
name, using that name is an ambiguity error naming both declarations. Import
order does not choose one. Use distinct qualified spellings to select one; a
local binding or a declaration in the current module can still shadow both.
Importing the same declaration through more than one route is unambiguous.

<!-- module: First.gob -->
```kotlin
export (label)
fun label() -> String = "first"
```

<!-- module: Second.gob -->
```kotlin
export (label)
fun label() -> String = "second"
```

<!-- error: 'label' is ambiguous: it is imported as 'First.label' and 'Second.label' -->
```kotlin
import First (label)
import Second (label)
fun main() { print(label()) }
```

## The entry point

A program starts by initializing the entry module's top-level bindings, and
those of every module it imports (see [Top-level bindings](declarations.md#top-level-bindings)),
and then calls `main`, which must be a function of type `fun() -> Unit`
declared in the entry module. Any other type is an error at `main`'s
definition:

<!-- error: 'main' has type 'fun() -> String', and the entry point must have type 'fun() -> Unit' -->
```kotlin
fun main() = "hello"
```

A `main` whose body only panics, such as `fun main() = error("todo")`, is
accepted, since a call to `error` fits the type `Unit`.

The program's command-line arguments are available from the library as
`System.Env.args()`, and `System.Env.exit(status)` ends the program with an
exit status ([Exit status](runtime-errors.md#exit-status)).

<!-- run -->
```kotlin
import System.Env

fun main() {
    let arguments = System.Env.args()
    print(len(arguments))
}
```

```text
0
```
