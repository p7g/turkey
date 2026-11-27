# Tasks

A task is a lightweight thread managed by Turkey's runtime. Tasks are provided
by a library module, `System.Task`, not by the language's syntax.

Concurrency in Turkey is structured. Every task belongs to a scope, and a
scope ends only when all its tasks have finished, so a task can't outlive the
scope that started it.

`runScope` creates a scope, and you can only start tasks inside one. To start
a task, pass a function to `spawn`. `join` waits for a task and returns its
result.

Pass `runScope` a `do` block. Inside the block, write `?` after each `spawn`
to get the task it started, and end with `pure(x)` to make `x` the result of
`runScope`. `runScope` returns once every task in the scope has finished.

<!-- run -->
```kotlin
import System.Parallel as Parallel
import System.Sync as Sync
import System.Task (join, runScope, spawn)

fun sumOfSquares(from, to) {
	var sum = 0
	for n in Int.range(from, to) {
		sum = sum + n * n
	}
	sum
}

fun main() {
	let (low, high) = runScope(do {
		let low = spawn(fun() = sumOfSquares(0, 500))?
		let high = spawn(fun() = sumOfSquares(500, 1000))?
		pure((low, high))
	})
	print(join(low) + join(high))
	print(Parallel.map([1, 2, 3, 4], _ * 10))
	let seen = Sync.mutex(0)
	runScope(do {
		for word in ["one", "two", "three"] {
			let _ = spawn(fun() = Sync.update(seen, _ + String.byteLength(word)))?
		}
		pure(())
	})
	print(Sync.withLock(seen, fun(n) = n))
}
```

```text
332833500
[10, 20, 30, 40]
11
```

Tasks share memory, so access to shared data must be synchronized.
`System.Sync` provides the primitives, such as the mutex in the program.

Scheduling is cooperative: a task runs until it waits or finishes.

## In the library

* [`System.Task`](https://p7g.github.io/turkey/System.Task.html)
* [`System.Parallel`](https://p7g.github.io/turkey/System.Parallel.html)
* [`System.Sync`](https://p7g.github.io/turkey/System.Sync.html)
