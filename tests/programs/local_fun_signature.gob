-- A local `fun` inside a body whose signature names a type variable. The
-- local's type was inferred against that signature's skolem, and the lowering
-- left the skolem in it: harmless in the generic body, where the enclosing
-- binder is a variable the checker lets match anything, and fatal in the
-- specialization, where `go : fun(k) -> v` sat inside `memoFix@Int`.
-- FINDINGS 83.

-- The report: a memoizing fixpoint whose local `fun` recurses through `f`.
fun memoFix[Hash k](f : fun(fun(k) -> v, k) -> v) -> fun(k) -> v {
    let table = Map.new()
    fun go(x) {
        match Map.get(table, x) {
            Some(y) -> y
            None -> {
                let y = f(go, x)
                Map.put(table, x, y)
                y
            }
        }
    }
    return go
}

-- Neither recursion nor a constraint is needed: only a written variable.
fun wrap(f : fun(k) -> v) -> fun(k) -> v {
    fun go(x) = f(x)
    return go
}

-- Nor a parameter of the variable's type on the local itself.
fun ident(y : k) -> k {
    fun go() = y
    return go()
}

-- A local that recurses on its own, returning the outer variable's value.
fun repeatLast(x : k, n : Int) -> k {
    fun again(m) = if m == 0 { x } else { again(m - 1) }
    return again(n)
}

fun main() {
    let fib = memoFix(fun(rec, n) = if n < 2 { n } else { rec(n - 1) + rec(n - 2) })
    print(fib(30))
    let words = memoFix(fun(rec, s : String) = if s == "" { 0 } else { 1 })
    print(words("hi"))
    print(wrap(fun(n) = n + 1)(80))
    print(ident("same"))
    print(repeatLast(True, 5))
}
