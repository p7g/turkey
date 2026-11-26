# Option, Either and `!`

Turkey has no null and no exceptions. A value that may be missing is an
`Option`, and a result that may have failed is an `Either`. Both are ordinary
variants from the Prelude:

```text
type Option a = None | Some(a)
type Either l r = Left(l) | Right(r)
```

The `a`, `l` and `r` are type parameters, so `Int.parse` returns an
`Option Int`. By convention a `Left` holds why something failed and a
`Right` holds the result.

`x!` takes the value out of an `Option` or an `Either`, and panics if there
is none. Use it where the program's own logic makes sure the value is there.

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

A `!` on `None` stops the program with a panic. The trace names the line and
column of the `!`:

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

and on standard error, the panic and its trace:

<!-- stderr -->
```text
panic: unwrapped None
  at error (Control/Panic.gob:8:36)
  at main (Main.gob:5:32)
```

Return a failure the caller should handle instead of panicking on it.
[Errors as values](11-errors.md) shows how `?` passes one along.

## In the reference

* [`Option`](../ref/builtins.md#option)
* [`Either`](../ref/builtins.md#either)
* [Unwrapping with `!`](../ref/do-notation.md#unwrapping-with-)
* [Runtime errors](../ref/runtime-errors.md)
