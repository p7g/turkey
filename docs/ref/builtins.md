# Built-in types and classes

Several pieces of syntax are defined in terms of library types and classes:
`if` needs a `Bool`, `+` calls `Add`, a `for` loop calls `Iterator`, `a[i]`
calls `Index`, `a[i] = v` calls `SetIndex`, and `?` calls `Monad`. This
chapter describes those types and classes, and the Prelude that makes them
available. It does not document the rest of the library.

## The Prelude

Every module implicitly imports the module `Prelude`. A module that imports
`Prelude` explicitly gets that import instead of the implicit one, so
`import Prelude (print)` brings in only `print`.

The Prelude provides these names unqualified:

| Kind | Names |
|---|---|
| Types and their constructors | `Int`, `Float`, `Byte`, `Char`, `Unit`, `Bool`, `Option`, `Either`, `Ordering`, `SomeError`, and the types `String`, `Array`, `Vec` and `Map` without their constructors |
| Classes and their methods | `Eq`, `Ord`, `Add`, `Sub`, `Mul`, `Div`, `Rem`, `Neg`, `Zero`, `One`, `Show`, `Iterator`, `Index`, `SetIndex`, `Length`, `Functor`, `Applicative`, `Monad`, `Semigroup`, `Monoid`, `Foldable`, `Hash`, `Error`, `Typed`, `Object`, `Debug` |
| Functions | `print`, `write`, `dbg`, `error`, `fail`, `const`, `pipe`, `fmap`, `min`, `max`, `hash` |
| Operators | `+` `-` `*` `/` `%`, `==` `!=` `<` `<=` `>` `>=`, `&` `\|` `^` `<<` `>>`, `<>`, `\|>` `\|>>` `<&>` `<$>` ([Operators](expressions.md#operators)) |

With the Prelude, a module also imports the library modules `Data.Array`,
`Data.Bool`, `Data.Byte`, `Data.Char`, `Data.Either`, `Data.Error`,
`Data.Float`, `Data.Int`, `Iter`, `Data.Map`, `Data.Option`,
`Data.Ordering`, `Data.String` and `Data.Vec` under the last part of their
names, as `import Data.Vec as Vec` would. That is why `Vec.push` and
`Int.parse` need no import, while the bare names `push` and `parse` stay free
for a program to use. An explicit Prelude import keeps these, and only
`import Prelude ()` leaves them out.

No module a program can import exports the constructor of `String`. A
program reads a string's bytes with `String.toBytes`, which copies them,
`String.bytes`, which iterates over them ([String](types.md#string)), or
`String.view`, which reads them in place and cannot write
([`ByteView`](#byteview)).

A type is in scope only through an import, the implicit Prelude import
included. A module that replaces the Prelude import imports the types it
names, as it does anything else:

<!-- error: unknown type 'Int' -->
```kotlin
import Prelude (print)

fun double(n : Int) -> Int = n * 2

fun main() {
    print(double(21))
}
```

`import Prelude (print, Int)` fixes it. The unit type can always be written
`()`, which is syntax rather than a name ([Unit](types.md#unit)).

## `Bool`

```text
type Bool : Ord = False | True
```

An ordinary data type. `if`, `while`, the test of a C-style `for`, `&&`,
`||` and `!` all require this type. `==` and the other comparisons return it.
Its `Eq`, `Ord` and `Hash` are derived ([Derived instances](classes.md#derived-instances)),
so `False < True`.

## `Option`

```text
type Option a = None | Some(a)
```

A value that may be absent. The `Iterator` class's `next` returns an
`Option` to say whether there is another element, and `Option` is a `Monad`
whose `?` stops at the first `None` ([`?`](do-notation.md)).

## `Either`

```text
type Either l r = Left(l) | Right(r)
```

A value that is one of two things, conventionally an error (`Left`) or a
result (`Right`). `Either l` is a `Monad` whose `?` passes a `Left` through
and continues with the value in a `Right`.

When both sides are `Error`s, so is `Either l r`, and its `message` is the
message of the side it holds. That is how one error type can say "this or
that failed" without a type of its own:

<!-- run -->
```kotlin
type Unreadable = Unreadable
type Empty = Empty

instance Error Unreadable { fun message(e) = "the file could not be read" }
instance Error Empty { fun message(e) = "the file is empty" }

fun load(text : Option String) -> Either (Either Unreadable Empty) String = match text {
    None -> Left(Left(Unreadable))
    Some("") -> Left(Right(Empty))
    Some(s) -> Right(s)
}

fun main() {
    for text in [Some("hello"), Some(""), None] {
        match load(text) {
            Right(s) -> print(s)
            Left(e) -> print(message(e))
        }
    }
}
```

```text
hello
the file is empty
the file could not be read
```

## `Array`

`Array a` is a fixed-length, mutable sequence of `a`, with
[reference semantics](types.md#mutability-and-sharing). Its length is set when
it is made and never changes, but its elements can be assigned. Its
constructor is hidden: arrays are made with
[array literals](expressions.md#array-literals) and with library functions
such as `Array.filled` and `Array.init`. `Array.init(n, f)` is an array of
length `n` whose element `i` is `f(i)`, computed in order from `0`:

<!-- run -->
```kotlin
fun main() {
    print(Array.init(5, fun(i) = i * i))
}
```

```text
[0, 1, 4, 9, 16]
```

Arrays are indexed from zero by `Int` ([Indexing](expressions.md#indexing)),
their length is `len(xs)`, `for x in xs` visits their elements in order, and
`+` concatenates two arrays into a new one. Reading or writing outside
`0 .. len(xs) - 1` panics. `Array` is a `Monad`, whose `?` runs the rest of the
block once per element.

Assigning an element changes the array in place; nothing changes its length:

<!-- run -->
```kotlin
fun main() {
    let counts = Array.filled(3, 0)
    for word in ["a", "bb", "a", "ccc"] {
        let n = String.byteLength(word) - 1
        counts[n] = counts[n] + 1
    }
    print(counts)
}
```

```text
[2, 1, 1]
```

So the module `Array` has no `push`, `pop` or `clear`. A sequence that grows
is a [`Vec`](#vec):

<!-- error: 'Array.push' is not defined -->
```kotlin
fun main() {
    let xs = [1, 2]
    Array.push(xs, 3)
}
```

> **Coming from Rust:** `Array a` is not `[T; N]`: its length is not part of
> its type, and two arrays of different lengths have the same type. It is
> closest to a `Box<[T]>`, a boxed slice, shared by reference. `Vec` is
> Rust's `Vec`.

> **Coming from Haskell:** an `Array` is mutable. Assigning `xs[i] = v`
> changes the array every other reference to it sees; it is not a new array
> with one element replaced. It is closest to an `IOArray Int a` indexed from
> zero.

## `Vec`

`Vec a` is a growable, mutable sequence of `a`: its length changes as
`Vec.push` adds an element at the end, `Vec.pop` removes the last one, and `Vec.clear` removes them all. It
has the same instances as `Array` (indexing, `len`, `for`, `+`, `Show`,
`Monad` and the rest), and the module `Vec` has the same functions as `Array`,
such as `Vec.map`, `Vec.sort` and `Vec.slice`. Its constructor is hidden too: a
`Vec` is made with an [array literal](expressions.md#array-literals) that is
used as one, with `Vec.new` or `Vec.filled`, or by copying an array with
`Vec.fromArray`. `Vec.toArray` copies it back.

<!-- run -->
```kotlin
fun main() {
    let stack = []
    Vec.push(stack, "a")
    Vec.push(stack, "b")
    print(Vec.pop(stack))
    print(stack)
}
```

```text
Some("b")
["a"]
```

Nothing has to say that `stack` is a `Vec`: `Vec.push` takes one, so the
literal it was made from is one ([Array literals](inference.md#array-literals)).

`Map k v`, a hash map, also has `Index`, `Length` and `Iterator` instances,
and iterates over `(key, value)` pairs in insertion order.

## `Set`

`Set a`, in the module `Data.Set`, is a mutable hash set of `a`, for any `a`
with `Hash`. It is not in the Prelude, so a program imports it, usually as
`import Data.Set as Set`. `Set.new()` or `empty()` makes an empty set, and
`Set.fromIterator(xs)` a set of the elements of anything `for` can walk.
`Set.add` and `Set.remove` change a set in place, `Set.contains` tests for an
element, and `len`, `for` and `Set.elements` see the elements in the order
they were added. Adding an element the set holds changes nothing, and an
element removed and added again goes last.

`Set.union`, `Set.intersection`, `Set.difference` and
`Set.symmetricDifference` return a new set and leave their operands alone.
The operators are those functions: `a | b` is the union, `a & b` the
intersection, `a - b` the elements of `a` not in `b`, and `a ^ b` the
elements in exactly one of them. `a <> b` is the union too, and `empty()` is
its identity. `Set.isSubset(a, b)`, `Set.isDisjoint(a, b)` and
`Set.isEmpty(a)` test sets.

`Set.unionInto(into, from)` is the one that changes a set in place. It adds
the elements of `from` to `into`, and returns whether that added any. Grow a
set in a loop with it or with `Set.add`: `seen = seen | more` copies `seen`
every time.

<!-- run -->
```kotlin
import Data.Set as Set
import Data.Set (Set)

fun main() {
    let odd = Set.fromIterator([1, 3, 5, 7])
    let prime = Set.fromIterator([2, 3, 5, 7])
    print(odd & prime)
    print(odd - prime)
    print(odd ^ prime)
    print(Set.isSubset(odd & prime, odd))

    let seen = empty() : Set Int
    for step in [[1, 2], [2, 1]] { print(Set.unionInto(seen, Set.fromIterator(step))) }
    print(seen)
}
```

```text
{3, 5, 7}
{1}
{1, 2}
True
True
False
{1, 2}
```

## Operator classes

Each arithmetic, comparison and bitwise operator calls a class method
([Operators](expressions.md#operators-are-class-methods)):

```text
class Add a { fun add(a, a) -> a }
class Sub a { fun sub(a, a) -> a }
class Mul a { fun mul(a, a) -> a }
class Div a { fun div(a, a) -> a }
class Rem a { fun rem(a, a) -> a }
class Neg a { fun neg(a) -> a }
class Zero a { fun zero() -> a }
class One a { fun one() -> a }

class Eq a {
    fun eq(a, a) -> Bool
    fun ne(x : a, y : a) -> Bool = !eq(x, y)
}

class Ord a : Eq a {
    fun lt(a, a) -> Bool
    fun lte(x : a, y : a) -> Bool = lt(x, y) || eq(x, y)
    fun gt(x : a, y : a) -> Bool = lt(y, x)
    fun gte(x : a, y : a) -> Bool = !lt(x, y)
}

class BitAnd a { fun and(a, a) -> a }
class BitOr a { fun or(a, a) -> a }
class BitXor a { fun xor(a, a) -> a }

class Shift a {
    fun shl(a, Int) -> a
    fun shr(a, Int) -> a
}
```

`Int` and `Byte` have instances of the four bitwise classes
([Bitwise operators](expressions.md#bitwise-operators)), and `Set` has
`BitAnd`, `BitOr` and `BitXor` for intersection, union and symmetric
difference ([`Set`](#set)).

The Prelude's `min(x, y)` and `max(x, y)` answer the lesser and the greater
of two values of any `Ord` type. When neither is less than the other, `min`
answers `x` and `max` answers `y`:

<!-- run -->
```kotlin
type Entry = Entry { score : Int, name : String }

instance Eq Entry { fun eq(a, b) = a.score == b.score }
instance Ord Entry { fun lt(a, b) = a.score < b.score }

fun main() {
    print(max(3, 7))
    let ada = Entry { score = 9, name = "Ada" }
    let bo = Entry { score = 9, name = "Bo" }
    print(min(ada, bo).name + " " + max(ada, bo).name)
}
```

```text
7
Ada Bo
```

The instances the library provides for built-in types:

| Type | `Eq` | `Ord` | `Hash` | `Add` | `Sub` `Mul` `Div` `Neg` | `Rem` | `Show` |
|---|---|---|---|---|---|---|---|
| `Int` | yes | yes | yes | yes | yes | yes | yes |
| `Float` | yes | yes | no | yes | yes | no | yes |
| `Byte`, `Char`, `Bool` | yes | yes | yes | no | no | no | yes |
| `String` | yes | yes | yes | concatenation | no | no | yes |
| tuples of 2 to 4 elements | if the elements have it | if the elements have it | if the elements have it | no | no | no | if the elements have it |
| `Option a`, `Either l r` | if the contents have it | no | if the contents have it | no | no | no | if the contents have it |
| `Array a`, `Vec a` | if the elements have it | if the elements have it | no | concatenation | no | no | if the elements have it |
| `Map k v` | if `k` has `Hash` and `v` has `Eq` | no | no | no | no | no | if the keys and values have it |
| `Set a` (module `Data.Set`) | if `a` has `Hash` | no | no | no | `Sub` only, difference | no | if the elements have it |
| `Deque a` (module `Deque`) | if the elements have it | no | no | concatenation | no | no | if the elements have it |
| `PriorityQueue a` (module `PriorityQueue`) | no | no | no | no | no | no | if the elements have it |
| `Unit` | yes | no | yes | no | no | no | no |

Tuples compare lexicographically, and strings compare byte by byte. Arrays and
`Vec`s compare lexicographically too, and one that is a proper prefix of
another is the lesser. `Int`
and `Float` have `Zero` and `One`, the identities of `+` and `*`. Two
arrays are equal when they have the same length and equal elements at each
index, and so are two `Vec`s or two `Deque`s, whatever their capacity. Two
maps are equal when they have the same keys with equal values, and two `Set`s
when they have the same elements, whatever order the entries were added in.

<!-- run -->
```kotlin
fun main() {
    let stack = []
    Vec.push(stack, 1)
    Vec.push(stack, 2)
    print(stack == [1, 2])

    let ages = Map.new()
    Map.put(ages, "Ada", 36)
    Map.put(ages, "Bo", 41)
    let again = Map.new()
    Map.put(again, "Bo", 41)
    Map.put(again, "Ada", 36)
    print(ages == again)
}
```

```text
True
True
```

## `Show` and printing

```text
class Show a { fun show(a) -> String }
class Debug a { fun debug(a) -> String }

fun print[Show a](x : a) -> Unit
fun write[Show a](x : a) -> Unit
fun dbg[Debug a](x : a) -> a
```

Two classes turn a value into text. `Show` is text for the program's user,
and `Debug` is text for its programmer.

`show` converts a value to display text. `print` writes `show(x)` and a line
break to standard output; `write` does the same without the line break.
`show` of a `String` is the string itself, without quotes. A type gets a
`Show` instance only if someone writes one; `Show` is never derived.

`debug` converts a value to text that shows its structure, written as Turkey
would build it. Every type whose fields have `Debug` has it: the compiler
derives an instance for each type declaration with none written, and the
library writes those for the built-in types. A type whose fields do not all
have one, such as `type T f = T(f Int)`, has none, as [Derived
instances](classes.md#derived-instances) says under "When there is no
implicit instance".
`dbg(x)` writes `debug(x)` and a line break to standard error, after what
standard output holds, and returns `x`, so it can wrap any expression.

* A constructor prints as it is written, `Some(1)` or `Pair(1, 2)`, and a
  record with its field names, `Point { x = 1, y = 2 }`.
* A string prints in double quotes, a character in single quotes, with `"`
  or `'`, `\` and every control character escaped as a literal would write
  them.
* A `Float` prints with the shortest digits that read back as the same
  number. A `Byte` has no literal, so it prints as the integer it holds.
* An array or a `Vec` prints as an array literal, a `Map` as
  `{key: value, ...}` and a `Set` as `{element, ...}`, in the order the
  entries were first added.
* A `Deque` prints as the call that builds it, `Deque.fromArray([x, ...])`,
  front to back, and a `PriorityQueue` as `PriorityQueue.fromArray([x, ...])`
  with its elements in no particular order.
* What has no syntax prints as a placeholder: a function as `<fun>`, and a
  field whose type mentions a type an [existential
  constructor](types.md#existential-types) hides as `<hidden>`, unless its
  `Debug` follows from the classes the constructor carries.

A container's `Show` shows its elements' `Debug`. The `Show` of `Array`,
`Vec`, `Map`, `Set`, `Deque`, `PriorityQueue`, `Option`, `Either` and the
tuples is their `Debug`, so a string inside one is quoted and two lists of
strings cannot print alike:

<!-- run -->
```kotlin
type Point = Point { x : Int, y : Int }

type Shape = Circle(Point, Float) | Dot

fun main() {
    print("say \"hi\"")
    print(debug("say \"hi\""))
    print(["a,b", "c"])
    print(["a", "b,c"])
    print(Some('x'))
    print(debug([Circle(Point { x = 1, y = 2 }, 0.5), Dot]))
    print(dbg(2 + 3) * 2)
}
```

```text
say "hi"
"say \"hi\""
["a,b", "c"]
["a", "b,c"]
Some('x')
[Circle(Point { x = 1, y = 2 }, 0.5), Dot]
10
```

The last line also writes `5` to standard error.

**Cycles.** Only an object, an array or a record with a `var` field, can
come to contain itself, through an assignment. `debug` keeps the objects it
is in the middle of printing, and an object met again inside itself prints
as `<cycle>`. An object reached twice by different routes, and not inside
itself, prints twice:

<!-- run -->
```kotlin
type Node = Node { value : Int, var next : Option Node }

fun main() {
    let a = Node { value = 1, next = None }
    let b = Node { value = 2, next = Some(a) }
    a.next = Some(b)
    print(debug(a))
    let shared = [1]
    print(debug([shared, shared]))
}
```

```text
Node { value = 1, next = Some(Node { value = 2, next = Some(<cycle>) }) }
[[1], [1]]
```

**Hiding a type's fields.** `!Debug` in a type's [derive
list](classes.md#derived-instances) keeps its instance and makes it print as
`<Name>` without reading a field: for a secret, or for state that may be read
only under a protocol, as a lock's contents may:

<!-- run -->
```kotlin
type Password : !Debug = Password(String)

fun main() {
    print(Some(Password("hunter2")))
}
```

```text
Some(<Password>)
```

**Writing one by hand.** A `Debug` instance written by hand replaces the
derived one. What it prints is checked for cycles at each object it reaches
through an instance:

* an instance for a type whose every value is an object, such as a record
  with a `var` field or a wrapper over an array, is checked as a derived one
  is, with nothing written for it;
* an instance for a value type is not checked itself. A cycle through it
  ends only if it passes through an object some checked instance prints. One
  that reads an array's elements itself and calls `debug` on each, rather
  than calling `debug` on the array, can run until the stack overflows.

A cycle through a checked instance prints `<cycle>` where it comes back to
the object:

<!-- run -->
```kotlin
type Stack = Stack { var items : Array Stack, name : String }

instance Debug Stack {
    fun debug(s) = s.name + " holding " + debug(s.items)
}

fun main() {
    let s = Stack { items = [], name = "s" }
    s.items = [s]
    print(debug(s))
}
```

```text
s holding [<cycle>]
```

### When standard output is written

Standard output is buffered: `print` and `write` add their text to one buffer
that the whole program shares, and the buffer is written to the operating
system later. Whether standard output is a terminal is checked once, when the
program starts. The buffer is written out:

* after every line, when standard output is a terminal;
* otherwise, when it fills (it holds 64 KiB), up to its last complete line.
  A single line longer than the buffer is written as it stands;
* before each write to standard error through `System.IO.stderr()`, so that
  the two streams come out in the order the program wrote them;
* before each read of standard input through `System.IO.stdin()`, when
  standard input is a terminal, so that a prompt appears before the program
  waits for an answer;
* when the program ends: when `main` returns, when `System.Env.exit` is called,
  and when the program panics, before the panic's message;
* when the program calls `System.IO.flush(System.IO.stdout())`.

A line printed by `print` is added whole, so lines printed by tasks running at
the same time never mix within a line. Output still in the buffer is lost only
if the process is killed by a signal.

`print` and `write` do not report a failed write, so a program whose standard
output is a closed pipe need not test every line it prints. `flush` reports
the first failure since the last `flush`.

<!-- run -->
```kotlin
import System.IO as IO

fun main() {
    write("working... ")
    -- Without this, a program whose output goes to a pipe would show nothing
    -- until the buffer filled or the program ended.
    match IO.flush(IO.stdout()) {
        Left(e) -> {
            let _ = IO.writeText(IO.stderr(), message(e) + "\n")
        }
        Right(_) -> {}
    }
    print("done")
}
```

```text
working... done
```

> **Coming from Rust:** Rust's `println!` writes every line as it is printed,
> even into a pipe. Turkey's output to a file or a pipe is written a buffer at
> a time, as C's `printf` does, and a program that talks to another over a
> pipe flushes before it waits for the answer.
>
> **Coming from C:** writing to standard error writes out standard output
> first, which C's `stdio` does not do.

## `Iterator`

```text
class Iterator c {
    type Item c
    type Cursor c

    fun iter(c) -> Cursor c
    fun next(c, Cursor c) -> Option (Item c)
}
```

`for x in c` runs over any type with an `Iterator` instance
([`for` over a collection](statements.md#for-over-a-collection)). `Item c` is
the element type, and `Cursor c` is the type of the state that records how far
the loop has got. `iter` makes a new cursor, and `next` returns the next
element and advances the cursor, or returns `None` when there are no more. The
cursor is usually a mutable record, and nothing asks the collection for its
length, so a type can be iterated even when it cannot be indexed, such as a
linked list or a generated sequence:

<!-- run -->
```kotlin
type Countdown = Countdown { from : Int }
type CountdownCursor = CountdownCursor { var current : Int }

instance Iterator Countdown {
    type Item = Int
    type Cursor = CountdownCursor

    fun iter(c) = CountdownCursor { current = c.from }

    fun next(c, cursor) {
        if cursor.current == 0 {
            return None
        }
        let value = cursor.current
        cursor.current = value - 1
        Some(value)
    }
}

fun main() {
    for n in (Countdown { from = 3 }) {
        print(n)
    }
}
```

```text
3
2
1
```

`Iter.any`, `Iter.all`, `Iter.count` and `Iter.find` ask a
predicate of each element of any iterator in turn: whether it holds for some
element, for every element, for how many, and the first it holds for. `any`,
`all` and `find` stop at the element that decides, and take nothing after it.

<!-- run -->
```kotlin
fun main() {
    let words = ["fig", "pear", "plum"]
    print(Iter.any(words, String.startsWith(_, "p")))
    print(Iter.all(words, String.startsWith(_, "p")))
    print(Iter.count(words, String.startsWith(_, "p")))
    print(Iter.find(words, String.startsWith(_, "p")))
}
```

```text
True
False
2
Some("pear")
```

`Iter.fold` threads an accumulator through the elements of any iterator in
order. `Iter.sum` and `Iter.product` fold with `+` and `*` from `zero()`
and `one()`, so they answer `0` and `1` for no elements. `Iter.minimum` and
`Iter.maximum` answer the least and greatest element as an `Option`, and
`Iter.minBy` and `Iter.maxBy` the element whose key is least or
greatest; each calls the key function once per element. When several elements
tie, all four answer the first.

<!-- run -->
```kotlin
fun main() {
    let xs = [3, 1, 4, 1, 5]
    print(Iter.sum(xs))
    print(Iter.product(Int.range(1, 6)))
    print(Iter.minimum(xs))
    print(Iter.maximum(([] : Array Int)))
    print(Iter.maxBy(["bb", "a", "cc"], String.byteLength))
    print(Iter.fold(xs, "", fun(acc, x) = acc + Int.toString(x)))
}
```

```text
14
120
Some(1)
None
Some("bb")
31415
```

## `Index`

```text
class Index c {
    type Key c
    type Value c

    fun get(c, Key c) -> Value c
}

class SetIndex c : Index c { fun set(c, Key c, Value c) -> Unit }
```

`c[k]` calls `get(c, k)`, and the assignment `c[k] = v` calls `set(c, k, v)`
([Indexing](expressions.md#indexing)). `Key c` and `Value c` are the key and
element types: `Int` and `a` for `Array a` and `Vec a`, and `k` and `v` for
`Map k v`. All three have both instances.

Writing is a class of its own so that a type can be read by key without being
writable. A type with only an `Index` instance can be read with `c[k]`, and
`c[k] = v` on it is rejected:

<!-- error: no instance for 'SetIndex Squares' -->
```kotlin
type Squares = Squares

instance Index Squares {
    type Key = Int
    type Value = Int
    fun get(_, i) = i * i
}

fun main() {
    let s = Squares
    print(s[4])
    s[4] = 0
}
```

> **Coming from Rust:** `Index` and `SetIndex` are Rust's `Index` and
> `IndexMut`. `SetIndex` takes the new value rather than handing out a
> mutable reference, since Turkey has no references to an element.

## `Length`

```text
class Length c { fun len(c) -> Int }
```

`len(xs)` is the number of elements in an `Array`, a `Vec`, a `Map`, a
`Set`, a `Deque` or a `PriorityQueue`.
`String` has no `Length` instance ([String](types.md#string)).

## `hash`

```text
fun hash[Hash a](x : a) -> Int
```

`hash(x)` is a hash of `x`: equal values hash equal for as long as the program
runs, and nothing else is promised. The function may differ between runs of a
program and between versions of Turkey, so a hash that is stored or sent to
another process is computed with a hash function the library names, not with
`hash`.

<!-- run -->
```kotlin
fun main() {
    print(hash("turkey") == hash("tur" + "key"))
    print(hash((1, "a")) == hash((1, "a")))
}
```

```text
True
True
```

`hash` feeds its argument to a hasher, the state of a hash function, through
the argument's `Hash` instance. Both classes are in `Data.Hashable`:

```text
class Hasher h {
    fun startHash(Int) -> h
    fun hashInt(h, Int) -> h
    fun hashBytes(h, ByteView) -> h
    fun finishHash(h) -> Int
}

class Hash a : Eq a {
    fun hashInto[Hasher h](a, h) -> h
}
```

A `Hash` instance hands its value's parts to the hasher in order: each scalar
as an `Int` through `hashInt`, and each run of bytes, such as a string's,
through `hashBytes`. Every method returns the next state, and the caller uses
each state once. A program can write its own `Hasher`, which reads each run of
bytes through a [`ByteView`](#byteview):

<!-- run -->
```kotlin
import Data.Hashable (Hasher(..))

-- Counts what it is fed instead of hashing it.
type Tally = Tally { words : Int, bytes : Int }

instance Hasher Tally {
    fun startHash(_) = Tally { words = 0, bytes = 0 }
    fun hashInt(t, _) = Tally { words = t.words + 1, bytes = t.bytes }
    fun hashBytes(t, run) = Tally { words = t.words, bytes = t.bytes + len(run) }
    fun finishHash(t) = t.words * 1000 + t.bytes
}

fun main() {
    let start : Tally = startHash(0)
    print(finishHash(hashInto((7, "hé"), start)))
}
```

```text
1003
```

A run of bytes is one part, and a hasher marks where each run ends. The pairs
`("ab", "c")` and `("a", "bc")` feed the same bytes, but in runs that end in
different places, so the hasher sees them as different keys. For the same
reason, a run fed to `hashBytes` in pieces need not hash as the whole run does:
a hasher hashes a key's parts, not a stream of bytes.

### `ByteView`

**Syntax**

```text
import Bytes (ByteView)
String.view(s)
Array.view(xs)
```

A `ByteView` is a run of bytes that can be read but not written. It has
`Index` with `Int` keys and `Byte` values, `Length` and `Iterator`, and no
`SetIndex`. `String.view(s)` views the bytes of a string, and `Array.view(xs)`
those of an `Array Byte`, neither by copying them. Nothing a program can import
returns the storage a view reads.

A view of a string never changes, because a string is never written. A view of
an array is read-only but not *frozen*: whoever holds the array can still
assign its elements, and the view shows each change as it happens:

<!-- run -->
```kotlin
import Bytes (ByteView)

fun total(v : ByteView) -> Int {
    var n = 0
    for b in v { n = n + Byte.toInt(b) }
    n
}

fun main() {
    let xs = [Byte.truncate(1), Byte.truncate(2), Byte.truncate(3)]
    let v = Array.view(xs)
    print(total(v))
    xs[0] = Byte.truncate(10)
    print(total(v))
    print(len(String.view("hé")))
}
```

```text
6
15
3
```

<!-- error: no instance for 'SetIndex ByteView' -->
```kotlin
fun main() {
    let v = String.view("abc")
    v[0] = Byte.truncate(122)
}
```

## `Functor`, `Applicative` and `Monad`

```text
class Functor f {
    fun map(f a, fun(a) -> b) -> f b
}

class Applicative f : Functor f {
    fun pure(a) -> f a
}

class Monad m : Applicative m {
    fun bind(m a, fun(a) -> m b) -> m b
}
```

`e?` calls `bind`, and the translation of `?` may call `pure`
([The `?` operator](do-notation.md)). The library provides instances for
`Option`, `Either l`, `Array` and `Vec`. A program can write instances for its
own types; a `Monad` instance needs `Functor` and `Applicative` instances too.

The Prelude's `fmap(f, xs)` is `map(xs, f)` with the function first. Three of
its operators call these methods: `xs <&> f` is `map(xs, f)`, `f <$> xs` is
`fmap(f, xs)`, and `m |>> f` is `bind(m, f)`
([The Prelude's other operators](expressions.md#the-preludes-other-operators)):

<!-- run -->
```kotlin
fun half(n : Int) -> Option Int = if n % 2 == 0 { Some(n / 2) } else { None }

fun main() {
    print(Some(20) |>> half <&> show)
    print(fmap(half, [4, 5]))
    print(half <$> Some(8))
}
```

```text
Some("10")
[Some(2), None]
Some(Some(4))
```

## `Semigroup` and `Monoid`

```text
class Semigroup a { fun combine(a, a) -> a }

class Monoid a : Semigroup a { fun empty() -> a }
```

`combine` joins two values associatively, and `empty()` is its identity on
either side. The operator `a <> b` calls `combine(a, b)`
([The Prelude's other operators](expressions.md#the-preludes-other-operators)).
`String`, `Array` and `Vec` have instances, which concatenate, and `Set`
has one, which takes the union.

## `error`

```text
fun error(message : String) -> a
```

`error` [panics](runtime-errors.md) with the message. Because its result type
is a variable that its parameter does not mention, a call to it fits any type.

## `Typed` and `cast`

```text
class Typed a { fun typeRep(Proxy a) -> TypeRep }
```

`Typed` gives a runtime description of a type. The compiler supplies an
instance for every type, and a program may not declare one, because checked
downcasting relies on the answer being true.

Its use is recovering the concrete type of an error that has been packed into
the library's `SomeError`. `SomeError` is an
[existential type](types.md#existential-types) that holds a value of any type
with an `Error` instance, and `Error` has `Typed` as a superclass. So
`Error.cast` can check whether a `SomeError` holds a particular type, and
return it:

<!-- run -->
```kotlin
type NotFound = NotFound { path : String }

instance Error NotFound {
    fun message(e) = "not found: " + e.path
}

fun load(path) = if path == "" { Right("empty") } else { Left(fail(NotFound { path })) }

fun main() {
    match load("notes.txt") {
        Left(err) -> {
            print(Error.describe(err))
            match Error.cast(err) : Option NotFound {
                Some(missing) -> print("could create " + missing.path)
                None -> print("some other error")
            }
        }
        Right(text) -> print(text)
    }
}
```

```text
not found: notes.txt
could create notes.txt
```

`fail(e)` packs a value into a `SomeError`, and `Error.cast` returns `Some`
only when the packed value has exactly the requested type.

The requested type is compared argument by argument, and that includes an
argument that is itself a type constructor. With `type Cell f = Cell(f Int)`,
`Cell Option` and `Cell Array` are different types with different
descriptions, and a value packed as one never casts to the other:

<!-- run -->
```kotlin
type Cell f = Cell(f Int)

-- The payload: `Cell` itself cannot have an `Error` instance (see below).
type Packed a = Packed(a)

instance Error (Packed a) : Typed a {
    fun message(_) = "packed"
}

fun main() {
    let err = fail(Packed(Cell(Some(1))))
    match Error.cast(err) : Option (Packed (Cell Option)) {
        Some(Packed(Cell(o))) -> print(o)
        None -> print("not a Cell Option")
    }
    match Error.cast(err) : Option (Packed (Cell Array)) {
        Some(_) -> print("a Cell Array")
        None -> print("not a Cell Array")
    }
}
```

```text
Some(1)
not a Cell Array
```

A context can ask for `Typed` only of a type, though, and not of a type
constructor such as `f`. So `Cell` cannot have an `Error` instance: the
instance would need `Typed (Cell f)`, which needs `Typed f`, and no context
can say so. A payload like `Packed` above carries such a type instead.

<!-- error: but 'Typed' constrains a type of kind * -->
```kotlin
import Data.Typed (Proxy(..), typeRep, describe)

type Cell f = Cell(f Int)

fun shown[Typed f](p : Proxy (Cell f)) -> String = describe(typeRep(p))
```

Inference is not bound by that rule. A function with no signature that
needs the rep of a `Cell f` is inferred with exactly the context no program
can write, and works, but cannot be given a signature that restates its type:

<!-- run -->
```kotlin
import Data.Typed (Proxy(..), typeRep, describe)

type Cell f = Cell(f Int)

fun proxyFor(x : a) -> Proxy a = Proxy

-- repOf : [Typed a] fun(Cell a) -> TypeRep, where `a` has kind * -> *
fun repOf(cell) {
    let Cell(_) = cell
    typeRep(proxyFor(cell))
}

fun main() {
    print(describe(repOf(Cell(Some(1)))))
    print(repOf(Cell(Some(1))) == repOf(Cell([1])))
}
```

```text
Main#Cell Data.Option#Option
False
```

## `Object` and `identical`

```text
class Object a { fun identical(a, a) -> Bool }
```

`==` compares what two values hold, so two arrays with the same elements are
equal. `identical(x, y)` asks instead whether `x` and `y` are one object:
whether an assignment through one would be seen through the other
([Mutability and sharing](types.md#mutability-and-sharing)).

<!-- run -->
```kotlin
type Account = Account { owner : String, var balance : Int }

fun main() {
    let a = Account { owner = "alice", balance = 10 }
    let b = Account { owner = "alice", balance = 10 }
    let alsoA = a
    print(a == b)
    print(identical(a, b))
    print(identical(a, alsoA))
}
```

```text
True
False
True
```

The compiler supplies an instance of `Object` for each type whose values are
objects, and a program may not declare one. Those types are:

* `Array a`, whose elements can be assigned;
* `System.Sync.AtomicInt`, an atomic cell, which every copy of it shares;
* a record with a `var` field;
* a type with one constructor and one field, not `var`, whose type has
  `Object`. Such a value is its field, so `Vec a`, `Map k v` and
  `Set a` are objects, and `type Box a = Box(a)` has the instance
  `Object (Box a) : Object a`.

Every other type is a value: the primitive types, strings, tuples,
functions, and the types whose constructors have no `var` field. A value
can be copied, or rebuilt from its fields, without the program telling the
difference, so it has no identity to ask about, and calling `identical` on
one is an error:

<!-- error: 'Int' is a value, not an object -->
```kotlin
fun main() {
    print(identical(1, 1))
}
```

A `String` is a value too, although its text is held in an array: two
strings with the same text may be one string or two, however they were
built. So a wrapper over one is a value, and the error says why:

<!-- error: 'Name' is a value, not an object: field 0 of 'Name' has type 'String', which opts out of 'Object' -->
```kotlin
type Name = Name(String)

fun main() {
    print(identical(Name("a"), Name("a")))
}
```

`!Object` in a type's [derive list](classes.md#derived-instances) refuses
the instance, as `String` does, for a type whose values the program should
not tell apart by identity.

A function that compares by identity asks for `Object` of its type
parameter. This one asks whether an array holds one particular object,
where `==` would accept any equal one:

<!-- run -->
```kotlin
type Task = Task { name : String, var done : Bool }

fun holds[Object a](xs : Array a, x : a) -> Bool = Iter.any(xs, identical(_, x))

fun main() {
    let first = Task { name = "write", done = False }
    let second = Task { name = "write", done = False }
    print(holds([first], second))
    print(holds([first, second], second))
}
```

```text
False
True
```
