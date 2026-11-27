# Types you don't write

Turkey is statically typed, and yet the functions so far have no type
annotations. The compiler infers the type of every variable and function, and
type-checks the program with them.

The compiler infers the most general type that a function's body allows,
called its *principal type*. When the body works for many types, the function
is generic, and you don't have to say so.

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

`twice` calls `f` on `x` and again on the result, so `f` must return the type
it takes. The body requires nothing else, so `twice` is generic: `main` calls
it with a function on `Int`s and with a function on `String`s.

Field access is generic too. `area` multiplies the fields `width` and
`height`, so it accepts any record type that has both, as long as they have
the same type and that type supports `*`. `Room` has `Int` fields and `Poster`
has `Float` fields, and `area` accepts both.

Records are still nominally typed. `Room` and `Poster` are distinct types, and
`area` accepts each one because its declaration has the two fields.

You can annotate a parameter with `: Type` and a function's result with
`-> Type`. Annotations are almost always optional. Add them where they
document an interface, such as a function that other modules call. The
compiler checks every annotation, so it can't go out of date.

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

Occasionally the compiler can't infer a type, and asks for an annotation. An
empty array literal is one case: `print` needs the element type to print the
array, and nothing in the program determines it.

<!-- error: add a type annotation -->
```kotlin
fun main() {
	print([])
}
```

`print([] : Array Int)` compiles, and prints `[]`. The reference lists the
other places that need an annotation.

**Try this:** annotate the parameter of `area`, as in
`fun area(r : Room) = r.width * r.height`, and run the program. The compiler
rejects the call with a `Poster`: an annotation can make a function less
general than its body allows.

## In the reference

* [Principal types](../ref/inference.md#principal-types)
*
  [Field and projection constraints](../ref/inference.md#field-and-projection-constraints)
*
  [Where annotations are needed](../ref/inference.md#where-annotations-are-needed)
