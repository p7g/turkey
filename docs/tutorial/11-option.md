# Option, Either and `!`

Turkey has no null and no exceptions. An optional value is an `Option`, and
the result of an operation that can fail is an `Either`. Both are ordinary
variant types. They are declared in the Prelude, the module that every program
imports automatically:

```text
type Option a = None | Some(a)
type Either l r = Left(l) | Right(r)
```

`Option` and `Either` are generic: `a`, `l` and `r` are type parameters.
`Int.parse` returns an `Option Int`, which is `None` or a `Some` that carries
an `Int`. By convention, `Left` carries an error and `Right` carries a result.

To use the value, match on the `Option` or the `Either`, as `main` does with
the result of `age`.

The `!` operator unwraps an `Option` without a `match`. `x!` evaluates to the
value in a `Some`, and panics on `None`. A panic stops the program, so use `!`
only when you know the value is there. `!` also unwraps the `Right` of an
`Either` whose `Left` is an error type ([Errors as values](19-error-types.md)).

<!-- run -->
```kotlin
fun age(text) = match Int.parse(text) {
	Some(n) -> if n < 0 { Left("${n} is negative") } else { Right(n) }
	None    -> Left("'${text}' is not a number")
}

fun main() {
	print(Int.parse("42"))
	print(Int.parse("forty-two"))
	print(Option.unwrapOr(Int.parse("forty-two"), 0))
	for text in ["36", "-1", "old"] {
		match age(text) {
			Right(n)  -> print("age ${n}")
			Left(why) -> print("no age: ${why}")
		}
	}
	print(Int.parse("2026")! + 1)
}
```

```text
Some(42)
None
0
age 36
no age: -1 is negative
no age: 'old' is not a number
2027
```

When `!` panics, the stack trace gives the line and column of the `!`. Here
`Map.get` returns `None`, because the map has no `"gopher"` key:

<!-- panic: unwrapped None -->
```kotlin
fun main() {
	let ports = Map.new()
	ports["http"] = 80
	print(ports["http"])
	print(Map.get(ports, "gopher")!)
}
```

```text
80
```

The panic message and the stack trace go to standard error:

<!-- stderr -->
```text
panic: unwrapped None
  at error (Control/Panic.gob:8:36)
  at main (Main.gob:5:32)
```

Don't panic on a failure that the caller could handle. Return the failure
instead. [Errors as values](19-error-types.md) shows how `?` propagates it.

## In the reference

* [`Option`](../ref/builtins.md#option)
* [`Either`](../ref/builtins.md#either)
* [Unwrapping with `!`](../ref/do-notation.md#unwrapping-with-)
* [Runtime errors](../ref/runtime-errors.md)
