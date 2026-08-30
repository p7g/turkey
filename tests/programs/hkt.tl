-- Abstraction over a type constructor. `f` in `Wrap f a` has kind `* -> *`,
-- which nothing in the source says: it is discovered from `f a` in the body,
-- and every use of `Wrap` then has to supply a constructor rather than a type.

type Option a = None | Some(a)

type Wrap f a = Wrap(f a)

-- An alias may take a higher-kinded parameter too, and is expanded where it is
-- used, so `Boxed Array` is `Wrap Array Int` and not a type of its own.
type Boxed f = Wrap f Int

fun unwrap(w) = match w {
    Wrap(inner) -> inner
}

-- One function over both `Wrap Option a` and `Wrap Array a`: the argument the
-- constructor is applied to is what varies, and `f` is what is quantified.
fun describe(w : Wrap Option Int) -> String = match unwrap(w) {
    Some(n) -> Int.toString(n)
    None -> "nothing"
}

fun total(w : Boxed Array) -> Int {
    var sum = 0
    for n in unwrap(w) {
        sum = sum + n
    }
    return sum
}

fun main() {
    print(describe(Wrap(Some(3))))
    print(describe(Wrap(None)))
    print(Int.toString(total(Wrap([1, 2, 3, 4]))))
    -- Nesting: `Wrap (Wrap Array) Int` needs `Wrap Array` in head position,
    -- which is a partially applied constructor.
    let nested : Wrap (Wrap Array) Int = Wrap(Wrap([5, 6]))
    print(Int.toString(total(unwrap(nested))))
}
