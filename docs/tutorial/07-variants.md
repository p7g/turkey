# Variants and pattern matching

A type can have several constructors, each with its own payload. A `Shape` is
a `Circle` with a radius, a `Rect` with a width and a height, or a `Point`
with nothing.

`match` tries each arm's pattern in turn and runs the first that matches,
with the pattern's names bound to the parts of the value. `|` joins two
patterns into one arm.

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

A `match` must cover every value. One that misses a constructor is an error
that names what it doesn't handle, so adding a constructor to `Shape` points
at every `match` that needs a new arm:

<!-- error: this match is not exhaustive; 'Point' is not handled -->
```kotlin
type Shape = Circle(Float) | Rect(Float, Float) | Point

fun area(shape) = match shape {
	Circle(r)  -> 3.14159 * r * r
	Rect(w, h) -> w * h
}
```

Patterns nest, and they match literals too: `Some(0)`, `"port"` and
`(_, 'x')` are patterns.

## In the reference

* [Data types](../ref/types.md#data-types)
* [Patterns](../ref/patterns.md)
* [Alternatives](../ref/patterns.md#alternatives)
* [Exhaustiveness](../ref/patterns.md#exhaustiveness)
