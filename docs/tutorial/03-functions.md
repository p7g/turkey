# Functions

A function's body is either `= expression`, for one that fits on a line, or a
block. A block's value is the value of its last line, so there is no
`return` at the end.

`fun(x) = ...` without a name is a lambda, a function written where it is
used. A short lambda that uses its parameter once can leave the parameter
out: `_.moons > 0` is `fun(p) = p.moons > 0`, and `_.name` reads each
planet's `name`.

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

`Planet` is a record, which [Records](06-records.md) covers. `if` is an
expression, so `word` is whichever branch ran. `|>` passes the value on its
left to the function on its right, and `Array.map(_, _.name)` is a function
of the array, because each `_` makes a function of the call it stands in.

Functions can call each other in any order, and a function declared inside a
block is visible to the lines after it:

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
