module Data.Option (Option(..), isSome, unwrapOr, map, fold)

import Std.Classes
import Data.Bool.Type (Bool(..))
import Data.Option.Type (Option(..))

fun isSome(o : Option a) -> Bool = match o {
    Some(_) -> True
    None -> False
}

fun unwrapOr(o : Option a, fallback : a) -> a = match o {
    Some(x) -> x
    None -> fallback
}

fun map(o : Option a, f : fun(a) -> b) -> Option b = match o {
    Some(x) -> Some(f(x))
    None -> None
}

fun fold(o : Option a, onNone : b, onSome : fun(a) -> b) -> b = match o {
    Some(x) -> onSome(x)
    None -> onNone
}

instance Functor Option {
    fun map(o, f) = map(o, f)
}

instance Applicative Option { fun pure(x) = Some(x) }

instance Monad Option {
    fun bind(o, g) = match o {
        Some(x) -> g(x)
        None -> None
    }
}

instance Show (Option a) : Show a {
    fun show(o) = match o {
        None -> "None"
        Some(x) -> Prim.stringConcat(Prim.stringConcat("Some(", show(x)), ")")
    }
}
