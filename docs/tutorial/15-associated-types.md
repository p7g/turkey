# Associated types

A class has one type parameter, and an *associated type* lets it refer to a
second type that depends on the first: a container's element type, say.
The class declares it with `type Elem c`, and each instance sets it.
`Elem (Pair Int)` is `Int`, and `Elem (Array String)` is `String`. `first`
returns an `Option (Elem c)`, since an array may have no first element.

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

A constraint can fix an associated type with `~`. The library's `Iterator`
class has one for its elements, `Item`, so `total` takes anything iterable
whose elements are `Int`s, and adds them as `Int`s:

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
