-- Every operator is a class method (design.md 8.2). Nothing below is built in:
-- `+` means `Add.add`, and `instance Add Money` is what makes the last section
-- run.

type Money = Money { cents : Int }

instance Add Money {
    fun add(a, b) = Money { cents = a.cents + b.cents }
}

instance Neg Money {
    fun neg(a) = Money { cents = -a.cents }
}

instance Eq Money {
    fun eq(a, b) = a.cents == b.cents
}

instance Ord Money {
    fun lt(a, b) = a.cents < b.cents
}

-- Inferred, and general: nothing here says which type is being added.
fun sum3(a, b, c) = a + b + c

fun biggest(xs) {
    var best = xs[0]
    for x in xs {
        if x > best { best = x }
    }
    best
}

-- `print` is `Prim.print(show(x))`, so a type that says how it looks is a
-- type that can be printed.
instance Show Money {
    fun show(m) = Int.toString(m.cents) ++ "c"
}

fun main() {
    print(Int.toString(7 / 2) ++ " " ++ Int.toString(-7 / 2))
    print(Int.toString(7 % 2) ++ " " ++ Int.toString(-7 % 2))
    print(Float.toString(1 + 2.5))
    print(Bool.toString("abc" < "abd") ++ " " ++ Bool.toString('a' == 'a'))
    print(Bool.toString(False < True) ++ " " ++ Bool.toString(1.5 != 1.5))

    print(Int.toString(sum3(1, 2, 3)))
    print(Float.toString(sum3(0.5, 0.25, 0.25)))
    print(sum3(Money { cents = 1 }, Money { cents = 2 }, Money { cents = 3 }))

    print(-Money { cents = 5 })
    print(Bool.toString(Money { cents = 1 } < Money { cents = 2 }))
    print(biggest([Money { cents = 3 }, Money { cents = 9 }, Money { cents = 4 }]))
    print(Int.toString(biggest([3, 9, 4])))
}
