# Records

A record is a collection of named fields.

Create a record with `Name { field = value }`, and access a field with a dot,
as in `player.score`. To destructure a record, use a record pattern:
`Player { name, score }` binds the variables `name` and `score` to the two
fields. The same shorthand works when you create a record:
`Player { name, score }` is short for `Player { name = name, score = score }`.

Fields are immutable unless you declare them with `var`. In a `Player`, you
can assign to `score` but not to `name`.

A record with a `var` field has reference semantics. Passing it to a function
doesn't copy it, so when `award` updates a player's score, the caller sees the
change.

<!-- run -->
```kotlin
type Player = Player { name : String, var score : Int }

fun award(player, points) {
	player.score = player.score + points
}

fun main() {
	let players = [
		Player { name = "Ada", score = 0 },
		Player { name = "Grace", score = 0 },
	]
	for player in players {
		award(player, String.byteLength(player.name))
	}
	award(players[0], 10)
	for Player { name, score } in players {
		print("${name}: ${score}")
	}
}
```

```text
Ada: 13
Grace: 5
```

`players` is declared with `let`, and its players still change. `let` makes
the variable immutable, not the object it refers to. Mutability is a property
of the type: a `Player` is mutable because its declaration has a `var` field.
Arrays are mutable too, and you can assign to their elements.

Assigning to a field that isn't `var` is a compile error. The message names
the type whose declaration needs the `var`:

<!-- error: cannot assign to field 'name': it is not 'var' in 'Player' -->
```kotlin
type Player = Player { name : String, var score : Int }

fun main() {
	let player = Player { name = "Ada", score = 0 }
	player.name = "Grace"
}
```

A record with no `var` field is immutable: after you create it, it never
changes.

## In the reference

* [Records](../ref/types.md#records)
* [Mutability and sharing](../ref/types.md#mutability-and-sharing)
* [Record construction](../ref/expressions.md#record-construction)
* [Record patterns](../ref/patterns.md#record-patterns)
