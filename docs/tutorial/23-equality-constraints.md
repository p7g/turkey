# Equality constraints

A constraint can require an associated type to equal another type. Write the
equality with `~`.

A function that accepts any iterable knows nothing about the elements: their
type is `Item s`, which differs from one collection to the next. To add them
up, `total` needs them to be `Int`s. The constraints
`[Iterator s, Item s ~ Int]` require `s` to implement `Iterator` and its
`Item` type to be `Int`. Inside `total`, the loop variable `n` is then an
`Int`.

<!-- run -->
```kotlin
fun total[Iterator s, Item s ~ Int](numbers : s) -> Int {
	var sum = 0
	for n in numbers {
		sum = sum + n
	}
	sum
}

fun main() {
	print(total([1, 2, 3]))
	print(total(Int.range(1, 101)))
	print(total(Some(7)))
}
```

```text
6
5050
7
```

`total` accepts any iterable collection of `Int`s: an array, a range, or an
`Option`, which yields its value if it has one.

You can also require an associated type to implement a class.
`[Iterator s, Show (Item s)]` accepts any iterable whose elements can be
shown.

**Try this:** delete `, Item s ~ Int` from the brackets, and run the program.
The compiler rejects the function, and names the constraint to add.

## In the reference

* [Equality constraints](../ref/classes.md#equality-constraints)
* [Associated types](../ref/classes.md#associated-types)
* [`Iterator`](../ref/builtins.md#iterator)
