# Patterns

A pattern describes the shape of a value and names its parts. Patterns appear
in `match` arms, in `if let` and `while let`, in `let` and `var` (with or
without `else`), in function
and lambda parameters, and in `for ... in` loops. This chapter covers the
pattern forms, what it means for a `match` to be exhaustive, and which
patterns each of those places accepts.

## Pattern forms

**Syntax**

```ebnf
pattern      ::= alternative ("|" alternative)*          -- alternatives
alternative  ::= pattern-atom (":" type)?
pattern-atom ::= IDENT                                   -- variable
               | "_"                                     -- wildcard
               | INT | FLOAT | STRING | CHAR             -- literal
               | "(" pattern ("," pattern)+ ")"          -- tuple
               | "(" pattern ")"                         -- grouping
               | qualified-CONID                         -- constructor, no payload
               | qualified-CONID "(" (pattern ("," pattern)*)? ")"
               | qualified-CONID "{" field-pattern (separator field-pattern)* (separator "..")? "}"
               | qualified-CONID "{" ".." "}"
field-pattern ::= IDENT "=" pattern
               | IDENT
```

| Pattern | Matches | Binds |
|---|---|---|
| `x` | any value | `x` to the value |
| `_` | any value | nothing |
| `42`, `2.5`, `"yes"`, `'a'` | a value equal to the literal | nothing |
| `(p1, p2)` | a tuple whose elements match `p1` and `p2` | what `p1` and `p2` bind |
| `None` | the constructor `None` | nothing |
| `Some(p)` | a `Some` whose payload matches `p` | what `p` binds |
| `C { f = p, g }` | a `C` whose field `f` matches `p` | what `p` binds, and `g` |
| `p : T` | what `p` matches, checking it has type `T` | what `p` binds |
| `p1 \| p2` | what `p1` or `p2` matches | what the alternative that matched binds |

Patterns nest: `Some((x, _))` matches a `Some` holding a pair, and binds the
pair's first element.

<!-- run -->
```kotlin
type Shape = Circle(Float) | Rect(Float, Float)

fun describe(entry) = match entry {
    (name, None) -> name + ": nothing"
    (name, Some(Circle(r))) -> name + ": circle of radius " + show(r)
    (name, Some(Rect(w, h))) -> name + ": " + show(w) + " by " + show(h)
}

fun main() {
    print(describe(("lid", Some(Circle(2.0)))))
    print(describe(("tray", Some(Rect(3.0, 4.0)))))
    print(describe(("box", None)))
}
```

```text
lid: circle of radius 2.0
tray: 3.0 by 4.0
box: nothing
```

### Variables and wildcards

A lowercase name matches anything and binds that name. A name may appear only
once in a pattern, so `(x, x)` does not test for equal elements; it is an
error. `_` also matches anything, but binds nothing, so it
may appear any number of times.

### Literal patterns

An integer, floating-point, string or character literal matches values equal
to it. A literal pattern has no sign, so `-1` cannot be written as a pattern;
bind the value and compare it instead.

### Tuple patterns

`(p1, p2, ...)` matches a tuple with the same number of elements, element by
element. `(p)` is just `p`.

### Constructor patterns

A constructor with no payload matches only itself. A constructor with a
positional payload takes one pattern per field, in parentheses, and the count
must match the declaration. The parentheses are required, even for a single
field: `Some(x)`, never `Some x`.

A constructor from another module is written the way that module's import
brings it into scope: `Point(x, y)` after a plain `import Geometry`, or
`G.Point(x, y)` after `import Geometry as G`
([Qualified names](modules.md#qualified-names)).

### Record patterns

A record constructor can be matched by field name. Each field is written
`name = pattern`, or just `name`, which is short for `name = name` and binds a
variable with the field's name. Fields may be listed in any order and are
separated by commas or line breaks.

A record pattern must mention **every** field of the constructor, or end with
`..` to ignore the rest on purpose. Writing a field as `name = _` mentions it
without binding it. This makes adding a field to a record an error at every
place that takes the record apart without saying what to do with the rest.

<!-- run -->
```kotlin
type Employee = Employee { name : String, role : String, salary : Int }

fun badge(Employee { name, role, salary = _ }) = name + " (" + role + ")"

fun payroll(employee) {
    let Employee { salary, .. } = employee
    salary
}

fun main() {
    let ada = Employee { name = "Ada", role = "engineer", salary = 100 }
    print(badge(ada))
    print(payroll(ada))
}
```

```text
Ada (engineer)
100
```

<!-- error: the pattern 'Employee' does not mention field 'salary'; name it, or write '..' to ignore the rest -->
```kotlin
type Employee = Employee { name : String, role : String, salary : Int }

fun badge(Employee { name, role }) = name + " (" + role + ")"
```

A record constructor can also be matched positionally, with the fields in
declaration order: `Employee(name, _, _)`. The positional and record forms
match the same values.

### Annotated patterns

`p : T` matches what `p` matches and requires the value to have type `T`. It
is how a parameter gets a type annotation: in `fun f(x : Int)`, `x : Int` is a
pattern. Annotated patterns can appear inside other patterns, as in
`(count : Int, name)`.

### Existential patterns

A constructor pattern whose constructor is
[existential](types.md#existential-types) opens the value: each hidden type
becomes a new type, known only inside the `match` arm or the function whose
pattern it is. Existential patterns are allowed in `match` arms and in
function and lambda parameters, and nowhere else, and not inside
[alternatives](#alternatives).

## Alternatives

**Syntax**

```ebnf
pattern ::= alternative ("|" alternative)*
```

`p1 | p2` matches a value that `p1` or `p2` matches. Alternatives can be
written anywhere a pattern can: at the top of a `match` arm, and nested inside
a tuple, a constructor's arguments or a record's fields. `|` binds more
loosely than anything else in a pattern, so `Some(x) : T | None` is
`(Some(x) : T) | None`; write parentheses to group alternatives where a single
pattern is expected, as in `Some((1 | 2, name))`.

Every alternative must bind the same variables, at the same types, so that the
code after the pattern can use them whichever alternative matched:

<!-- run -->
```kotlin
type Token = Number(Int) | Negated(Int) | Name(String)

fun magnitude(token) = match token {
    Number(n) | Negated(n) -> n
    Name(_) -> 0
}

fun main() {
    print(magnitude(Negated(7)))
}
```

```text
7
```

A line break before `|` does not end the arm, so a long list of alternatives
can be continued on the next line:

<!-- run -->
```kotlin
type Shape = Circle(Int) | Square(Int) | Triangle(Int) | Hexagon(Int) | Dot

fun size(shape) = match shape {
    Circle(n) | Square(n)
        | Triangle(n) | Hexagon(n) -> n
    Dot -> 0
}

fun main() {
    print(size(Triangle(3)))
}
```

```text
3
```

An arm cannot begin with `|` ([`match`](statements.md#match)).

Nested alternatives save repeating the rest of a pattern. A router that
answers `HEAD` like `GET` writes the method's alternatives inside the tuple,
rather than `(GET, "/health") | (HEAD, "/health")`:

<!-- run -->
```kotlin
type Method = GET | HEAD | POST

fun route(method : Method, path : String) -> String = match (method, path) {
    (GET | HEAD, "/health") -> "200 ok"
    (POST, "/orders") -> "201 created"
    (_, "/health" | "/orders") -> "405 method not allowed"
    _ -> "404 not found"
}

fun main() {
    print(route(HEAD, "/health"))
    print(route(GET, "/orders"))
}
```

```text
200 ok
405 method not allowed
```

Alternatives are tried from left to right. When alternatives nested inside a
pattern overlap, the first one that matches is the one taken, and its
variables are the ones bound; if the rest of the pattern does not match, or a
later condition of an [`if let`](statements.md#if-let-and-if-var) fails, the
other alternatives are not tried. The alternatives at the top of an arm, or of
the pattern of a `let` condition, are each tried in turn instead:

<!-- run -->
```kotlin
fun larger(pair : (Int, Int)) -> String {
    let nested = if let ((x, _) | (_, x)) = pair, x > 5 { show(x) } else { "none" }
    let listed = if let (x, _) | (_, x) = pair, x > 5 { show(x) } else { "none" }
    nested + " " + listed
}

fun main() {
    print(larger((1, 9)))
}
```

```text
none 9
```

## Exhaustiveness

A `match` must be **exhaustive**: every value of the subject's type must match
at least one arm. The compiler checks this, and a `match` that misses a case
is an error that names a value it does not handle:

<!-- error: this match is not exhaustive; 'Some(None)' is not handled -->
```kotlin
fun flatten(o) = match o {
    Some(Some(x)) -> x
    None -> 0
}
```

A type with infinitely many values, such as `Int` or `String`, can only be
covered with a variable or a wildcard:

<!-- error: this match is not exhaustive; it needs a catch-all arm -->
```kotlin
fun name(n) = match n {
    0 -> "zero"
    1 -> "one"
}
```

The arms are tried in order and the first one that matches is taken, so an arm
after one that already matches everything it could is never reached. The
compiler does not report such arms.

## Irrefutable patterns

A pattern is **irrefutable** if it matches every value of its type. Variables,
wildcards, tuples of irrefutable patterns, and constructors of types that have
only one constructor, with irrefutable payload patterns, are irrefutable.

Alternatives are irrefutable when together they match every value:

<!-- run -->
```kotlin
fun magnitude(signed : Either Int Int) -> Int {
    let Left(n) | Right(n) = signed
    n
}

fun main() {
    print(magnitude(Right(4)))
}
```

```text
4
```

Places that bind without a second arm to fall back on accept only irrefutable
patterns: `let` and `var` without `else`, function and lambda parameters, and
the pattern of a `for ... in` loop. Anything else there is an error that names a value the
pattern would not match:

<!-- error: this pattern does not match 'None'; add an 'else' block, or use 'if let' or 'match' -->
```kotlin
fun main() {
    let Some(x) = Vec.pop([1, 2, 3])
    print(x)
}
```

Use [`let ... else`](statements.md#let--else-and-var--else), [`if
let`](statements.md#if-let-and-if-var), [`while
let`](statements.md#while-let-and-while-var) or [`match`](statements.md#match)
when a pattern may fail:

<!-- run -->
```kotlin
type Point = Point { x : Int, y : Int }

fun main() {
    let Point { x, y } = Point { x = 3, y = 4 }
    for (label, value) in [("x", x), ("y", y)] {
        print(label + " = " + show(value))
    }
    if let Some(last) = Vec.pop([1, 2, 3]) {
        print(last)
    }
}
```

```text
x = 3
y = 4
3
```
