# Associated types

An associated type is a type that a class declares and each instance defines.
Use one when a method's signature needs a type that depends on the instance.

`Container` is a class of collections with a method `first`. What `first`
returns depends on the container: an `Int` for a `Pair Int`, a `String` for an
`Array String`. So the class declares the element type as an associated type,
`type Elem c`, and each instance defines it with `type Elem = ...`.
`Elem (Pair Int)` is `Int`, and `Elem (Array String)` is `String`.

`first` returns an `Option (Elem c)`, because a container can be empty.

<!-- run -->
```kotlin
class Container c {
	type Elem c
	fun first(c) -> Option (Elem c)
}

type Pair a = Pair(a, a)

instance Container (Pair a) {
	type Elem = a
	fun first(Pair(x, _)) = Some(x)
}

instance Container (Array a) {
	type Elem = a
	fun first(xs) = if len(xs) == 0 { None } else { Some(xs[0]) }
}

fun main() {
	print(first(Pair(10, 20)))
	print(first(["left", "right"]))
	print(first([] : Array Int))
}
```

```text
Some(10)
Some("left")
None
```

A constraint can require an associated type to equal another type. Write the
equality with `~`. The library's `Iterator` class has an associated type
`Item`, the type of the elements. The constraints `[Iterator s, Item s ~ Int]`
require `s` to be iterable and its elements to be `Int`s. So `total` accepts
any iterable collection of `Int`s: an array, a range, or an `Option`.

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

## In the reference

* [Associated types](../ref/classes.md#associated-types)
* [Equality constraints](../ref/classes.md#equality-constraints)
* [`Iterator`](../ref/builtins.md#iterator)
