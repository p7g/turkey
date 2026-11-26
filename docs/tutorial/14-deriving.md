# Deriving

Every type declaration gets `Eq` and `Hash` without asking, wherever its
fields have them. So two values compare by their fields, and a type of your
own works as a `Map` key with no code. `Point` below has no instance written
and is both.

`Ord` is derived only when the declaration asks for it, with `: Ord` after
the type's name: the order follows the order the constructors are written
in, and reordering them shouldn't quietly reorder every sort. A record
compares its fields from first to last.

`Debug` is derived for every type. It prints a value as Turkey would build
it, and that is what `print` shows for each element of a collection. `dbg(x)`
writes `debug(x)` to standard error and returns `x`, so it can wrap any
expression.

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

`dbg` wrote `Point { x = 3, y = 4 }` on standard error, beside the output.

<!-- stderr -->
```text
Point { x = 3, y = 4 }
```

A type whose fields don't all have a class has no instance of it, and the
error follows the fields to the one that lacks it:

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
