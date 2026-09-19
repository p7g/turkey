# Expressions

Almost everything in a Turkey function body is an expression, including
blocks, `if`, `match` and the loops, which are covered in [Statements and
control flow](statements.md). This chapter covers the rest: operators,
function calls, field access, projection, indexing, record construction,
tuples, array literals, and type annotations.

## Evaluation order

Evaluation is strict: an expression's operands are evaluated before the
expression itself, and a function's arguments are evaluated before the
function runs. Operands, arguments, tuple elements, array elements and record
fields are evaluated **left to right**, in the order they are written. The
exceptions are `&&` and `||`, which may skip their right operand.

<!-- run -->
```kotlin
fun traced(label, value) {
    print(label)
    value
}

fun main() {
    let total = traced("left", 1) + traced("right", 2)
    let pair = (traced("first", "a"), traced("second", "b"))
    let skipped = traced("tested", False) && traced("never printed", True)
    print(skipped)
}
```

```text
left
right
first
second
tested
False
```

## Operators

| Precedence | Operators | Associativity | Meaning |
|---|---|---|---|
| highest | `f(...)` `a[i]` `a.f` `a.0` `e?` | left | call, index, field, projection, [`?`](do-notation.md) |
| | `-e` `!e` | prefix | negation, logical not |
| | `*` `/` `%` | left | `mul`, `div`, `rem` |
| | `+` `-` | left | `add`, `sub` |
| | `<` `<=` `>` `>=` | left | `lt`, `lte`, `gt`, `gte` |
| | `==` `!=` | left | `eq`, `ne` |
| | `&&` | left | logical and |
| | `\|\|` | left | logical or |
| lowest | `e : T` | | [type annotation](#type-annotations) |

<!-- run -->
```kotlin
fun main() {
    print(1 + 2 * 3 - 4 / 2)
    print(-2 * 3)
    print(1 < 2 == 3 < 4)
    print(!False || False)
}
```

```text
5
-6
True
True
```

Comparisons are left-associative like everything else, so `a < b < c` means
`(a < b) < c`, which compares a `Bool` with `c` and is usually a type error.
Write `a < b && b < c`.

### Operators are class methods

The arithmetic and comparison operators are not built into any type. Each is
a call to a method of a [class](classes.md) from the Prelude:

| Expression | Means | Class |
|---|---|---|
| `a + b` | `add(a, b)` | `Add` |
| `a - b` | `sub(a, b)` | `Sub` |
| `a * b` | `mul(a, b)` | `Mul` |
| `a / b` | `div(a, b)` | `Div` |
| `a % b` | `rem(a, b)` | `Rem` |
| `-a` | `neg(a)` | `Neg` |
| `a == b`, `a != b` | `eq(a, b)`, `ne(a, b)` | `Eq` |
| `a < b`, `a <= b`, `a > b`, `a >= b` | `lt`, `lte`, `gt`, `gte` | `Ord` |

So an operator works on any type with an instance of its class, including the
types a program declares:

<!-- run -->
```kotlin
type Money = Money(Int)

instance Add Money {
    fun add(Money(a), Money(b)) = Money(a + b)
}

instance Show Money {
    fun show(Money(cents)) = "$" + show(cents / 100) + "." + show(cents % 100)
}

fun main() {
    let price = Money(1250) + Money(199)
    print(price)
}
```

```text
$14.49
```

Both operands of a binary operator, and its result, have the same type,
because each class has a single type parameter. `Money * Int` cannot be given
a meaning with `*`; write a function instead.

The operator always means the class method, even in a module that declares its
own function called `add` or `eq`.

Which instances the library provides for the built-in types is listed in
[Built-in types and classes](builtins.md#operator-classes). In particular `+`
concatenates strings, there is no `%` on `Float`, and `Byte` has no arithmetic.

### Logical operators

`&&`, `||` and `!` are built in and work only on `Bool`. `&&` and `||`
short-circuit: the right operand is evaluated only if the left one does not
already decide the answer, so `a && b` is the same as
`if a { b } else { False }`.

## Function calls

**Syntax**

```ebnf
call ::= postfix "(" (expression ("," expression)*)? ")"
```

A call applies a function to arguments. The function can be any expression
whose type is a function type, such as a name, a field holding a function, or
a call that returns one: `makeAdder(1)(2)`. The number of arguments must match
the number of parameters exactly, because functions are [uncurried](types.md#function-types).

<!-- error: this function takes 2 arguments but 1 was supplied -->
```kotlin
fun main() {
    let add = fun(a, b) = a + b
    print(add(1))
}
```

A constructor with a payload is called the same way: `Some(3)`, `Card(10, Hearts)`.

A module-qualified name, such as `Array.push` or `String.split`, is a function
from another module ([Modules](modules.md#qualified-names)). A qualified
function is an ordinary value and can be passed like any other:
`Array.map(words, String.byteLength)`.

## Field access

**Syntax**

```ebnf
field ::= postfix "." IDENT
```

`e.name` reads a field of a [record](types.md#records). The type of `e` must
have exactly one constructor, and that constructor must be a record with a
field called `name`. Fields of a mutable record can also be assigned:
`e.name = value` ([Assignment](statements.md#assignment)).

The type of `e` does not need to be known where the field is read. A function
that reads a field works on every record type that has a field of that name,
and its inferred type says so ([Field constraints](inference.md#field-and-projection-constraints)):

<!-- run -->
```kotlin
type Rectangle = Rectangle { width : Int, height : Int }
type Crate = Crate { width : Int, height : Int, depth : Int }

fun frontArea(thing) = thing.width * thing.height

fun main() {
    print(frontArea(Rectangle { width = 3, height = 4 }))
    print(frontArea(Crate { width = 2, height = 5, depth = 9 }))
}
```

```text
12
10
```

## Tuple projection

**Syntax**

```ebnf
projection ::= postfix "." INT
```

`e.0`, `e.1`, and so on read the elements of a tuple, counting from zero. They
also read the payload of a value whose type has exactly one constructor with a
positional payload, such as `type Point = Point(Int, Int)`. Projections are
read-only, and an index past the last element is an error.

<!-- run -->
```kotlin
type Point = Point(Int, Int)

fun main() {
    let pair = ("x", (1, 2))
    print(pair.0)
    print(pair.1.1)
    let p = Point(3, 4)
    print(p.0 + p.1)
}
```

```text
x
2
7
```

Projection is not available on records (use the field name) or on types with
more than one constructor (use `match`).

## Indexing

**Syntax**

```ebnf
index ::= postfix "[" expression "]"
```

`c[k]` reads the element of `c` at key `k`, and `c[k] = v` replaces it. They
mean `get(c, k)` and `set(c, k, v)`, the methods of the class
[`Index`](builtins.md#index), so indexing works on any type with an instance.
The library has instances for `Array`, whose keys are `Int` positions counting
from zero, and for `Map`, whose keys are the map's key type.

<!-- run -->
```kotlin
fun main() {
    let scores = [70, 85, 90]
    scores[0] = 75
    print(scores[0] + scores[2])

    let ages = Map.new()
    ages["ada"] = 36
    ages["alan"] = 41
    print(ages["alan"])
}
```

```text
165
41
```

Reading an array outside its bounds, or a map at a missing key,
[panics](runtime-errors.md). To look up a key that may be missing, use a
function that returns an `Option`, such as `Map.get`.

## Record construction

**Syntax**

```ebnf
record-expr ::= qualified-CONID "{" field-init (separator field-init)* "}"
field-init  ::= IDENT "=" expression
              | IDENT
```

`C { f1 = e1, f2 = e2 }` builds a value with the record constructor `C`. Every
field must be given exactly once, in any order. Fields are separated by commas
or line breaks.

Writing a field name alone, `C { f }`, is short for `C { f = f }`. This is
called *punning*, and it can be mixed with ordinary fields:

<!-- run -->
```kotlin
type Order = Order { item : String, quantity : Int, rush : Bool }

fun order(item, quantity) = Order {
    item
    quantity
    rush = quantity > 10
}

fun main() {
    let o = order("bolts", 25)
    print(o.rush)
}
```

```text
True
```

<!-- error: 'Order' is missing field(s): rush -->
```kotlin
type Order = Order { item : String, quantity : Int, rush : Bool }

fun main() {
    let o = Order { item = "bolts", quantity = 25 }
}
```

A record constructor can also be called positionally, with the fields in
declaration order: `Order("bolts", 25, True)`.

A record expression cannot appear directly as the condition of `if` or
`while`, the subject of `match`, or the collection of `for ... in`, because
there `C {` would be read as a constructor followed by a block. Put the record
expression in parentheses in those positions.

## Tuples and unit

**Syntax**

```ebnf
tuple ::= "(" expression ("," expression)+ ")"
unit  ::= "(" ")"
```

`(e1, e2, ...)` builds a tuple of two or more elements. `(e)` is `e` in
parentheses, and `()` is the unit value ([Tuples](types.md#tuples)).

## Array literals

**Syntax**

```ebnf
array ::= "[" (expression ("," expression)*)? "]"
```

`[e1, e2, ..., en]` builds a new `Array` holding the values of `e1` to `en` in
order. All elements must have the same type. `[]` is a new empty array, and
its element type is inferred from how the array is used.

Each evaluation of an array literal creates a new array, which is
[mutable](types.md#mutability-and-sharing). It is approximately equivalent to:

```ebnf
{
    let $array = Array.new(n)
    Array.push($array, e1)
    ...
    Array.push($array, en)
    $array
}
```

<!-- run -->
```kotlin
fun main() {
    let evens = []
    for n in [1, 2, 3, 4, 5, 6] {
        if n % 2 == 0 {
            Array.push(evens, n)
        }
    }
    print(evens)
}
```

```text
[2, 4, 6]
```

## Blocks

A [block](statements.md#blocks), `{ ... }`, is an expression whose value is
the value of its last statement. `if`, `match`, `loop` and the other control
constructs in [Statements and control flow](statements.md) are expressions
too.

## Type annotations

**Syntax**

```ebnf
annotated ::= expression ":" type
```

`e : T` checks that `e` has type `T`, and has the value of `e`. It has the
lowest precedence of any operator, so `a + b : Int` annotates the sum. It is
most useful where nothing else fixes a type, such as an empty array or a
literal:

<!-- run -->
```kotlin
fun main() {
    let names = [] : Array String
    Array.push(names, "Ada")
    print(names)
    print(3 : Float)
}
```

```text
[Ada]
3.0
```

## Variables and constructors

A lowercase name is a variable: a parameter, a local binding, or a top-level
function or binding. An uppercase name is a constructor. A constructor with no
payload is a value on its own (`None`, `True`), and a constructor with a
payload is a function (`Some`, `Card`) that can also be passed as a value:
`Array.map([1, 2], Some)`.
