# Iterators and cursors

To make a type iterable, implement the `Iterator` class. `for` then works on
the type, and so does every function that takes an iterable.

Iteration in Turkey uses cursors. A cursor is an immutable value that marks a
position in a collection. `Iterator` has two associated types and two methods:

* `Item` is the type of the elements, and `Cursor` is the type of the cursors.
* `iter(c)` returns the cursor of the first element.
* `next(c, cursor)` returns `Iter.Yield(item, after)`, with the element at
  `cursor` and the cursor after it, or `Iter.Done` at the end.

`next` has no side effects. You can keep a cursor and resume from it, and a
second loop over the same collection starts from the beginning. Neither method
needs a length, so you can iterate over a type that can't be indexed, such as
a linked list or a generated sequence.

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

`Countdown` uses an `Int` as its cursor: the number it will yield next.
`Iter.sum` and `Iter.pairwise` accept any iterable, so they work on a
`Countdown` with no more code.

An iterator can also be stateful. Its cursor type is then `()`, and each loop
over it continues where the previous one stopped. `Iter.resumable(xs)` makes a
stateful iterator from any iterable. The example reads a header with one loop
and the body with the next:

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

**Try this:** copy the first `for` loop in `main` and paste it under itself,
then run the program. Both loops print `3`, `2`, `1`, because a `Countdown`
holds no position of its own.

## In the reference

* [`Iterator`](../ref/builtins.md#iterator)
* [`for` over a collection](../ref/statements.md#for-over-a-collection)
