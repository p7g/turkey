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
Output written before the panic stays written.

<!-- panic: array index out of bounds -->
```kotlin
fun lastScore(scores) = scores[len(scores)]

fun main() {
    print("checking")
    print(lastScore([90, 75]))
}
```

```text
checking
```

prints, on standard error:

```text
panic: array index out of bounds: read at index 2, length 2
  at outOfBounds (Data/Array.gob:85:5)
  at main (main.gob:5:11)
```

Each line gives a function and the position of the call or operation that was
running in it. The list is a guide rather than a complete call stack: a
function the compiler has inlined into its caller does not appear, and a line
may point at the caller instead.

## What panics

| Cause | Message |
|---|---|
| `Int` arithmetic overflows (`+`, `-`, `*`, unary `-`) | `integer overflow in +` (and so on) |
| `Int` division or remainder by zero | `division by zero`, `remainder by zero` |
| An array is indexed outside its bounds | `array index out of bounds: ...` |
| A map is indexed with a key it does not contain | `No such key` |
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
panic message.
