module Data.Tuple ()

import Std.Classes

instance Eq (a, b) : Eq a, Eq b {
    fun eq((a, b), (c, d)) = eq(a, c) && eq(b, d)
}

instance Ord (a, b) : Ord a, Ord b {
    fun lt((a, b), (c, d)) = if eq(a, c) { lt(b, d) } else { lt(a, c) }
}

instance Show (a, b) : Show a, Show b {
    fun show((a, b)) = Prim.stringConcat(
        Prim.stringConcat(Prim.stringConcat("(", show(a)), ", "),
        Prim.stringConcat(show(b), ")"),
    )
}

instance Eq (a, b, c) : Eq a, Eq b, Eq c {
    fun eq((a, b, c), (d, e, f)) = eq(a, d) && eq(b, e) && eq(c, f)
}

instance Ord (a, b, c) : Ord a, Ord b, Ord c {
    fun lt((a, b, c), (d, e, f)) = if eq(a, d) {
        if eq(b, e) { lt(c, f) } else { lt(b, e) }
    } else { lt(a, d) }
}

instance Show (a, b, c) : Show a, Show b, Show c {
    fun show((a, b, c)) = Prim.stringConcat(
        Prim.stringConcat(
            Prim.stringConcat(Prim.stringConcat("(", show(a)), ", "),
            Prim.stringConcat(Prim.stringConcat(show(b), ", "), show(c)),
        ),
        ")",
    )
}

instance Eq (a, b, c, d) : Eq a, Eq b, Eq c, Eq d {
    fun eq((a, b, c, d), (e, f, g, h)) =
        eq(a, e) && eq(b, f) && eq(c, g) && eq(d, h)
}

instance Ord (a, b, c, d) : Ord a, Ord b, Ord c, Ord d {
    fun lt((a, b, c, d), (e, f, g, h)) = if eq(a, e) {
        if eq(b, f) {
            if eq(c, g) { lt(d, h) } else { lt(c, g) }
        } else { lt(b, f) }
    } else { lt(a, e) }
}

instance Show (a, b, c, d) : Show a, Show b, Show c, Show d {
    fun show((a, b, c, d)) = Prim.stringConcat(
        Prim.stringConcat(
            Prim.stringConcat(
                Prim.stringConcat(Prim.stringConcat("(", show(a)), ", "),
                Prim.stringConcat(Prim.stringConcat(show(b), ", "), show(c)),
            ),
            ", ",
        ),
        Prim.stringConcat(show(d), ")"),
    )
}
