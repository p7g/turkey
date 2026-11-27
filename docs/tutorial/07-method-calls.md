# Method calls and `|>`

Nested calls read inside out. In
`String.replace(String.trim(line), ", ", " + ")`, the call that runs first is
written second. Turkey has two ways to write a chain of calls in the order it
runs.

The first is method syntax. `x.f(args)` calls the function `f` from the module
that declares the type of `x`, and passes `x` as the first argument. `line` is
a `String`, so `line.trim()` is the same call as `String.trim(line)`. Method
calls chain from left to right.

The second is the pipe operator. `x |> f` calls `f(x)`, and it works with any
function, whichever module declares it.

To pipe into a function that takes more arguments, write `_` for the piped
one. An argument that is only `_` makes the whole call a function of that
argument, so `String.replace(_, ", ", " + ")` means
`fun(s) = String.replace(s, ", ", " + ")`.

<!-- run -->
```kotlin
fun shout(text) = text + "!"

fun main() {
	let line = "  apples, pears, figs  "
	print(String.replace(String.trim(line), ", ", " + "))
	print(line.trim().replace(", ", " + "))
	print(line |> String.trim |> shout)
	print(line |> String.trim |> String.replace(_, ", ", " + ") |> shout)
}
```

```text
apples + pears + figs
apples + pears + figs
apples, pears, figs!
apples + pears + figs!
```

The last two lines need the pipe to call `shout`. The program declares
`shout`, and the `String` module doesn't, so method syntax can't find it.

## In the reference

* [Method calls](../ref/expressions.md#method-calls)
*
  [The Prelude's other operators](../ref/expressions.md#the-preludes-other-operators)
* [Placeholder functions](../ref/declarations.md#placeholder-functions)
