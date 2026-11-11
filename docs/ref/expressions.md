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

**Syntax**

```ebnf
chain ::= unary (OPERATOR unary)*
```

A sequence of binary operators is grouped by **precedence groups**. Every
binary operator belongs to one group, and a group's associativity applies to
all of its operators. The groups are fixed. `&&` and `||` are built in, and
the other operators in the table below are
[declared by the Prelude](#operators-are-class-methods). The group of each
arithmetic, comparison and bitwise operator is fixed too; `<>`, `|>`, `|>>`,
`<&>` and `<$>` are [the Prelude's other operators](#the-preludes-other-operators),
which a module may replace with its own in any group. The `Choice` group has
no Prelude operator, and is for [declared operators](#declared-operators).

| Group | Operators | Associativity | Binds tighter than |
|---|---|---|---|
| | `f(...)` `a[i]` `a.f` `a.0` `e?` | left | everything below: call, index, field, projection, [`?`](do-notation.md) |
| | `-e` `!e` | prefix | every group: negation, logical not |
| `Multiplication` | `*` `/` `%` | left | `Addition` |
| `Addition` | `+` `-` | left | `Comparison` |
| `BitShift` | `<<` `>>` | left | `BitAnd`, `Comparison` |
| `BitAnd` | `&` | left | `BitOr`, `BitXor`, `Comparison` |
| `BitOr` | `\|` | left | `Comparison` |
| `BitXor` | `^` | left | `Comparison` |
| `Combine` | `<>` | right | `Comparison` |
| `Comparison` | `<` `<=` `>` `>=` | left | `Equality` |
| `Equality` | `==` `!=` | left | `Conjunction` |
| `Conjunction` | `&&` | left | `Disjunction` |
| `Disjunction` | `\|\|` | left | `Choice` |
| `Choice` | | left | `Sequence` |
| `Sequence` | `\|>` `\|>>` `<&>` `<$>` | left | |
| | `e : T` | | binds loosest of all: [type annotation](#type-annotations) |

"Binds tighter than" is transitive: `Multiplication` binds tighter than
`Addition`, which binds tighter than `Comparison`, so `Multiplication` binds
tighter than `Comparison` too. Two groups with no path between them in that
column are **unrelated**, and an expression that mixes operators from
unrelated groups must use parentheses to say which applies first. So
`Addition` and `BitShift` are unrelated, and `a + b << c` is an error where
`(a + b) << c` is not.

For readers new to this: this is a *partial order*. Most languages rank every
operator against every other, so any two operators can be mixed and a reader
has to remember which binds tighter. Here a pair that is seldom written
together, and that languages disagree about, has no order at all, and the
compiler asks for the parentheses a reader would want anyway.

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

`Comparison` is left-associative, so `a < b < c` means
`(a < b) < c`, which compares a `Bool` with `c` and is usually a type error.
Write `a < b && b < c`.

### Operators are class methods

The arithmetic and comparison operators are not built into any type. The
Prelude [declares](#declared-operators) each one as a call to a method of a
[class](classes.md):

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
own function called `add` or `eq`, or a class of its own with a method called
`neg`. Being Prelude declarations, these operators
are imported like the Prelude's other names: a module that writes
`import Prelude ()` has none of them until it imports them from the modules
that declare them, `Data.Num`, `Data.Eq`, `Data.Ord` and, for the
[bitwise operators](#bitwise-operators), `Bits`
([Operators in imports](modules.md#operators-in-imports)):

<!-- run -->
```kotlin
import Prelude ()
import Data.Num ((+), (*))
import System.IO (print)
import Text.Show (show)

fun main() {
    print(show(1 + 2 * 3))
}
```

```text
7
```

Their groups are fixed. A module may declare one of these operators itself,
shadowing the Prelude's, but only in the group the table at the top of this
section gives it, so `a + b * c` groups the same way in every module.

Which instances the library provides for the built-in types is listed in
[Built-in types and classes](builtins.md#operator-classes). In particular `+`
concatenates strings, there is no `%` on `Float`, and `Byte` has no arithmetic.

### Bitwise operators

`&`, `|`, `^`, `<<` and `>>` are declared by the Prelude the same way, each
calling the method of a class of its own, so a type can have some of them and
not the others:

| Expression | Means | Class |
|---|---|---|
| `a & b` | `and(a, b)` | `BitAnd` |
| `a \| b` | `or(a, b)` | `BitOr` |
| `a ^ b` | `xor(a, b)` | `BitXor` |
| `a << n`, `a >> n` | `shl(a, n)`, `shr(a, n)` | `Shift` |

The shift amount is an `Int` whatever is shifted. `Int` and `Byte` have all
four classes ([Built-in types and classes](builtins.md#operator-classes)).

The bitwise groups bind tighter than the comparisons, so a mask test needs no
parentheses, but they are unrelated to the arithmetic groups and to each other
except where the [table](#operators) relates them:

<!-- run -->
```kotlin
fun isSet(flags : Int, bit : Int) -> Bool = flags & (1 << bit) != 0

fun main() {
    let flags = 0b0101
    print(isSet(flags, 2))
    print(isSet(flags, 1))
    print(flags | 1 << 3)
}
```

```text
True
False
13
```

<!-- error: '+' (Addition) and '<<' (BitShift) have no precedence between them -->
```kotlin
fun main() {
    print(1 + 2 << 3)
}
```

### The Prelude's other operators

The Prelude declares five more operators, for combining values and for
pipelines:

| Expression | Means | Class |
|---|---|---|
| `a <> b` | `combine(a, b)` | `Semigroup` |
| `x \|> f` | `pipe(x, f)`, which is `f(x)` | |
| `m \|>> f` | `bind(m, f)` | `Monad` |
| `xs <&> f` | `map(xs, f)` | `Functor` |
| `f <$> xs` | `fmap(f, xs)`, which is `map(xs, f)` | `Functor` |

`<>` is in `Combine`, which is right-associative and binds tighter than the
comparisons. The other four are in `Sequence`, the loosest group, so each
applies to everything on its left, and a chain of them runs left to right.
`|>>` is `|>` for a [monadic](builtins.md#functor-applicative-and-monad) value:
the function returns another such value, and a `None` or a `Left` stops the
chain.

<!-- run -->
```kotlin
fun half(n : Int) -> Option Int = if n % 2 == 0 { Some(n / 2) } else { None }

fun double(n : Int) -> Int = n * 2

fun main() {
    print("Hello" <> ", " <> "world" |> String.codePointCount)
    print("ab" <> "c" == "abc")
    print(Some(12) |>> half |>> half)
    print(Some(12) |>> half |>> half |>> half)
    print([1, 2, 3] <&> double <&> double)
    print(double <$> Some(21))
}
```

```text
12
True
Some(3)
None
[4, 8, 12]
Some(42)
```

As a desugaring, the first and third `print`s are roughly:

```text
print(pipe(combine("Hello", combine(", ", "world")), String.codePointCount))
print(bind(bind(Some(12), half), half))
```

`Combine` is right-associative so that a chain of appends is cheap:
`a <> b <> c` is `a <> (b <> c)`, which on strings and vectors copies each
part once, where `(a <> b) <> c` would copy `a` again at every step.

`<&>` and `<$>` are the same `map` with the operands in either order. Use
`<&>` for a chain: `xs <&> f <&> g` applies `f` and then `g`. Since `Sequence`
is left-associative, `g <$> f <$> xs` is `(g <$> f) <$> xs`, which maps over
the function `f` and is a type error:

<!-- error: in the operands of '<$>' -->
```kotlin
fun double(n : Int) -> Int = n * 2

fun main() {
    print(show <$> double <$> [1, 2])
}
```

An operator is not a boundary of a [placeholder function](declarations.md#placeholder-functions),
so `xs <&> _ + 1` is one function of the whole expression rather than a map.
Give the function a name, or write it with `fun`, or call `map(xs, _ + 1)`,
where the argument is a boundary.

Like any [declared operator](#declared-operators), each of these is shadowed
by a module's own `infix` declaration of the same operator, which may put it
in another group. `import Prelude ()` leaves all five out; they are declared
in `Data.Semigroup`, `Data.Function`, `Control.Monad` and `Data.Functor`.

The `Choice` group, between `Disjunction` and `Sequence`, has no Prelude
operator. The library module `Regex` declares `<|>` there, as its `orElse`.

### Logical operators

`&&`, `||` and `!` are built in and work only on `Bool`. `&&` and `||`
short-circuit: the right operand is evaluated only if the left one does not
already decide the answer, so `a && b` is the same as
`if a { b } else { False }`.

### Declared operators

**Syntax**

```ebnf
infix-decl ::= "infix" CONID "(" OPERATOR ")" "=" target
target     ::= IDENT | CONID ("." CONID)* "." IDENT
```

An `infix` declaration, at the top level of a module, makes an operator an
alias for a function: `infix Combine (<>) = combine` puts `<>` in the group
`Combine`, and makes `a <> b` mean `combine(a, b)`.

* The group is one of the groups in the [table above](#operators). A program
  chooses among them and cannot add its own.
* The operator is any [operator token](lexical.md#operators-and-punctuation)
  other than the built-in `&&`, `||` and `!`, the punctuation `=`, `->` and
  `~`, and an operator that ends in `=` without beginning with one: `+=` and
  `>>=` cannot be declared, and `===` can. An arithmetic, comparison or
  bitwise operator may be declared only in its own group
  ([Operators are class methods](#operators-are-class-methods)). `|` is
  declared in `BitOr` though patterns and type declarations use it too: those
  are read separately from expressions.
* The target is a top-level function or a class method that takes two
  parameters, from this module or one it imports. It may be qualified.
* A use is an ordinary call of the target. Its types are the target's, a class
  method's class is demanded as by any call, and both operands are evaluated,
  left to right, before the call: unlike `&&` and `||`, a declared operator
  never skips its right operand.
* The operator may be used anywhere in the module that declares it, before the
  declaration too. Other modules use it only by naming it in an import list
  ([Operators in imports](modules.md#operators-in-imports)).
* An operator is declared once per module, in one group.

`infix` is a keyword only at the start of a top-level declaration, so it is
still an ordinary name everywhere else.

<!-- run -->
```kotlin
infix Combine (</>) = joinPath

fun joinPath(dir : String, name : String) -> String = dir + "/" + name

fun main() {
    print("usr" </> "local" </> "bin")
    print("usr" </> "lib" == "usr/lib")
}
```

```text
usr/local/bin
True
```

`Combine` is right-associative and binds tighter than `Equality`, so as a
desugaring the two `print`s are roughly:

```text
print(joinPath("usr", joinPath("local", "bin")))
print(eq(joinPath("usr", "lib"), "usr/lib"))
```

Mixing unrelated groups is an error that names both operators:

<!-- error: '+' (Addition) and '<>' (Combine) have no precedence between them -->
```kotlin
fun main() {
    print("a" + "b" <> "c")
}
```

A declared operator, like a built-in binary one, may begin a line that
continues the line before it ([Line breaks](lexical.md#line-breaks-and-semicolons)):

<!-- run -->
```kotlin
fun main() {
    let shown = [3, 1, 2]
        |> Vec.fromArray
        |> Vec.sort
        |> show
    print(shown)
}
```

```text
[1, 2, 3]
```

> **Coming from Haskell:** there are no numeric levels and no `infixl` or
> `infixr`: an operator takes its precedence and associativity from its group.
> An operator is not a value, so there are no sections and no `(<>)` to pass
> to a function; pass the named function instead.

> **Coming from Rust:** a declared operator is not a trait of its own. It is
> another spelling of a function, so it is overloaded exactly when that
> function is a class method.

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

A module-qualified name, such as `Vec.push` or `String.split`, is a function
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
field called `name`. A field declared `var` can also be assigned:
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
projection ::= postfix "." INDEX
INDEX      ::= "0" | [1-9] [0-9]*
```

`e.0`, `e.1`, and so on read the elements of a tuple, counting from zero. The
index is written in plain decimal: `e.01`, `e.0x1` and `e.1_0` are errors. They
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
mean `get(c, k)` and `set(c, k, v)`, the methods of the classes
[`Index` and `SetIndex`](builtins.md#index), so indexing works on any type with
an instance. The library has instances for `Array` and `Vec`, whose keys are
`Int` positions counting from zero, and for `Map`, whose keys are the map's key
type. They mean those two methods even in a module with a class of its own
whose method is called `get` or `set`.

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

<!-- error: 'Order' is missing field 'rush' -->
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

`[e1, e2, ..., en]` builds a new [`Array`](builtins.md#array) or
[`Vec`](builtins.md#vec) holding the values of `e1` to `en` in order. All
elements must have the same type. `[]` is a new empty one, and its element type
is inferred from how it is used.

Which of the two a literal builds is also inferred from how it is used, the
way a numeric literal's type is: a literal passed to a function that takes a
`Vec` is a `Vec`, and one that nothing decides is an `Array`
([Array literals](inference.md#array-literals)).

Each evaluation of an array literal creates a new container, which is
[mutable](types.md#mutability-and-sharing). When it is an `Array`, it is
approximately equivalent to:

```ebnf
{
    let $array = Array.filled(n, e1)
    $array[1] = e2
    ...
    $array[n - 1] = en
    $array
}
```

with `[]` an empty `Array`, and when it is a `Vec`, to:

```ebnf
{
    let $vec = Vec.new(n)
    Vec.push($vec, e1)
    ...
    Vec.push($vec, en)
    $vec
}
```

<!-- run -->
```kotlin
fun main() {
    let evens = []
    for n in [1, 2, 3, 4, 5, 6] {
        if n % 2 == 0 {
            Vec.push(evens, n)
        }
    }
    print(evens)
}
```

```text
[2, 4, 6]
```

Here `evens` is a `Vec`, because `Vec.push` takes one, and `[1, 2, 3, 4, 5, 6]`
is an `Array`, because `for` accepts either and nothing else decides.

> **Coming from Rust:** `[a, b]` is not a fixed-size `[T; 2]`, and it is not
> always an array either: it is whichever of `Array` and `Vec` the program uses
> it as, so it also stands for `vec![a, b]`.

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
    let names = [] : Vec String
    Vec.push(names, "Ada")
    print(names)
    print(3 : Float)
}
```

```text
["Ada"]
3.0
```

## Variables and constructors

A lowercase name is a variable: a parameter, a local binding, or a top-level
function or binding. An uppercase name is a constructor. A constructor with no
payload is a value on its own (`None`, `True`), and a constructor with a
payload is a function (`Some`, `Card`) that can also be passed as a value:
`Array.map([1, 2], Some)`.
