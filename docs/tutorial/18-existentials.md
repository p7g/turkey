# Existential types

A constructor can hide the type of what it holds. `Shown[Show a](a)` declares
that a `Shown` holds a value of *some* type `a` that has a `Show` instance.
Each `Shown` can hold a different type, and they all have the one type
`Shown`, so an array can mix numbers, strings and options.

A pattern opens one, in a function's parameter or a `match` arm. Inside the
function that opened it, the hidden type has only the instances the
brackets declared, so `cell` can show `value` and do nothing else with it.

<!-- run -->
```kotlin
type Shown = Shown[Show a](a)

fun cell(Shown(value)) = "[${value}]"

fun main() {
	let row = [Shown(42), Shown("text"), Shown(Some(1.5)), Shown([True])]
	for shown in row {
		print(cell(shown))
	}
}
```

```text
[42]
[text]
[Some(1.5)]
[[True]]
```

A record constructor can hide a type too, and its fields can be functions
over it. That packs state together with what works on it, and the users of a
`Counter` never learn what its state is:

<!-- run -->
```kotlin
type Counter = Counter[s] {
	start  : s
	step   : fun(s) -> s
	report : fun(s) -> String
}

fun runCounter(Counter { start, step, report }, times) {
	var state = start
	for _ in Int.range(0, times) {
		state = step(state)
	}
	report(state)
}

fun main() {
	let ticks = Counter { start = 0, step = _ + 1, report = show }
	let marks = Counter { start = "", step = _ + "|", report = fun(s) = s }
	print(runCounter(ticks, 3))
	print(runCounter(marks, 3))
}
```

```text
3
|||
```

The hidden type can't leave the place that opened it, so a function can't
return the value a `Shown` holds:

<!-- error: the type 'a' hidden by 'Shown' cannot leave the 'match' arm or function that opened it -->
```kotlin
type Shown = Shown[Show a](a)

fun unwrap(Shown(value)) = value
```

## In the reference

* [Existential types](../ref/types.md#existential-types)
* [Existential patterns](../ref/patterns.md#existential-patterns)
