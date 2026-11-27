# Errors as values: `throw` and `?`

Errors in Turkey are values. A function that can fail returns an `Either`: a
`Right` with its result, or a `Left` with an error.

The `?` operator propagates errors. `e?` evaluates to the value in a `Right`.
If `e` is a `Left`, the rest of the function doesn't run, and the function
returns that `Left`. With `?`, a sequence of calls that can fail reads as
straight-line code.

`?` doesn't convert errors, so every error that a function propagates must
have the same type. When a function can fail with errors of different types,
use `SomeError`. A `SomeError` holds a value of any type that implements the
`Error` class. To implement `Error` for a type, define `message` in an
`instance` declaration, as the program does for `NotANumber` and `OutOfRange`.
[Type classes](15-classes.md) explains classes and instances.

`throw(e)` wraps `e` in a `SomeError` and returns it in a `Left`. `throw` is
an ordinary function and doesn't exit its caller, which is why `percent`
writes `return throw(...)`. `Error.describe` returns an error's message.

<!-- run -->
```kotlin
type NotANumber = NotANumber { text : String }
type OutOfRange = OutOfRange { value : Int }

instance Error NotANumber { fun message(e) = "not a number: ${e.text}" }
instance Error OutOfRange { fun message(e) = "out of range: ${e.value}" }

fun parse(text) = match Int.parse(text) {
	Some(n) -> Right(n)
	None    -> throw(NotANumber { text })
}

fun percent(text) {
	let n = parse(text)?
	if n < 0 || n > 100 {
		return throw(OutOfRange { value = n })
	}
	Right(n)
}

fun main() {
	for text in ["40", "140", "loud"] {
		match percent(text) {
			Right(n)  -> print("${n}%")
			Left(err) -> print("error: ${Error.describe(err)}")
		}
	}
}
```

```text
40%
error: out of range: 140
error: not a number: loud
```

`?` doesn't wrap the result for you. A function that uses `?` must still end
with an `Either`: `percent` ends with `Right(n)`, not `n`.

You can add context to an error as it propagates. `Error.context(err, e)`
wraps `err` in the new error `e` without discarding it, and `Error.describe`
returns the messages of the whole chain, outermost first.

In the example, `volume` calls `Either.mapLeft`, which applies a function to
the error in a `Left` and returns a `Right` unchanged. Here the function is
`Error.context(_, Setting { name = "volume" })`, which adds the context:

<!-- run -->
```kotlin
type NotANumber = NotANumber { text : String }
type Setting = Setting { name : String }

instance Error NotANumber { fun message(e) = "not a number: ${e.text}" }
instance Error Setting { fun message(e) = "in setting ${e.name}" }

fun parse(text) = match Int.parse(text) {
	Some(n) -> Right(n)
	None    -> throw(NotANumber { text })
}

fun volume(text) = Either.mapLeft(parse(text), Error.context(_, Setting { name = "volume" }))

fun main() {
	match volume("loud") {
		Right(n)  -> print(n)
		Left(err) -> print(Error.describe(err))
	}
}
```

```text
in setting volume: not a number: loud
```

## In the reference

* [Stopping with a reason: `Either`](../ref/do-notation.md#stopping-with-a-reason-either)
* [Errors from different sources: `SomeError`](../ref/do-notation.md#errors-from-different-sources-someerror)
* [Control flow with `?`](../ref/do-notation.md#control-flow-with-)
* [No implicit `pure`](../ref/do-notation.md#no-implicit-pure)
