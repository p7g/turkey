# Deriving

The compiler can derive an instance of some classes: it writes the instance
for you from the type's declaration.

`Eq` and `Hash` are derived automatically for every type whose fields
implement them. So you can compare two values of your own type with `==`,
which compares their fields, and you can use the type as a `Map` key. The
program compares two `Card`s and uses a `Point` as a key, and declares no
instance for either.

`Ord` is derived only if you ask for it. Write `: Ord` after the type's name.
Constructors are ordered as they are declared, and records are compared field
by field, first field first. That is why `Ord` is opt-in: if it were
automatic, reordering a type's constructors would silently change the result
of every sort.

`Debug` is derived automatically for every type whose fields implement it.
`debug(x)` formats a value as the expression that constructs it, and `print`
formats the elements of a collection the same way. `dbg(x)` writes `debug(x)`
to standard error and returns `x`, so you can wrap it around any expression.

<!-- run -->
```kotlin
type Suit : Ord = Hearts | Diamonds | Clubs | Spades

type Card : Ord = Card { rank : Int, suit : Suit }

type Point = Point { x : Int, y : Int }

fun main() {
	let low = Card { rank = 10, suit = Hearts }
	let high = Card { rank = 10, suit = Spades }
	print(low == high)
	print(low < high)
	print(Array.sort([high, Card { rank = 2, suit = Clubs }, low]))
	let names = Map.new()
	names[Point { x = 0, y = 0 }] = "origin"
	print(Map.get(names, Point { x = 0, y = 0 }))
	print(dbg(Point { x = 3, y = 4 }).x)
}
```

```text
False
True
[Card { rank = 2, suit = Clubs }, Card { rank = 10, suit = Hearts }, Card { rank = 10, suit = Spades }]
Some("origin")
3
```

`dbg` wrote the `Point` to standard error:

<!-- stderr -->
```text
Point { x = 3, y = 4 }
```

A type gets a derived instance only if all its fields implement the class.
When one doesn't, the compile error names the field:

<!-- error: 'Job' has no 'Eq': field 'run' has type 'fun() -> Int', which has no 'Eq' -->
```kotlin
type Job = Job { id : Int, run : fun() -> Int }

fun main() {
	let job = Job { id = 1, run = fun() = 1 }
	print(job == job)
}
```

## In the reference

* [Derived instances](../ref/classes.md#derived-instances)
* [`Show` and printing](../ref/builtins.md#show-and-printing)
* [`hash`](../ref/builtins.md#hash)
