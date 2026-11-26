# Iterators and cursors

`for` works on any type with an `Iterator` instance. The instance gives two
associated types, `Item` for the elements and `Cursor` for a position, and
two methods: `iter` returns the first position, and `next` returns the
element at a position with the position after it, or `Iter.Done`.

`next` changes nothing. A position is a value that can be kept and stepped
from again, and a second loop over a `Countdown` starts from the top. Nothing
asks for a length, so a type can be iterated even when it can't be indexed.

<!-- run -->
```kotlin
type Countdown = Countdown { from : Int }

instance Iterator Countdown {
	type Item = Int
	type Cursor = Int

	fun iter(c) = c.from

	fun next(_, n) = if n == 0 { Iter.Done } else { Iter.Yield(n, n - 1) }
}

fun main() {
	let launch = Countdown { from = 3 }
	for n in launch {
		print(n)
	}
	print(Iter.sum(launch))
	for (a, b) in Iter.pairwise(launch) {
		print("${a} then ${b}")
	}
}
```

```text
3
2
1
6
3 then 2
2 then 1
```

`Iter.sum` and `Iter.pairwise` take any iterator, so a new type gets them
with no more code.

A source that keeps its own position has `()` as its cursor, and a loop over
it goes on from where the last one stopped. `Iter.resumable(xs)` makes one,
which splits a header from a body:

<!-- run -->
```kotlin
fun main() {
	let lines = Iter.resumable(["title", "", "a", "b"])
	for line in lines {
		if line == "" {
			break
		}
		print("header: ${line}")
	}
	for line in lines {
		print("body: ${line}")
	}
}
```

```text
header: title
body: a
body: b
```

## In the reference

* [`Iterator`](../ref/builtins.md#iterator)
* [`for` over a collection](../ref/statements.md#for-over-a-collection)
