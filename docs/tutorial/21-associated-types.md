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

The library uses associated types the same way. The `Iterator` class, in the
next lesson, has one for the type of a collection's elements.

**Try this:** in the instance for `Pair a`, change `type Elem = a` to
`type Elem = Int`, and run the program. The compiler rejects `first`, which
returns an `a` where the class now promises an `Int`.

## In the reference

* [Associated types](../ref/classes.md#associated-types)
