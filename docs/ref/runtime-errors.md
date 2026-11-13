# Runtime errors

Most mistakes in a Turkey program are caught when it is compiled. The ones
that can only be detected while it runs stop the program with a **panic**.
This chapter lists what panics and what a panic does. Errors that a program
is expected to handle are ordinary values, usually `Option` or `Either`,
moved around with [`?`](do-notation.md), and are not panics.

## What a panic does

A panic stops the whole program immediately. Nothing runs after it: there is
no unwinding, no handler, and no way to catch a panic from Turkey code. The
program writes `panic: ` and a message to standard error, followed by the
function calls that were active, innermost first, and exits with status 1.
Output printed before the panic is written out before the panic's message,
including output still waiting in standard output's buffer
([When standard output is written](builtins.md#when-standard-output-is-written)).

<!-- panic: there are no scores -->
```kotlin
fun lastScore(scores : Array Int) -> Int {
    if len(scores) == 0 {
        error("there are no scores")
    }
    scores[len(scores) - 1]
}

fun main() {
    print("checking")
    print(lastScore([]))
}
```

```text
checking
```

prints, on standard error:

<!-- stderr -->
```text
panic: there are no scores
  at error (Control/Panic.gob:8:36)
  at lastScore (Main.gob:3:9)
  at main (Main.gob:10:11)
```

### The trace

Each line after the message is one call that was active when the program
panicked, innermost first: the function, then the file, line and column of
the call or operation it had reached. Reading down from the top, the first
line in one of the program's own files is usually the place to look. Here
that is line 3 of `Main.gob`, the call to `error`, which `main` reached
through its call on line 10.

* **Files.** A file of the program is named by its path from the directory
  of the file the program was built from. A library file is named by its path
  in the library, as `Control/Panic.gob` is above, so the same program prints
  the same trace wherever it is built.
* **Functions.** A function is named without its module. A method of an
  instance is named with its class and type, as in `Index(Array(Int)).get`
  for indexing an array, and an anonymous function by the function it is
  written in, as in `main.<lambda0>`.
* **Library calls are listed too.** Most panics are raised inside the
  library, so a trace usually starts with a line or more of library
  functions, as this one starts with `error`.
* **Not every call has a line.** A function that ends in a
  [tail call](declarations.md#functions) is no longer active once the call is
  made, so it is not in the trace. A call the compiler has replaced with the
  called function's body usually keeps its line, but a call to a function
  that was passed as an argument can lose it: the lines of an anonymous
  function's body are then listed under the function it is written in.
* **A deep trace is shortened.** When more than 64 calls are active, the
  trace has the innermost 48 and the outermost 16, with a line between them
  such as `  ... 939 more calls`.

## What panics

| Cause | Message |
|---|---|
| `Int` arithmetic overflows (`+`, `-`, `*`, unary `-`) | `integer overflow in +` (and so on) |
| `Int` division or remainder by zero | `division by zero`, `remainder by zero` |
| An array is indexed outside its bounds | `array index out of bounds: ...` |
| A map is indexed with a key it does not contain | `map has no entry for this key; ...` |
| A call to `error(message)` | the message |

<!-- panic: integer overflow in + -->
```kotlin
fun main() {
    let biggest = Int.maxValue()
    print(biggest + 1)
}
```

<!-- panic: division by zero -->
```kotlin
fun average(xs : Array Int) -> Int {
    var sum = 0
    for x in xs {
        sum = sum + x
    }
    sum / len(xs)
}

fun main() {
    print(average([]))
}
```

Library functions may panic as well, when called outside what they are
defined for; each one says so where it is defined.

A few things that panic in other languages do not panic in Turkey:

* **`Float` arithmetic** never panics. Dividing by zero gives an infinity or
  NaN ([Float](types.md#float)).
* **A `match` never fails at run time**, because the compiler rejects a
  `match` that does not cover every case ([Exhaustiveness](patterns.md#exhaustiveness)).
  Neither does a `let` or a parameter, since their patterns must be
  irrefutable.
* **A value is never of the wrong type or missing.** There is no null, and
  there are no unchecked casts.

## `error`

`error(message)` panics with the given message. Its type is
`fun(String) -> a`, so a call to it fits wherever any type is expected, which
makes it convenient for cases that a program's logic rules out:

<!-- panic: no route from A to Z -->
```kotlin
fun route(from, to) = match (from, to) {
    ("A", "B") -> 5
    ("B", "C") -> 3
    _ -> error("no route from " + from + " to " + to)
}

fun main() {
    print(route("A", "B"))
    print(route("A", "Z"))
}
```

```text
5
```

For a failure the caller should handle, return an `Option` or an `Either`
instead.

## Exit status

A program that returns from `main` normally exits with status 0, and one that
panics exits with status 1. To exit with another status, call
`System.Env.exit(status)` from the library module `System.Env`
([Modules](modules.md#imports)); it stops the program at once, without a
panic message, after writing out what is waiting in standard output's
buffer.
