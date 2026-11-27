# Conditions that bind

`if let` is a conditional that pattern-matches. It tests a value against a
pattern and, when the pattern matches, runs its block with the pattern's
variables bound. Use it instead of a `match` when only one case matters.

You can chain conditions with commas. Each condition is a pattern match or a
`Bool` expression. They are tested from left to right, and each can use the
variables that the ones before it bound. In `portOf`, the second condition
tests the `key` that the first one bound.

`let ... else` is for a value that the rest of the block needs.
`let Some(port) = portOf(setting) else { ... }` binds `port` for the rest of
the block. When the pattern doesn't match, the `else` block runs instead. The
`else` block must exit: with `return`, `break` or `continue`, or with a call
that never returns, such as `error(...)`.

`while let` loops for as long as a pattern matches.

<!-- run -->
```kotlin
fun portOf(setting) {
	if let Some((key, value)) = String.splitOnce(setting, "="), key == "port" {
		Int.parse(value)
	} else {
		None
	}
}

fun describe(setting) {
	let Some(port) = portOf(setting) else {
		return "no port in '${setting}'"
	}
	"port ${port}"
}

fun main() {
	for setting in ["port=8080", "host=example.com", "port=eighty"] {
		print(describe(setting))
	}
	let pending = ["deploy", "test", "build"]
	while let Some(step) = Vec.pop(pending) {
		print("running ${step}")
	}
}
```

```text
port 8080
no port in 'host=example.com'
no port in 'port=eighty'
running build
running test
running deploy
```

In `main`, the loop ends when `Vec.pop` returns `None`, which it does once
`pending` is empty. `pending` is a `Vec`, a growable array that
[Loops and collections](10-loops.md) explains.

A plain `let` accepts only an irrefutable pattern: one that matches every
value of its type, such as a tuple pattern or a record pattern. For any other
pattern, the compiler reports a value that the pattern doesn't match:

<!-- error: this pattern does not match 'None' -->
```kotlin
fun main() {
	let Some(x) = Int.parse("12")
	print(x)
}
```

## In the reference

* [`if let` and `if var`](../ref/statements.md#if-let-and-if-var)
* [Condition lists](../ref/statements.md#condition-lists)
* [`while let` and `while var`](../ref/statements.md#while-let-and-while-var)
* [Irrefutable patterns](../ref/patterns.md#irrefutable-patterns)
