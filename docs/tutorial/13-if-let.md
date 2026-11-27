# `if let` and `while let`

`if let` is a conditional that pattern-matches. It tests a value against a
pattern and, when the pattern matches, runs its block with the pattern's
variables bound. Otherwise it runs its `else` block, if it has one. Use it
instead of a `match` when only one case matters.

You can chain conditions with commas. Each condition is a pattern match or a
`Bool` expression. They are tested from left to right, and each can use the
variables that the ones before it bound.

`while let` is the loop form. It repeats its block for as long as the pattern
matches.

<!-- run -->
```kotlin
fun portOf(setting) {
	if let Some((key, value)) = String.splitOnce(setting, "="), key == "port" {
		Int.parse(value)
	} else {
		None
	}
}

fun main() {
	for setting in ["port=8080", "host=example.com", "port=eighty"] {
		if let Some(port) = portOf(setting) {
			print("port ${port}")
		} else {
			print("no port in '${setting}'")
		}
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

In `portOf`, the first condition splits the setting at the `=` and binds `key`
and `value`, and the second tests `key`. `String.splitOnce` returns an
`Option` of a tuple, so the pattern is `Some((key, value))`.

In `main`, the `while let` loop ends when `Vec.pop` returns `None`, which it
does once `pending` is empty. The program passes `pending` to `Vec.pop`, so it
is a `Vec`.

**Try this:** add `"size=3"` to the settings in `main`, delete
`, key == "port"` from `portOf`, and run the program. Without the second
condition, `size=3` counts as a port.

## In the reference

* [`if let` and `if var`](../ref/statements.md#if-let-and-if-var)
* [Condition lists](../ref/statements.md#condition-lists)
* [`while let` and `while var`](../ref/statements.md#while-let-and-while-var)
