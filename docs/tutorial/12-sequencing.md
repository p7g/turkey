# `?` beyond errors

`?` is not an early return, and nothing about it is specific to errors. It
hands the rest of the block, as a function, to the `bind` method of the
value's `Monad` instance, and `bind` decides what happens next. `Option`'s
and `Either`'s stop at the first failure. `Array`'s runs the rest of the
block once for each element, and joins what each run returns.

So `outfits` runs its last line once for every shirt and every pair of
trousers, with no loop written:

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

A function whose body has a `?` returns what `bind` returns, so its result is
monadic. `do { ... }` makes a block of its own for `?`, inside a function
that returns something else.

A type of your own becomes a monad with three instances. `Logged` carries
log lines beside its value, and its `bind` joins each step's lines:

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
