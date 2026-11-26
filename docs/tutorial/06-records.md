# Records

A record type names its fields. A value is built with `Name { field = value }`,
a field is read with `.field`, and a pattern takes a record apart:
`Player { name, score }` binds both fields.

Whether a value can change depends on its type, not on how it was bound. A
field marked `var` can be assigned, and every other field keeps the value it
was built with. A record with a `var` field is one object wherever it is
passed, so `award` changes the player its caller holds.

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

`players` is bound with `let`, and its players still change, because `let`
fixes only which object the name refers to. Arrays work the same way, and
their elements can be assigned too.

A field without `var` can't be assigned, and the error names the type whose
declaration would need the `var`:

<!-- error: cannot assign to field 'name': it is not 'var' in 'Player' -->
```kotlin
type Player = Player { name : String, var score : Int }

fun main() {
	let player = Player { name = "Ada", score = 0 }
	player.name = "Grace"
}
```

A value with no `var` field never changes, so a program can't tell whether
it was copied or shared.

## In the reference

* [Records](../ref/types.md#records)
* [Mutability and sharing](../ref/types.md#mutability-and-sharing)
* [Record construction](../ref/expressions.md#record-construction)
* [Record patterns](../ref/patterns.md#record-patterns)
