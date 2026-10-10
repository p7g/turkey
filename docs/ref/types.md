# Types

Every Turkey expression has a type, checked when the program is compiled.
Types are inferred, so a program needs few annotations ([Type
inference](inference.md)). This chapter covers the types themselves: the
primitive types, tuples, function types, the data types a program declares,
type aliases, which values are mutable, and existential types.

## Type expressions

**Syntax**

```ebnf
type     ::= atype atype*                      -- application
atype    ::= IDENT                             -- type variable
           | qualified-CONID                   -- type constructor
           | "fun" "(" (type ("," type)*)? ")" "->" type
           | "(" type ")"
           | "(" type ("," type)+ ")"          -- tuple
qualified-CONID ::= CONID ("." CONID)*
```

A type is a type constructor or a type variable, applied to zero or more
arguments. Application is written by juxtaposition, as in `Option Int` or
`Map String (Array Int)`, and it associates to the left. Parentheses group.

A lowercase name in a type is a **type variable**. In a declaration it stands
for any type (`fun(Array a) -> Int`), and within one declaration the same name
stands for the same type everywhere it appears.

**Kinds.** Type constructors take arguments, and that is tracked by *kinds*.
A type that has values, such as `Int` or `Array String`, has kind `*`. `Array`
alone has kind `* -> *`: it takes one type and gives a type with values. A
type variable can stand for a constructor as well as for a complete type, so
`f a` is legal when `f` has kind `* -> *`. Kinds are never written. They are
worked out from how each type is used, and applying a type to too many or too
few arguments is an error:

<!-- error: 'Array' has kind * -> *, but a type of kind * is needed here -->
```kotlin
fun count(xs : Array) -> Int = len(xs)
```

> **For readers new to this.** A kind is the "type of a type". Kinds are what
> let a class such as `Functor` abstract over `Option` and `Array` themselves,
> rather than over `Option Int` or `Array String`.

## Primitive types

These types are built into the language:

| Type | Values |
|---|---|
| `Int` | 64-bit signed integers |
| `Float` | 64-bit IEEE 754 floating-point numbers |
| `Byte` | integers from 0 to 255 |
| `Char` | Unicode scalar values |
| `Unit` | the single value `()` |

`String`, `Bool`, `Option`, `Either` and `Array` are not primitive. They are
declared by the library, and the language refers to them by name: a string
literal is a `String`, `if` needs a `Bool`, and a `for` loop gets an `Option`
from its iterator. `String` is described [below](#string) with the primitive
types, because it is always in scope and its constructor is hidden, so a
program can use it only as it would a primitive type. See [Built-in types and
classes](builtins.md) for the others.

There are no implicit conversions between primitive types. Converting an
`Int` to a `Float` is an explicit call, `Float.fromInt(n)`. An *integer
literal* can still be used as a `Float`, because a literal's
type is chosen from context ([Numeric literals](inference.md#numeric-literals)).

### Int

An `Int` is a two's-complement signed 64-bit integer, from `-2^63` to
`2^63 - 1`, on every platform.

* `+`, `-`, `*` and unary `-` **panic** when the result does not fit. They
  never wrap around.
* `/` rounds toward zero, and `%` is the remainder, whose sign follows the
  dividend. So `-7 / 2` is `-3` and `-7 % 2` is `-1`, and
  `a == (a / b) * b + a % b` for every `b` that is not zero.
* `/` and `%` panic when the divisor is zero.

<!-- run -->
```kotlin
fun main() {
    print(-7 / 2)
    print(-7 % 2)
    print(7 / -2)
}
```

```text
-3
-1
-3
```

<!-- panic: integer overflow -->
```kotlin
fun main() {
    var n = 1
    for var i = 0; i < 64; i = i + 1 {
        n = n * 2
    }
    print(n)
}
```

The library module `Int` has arithmetic that wraps around or returns an
`Option` instead of panicking, a floored modulus (`Int.mod`), and the bitwise
operations. There are no bitwise operators. The shifts, `Int.shl` and
`Int.shr`, panic when the shift amount is outside 0 to 63 rather than masking
it, and `Int.shr` copies the sign bit.

### Float

A `Float` is an IEEE 754 binary64 number, and arithmetic on it follows IEEE
754 with round-to-nearest-even. Division by zero does not panic: it gives an
infinity, or NaN for `0.0 / 0.0`.

Comparisons follow IEEE 754 as well. Every comparison involving NaN is false,
including `==`, so `NaN == NaN` is false and `NaN != NaN` is true, and
`-0.0 == 0.0` is true. This makes `Float` the one type whose `Eq` and `Ord`
instances do not obey the usual laws: equality is not reflexive and ordering is
not total. There is no `%` on `Float`: `Float.fmod` and `Float.remainder`
are the C and IEEE 754 remainders, which round the quotient differently.

Two consequences follow. There is no `Hash` instance for `Float`, because a
key that is not equal to itself could be stored in a map and never found
again, so a `Float` cannot be a map key. And sorting by `<` puts NaN wherever
it happens to land. `Float.totalCompare` is the IEEE 754 total order, which
places every value, NaN included, and distinguishes `-0.0` from `0.0`:

<!-- run -->
```kotlin
fun main() {
    let nan = 0.0 / 0.0
    print(nan < 1.0)
    print(nan > 1.0)
    print(Float.totalCompare(nan, 1.0))
    print(Float.totalCompare(-0.0, 0.0))
}
```

```text
False
False
GT
LT
```

<!-- error: no instance for 'Hash Float' -->
```kotlin
fun main() {
    let prices = Map.new()
    prices[19.99] = "lunch"
}
```

Converting a `Float` to an `Int` can fail, so `Float.truncate` returns an
`Option Int`: `None` for NaN, the infinities, and anything outside the range of
`Int`. `Float.floor`, `Float.ceil`, `Float.round` and `Float.trunc` round
without leaving `Float`. `Float.fromInt` is exact up to `2^53` and rounds
beyond it.

<!-- run -->
```kotlin
fun main() {
    print(Float.truncate(-3.7))
    print(Float.truncate(1.0e300))
    print(Float.floor(-3.7))
}
```

```text
Some(-3)
None
-4.0
```

<!-- run -->
```kotlin
fun main() {
    let inf = 1.0 / 0.0
    let nan = 0.0 / 0.0
    print(inf)
    print(nan == nan)
    print(nan != nan)
    print(-0.0 == 0.0)
}
```

```text
Infinity
False
True
True
```

`show` of a `Float` is the shortest decimal that reads back as exactly the same
`Float`. It is written out in full when the value's decimal exponent is from
-4 to 15, so for magnitudes from `0.0001` up to just under `10^16`, and in
exponent form otherwise: one digit, a point, the remaining digits, `e`, a sign,
and an exponent of at least two digits. Either way there is a point with a
digit after it, `.0` if nothing else, so the text of a finite `Float` is a
valid float literal and never an integer one. Zero keeps its sign, and the
values with no literal print as `Infinity`, `-Infinity` and `NaN`.

<!-- run -->
```kotlin
fun main() {
    print(100.0)
    print(0.1 + 0.2)
    print(0.0001)
    print(0.00001)
    print(1.0e16)
    print(-2.5e-300)
    print(-0.0)
}
```

```text
100.0
0.30000000000000004
0.0001
1.0e-05
1.0e+16
-2.5e-300
-0.0
```

`Float.parse` reads a string back. It accepts a float literal without `_`
separators, with an optional leading `+` or `-`, and the three special names,
and nothing else: no surrounding space, no `1_000.0`, no `inf`. The result is the
`Float` nearest the decimal value, with an exact tie going to the one whose
last bit is zero; a value too large becomes an infinity and one too small a
zero, each keeping its sign.

<!-- run -->
```kotlin
fun main() {
    print(Float.parse("-1.5e3"))
    print(Float.parse("Infinity"))
    print(Float.parse("9007199254740993.0"))
    print(Float.parse("1_000.0"))
}
```

```text
Some(-1500.0)
Some(Infinity)
Some(9007199254740992.0)
None
```

### Byte

A `Byte` is an unsigned 8-bit integer. It exists to hold raw bytes, for
example the contents of a file, and it supports comparison but no arithmetic.
To compute with a byte, convert it to an `Int` with `Byte.toInt`.
`Byte.fromInt(n)` returns an `Option Byte` that is `None` when `n` is out of
range, and `Byte.truncate(n)` keeps the low eight bits. There is no byte
literal.

### Char

A `Char` is one Unicode scalar value: a code point from `0` to `10FFFF`,
excluding the surrogates `D800` to `DFFF`. It is written as a
[character literal](lexical.md#character-literals), such as `'a'` or
`'\u{1F600}'`.

A `Char` has no arithmetic, because adding to a scalar value means nothing
once text is encoded in a variable number of bytes. `Char.toInt` gives the
number, and `Char.fromInt` returns an `Option Char` that is `None` for a
surrogate or a number out of range:

<!-- run -->
```kotlin
fun main() {
    print(Char.toInt('A'))
    print(Char.fromInt(66))
    print(Char.fromInt(55296))
}
```

```text
65
Some(B)
None
```

A `Char` is not always what a reader would call a character. `é` can be one
scalar value, or two: `e` followed by a combining accent.

### String

`String` is in scope in every module without an import, like the primitive
types. A `String` is an immutable sequence of bytes that is always valid UTF-8. That
guarantee holds for every string a program can create: literals cannot contain
invalid UTF-8, and a string built from bytes is checked when it is built.

Strings are not indexed and have no `len`. A byte offset can land in the
middle of a character, and "length" could mean bytes, code points or visible
characters. The library offers each of those explicitly instead. `==` and `<`
compare strings byte by byte, and `+` concatenates:

<!-- run -->
```kotlin
fun main() {
    let greeting = "hello" + ", " + "world"
    print(greeting)
    print("apple" < "banana")
    print(String.byteLength("é"))
}
```

```text
hello, world
True
2
```

Comparing bytes is the same as comparing scalar values, one after another, and
nothing more. `==` does not normalize, so two strings that display the same
can be unequal, and `<` is not alphabetical order in any language: every
uppercase ASCII letter sorts before every lowercase one.

<!-- run -->
```kotlin
fun main() {
    print("\u{E9}" == "\u{65}\u{301}")
    print("Zebra" < "apple")
}
```

```text
False
True
```

### Unit

`Unit` has exactly one value, written `()`. It is the type of expressions that
are evaluated only for their effect: an assignment, a loop, a call to `print`,
or an `if` with no `else`. A function whose body produces nothing useful
returns `Unit`.

## Tuples

A tuple type is written `(A, B)`, `(A, B, C)`, and so on, and a tuple value is
written the same way with expressions: `(1, "one")`. A tuple has at least two
elements: `(x)` is just `x` in parentheses, `(x,)` is an error, and `()` is the
unit value rather than an empty tuple.

Tuples are immutable. Their elements are read with a
[pattern](patterns.md#tuple-patterns) or with
[projection](expressions.md#tuple-projection), `t.0`, `t.1`, and so on.

<!-- run -->
```kotlin
fun divide(a, b) = (a / b, a % b)

fun main() {
    let (quotient, remainder) = divide(17, 5)
    print(quotient)
    print(remainder)
    print(divide(9, 4).1)
}
```

```text
3
2
1
```

## Function types

**Syntax**

```ebnf
function-type ::= "fun" "(" (type ("," type)*)? ")" "->" type
```

`fun(A, B) -> C` is the type of a function that takes two arguments, of types
`A` and `B`, and returns a `C`. Functions are values: they can be stored in
variables, records and arrays, passed as arguments, and returned.

Functions are **uncurried**. A function takes all of its arguments at once,
and the number of arguments is part of its type. `fun(Int, Int) -> Int` and
`fun(Int) -> fun(Int) -> Int` are different types, and there is no partial
application. A function that should take its arguments one at a time returns a
function explicitly:

<!-- run -->
```kotlin
fun adder(n : Int) -> fun(Int) -> Int = fun(x) = x + n

fun applyTwice(f : fun(Int) -> Int, x : Int) -> Int = f(f(x))

fun main() {
    let addTen = adder(10)
    print(applyTwice(addTen, 1))
}
```

```text
21
```

A function with no parameters has type `fun() -> T`.

> **Coming from Haskell.** `fun(A, B) -> C` is not `A -> B -> C`, and there is
> no arrow type without `fun`. A multi-argument function is closer to one that
> takes a tuple, except that its arity is fixed and its arguments are passed
> without building a tuple.

## Data types

**Syntax**

```ebnf
type-decl    ::= "type" CONID IDENT* "=" constructor ("|" constructor)*
constructor  ::= CONID existential? payload?
payload      ::= "(" type ("," type)* ")"          -- positional
               | "{" field (separator field)* "}"   -- record
field        ::= "var"? IDENT ":" type
separator    ::= "," | line break
existential  ::= "[" (IDENT | CONID type) ("," (IDENT | CONID type))* "]"
```

A `type` declaration introduces a new type and the **constructors** that build
its values. A value of the type is exactly one of the constructors, carrying
that constructor's payload.

<!-- run -->
```kotlin
type Suit = Hearts | Diamonds | Clubs | Spades

type Card = Card(Int, Suit)

type Hand = Empty | Holding(Card, Hand)

fun isRed(suit) = match suit {
    Hearts | Diamonds -> True
    Clubs | Spades -> False
}

fun countRed(hand) = match hand {
    Empty -> 0
    Holding(Card(_, suit), rest) -> {
        let here = if isRed(suit) { 1 } else { 0 }
        here + countRed(rest)
    }
}

fun main() {
    let hand = Holding(Card(10, Hearts), Holding(Card(3, Spades), Empty))
    print(countRed(hand))
}
```

```text
1
```

The parts of a declaration:

* **Constructors with no payload**, such as `Hearts`, are values on their own.
* **Positional payloads** are listed in parentheses, like a function's
  parameters: `Card(Int, Suit)`. A constructor with a payload is used like a
  function, `Card(10, Hearts)`, and taken apart with a pattern, `Card(rank, suit)`.
  The parentheses are required, even for a single field.
* **Record payloads** name their fields: see [Records](#records).
* **Type parameters** follow the type's name. `type Pair a b = Pair(a, b)`
  declares a type constructor of two arguments. Every type variable in a
  constructor must be one of the parameters, unless it is bound by an
  [existential bracket](#existential-types). A parameter that no constructor
  uses is allowed; it is sometimes called a *phantom* parameter.
* **Recursion.** A type may refer to itself, as `Hand` does, and a group of
  types may refer to each other.

A single constructor may have the same name as its type, as in
`type Card = Card(Int, Suit)`. The two live in different namespaces: `Card`
in a type is the type, and `Card` in an expression or pattern is the
constructor.

A type's constructors are taken apart with [`match`](statements.md#match) and
the other [pattern](patterns.md) forms. A value of a type with one positional
constructor can also be [projected](expressions.md#tuple-projection) like a
tuple.

> **Coming from Rust.** A `type` with several constructors is an `enum`, and
> one with a single record constructor is a `struct`. Constructors are not
> namespaced under the type: `Hearts`, not `Suit::Hearts`.

## Records

A constructor whose payload is written in braces is a **record**: its fields
have names.

<!-- run -->
```kotlin
type Item = Item {
    name : String
    price : Int
    quantity : Int
}

fun total(item) = item.price * item.quantity

fun main() {
    let pens = Item { name = "pen", price = 3, quantity = 12 }
    print(total(pens))
}
```

```text
36
```

Fields are separated by commas, or by line breaks when each field is on its
own line. The same holds wherever fields are listed: in a declaration, in a
[record expression](expressions.md#record-construction), and in a
[record pattern](patterns.md#record-patterns). A trailing comma is allowed.
Field names must be distinct within one constructor. Different types may use
the same field names. A field marked `var` can be assigned after the value is
built; every other field keeps the value it was built with (see
[Mutability and sharing](#mutability-and-sharing)).

A constructor has at most 21 fields, positional or named, and a tuple at most
21 elements. A wider value is an error where it is declared or written; group
related fields into a record of their own instead.

<!-- error: constructor 'Wide' has 22 fields, and a value can have at most 21 -->
```kotlin
type Wide = Wide {
    f0 : Int, f1 : Int, f2 : Int, f3 : Int, f4 : Int, f5 : Int
    f6 : Int, f7 : Int, f8 : Int, f9 : Int, f10 : Int, f11 : Int
    f12 : Int, f13 : Int, f14 : Int, f15 : Int, f16 : Int, f17 : Int
    f18 : Int, f19 : Int, f20 : Int, f21 : Int
}
```

A record constructor is still a constructor with a payload. `Item("pen", 3, 12)`
builds the same value as the record expression above, with the fields in
declaration order, and `Item(n, p, q)` is a valid pattern for it.

**Field access** with `.name` is available when the type has exactly one
constructor, and that constructor is a record. A type with several
constructors is taken apart with `match`, even if all of its constructors are
records:

<!-- error: cannot read field 'radius': 'Shape' is not a single-variant record type -->
```kotlin
type Shape = Circle { radius : Float } | Square { side : Float }

fun radius(s : Shape) -> Float = s.radius
```

## Type aliases

A `type` declaration whose right-hand side is an existing type, rather than a
constructor, declares an **alias**. The alias and the type it stands for are
interchangeable everywhere.

<!-- run -->
```kotlin
type Grid = Array (Array Int)
type Pair a = (a, a)

fun swap(p : Pair Int) -> Pair Int = (p.1, p.0)

fun main() {
    let g : Grid = [[1, 2], [3, 4]]
    print(g[1][0])
    print(swap((1, 2)))
}
```

```text
3
(2, 1)
```

The right-hand side of `type T = C args` can read either way: as an alias for
an existing type `C`, or as a data type with one constructor named `C`. The
compiler decides as follows:

1. If there is a `|`, or the constructor has a `{ ... }` payload, it is a data
   type.
2. Otherwise, if `C` names a type that is already known (a primitive, a type
   declared anywhere in the same module, or an imported type), it is an alias.
3. Otherwise it is a data type with one constructor named `C`. In particular
   `type Meters = Meters(Float)`, whose constructor has the type's own name, is
   always a data type.

The right-hand side of an alias must be a type with values, so `type A = Array`
is an error. An alias must be given all of its arguments wherever it is used,
because it cannot be partially applied, and an alias cannot refer to itself.

<!-- error: an alias cannot be partially applied -->
```kotlin
type Pair a = (a, a)

fun first(p : Pair) = p.0
```

> **Coming from Haskell.** An alias is `type`, and a data type is also `type`.
> Turkey has no `newtype`: `type Meters = Meters(Float)` is an ordinary data
> type with one constructor.

## Mutability and sharing

Whether a value can be changed depends on its type, not on how it was bound.
A record field can be assigned only if its declaration marks it `var`:

<!-- check -->
```kotlin
type Account = Account { owner : String, var balance : Int }
```

* **Mutable:** arrays, whose elements can be assigned; `Vec`s, whose
  elements can be assigned and which can also grow and shrink; and records
  with at least one `var` field. An `Array` keeps the length it was made
  with ([`Array`](builtins.md#array)). Only a record's `var` fields can be
  assigned ([Assignment](statements.md#assignment)); `owner` above is fixed
  when the `Account` is built.
* **Immutable:** everything else. That is every primitive type, every string,
  every tuple, every function, every positional constructor, every record
  with no `var` field, and every type with more than one constructor, even
  when the constructors are records.

`var` is allowed only in a type with exactly one constructor, and not in an
existential one. An assignment `r.name = e` names a field of whatever `r`
holds, and in a type with several constructors that would be a field only
some of its values have:

<!-- error: field 'radius' cannot be 'var': 'Shape' has more than one constructor -->
```kotlin
type Shape = Circle { var radius : Float } | Square { side : Float }
```

Mutable values have **reference semantics**. Creating one allocates a single
object, and every binding, parameter, field or array element that holds it
refers to that same object. A change made through one reference is visible
through all of them:

<!-- run -->
```kotlin
type Account = Account { owner : String, var balance : Int }

fun deposit(account, amount) {
    account.balance = account.balance + amount
}

fun main() {
    let alice = Account { owner = "alice", balance = 10 }
    let alsoAlice = alice
    deposit(alsoAlice, 5)
    print(alice.balance)

    let xs = [1, 2]
    let ys = xs
    ys[0] = 7
    print(xs)

    let stack = [1, 2]
    let same = stack
    Vec.push(same, 3)
    print(stack)
}
```

```text
15
[7, 2]
[1, 2, 3]
```

Immutable values can never be changed, so a program cannot tell whether they
are copied or shared. The same holds for a field that is not `var`: reading
it through any reference gives the value the record was built with.

Assigning a field that is not `var` is an error, and the message says where
the field is declared, since that is where the fix goes:

<!-- error: cannot assign to field 'owner': it is not 'var' in 'Account' -->
```kotlin
type Account = Account { owner : String, var balance : Int }

fun main() {
    let account = Account { owner = "alice", balance = 10 }
    account.owner = "bob"
}
```

A function that assigns a field accepts every record type whose field of that
name is `var` ([Field constraints](inference.md#field-and-projection-constraints)),
so when the record's type is only known at the call, the error is reported
there:

<!-- error: cannot assign to field 'owner': it is not 'var' in 'Account' -->
```kotlin
type Account = Account { owner : String, var balance : Int }

fun rename(holder, name) {
    holder.owner = name
}

fun main() {
    rename(Account { owner = "alice", balance = 10 }, "bob")
}
```

`let` and `var` control the *binding*, not the object. A `let` binding cannot
be pointed at a different value, but if it holds a mutable object, the
object's fields can still be assigned, as `deposit` does above
([`let` and `var`](declarations.md#let-and-var)).

> **Coming from Rust.** There is no ownership or borrowing. A record with
> `var` fields behaves like a reference-counted object whose `var` fields are
> interior-mutable, and any number of references to it may write to them.

> **Coming from OCaml or F#.** `var` on a field is `mutable` on a record
> field: records are immutable unless a field says otherwise.

## Existential types

A constructor can hide the type of part of its payload. The hidden types are
listed in square brackets after the constructor's name:

<!-- run -->
```kotlin
type Shown = Shown[Show a](a)

fun main() {
    let row = [Shown(42), Shown("text"), Shown(Some(1.5))]
    for cell in row {
        match cell {
            Shown(value) -> print(show(value))
        }
    }
}
```

```text
42
text
Some(1.5)
```

`Shown[Show a](a)` says that a `Shown` holds a value of *some* type `a` that
has a `Show` instance. Each `Shown` can hold a different type, so an array of
them can mix integers, strings and options, and they all have the one type
`Shown`.

Each entry in the bracket is either a bare type variable, which hides a type
with no requirements, or a class applied to a type variable, as in `Show a`,
which hides the type and also stores that class's instance for it. A variable
in the bracket must not also be one of the type's parameters.

**Building** an existential value is an ordinary constructor call. Its hidden
types are the types of the arguments, and the instances the bracket asks for
must exist for those types.

**Opening** an existential value is done with a constructor pattern, in a
`match` arm or a function parameter. Inside that arm or function, the hidden
type is a new type, distinct from every other type, about which the program
knows only what the bracket said: in the example above, only that `value` has
a `Show` instance. The hidden type cannot leave the place where it was opened,
so a function cannot return the unwrapped value:

<!-- error: cannot escape the pattern 'Shown' that opened it -->
```kotlin
type Shown = Shown[Show a](a)

fun unwrap(s) = match s {
    Shown(value) -> value
}
```

An existential pattern may not appear in a `let`, a `var`, a `for` loop's
pattern, or a `match` arm that has `|` alternatives.

A record constructor can be existential too. Its fields can hold functions
over the hidden type, so a value can package state together with the
operations on it:

<!-- run -->
```kotlin
type Counter = Counter[s] {
    start : s
    step : fun(s) -> s
    report : fun(s) -> String
}

fun runCounter(Counter { start, step, report }, times) {
    var state = start
    for var i = 0; i < times; i = i + 1 {
        state = step(state)
    }
    report(state)
}

fun main() {
    let ticks = Counter { start = 0, step = fun(n) = n + 1, report = show }
    let marks = Counter { start = "", step = fun(s) = s + "|", report = fun(s) = s }
    print(runCounter(ticks, 3))
    print(runCounter(marks, 3))
}
```

```text
3
|||
```

An existential record is immutable, and its fields cannot be read with `.`,
because the type of a field that mentions the hidden type has no name outside
a pattern.

> **For readers new to this.** An *existential type* is a type that is known
> to exist but whose identity is hidden: "some type `a` with a `Show`
> instance". It is the dual of a type parameter. A function with a type
> parameter must work for *every* type its caller picks, while an existential
> value was built at *one* type that its user may not know.
>
> **Coming from Rust.** `Shown[Show a](a)` is close to `Box<dyn Show>`,
> except that the hidden type is also available inside the pattern that opens
> it, so it can relate several fields to each other, as `Counter` does.
>
> **Coming from Haskell.** This is `data Shown = forall a. Show a => Shown a`,
> with the same restriction on `let`.

## Diverging expressions

Some expressions never produce a value: `return`, `break`, `continue`, a call
to `error`, and a `loop` that contains no `break`. Such an expression fits
wherever any type is expected, so it can be used in one branch of an `if` or
one arm of a `match` without disturbing the type of the other branches:

<!-- run -->
```kotlin
fun first(xs : Array Int) -> Int {
    if len(xs) == 0 { error("first of an empty array") } else { xs[0] }
}

fun main() {
    print(first([4, 5]))
}
```

```text
4
```

`error` has the type `fun(String) -> a`: its result type is a variable that
its parameter does not mention, so a call to it fits any type, just as the
keywords above do.
