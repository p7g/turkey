# Types you don't write

The programs so far have no type annotations, and every one of them is
checked. The compiler infers each function's *principal type*: the most
general type it can have. `twice` works for any function from a type to
itself, so the one `twice` adds 3 to a number twice and puts two `!` on a
string.

Reading a field doesn't fix the record's type either. `area` reads `width`
and `height`, so it takes any record that has both fields, as long as the
two can be multiplied: a `Room` measured in `Int`s and a `Poster` measured in
`Float`s.

<!-- run -->
```kotlin
type Room = Room { name : String, width : Int, height : Int }
type Poster = Poster { title : String, width : Float, height : Float }

fun area(r) = r.width * r.height

fun twice(f, x) = f(f(x))

fun main() {
	print(area(Room { name = "kitchen", width = 4, height = 3 }))
	print(area(Poster { title = "Map", width = 0.5, height = 0.7 }))
	print(twice(_ + 3, 10))
	print(twice(fun(s) = s + "!", "hey"))
}
```

```text
12
0.35
16
hey!!
```

Records are still nominal: `area` accepts a type because its declaration has
those fields, not because a value happens to have them.

An annotation is never needed for a program to be checked, except in a few
places the reference lists. Write one where it explains an interface, such as
a function other modules call. It is checked, so it can't drift from the
code:

<!-- run -->
```kotlin
fun average(xs : Array Float) -> Float = Iter.sum(xs) / Float.fromInt(len(xs))

fun main() {
	print(average([1.5, 2.5, 3.5]))
}
```

```text
2.5
```

One place an annotation is needed: an empty array whose element type
nothing decides. Printing it would need to know how to show its elements.

<!-- error: add a type annotation -->
```kotlin
fun main() {
	print([])
}
```

`print([] : Array Int)` prints `[]`.

## In the reference

* [Principal types](../ref/inference.md#principal-types)
* [Field and projection constraints](../ref/inference.md#field-and-projection-constraints)
* [Where annotations are needed](../ref/inference.md#where-annotations-are-needed)
