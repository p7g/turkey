# Associated types

A class has one type parameter, and an *associated type* lets it talk about
a second type that depends on the first: a container's element type, say.
The class declares it with `type Elem c`, and each instance says what it is.
`Elem (Pair Int)` is `Int`, and `Elem String` is `Char`.

<!-- run -->
```kotlin
class Container c {
	type Elem c
	fun first(c) -> Elem c
}

type Pair a = Pair(a, a)

instance Container (Pair a) {
	type Elem = a
	fun first(Pair(x, _)) = x
}

instance Container String {
	type Elem = Char
	fun first(s) = match String.step(s, String.start(s)) {
		Some((c, _)) -> c
		None         -> error("an empty string has no first character")
	}
}

fun main() {
	print(first(Pair(10, 20)))
	print(first("hello"))
	print(first(Pair("left", "right")))
}
```

```text
10
h
left
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
