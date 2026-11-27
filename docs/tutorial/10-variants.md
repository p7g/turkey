# Variants and pattern matching

A variant type is a sum type, also called a tagged union: each value is one of
several alternatives. Every alternative has a constructor, and a constructor
can carry data. A `Shape` is a `Circle` with a radius, a `Rect` with a width
and a height, or a `Point` with no data.

Use `match` to branch on a variant. `match` tests the patterns in order and
runs the first arm whose pattern matches. A pattern such as `Circle(r)` binds
the variable `r` to the data the constructor carries. `_` is a wildcard, which
matches any value without binding it. `|` combines two patterns in one arm.

<!-- run -->
```kotlin
type Shape = Circle(Float) | Rect(Float, Float) | Point

fun area(shape) = match shape {
	Circle(r)  -> 3.14159 * r * r
	Rect(w, h) -> w * h
	Point      -> 0.0
}

fun describe(shape) = match shape {
	Circle(r)  -> if r > 10.0 { "a big circle" } else { "a circle" }
	Rect(w, h) -> if w == h { "a square" } else { "a rectangle" }
	Point      -> "a point"
}

fun corners(shape) = match shape {
	Rect(_, _)        -> 4
	Circle(_) | Point -> 0
}

fun main() {
	for shape in [Circle(2.0), Circle(20.0), Rect(3.0, 3.0), Rect(2.0, 5.0), Point] {
		print("${describe(shape)} has area ${area(shape)} and ${corners(shape)} corners")
	}
}
```

```text
a circle has area 12.56636 and 0 corners
a big circle has area 1256.636 and 0 corners
a square has area 9.0 and 4 corners
a rectangle has area 10.0 and 4 corners
a point has area 0.0 and 0 corners
```

A `match` must be exhaustive: its patterns must cover every possible value. If
you miss a constructor, the compiler reports which one.

<!-- error: this match is not exhaustive; 'Point' is not handled -->
```kotlin
type Shape = Circle(Float) | Rect(Float, Float) | Point

fun area(shape) = match shape {
	Circle(r)  -> 3.14159 * r * r
	Rect(w, h) -> w * h
}
```

You can nest patterns, and you can match literals and tuples: `Some(0)`,
`"port"` and `(_, 'x')` are all patterns. A tuple, such as `(3, 'x')`, groups
values without a type declaration.

## In the reference

* [Data types](../ref/types.md#data-types)
* [Patterns](../ref/patterns.md)
* [Alternatives](../ref/patterns.md#alternatives)
* [Exhaustiveness](../ref/patterns.md#exhaustiveness)
