# Errors as values: `throw` and `?`

A function that can fail returns an `Either`. `e?` takes the value out of a
`Right` and goes on, or makes the whole block's result the `Left` it found.
So a chain of steps that can each fail reads as a straight line.

`?` never converts an error, so every `Left` in one block has the same type.
When failures come from different places, that type is `SomeError`, which
holds any value with an `Error` instance. `throw(e)` returns a `Left` holding
`e` as a `SomeError`, and `Error.describe` reads its message.

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

The last line of a block with a `?` is its result, and it is not wrapped for
you: `percent` ends with `Right(n)`, not `n`.

`fail(e)` packs `e` as a `SomeError` without making a `Left` of it.
`Error.context(err, e)` wraps an error in another without losing it, and
`Error.describe` reads the whole chain, outermost first:

<!-- run -->
```kotlin
type NotANumber = NotANumber { text : String }
type Setting = Setting { name : String }

instance Error NotANumber { fun message(e) = "not a number: ${e.text}" }
instance Error Setting { fun message(e) = "in setting ${e.name}" }

fun volume(text) = match Int.parse(text) {
	Some(n) -> Right(n)
	None    -> Left(Error.context(fail(NotANumber { text }), Setting { name = "volume" }))
}

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
