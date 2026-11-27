# `throw` and `SomeError`

A string is enough for an error message, but a caller can't tell one kind of
failure from another by its text. So programs declare a type for each kind of
error. That raises a problem: `?` doesn't convert errors, so every error that
a function propagates must have the same type.

`SomeError` is that type. A `SomeError` holds a value of any type that
implements the `Error` class. To make a type an error, implement `Error` for
it by defining `message`, as the program does for `NotANumber` and
`OutOfRange`.

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

`parse` fails with a `NotANumber` and `percent` with an `OutOfRange`. Both
functions return an `Either SomeError Int`, so the `?` in `percent` can
propagate the error from `parse`.

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

*
  [Errors from different sources: `SomeError`](../ref/do-notation.md#errors-from-different-sources-someerror)
* [`Either`](../ref/builtins.md#either)
