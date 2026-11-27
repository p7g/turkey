# `if let`

`if let` is a conditional that pattern-matches. It tests a value against a
pattern and, when the pattern matches, runs its block with the pattern's
variables bound. Otherwise it runs its `else` block, if it has one. Use it
instead of a `match` when only one case matters.

You can chain conditions with commas. Each condition is a pattern match or a
`Bool` expression. They are tested from left to right, and each can use the
variables that the ones before it bound.

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
}
```

```text
port 8080
no port in 'host=example.com'
no port in 'port=eighty'
```

In `portOf`, the first condition splits the setting at the `=` and binds `key`
and `value`, and the second tests `key`. `String.splitOnce` returns an
`Option` of a tuple, so the pattern is `Some((key, value))`.

**Try this:** add `"size=3"` to the settings in `main`, delete
`, key == "port"` from `portOf`, and run the program. Without the second
condition, `size=3` counts as a port.

## In the reference

* [`if let` and `if var`](../ref/statements.md#if-let-and-if-var)
* [Condition lists](../ref/statements.md#condition-lists)
