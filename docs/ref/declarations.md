# Declarations

A declaration gives a name to a function or a value. This chapter covers
functions (top-level and local), anonymous functions, `let` and `var`
bindings, type annotations, and constraint contexts. Type declarations are
covered in [Types](types.md), and classes and instances in
[Type classes](classes.md).

## Functions

**Syntax**

```ebnf
fun-decl ::= "fun" IDENT context? "(" (pattern ("," pattern)*)? ")" ("->" type)? body
body     ::= "=" expression
           | block
context  ::= "[" constraint ("," constraint)* "]"
```

A function declaration has a name, an optional [context](#contexts), a
parameter list, an optional return type, and a body. The body is either
`= expression`, for a function that fits on a line, or a
[block](statements.md#blocks), whose value is the value of its last statement:

<!-- run -->
```kotlin
fun square(x) = x * x

fun sumOfSquares(xs) {
    var total = 0
    for x in xs {
        total = total + square(x)
    }
    total
}

fun main() {
    print(sumOfSquares([1, 2, 3]))
}
```

```text
14
```

A block body can also end with [`return`](statements.md#return), and `return`
can leave a function early from anywhere inside its body.

**Parameters are patterns.** Each parameter is a
[pattern](patterns.md), usually just a name, and may carry a type annotation:
`fun area((w, h) : (Int, Int)) = w * h`. A parameter pattern must be
[irrefutable](patterns.md#irrefutable-patterns): it must match every value of
its type.

**Parameters can be reassigned.** Every name a parameter binds can be
assigned in the body, as if it had been declared with `var`. Assigning a
parameter changes only the function's own variable, never the caller's:

<!-- run -->
```kotlin
fun gcd(a, b) {
    while b != 0 {
        let rest = a % b
        a = b
        b = rest
    }
    a
}

fun main() {
    print(gcd(84, 36))
}
```

```text
12
```

**Recursion.** Every top-level function can call every other, in any order,
including itself. A group of functions that call each other is called
*mutually recursive*, and needs no forward declarations.

<!-- run -->
```kotlin
fun isEven(n) = if n == 0 { True } else { isOdd(n - 1) }
fun isOdd(n) = if n == 0 { False } else { isEven(n - 1) }

fun main() {
    print(isEven(10))
}
```

```text
True
```

**Names.** A function's name is an `IDENT`. A top-level function belongs to
its module's namespace, which it shares with top-level `let` and `var`
bindings. A function may share a name with a [class method](classes.md): the
method is still what an operator or a `for` loop calls.

## Local functions

A `fun` declaration can also appear as a statement inside a block. It is then
visible to the statements after it, and to itself, so it can be recursive:

<!-- run -->
```kotlin
fun main() {
    fun factorial(n) = if n <= 1 { 1 } else { n * factorial(n - 1) }
    print(factorial(10))
}
```

```text
3628800
```

## Anonymous functions

**Syntax**

```ebnf
lambda ::= "fun" "(" (pattern ("," pattern)*)? ")" ("->" type)? body
```

An anonymous function (a *lambda*) is written like a declaration without a
name, and it is an expression. It has the same two body forms:

<!-- run -->
```kotlin
fun main() {
    let double = fun(x) = x * 2
    let describe = fun(name, age) {
        name + " is " + show(age)
    }
    print(double(21))
    print(describe("Ada", 36))
    print(Array.map([1, 2, 3], fun(x) = x * x))
}
```

```text
42
Ada is 36
[1, 4, 9]
```

A lambda cannot have a context. Unlike a declared function, it is not
generalized: its parameters have one type each, fixed by how it is used
([Type inference](inference.md#generalization)).

**Closures.** A lambda or local function can use the variables of the scopes
around it. It captures them by reference, not by copying: if it assigns a
captured `var`, the assignment is visible outside, and if something outside
assigns it later, the lambda sees the new value.

<!-- run -->
```kotlin
fun makeCounter() {
    var count = 0
    fun() {
        count = count + 1
        count
    }
}

fun main() {
    let next = makeCounter()
    let _ = next()
    let _ = next()
    print(next())
}
```

```text
3
```

## Placeholder functions

**Syntax**

```ebnf
atom ::= "_"
       | "\" expression
```

A short function that uses its parameter once can be written without naming
it. An `_` in an expression stands for the parameter, and the expression
around it becomes the function:

<!-- run -->
```kotlin
type Planet = Planet { name : String, moons : Int }

fun main() {
    let planets = [Planet { name = "Mars", moons = 2 },
                   Planet { name = "Venus", moons = 0 }]
    print(Array.map([1, 2, 3], _ * 2))
    print(Array.map(planets, _.name))
    print(Array.map(Array.filter(planets, _.moons > 0), _.name))
}
```

```text
[2, 4, 6]
[Mars, Venus]
[Mars]
```

Each is shorthand for a lambda. `_ * 2` means `fun($x) = $x * 2`, and
`_.name` means `fun($x) = $x.name`.

**How far the function reaches.** The function is the smallest *boundary*
around the `_`. These are boundaries:

* a function argument, an index, a tuple component, an array element, and a
  record field;
* a statement, the right-hand side of a `let` or `var`, a function body, a
  match arm, and the value of `return` or `break`.

Operators, field access, `.0` projection, `?`, `:` annotations and the
function being called are not boundaries, and neither are grouping
parentheses. So `_ * 2 + 1` is one function, and so is `(_ + 1) * 2`.

A boundary that is *nothing but* `_` does not count, and the function reaches
one boundary further out. That is what makes `pow(_, 2)` a function, rather
than passing a function to `pow`:

<!-- run -->
```kotlin
fun pow(x, n) {
    var result = 1
    for var i = 0; i < n; i = i + 1 { result = result * x }
    result
}

fun main() {
    print(Array.map([1, 2, 3], pow(_, 2)))
    print(Array.map(["a", "b"], (_, 0)))
}
```

```text
[1, 4, 9]
[(a, 0), (b, 0)]
```

In the second line the tuple's first component is a bare `_`, so the function
is the whole tuple: `fun($x) = ($x, 0)`.

Because a function argument is a boundary, a `_` inside a call inside an
argument makes a function for the inner call, not for the outer one.
`show(_ * 2)` means `show(fun($x) = $x * 2)`, so this is an error:

<!-- error: where '_' means fun($x) = $x * 2 -->
```kotlin
fun main() {
    print(Array.map([1, 2, 3], show(_ * 2)))
}
```

When a type error is reported at a call whose argument is a placeholder
function, the message says what the `_` was taken to mean.

A function made by `_` has one parameter and uses it once: a second `_` in the
same function is an error. `_` on its own as a whole statement, `let` or
function body is an error too; write `fun(x) = x`.

<!-- error: a function made by '_' uses its parameter once -->
```kotlin
fun main() {
    print(Array.map([1, 2, 3], _ * _))
}
```

A statement is a boundary, so a block can end in a placeholder function and
answer it. `return _ + doubled` would answer the same function.

<!-- run -->
```kotlin
fun scale(factor) = _ * factor

fun offset(by) {
    let doubled = by * 2
    _ + doubled
}

fun main() {
    print(scale(3)(5))
    print(offset(1)(10))
}
```

```text
15
12
```

**Conditions.** A `_` cannot be used in the condition of `if` or `while`, the
sequence of `for`, the value `match` inspects, or the target of an assignment.
The function it would make there could never be used, so the compiler says so
rather than reporting a type error.

<!-- error: '_' cannot be used in the condition of 'if' -->
```kotlin
fun main() {
    let n = 3
    if _ > n { print("big") }
}
```

**Marking the start with `\`.** `\` in front of an expression makes it a
function of every `_` inside it, however deeply they are nested. It reaches
as far right as a lambda body would. Inside it, every `_` is the same
parameter, and none of the boundaries above apply:

<!-- run -->
```kotlin
type Planet = Planet { name : String, moons : Int }

fun label(name) = "<" + name + ">"

fun main() {
    let planets = [Planet { name = "Mars", moons = 2 },
                   Planet { name = "Venus", moons = 0 }]
    print(Array.map(planets, \label(_.name)))
    print(Array.map(planets, \_.name + " has " + show(_.moons)))
    print(Array.map([1, 2, 3], \Array.map([_, _ * 10], \_ + 1)))
}
```

```text
[<Mars>, <Venus>]
[Mars has 2, Venus has 0]
[[2, 11], [3, 21], [4, 31]]
```

`\label(_.name)` means `fun($x) = label($x.name)`; without the `\`, the
argument `_.name` would be the function. A `\` inside another `\` makes its
own function of the `_`s inside it, as the last line shows. A `\` with no `_`
in it is an error.

The rest of the expression is evaluated each time the function is called,
exactly as it would be in the lambda it stands for.

**Coming from Scala:** this is Scala's placeholder syntax, with two
differences. Grouping parentheses are not a boundary, so `(_ + 1) * 2` is one
function, where in Scala the parentheses would end it. And `_ + _` is an error
rather than a function of two parameters; use `fun(a, b) = a + b`.

**Coming from Haskell:** `\` starts a function, as it does in Haskell, but it
takes no parameter list. The parameter is `_`.

## `let` and `var`

**Syntax**

```ebnf
let-decl ::= "let" pattern "=" expression
var-decl ::= "var" pattern "=" expression
```

`let` binds the names in a pattern to the parts of a value. The binding cannot
be changed afterwards. `var` does the same, but each name it binds can later
be [assigned](statements.md#assignment) a new value of the same type.

<!-- error: cannot assign to 'limit': it was bound with 'let' -->
```kotlin
fun main() {
    let limit = 10
    limit = 20
}
```

The pattern must be [irrefutable](patterns.md#irrefutable-patterns), so
`let (x, y) = point` is fine and `let Some(x) = maybe` is an error. Use
[`if let`](statements.md#if-let-and-if-var) or [`match`](statements.md#match)
for a pattern that might not match.

`let` and `var` decide whether the *name* can be pointed at something else.
They do not decide whether the value is mutable. A `let` binding of a
[mutable record](types.md#mutability-and-sharing) can still have its fields
assigned.

A `let` or `var` in a block is visible to the statements after it. A later
`let` with the same name *shadows* the earlier one: the earlier binding still
exists but can no longer be named.

<!-- run -->
```kotlin
fun main() {
    let input = "42"
    let input = Int.parse(input)
    print(input)
}
```

```text
Some(42)
```

### Top-level bindings

`let` and `var` can also appear at the top level of a module. A top-level
`var` is a global variable that any function in the module can assign.

<!-- run -->
```kotlin
var calls = 0

fun track(x) {
    calls = calls + 1
    x
}

let answer = track(40) + 2

fun main() {
    print(answer)
    print(calls)
}
```

```text
42
1
```

Top-level declarations are not initialized in the order they are written. The
compiler orders them by what each one uses: a binding is initialized after
everything its initializer refers to, including bindings that functions it
calls refer to. So a top-level `let` can call a function declared further
down. Only functions can depend on each other in a cycle; a cycle that
includes a `let` or `var` is an error:

<!-- error: only functions may be mutually recursive -->
```kotlin
let a = b + 1
let b = a + 1

fun main() {
    print(a)
}
```

Once every top-level binding is initialized, the program calls
[`main`](modules.md#the-entry-point).

## Type annotations

A parameter, a return type, a pattern and an expression can each be given a
type:

<!-- check -->
```kotlin
fun scale(factor : Float, xs : Array Float) -> Array Float {
    let result : Array Float = []
    for x in xs {
        Array.push(result, x * factor)
    }
    result
}
```

Annotations are optional. A function with no annotations has its type
inferred ([Type inference](inference.md)). What an annotation means depends on
how much of the function's header is annotated.

### Signatures

A function whose parameters are **all** annotated and which has a return type
has a **signature**. Its type is exactly what the header says: the body is
checked against it, and the body may not make the type less general. Each type
variable in a signature stands for *any* type that a caller chooses, and the
[context](#contexts) must list every constraint the body needs.

<!-- error: no instance for 'Show a' -->
```kotlin
fun describe(x : a) -> String = show(x)
```

With the constraint written, the same function is accepted:

<!-- run -->
```kotlin
fun describe[Show a](x : a) -> String = "<" + show(x) + ">"

fun main() {
    print(describe(3))
    print(describe([True]))
}
```

```text
<3>
<[True]>
```

A signature is also what lets a function call itself at a different type. This
is called *polymorphic recursion*. It cannot be inferred, so it needs a
signature:

<!-- run -->
```kotlin
type Nested a = Flat(a) | Deeper(Nested (Array a))

fun depth(n : Nested a) -> Int = match n {
    Flat(_) -> 0
    Deeper(inner) -> 1 + depth(inner)
}

fun main() {
    print(depth(Deeper(Deeper(Flat([[1]])))))
}
```

```text
2
```

Without the annotation on `n`, the recursive call would force `a` and
`Array a` to be the same type, which is an error.

> **For readers new to this.** In a type like `fun(Array a) -> a`, `a` is
> *universally quantified*: the function works for every choice of `a`. A
> signature is a promise of that, and the compiler holds the body to it.

### Partial annotations

When only some of the header is annotated, the rest is inferred. What *was*
written still holds: each type variable written in the header must remain an
independent type that the body does not pin down, and the context must cover
every constraint the body places on those variables. Each of these is an
error:

<!-- error: the annotation on 'count' says 'a' is any type, but its body makes it 'String' -->
```kotlin
fun count(label : a, n) -> Int = if n > 3 { n } else { count("more", n + 1) }
```

<!-- error: says 'a' and 'b' are separate types, but its body makes them the same -->
```kotlin
fun pick(x : a, y : b) = if True { x } else { y }
```

<!-- error: the body of 'describe' needs 'Show a', which the context its annotation writes does not state -->
```kotlin
fun describe(x : a, label) = label + show(x)
```

Annotations on types that contain no type variables, such as `n : Int`, simply
fix that type.

Type variables are scoped to the whole function declaration: an `a` in a
parameter, the return type, and an annotation inside the body all refer to the
same type.

## Contexts

**Syntax**

```ebnf
context    ::= "[" constraint ("," constraint)* "]"
constraint ::= qualified-CONID atype          -- a class constraint
             | type "~" type                  -- an equality
```

A context lists the constraints a function's type variables must satisfy. It
goes between the function's name and its parameters:

<!-- run -->
```kotlin
fun largest[Ord a](xs : Array a) -> a {
    var best = xs[0]
    for x in xs {
        if x > best {
            best = x
        }
    }
    best
}

fun main() {
    print(largest([3, 9, 4]))
    print(largest(["pear", "apple"]))
}
```

```text
9
pear
```

`Ord a` requires an [instance](classes.md#instances) of the class `Ord` for
whatever type `a` is at each call. A class constraint applies a class to one
type, which may be a type variable, as in `Ord a`, or a type built from one,
as in `Show (Item c)`. An equality constraint, `Item c ~ Int`, is described
under [Equality constraints](classes.md#equality-constraints).

A context is needed only when a function has a signature or annotates a type
variable. Otherwise the constraints are inferred along with the rest of the
type, and the inferred type shows them in the same bracket form.

> **Coming from Rust.** `fun largest[Ord a](xs : Array a) -> a` is
> `fn largest<A: Ord>(xs: &[A]) -> A`. The type variables are not declared;
> any lowercase name in a type is one.

## Foreign functions

### Syntax

```ebnf
foreign-decl ::= "foreign" STRING "fun" IDENT "(" foreign-params? ")" "->" type body?
foreign-params ::= foreign-param ("," foreign-param)*
foreign-param ::= (IDENT ":")? type
body ::= "=" expression | block
```

The string names the C symbol. Without a body, the declaration calls a C
function and is allowed only in an `Unsafe.` module from the shipped library.
With a body, it defines a C-callable Turkey function and is allowed only in a
compiler-designated *giblet* module from that library. Giblet code does not
allocate or hold a traced value across a call, so a C caller needs no
collector setup. The compiler checks this on the optimized code, including
everything a giblet calls, and refuses a giblet that could do either.
Ordinary application modules cannot declare or define foreign functions.

Every parameter and the result must state its type. A definition also needs
a name for every parameter. The boundary types are `Int`, `Float`, `Byte`,
`Char`, `Bool`, `Unit` and `Prim.Ptr`; traced values such as `String` cannot
cross it. A definition accepts at most seven general-register arguments and
eight floating-point arguments. A C symbol may be defined only once.

<!-- error: may only appear in a giblet module -->
```kotlin
foreign "application_callback" fun callback(x : Int) -> Int = x
fun main() { }
```
