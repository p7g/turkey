# Conditions that bind

A `match` with one interesting arm says more as a condition. `if let` runs
its block when a pattern matches, with the pattern's names bound inside it.
Its head can list several conditions, separated by commas: patterns and
`Bool` tests, tried left to right, each seeing the names bound before it.

`let ... else` binds a pattern for the rest of the block, and its `else`
block runs when the pattern doesn't match. That block has to leave, with
`return`, `break` or `continue`, so the names are bound wherever the code
goes on.

`while let` runs its block for as long as a pattern matches.

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

`Vec.pop` returns the last element, or `None` once there are none, so the
`while let` ends when `pending` is empty.

A plain `let`, with no `else`, takes only a pattern that matches every value,
such as a tuple or a record. Anything else is an error that names a value it
would miss:

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
