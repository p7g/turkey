# Errors as values: `?`

Errors in Turkey are values. A function that can fail returns an `Either`: a
`Right` with its result, or a `Left` with an error. The caller decides what to
do with the error, and most callers pass it on to their own caller.

The `?` operator propagates errors. `e?` evaluates to the value in a `Right`.
If `e` is a `Left`, the rest of the function doesn't run, and the function
returns that `Left`. With `?`, a sequence of calls that can fail reads as
straight-line code.

<!-- run -->
```kotlin
fun parse(text) = match Int.parse(text) {
	Some(n) -> Right(n)
	None    -> Left("'${text}' is not a number")
}

fun percent(text) {
	let n = parse(text)?
	if n < 0 || n > 100 {
		return Left("${n} is out of range")
	}
	Right(n)
}

fun average(a, b) {
	let x = percent(a)?
	let y = percent(b)?
	Right((x + y) / 2)
}

fun main() {
	for (a, b) in [("40", "60"), ("40", "140"), ("loud", "60")] {
		match average(a, b) {
			Right(n)  -> print("average ${n}%")
			Left(why) -> print("error: ${why}")
		}
	}
}
```

```text
average 50%
error: 140 is out of range
error: 'loud' is not a number
```

Follow `("40", "140")` through `average`. `percent("40")` returns `Right(40)`,
so `x` is 40. `percent("140")` returns a `Left`, so `average` returns that
`Left` and never divides.

Without `?`, each call needs a `match` that returns the `Left` itself:

<!-- check -->
```kotlin
fun parse(text) = match Int.parse(text) {
	Some(n) -> Right(n)
	None    -> Left("'${text}' is not a number")
}

fun percent(text) {
	let n = match parse(text) {
		Right(n)  -> n
		Left(why) -> return Left(why)
	}
	if n < 0 || n > 100 {
		return Left("${n} is out of range")
	}
	Right(n)
}
```

`?` doesn't wrap the result for you. A function that uses `?` must still end
with an `Either`: `percent` ends with `Right(n)`, not `n`.

`?` doesn't convert errors either, so every error that a function propagates
must have the same type. Here they are all `String`s.
[`throw` and `SomeError`](19-error-types.md) shows what to do when they
differ.

## In the reference

*
  [Stopping with a reason: `Either`](../ref/do-notation.md#stopping-with-a-reason-either)
* [Control flow with `?`](../ref/do-notation.md#control-flow-with-)
* [No implicit `pure`](../ref/do-notation.md#no-implicit-pure)
