# Modules

A Turkey program is a set of modules, one per source file. A module decides
which of its names other modules may use, and imports the names it needs from
other modules. This chapter covers module headers and export lists, imports,
how names are resolved, and the entry point.

## Modules and files

Each `.gob` file is one module. A module is found by its path: `import
Shapes.Circle` loads the file `Shapes/Circle.gob`. The compiler looks for it
first relative to the directory of the program's entry file, then in the
standard library.

The file the compiler is given is the **entry module**, and
it must define [`main`](#the-entry-point).

Modules may not import each other in a cycle. If two modules need each other,
they belong in one module.

## Module headers

**Syntax**

```ebnf
header      ::= "module" modname ("(" export ("," export)* ")")?
modname     ::= CONID ("." CONID)*
export      ::= IDENT                          -- a function or value
              | CONID                          -- a type without its constructors, or a class
              | CONID "(" ".." ")"             -- a type with all its constructors
              | CONID "(" CONID ("," CONID)* ")"
              | "module" modname               -- a re-export
```

A module may start with a header naming the module and listing what it
**exports**. Only exported names can be used from other modules. A module
without a header, or with a header but no list, exports everything it
declares.

<!-- module: Inventory.gob -->
```kotlin
module Inventory (Item, restock, describe)

type Item = Item { name : String, count : Int }

fun restock(name, count) = Item { name, count = clamp(count) }

fun describe(item) = item.name + " x" + show(item.count)

fun clamp(n) = if n < 0 { 0 } else { n }
```

<!-- run -->
```kotlin
import Inventory

fun main() {
    print(describe(restock("bolts", 12)))
}
```

```text
bolts x12
```

`clamp` is not exported, so `Main` cannot call it:

<!-- module: Inventory.gob -->
```kotlin
module Inventory (Item, restock, describe)

type Item = Item { name : String, count : Int }

fun restock(name, count) = Item { name, count = clamp(count) }

fun describe(item) = item.name + " x" + show(item.count)

fun clamp(n) = if n < 0 { 0 } else { n }
```

<!-- error: 'clamp' is not defined -->
```kotlin
import Inventory

fun main() {
    print(clamp(-1))
}
```

**Types.** Exporting `T` exports the type but not its constructors. Other
modules can then use `T` in types and receive and pass `T` values, but cannot
build one with a constructor or take one apart with a pattern. This is how a
module makes a type *abstract*, so that only its own functions can create
values of it. `T(..)` exports the type and all its constructors.

**Classes.** Exporting `C(..)` exports a class and its methods. Exporting `C`
alone exports the class without its methods, so other modules can name it in
constraints but cannot call its methods.

**Re-exports.** `module M` in an export list passes on everything the module
has in scope under the qualifier `M`, still qualified. The Prelude uses this
to make `Array.push`, `Int.parse` and the like available everywhere
([The Prelude](builtins.md#the-prelude)).

## Imports

**Syntax**

```ebnf
import ::= "import" modname ("as" CONID)? import-list?
import-list ::= "(" item ("," item)* ")"
              | "hiding" "(" item ("," item)* ")"
item   ::= IDENT | CONID | CONID "(" ".." ")"
```

| Import | Brings into scope |
|---|---|
| `import Geometry` | every export of `Geometry`, both bare (`area`) and qualified (`Geometry.area`) |
| `import Geometry (area, Point(..))` | only the listed names, both bare and qualified |
| `import Geometry hiding (area)` | every export except the listed ones |
| `import Geometry as G` | every export, qualified only: `G.area`, not `area` |
| `import Geometry as G (area)` | only the listed names, qualified only |

<!-- module: Temperature.gob -->
```kotlin
module Temperature (toFahrenheit, toCelsius)

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

### Qualified names

A name from another module can be written with that module's name in front:
`Temperature.toCelsius`, or with the alias from `as`: `T.toCelsius`. A module
name with dots is written in full: `Shapes.Circle.area`.

Qualified names work for functions, for types (`T.Token` in a type), and for
constructors in expressions and [patterns](patterns.md#constructor-patterns)
alike: `G.Point(1, 2)` builds a point and `G.Point(x, y)` takes one apart.

<!-- module: Geometry.gob -->
```kotlin
module Geometry (Point(..), origin)

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

Every module imports the Prelude implicitly. An explicit `import Prelude ...`
replaces the implicit import. See [The Prelude](builtins.md#the-prelude) for
what it provides.

### The Target module

The library module `Target` says which platform the program is being compiled
for. `Target.os` is the operating system, of type `OS`, and `Target.arch` the
processor architecture, of type `Arch`. Each type lists only the targets the
compiler supports, which today is one: arm64 macOS, so `OS` has the single
constructor `Darwin` and `Arch` the single constructor `Arm64`. The compiler's `--target` option chooses among them; its value is
spelled `arch-os`, and `arm64-darwin` is the default:

```sh
boot native --target arm64-darwin main.gob > main.s
```

Code that differs by platform is an ordinary `match`:

<!-- run -->
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

```text
10
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

Exhaustiveness treats `OS` and `Arch` like any other type:

<!-- error: this match is not exhaustive; '(Darwin, True)' is not handled -->
```kotlin
import Target (OS(..))
import Target as Target

fun sigbus(fallback : Bool) -> Int = match (Target.os, fallback) {
    (Darwin, False) -> 10
}

fun main() {
    print(sigbus(False))
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

When two imports provide the same bare name, the later import shadows the
earlier one. The qualified spellings stay distinct, and are the clearer way to
use either.

## The entry point

A program starts by initializing the entry module's top-level bindings, and
those of every module it imports (see [Top-level bindings](declarations.md#top-level-bindings)),
and then calls `main`, which must be a function of type `fun() -> Unit`
declared in the entry module. Any other type is an error at `main`'s
definition:

<!-- error: 'main' is the entry point and must be fun() -> Unit, but this one is fun() -> String -->
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
