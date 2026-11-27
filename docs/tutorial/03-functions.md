# Functions

`fun` declares a function. Write a short body after `=`, as `square` does, and
a longer one as a block, as `describe` does.

A block evaluates to its last expression, so you don't write `return` at the
end. `if` is an expression too: in `describe`, `word` is the value of the
branch that ran.

An anonymous function, or lambda, is a `fun` without a name, such as
`fun(p) = square(p.moons)`. You can pass one wherever a function is expected.

When a lambda uses its parameter once, you can shorten it to a placeholder
function. Write `_` for the parameter and leave out the `fun`: `_.moons > 0`
means `fun(p) = p.moons > 0`, and `_.name` means `fun(p) = p.name`.

The pipe operator `|>` passes the value on its left to the function on its
right. On the last line of `main`, that function is `Array.map(_, _.name)`. An
argument that is only `_` makes the whole call a function of that argument, so
it means `fun(xs) = Array.map(xs, _.name)`.

<!-- run -->
```kotlin
type Planet = Planet { name : String, moons : Int }

fun square(x) = x * x

fun describe(planet) {
	let word = if planet.moons == 1 { "moon" } else { "moons" }
	planet.name + " has " + show(planet.moons) + " " + word
}

fun main() {
	let planets = [
		Planet { name = "Venus", moons = 0 },
		Planet { name = "Earth", moons = 1 },
		Planet { name = "Mars", moons = 2 },
	]
	print(square(12))
	for planet in planets {
		print(describe(planet))
	}
	print(Array.map(planets, fun(p) = square(p.moons)))
	print(Array.map(planets, _.name))
	print(Array.filter(planets, _.moons > 0) |> Array.map(_, _.name))
}
```

```text
144
Venus has 0 moons
Earth has 1 moon
Mars has 2 moons
[0, 1, 4]
["Venus", "Earth", "Mars"]
["Earth", "Mars"]
```

`Planet` is a record type, which [Records](06-records.md) explains.

You can declare top-level functions in any order: a function can call one that
is declared below it. You can also declare a function inside a block. It is in
scope for the statements after it and for itself, so it can be recursive:

<!-- run -->
```kotlin
fun main() {
	fun factorial(n) = if n <= 1 { 1 } else { n * factorial(n - 1) }
	print(factorial(10))
}
```

```text
3628800
```

## In the reference

* [Functions](../ref/declarations.md#functions)
* [Anonymous functions](../ref/declarations.md#anonymous-functions)
* [Placeholder functions](../ref/declarations.md#placeholder-functions)
* [`if`](../ref/statements.md#if)
