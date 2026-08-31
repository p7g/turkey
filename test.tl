fun fact(n) = if n == 0 { 1 } else { n * fact(n - 1) }
fun fib (n) = if n <= 1 { n } else { fib(n - 1) + fib(n - 2) }

fun gcd(a, b) {
    loop {
        if b == 0 {
            break a
        }

        let tmp = a % b
        a = b
        b = tmp
    }
}

fun isPrime(x) {
    for var d = 2; d * d <= x; d = d + 1 {
        if x % d == 0 {
            return False
        }
    }
    return x >= 2
}

-- Data modelling

type Point = Point(Int, Int)

fun origin() = Point(0, 0)
fun translate(Point(x, y), dx, dy) = Point(x + dx, y + dy)
-- No `**`: operators are homogeneous (design.md 8.2), so `Float ** Int` is
-- inexpressible and `Int ** Int` is partial at a negative exponent.
fun square(n) = n * n
fun distanceSquared(Point(ax, ay), Point(bx, by)) = square(bx - ax) + square(by - ax)

type Rect = Rect {
    origin : Point,
    width : Int,
    height : Int,
}

fun area(Rect { width, height }) = width * height
fun between(x, lo, hi) {
    if lo < hi {
        lo <= x && x <= hi
    } else {
        hi <= x && x <= lo
    }
}
fun contains(r, Point(x, y)) {
    let cx = between(x, r.origin.x, r.origin.x + r.width)
    let cy = between(y, r.origin.y, r.origin.y + r.height)
    cx && cy
}
fun move(Rect { origin, width, height }, dx, dy) =
    Rect(translate(origin, dx, dy), width, height)
fun center(Rect { origin, width, height }) = translate(origin, width/2, height/2)

type Color = Color { r : Int, g : Int, b : Int }
fun newColor(r, g, b) = Color(clamp(r, 0, 255), clamp(g, 0, 255), clamp(b, 0, 255))
fun black() = Color(0, 0, 0)
fun white() = Color(255, 255, 255)
fun clamp(n, lo, hi) = if n < lo { lo } else if n > hi { hi } else { n }
-- Not `add`: the prelude's `Add` class owns that name, and a method shares the
-- namespace of ordinary functions. A module system is what would fix it.
fun addColor(a, b) = newColor(a.r + b.r, a.g + b.g, a.b + b.b)
fun scaleColor(c, factor) = newColor(c.r * factor, c.g * factor, c.b * factor)
fun invert(c) = Color(255 - c.r, 255 - c.g, 255 - c.b)

type Shape = Circle { center : Point, radius : Int }
           | Rectangle(Rect)

fun shapeArea(s) = match s {
    Rectangle(r) -> area(r)
    Circle { radius } -> 3 * square(radius) -- approx pi, no conversions yet
}
fun shapeTranslate(s, dx, dy) = match s {
    Rectangle(r) -> Rectangle(move(r, dx, dy))
    Circle { center, radius } -> Circle {
        center = translate(center, dx, dy),
        radius            -- punning, since M9.2
    }
}

-- `Option` is the prelude's now.

fun map(opt, f) = match opt {
    Some(x) -> Some(f(x))
    None    -> None
}

fun orElse(opt, f) = match opt {
    Some(_) -> opt
    None    -> f()
}

fun unwrapOr(opt, default) = match opt {
    Some(x) -> x
    None    -> default
}

fun firstPositive(xs) {
    for x in xs {
        if x > 0 {
            return Some(x)
        }
    }
    None
}

type Result a b = Ok(a) | Err(b)
type DivideError = DivisionByZero
fun divide(a, b) = if b == 0 { Err(DivisionByZero) } else { Ok(a / b) }
fun save_average(xs) {
    var sum = 0
    for x in xs {
        sum = sum + x
    }
    divide(sum, xs.length)
}

type Person   = Person   { name : String, age : Int }
type Employee = Employee { person : Person, salary : Int }
type Company  = Company  { employees : Array Employee }

fun giveRaise(employee, amount) {
    employee.salary = employee.salary + amount
}
fun isAdult(person) = person.age >= 21
fun employeeCost(employee) = employee.salary

-- Expression AST

type Expr = ELit(Int)
          | EAdd(Expr, Expr)
          | EMul(Expr, Expr)
          | ENeg(Expr)

fun eval(expr) = match expr {
    ELit(n)    -> n
    EAdd(a, b) -> eval(a) + eval(b)
    EMul(a, b) -> eval(a) * eval(b)
    ENeg(a)    -> -eval(a)
}

-- Tiny stack machine

type Stack a = Stack (Array a)

fun newStack()              = Stack(Array.new(0))
fun push    (Stack(arr), x) = Array.push(arr, x)
fun pop     (Stack(arr))    = Array.pop(arr)
fun popOr   (s, default)    = unwrapOr(pop(s), default)

type Instr = IPush(Int)
           | IAdd
           | IMul
           | INeg

-- The annotation is documentation, not a requirement. It once was one: `Item a`
-- is not injective (M7), so matching the loop variable against `IPush` cannot
-- say which `Iterator` instance produced it. Since delta 39 a scheme may carry
-- the equation instead, and without the annotation this infers
-- `[Item a ~ Instr, Iterator a] fun(a) -> Int` -- which is the more general
-- type, and says out loud what the body needed.
fun exec(instrs : Array Instr) {
    let s = newStack()
    for i in instrs {
        push(s, match i {
            IPush(n) -> n
            IAdd     -> popOr(s, 0) + popOr(s, 0)
            IMul     -> popOr(s, 0) * popOr(s, 0)
            INeg     -> -popOr(s, 0)
        })
    }
    popOr(s, 0)
}

fun main() {
    print(Int.toString(fact(10)))
    print(Int.toString(fib(10)))
    print(Int.toString(gcd(12, 18)))
    print(isPrime(13) && !isPrime(12))

    -- Data modelling
    let p = Point(10, 20)
    let q = translate(p, 3, -5)
    let d = distanceSquared(p, q)
    print(Int.toString(d))

    -- Expression AST
    let expr = EMul(EAdd(ELit(2), ELit(3)), ENeg(ELit(4)))
    print(Int.toString(eval(expr)))

    -- Tiny stack machine
    let instrs = [IPush(2), IPush(3), IAdd, IPush(4), INeg, IMul]
    print(Int.toString(exec(instrs)))
}
