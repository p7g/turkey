# Statements and control flow

Turkey is a procedural language: function bodies are blocks of statements,
with assignment, loops and early exits. It is also expression-oriented: a
block, an `if`, a `match` and a `loop` all have values. This chapter covers
blocks, assignment, and each control construct, with the type each one has.

## Blocks

**Syntax**

```ebnf
block     ::= "{" (statement (separator statement)*)? "}"
statement ::= let-decl | var-decl | fun-decl
            | assignment
            | expression
separator ::= line break | ";"
```

A block is a sequence of statements in braces, separated by line breaks or
`;` ([Line breaks](lexical.md#line-breaks-and-semicolons)). The statements run
in order, and the value of the block is the value of its last statement. If
the last statement is a `let`, `var` or `fun` declaration, or the block is
empty, its value is `()`.

<!-- run -->
```kotlin
fun main() {
    let area = {
        let width = 3
        let height = 4
        width * height
    }
    print(area)
}
```

```text
12
```

A declaration in a block is visible from the next statement to the end of the
block. A block is a scope: its declarations are not visible after it.

### Discarded values

A statement whose value is not used must have type `Unit`. Computing a value
and dropping it is usually a mistake, such as calling `Array.pop` for its
effect and forgetting that the popped element was the point. So it is an
error:

<!-- error: this expression's value has type 'Option Int' and is discarded; use it, or write 'let _ = ...' -->
```kotlin
fun main() {
    let stack = [1, 2, 3]
    Array.pop(stack)
    print(stack)
}
```

To drop a value on purpose, bind it to `_`:

<!-- run -->
```kotlin
fun main() {
    let stack = [1, 2, 3]
    let _ = Array.pop(stack)
    print(stack)
}
```

```text
[1, 2]
```

The rule applies to every statement of a block except the last one, to the
last statement of the body of a `while`, `for` or `loop`, and to the last
statement of an `if` without an `else`. An expression that never produces a
value, such as `return` or a call to `error`, is not a discarded value.

## Assignment

**Syntax**

```ebnf
assignment ::= IDENT "=" expression
             | postfix "." IDENT "=" expression
             | postfix "[" expression "]" "=" expression
```

There are three forms of assignment:

* `x = e` gives a new value to a variable declared with `var`, or to a
  function parameter.
* `r.field = e` changes a field of a [mutable record](types.md#mutability-and-sharing).
* `c[k] = e` replaces an element, through the [`Index`](builtins.md#index)
  class's `set` method.

The new value must have the same type as the old one. An assignment is a
statement, not an expression, and has type `Unit`. There are no compound
assignment operators such as `+=`.

<!-- run -->
```kotlin
type Tally = Tally { count : Int }

fun main() {
    var label = "start"
    label = label + "ed"
    let tally = Tally { count = 0 }
    tally.count = tally.count + 1
    let slots = [0, 0, 0]
    slots[1] = 7
    print(label)
    print(tally.count)
    print(slots)
}
```

```text
started
1
[0, 7, 0]
```

## `if`

**Syntax**

```ebnf
if-expr ::= "if" expression block ("else" (block | if-expr))?
```

The condition must be a `Bool`. It needs no parentheses, and the branches must
be blocks. `else if` chains are written directly.

`if` is an expression. With an `else`, its value is the value of whichever
branch runs, and both branches must have the same type. Without an `else`, its
type is `Unit`.

<!-- run -->
```kotlin
fun sign(n) = if n < 0 { "negative" } else if n == 0 { "zero" } else { "positive" }

fun main() {
    print(sign(-4))
    print(sign(0))
    if sign(9) == "positive" {
        print("nine is positive")
    }
}
```

```text
negative
zero
nine is positive
```

The condition cannot be a bare record expression; see
[Record construction](expressions.md#record-construction).

> **Coming from Rust.** `if` works as in Rust, including as an expression, and
> the block's last statement is its value. There is no `;` rule to decide
> whether a block returns its last value: it always does.

## `if let` and `if var`

**Syntax**

```ebnf
if-let ::= "if" ("let" | "var") pattern "=" expression block ("else" (block | if-expr))?
```

`if let p = e { A } else { B }` evaluates `e`, and runs `A` with the pattern's
variables bound if `p` matches, or `B` otherwise. It is exactly

```text
match e {
    p -> A
    _ -> B
}
```

and without an `else`, `B` is `()`. The variables `p` binds are visible in `A`
only. `if var` is the same, except that the bound variables can be assigned
inside `A`.

<!-- run -->
```kotlin
fun main() {
    let queue = [3, 8]
    if let Some(last) = Array.pop(queue) {
        print("took " + show(last))
    } else {
        print("empty")
    }
    if var Some(n) = Int.parse("41") {
        n = n + 1
        print(n)
    }
}
```

```text
took 8
42
```

## `match`

**Syntax**

```ebnf
match-expr ::= "match" expression "{" match-arm (separator match-arm)* "}"
match-arm  ::= "|"? pattern ("|" pattern)* "->" expression
```

`match` evaluates its subject, then tries each arm's [patterns](patterns.md)
in order. The first arm that matches runs, with the pattern's variables bound,
and its value is the value of the `match`. All arms must have the same type.
The arms must cover every possible value of the subject
([Exhaustiveness](patterns.md#exhaustiveness)).

Arms are separated by line breaks or `;`. An arm's body is one expression,
which may be a block.

<!-- run -->
```kotlin
type Command = Push(Int) | Pop | Clear

fun apply(stack, command) = match command {
    Push(n) -> Array.push(stack, n)
    Pop -> {
        let _ = Array.pop(stack)
    }
    Clear -> Array.clear(stack)
}

fun main() {
    let stack = []
    for command in [Push(1), Push(2), Pop, Push(3)] {
        apply(stack, command)
    }
    print(stack)
}
```

```text
[1, 3]
```

## `while`

**Syntax**

```ebnf
while-expr ::= "while" expression block
```

`while c { B }` evaluates `c`, which must be a `Bool`, and runs `B` for as
long as it is `True`. Its type is `Unit`. `break` leaves the loop, and
`continue` goes straight to the next test of `c`.

<!-- run -->
```kotlin
fun main() {
    var n = 27
    var steps = 0
    while n != 1 {
        n = if n % 2 == 0 { n / 2 } else { 3 * n + 1 }
        steps = steps + 1
    }
    print(steps)
}
```

```text
111
```

## `while let` and `while var`

**Syntax**

```ebnf
while-let ::= "while" ("let" | "var") pattern "=" expression block
```

`while let p = e { B }` evaluates `e` and runs `B` while `p` matches. It is
exactly

```text
loop {
    match e {
        p -> B
        _ -> break
    }
}
```

so `e` is evaluated again before every iteration, including after a
`continue`. `while var` makes the bound variables assignable inside `B`.

<!-- run -->
```kotlin
fun main() {
    let pending = ["c", "b", "a"]
    while let Some(task) = Array.pop(pending) {
        print("doing " + task)
    }
}
```

```text
doing a
doing b
doing c
```

## `for` over a collection

**Syntax**

```ebnf
for-in ::= "for" pattern "in" expression block
```

`for x in e { B }` runs `B` once for each element of `e`, with `x` bound to
the element. Any type with an instance of the [`Iterator`](builtins.md#iterator)
class can be iterated. The library provides instances for arrays, maps, sets,
and a string's bytes and code points. The loop's type is `Unit`.

The loop pattern must be [irrefutable](patterns.md#irrefutable-patterns), so it
can take elements apart but cannot filter them:

<!-- run -->
```kotlin
fun main() {
    let stock = Map.new()
    stock["apples"] = 3
    stock["pears"] = 0
    for (fruit, count) in stock {
        if count == 0 {
            continue
        }
        print(fruit + ": " + show(count))
    }
    for c in String.codePoints("héllo") {
        if c == 'l' {
            break
        }
        print(c)
    }
}
```

```text
apples: 3
h
é
```

The loop asks the collection for a *cursor*, then asks for the next element
until there is none. It is approximately equivalent to:

```text
{
    let $cursor = iter(e)
    loop {
        match next(e, $cursor) {
            Some(x) -> B
            None -> break
        }
    }
}
```

`e` is evaluated once. `x` is a new immutable binding for each element; if the
element is a mutable record, `x` refers to the same record the collection
holds. See [`Iterator`](builtins.md#iterator) for how to make a type iterable.

## `for` with a counter

**Syntax**

```ebnf
for-c ::= "for" statement? ";" expression ";" statement? block
```

The C-style `for init; test; step { B }` runs `init` once, then repeats: test
`test`, which must be a `Bool`, and stop if it is `False`; run `B`; run `step`.
`continue` skips the rest of `B` but still runs `step`. Its type is `Unit`.

<!-- run -->
```kotlin
fun main() {
    for var row = 1; row <= 3; row = row + 1 {
        var line = ""
        for var col = 1; col <= row; col = col + 1 {
            line = line + "*"
        }
        print(line)
    }
}
```

```text
*
**
***
```

A variable declared in `init` is visible in the test, the step and the body,
and not after the loop. Either `init` or `step` may be left out, but both `;`
are required.

## `loop`

**Syntax**

```ebnf
loop-expr ::= "loop" block
```

`loop { B }` runs `B` forever, until a `break`, a `return`, or a panic. It is
the one loop that has a value: `break e` leaves the loop with the value `e`,
and every `break` in the same loop must carry a value of the same type. A
`loop` with no `break` never finishes, so it can stand where any type is
expected ([Diverging expressions](types.md#diverging-expressions)).

<!-- run -->
```kotlin
fun main() {
    var guess = 1
    let root = loop {
        if guess * guess >= 50 {
            break guess
        }
        guess = guess + 1
    }
    print(root)
}
```

```text
8
```

## `break` and `continue`

**Syntax**

```ebnf
break-expr    ::= "break" expression?
continue-expr ::= "continue"
```

`break` leaves the innermost enclosing loop, and `continue` starts its next
iteration. Only `loop` can be left with a value; `break e` inside a `while` or
`for` is an error. Using either outside a loop is an error. Neither produces a
value, so both can appear where any type is expected.

There are no loop labels: `break` and `continue` always refer to the innermost
loop.

## `return`

**Syntax**

```ebnf
return-expr ::= "return" expression?
```

`return e` leaves the innermost enclosing function, which may be a lambda,
with the value `e`. A bare `return` returns `()`. Like `break`, it produces no
value itself and can appear where any type is expected. A function whose body
is a block does not need `return` to produce its result, because the block's
last statement already is the result.

<!-- run -->
```kotlin
fun indexOf(xs, target) {
    var i = 0
    for x in xs {
        if x == target {
            return Some(i)
        }
        i = i + 1
    }
    None
}

fun main() {
    print(indexOf(["a", "b", "c"], "b"))
    print(indexOf(["a"], "z"))
}
```

```text
Some(1)
None
```

## Summary of types

| Construct | Type |
|---|---|
| `{ ...; e }` | the type of `e`, or `Unit` if the last statement is a declaration |
| `if c { A } else { B }` | the type of `A` and `B`, which must agree |
| `if c { A }` | `Unit` |
| `if let p = e { A } else { B }` | the type of `A` and `B` |
| `match e { ... }` | the type of the arms, which must agree |
| `while`, `while let`, `for` | `Unit` |
| `loop { ... }` | the type of its `break` values, `Unit` for a bare `break`, or any type if it has no `break` |
| `return`, `break`, `continue` | any type (they never produce a value) |
| assignment | `Unit` |
