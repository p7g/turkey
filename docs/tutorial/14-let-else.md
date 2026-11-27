# `let ... else` and `while let`

`let ... else` is a pattern match that the rest of the block depends on.
`let Some(number) = Int.parse(value) else { ... }` binds `number` for the rest
of the block. When the pattern doesn't match, the `else` block runs instead.
The `else` block must exit: with `return`, `break` or `continue`, or with a
call that never returns, such as `error(...)`.

Use it as a guard clause. `describe` handles each failure where it happens,
and its main path stays unindented.

`while let` loops for as long as a pattern matches.

<!-- run -->
```kotlin
fun describe(setting) {
	let Some((key, value)) = String.splitOnce(setting, "=") else {
		return "'${setting}' has no '='"
	}
	let Some(number) = Int.parse(value) else {
		return "${key} is not a number"
	}
	"${key} is ${number}"
}

fun main() {
	for setting in ["port=8080", "verbose", "port=eighty"] {
		print(describe(setting))
	}
	let pending = ["deploy", "test", "build"]
	while let Some(step) = Vec.pop(pending) {
		print("running ${step}")
	}
}
```

```text
port is 8080
'verbose' has no '='
port is not a number
running build
running test
running deploy
```

In `main`, the loop ends when `Vec.pop` returns `None`, which it does once
`pending` is empty. The program passes `pending` to `Vec.pop`, so it is a
`Vec`.

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

Function parameters are patterns too, and follow the same rule:
`fun area((w, h)) = w * h` takes a tuple.

**Try this:** delete the first `return` in `describe`, and run the program.
The compiler rejects the `else` block, because it can fall through.

## In the reference

* [`let` and `var`](../ref/declarations.md#let-and-var)
* [`while let` and `while var`](../ref/statements.md#while-let-and-while-var)
* [Irrefutable patterns](../ref/patterns.md#irrefutable-patterns)
