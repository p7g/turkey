# `?` beyond errors

In the last lesson, `?` looked like an early return. It is more general than
that: `?` works on any monad, and `Either` is one of several.

A monad is a type with a `bind` method. `bind` takes a value of the type and a
function, and decides how to apply the function to what the value holds. `e?`
calls `bind` with `e` and, as the function, the rest of the block:

* `Option` and `Either` short-circuit. Their `bind` runs the rest of the block
  on the value in a `Some` or a `Right`, and returns a `None` or a `Left`
  without running it.
* `Array` iterates. Its `bind` runs the rest of the block once for each
  element, and concatenates the results.

`outfits` uses `?` on two arrays, so its last line runs once for each
combination of a shirt and a pair of trousers, as it would inside two nested
loops.

A block that uses `?` evaluates to what `bind` returns, so it must end with a
value of the same monad. `outfits` ends with an array, and returns an array.
To use `?` in a function that returns something else, put it in a `do` block,
as `main` does with two `Option`s.

<!-- run -->
```kotlin
fun outfits(shirts, trousers) {
	let shirt = shirts?
	let pair = trousers?
	["${shirt} with ${pair}"]
}

fun main() {
	for outfit in outfits(["red", "blue"], ["jeans", "shorts"]) {
		print(outfit)
	}
	let total = do {
		let a = Int.parse("19")?
		let b = Int.parse("23")?
		Some(a + b)
	}
	print(total)
}
```

```text
red with jeans
red with shorts
blue with jeans
blue with shorts
Some(42)
```

To make your own type a monad, implement the classes `Functor`, `Applicative`
and `Monad` for it. [Type classes](15-classes.md) explains instances. `Logged`
pairs a value with log lines. Its `bind` runs the rest of the block and
appends the lines that the rest logged. `pure(x)` wraps a plain value in the
monad: for `Logged`, that is `x` with an empty log.

<!-- run -->
```kotlin
type Logged a = Logged(a, Array String)

instance Functor Logged {
	fun map(Logged(value, log), f) = Logged(f(value), log)
}

instance Applicative Logged {
	fun pure(value) = Logged(value, [])
}

instance Monad Logged {
	fun bind(Logged(value, log), f) {
		let Logged(result, more) = f(value)
		Logged(result, log + more)
	}
}

fun note(line) = Logged((), [line])

fun area(width, height) {
	note("width is ${width}")?
	note("height is ${height}")?
	pure(width * height)
}

fun main() {
	let Logged(value, log) = area(3, 4)
	for line in log {
		print(line)
	}
	print(value)
}
```

```text
width is 3
height is 4
12
```

## In the reference

* [Every combination: `Array`](../ref/do-notation.md#every-combination-array)
* [Accumulating: a monad of your own](../ref/do-notation.md#accumulating-a-monad-of-your-own)
* [Do-contexts](../ref/do-notation.md#do-contexts)
* [`Functor`, `Applicative` and `Monad`](../ref/builtins.md#functor-applicative-and-monad)
