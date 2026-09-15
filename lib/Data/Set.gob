module Data.Set (Set, new, add, remove, contains, elements)

-- A set is a map that has forgotten its values.
--
-- Written as a wrapper rather than as a second hash table, because there is no
-- second implementation worth having: `Map k Unit` is already open-addressed,
-- already insertion-ordered, and already the thing that would be copied. What
-- the wrapper buys is the type -- `Set String` says what `Map String Unit`
-- means -- and it costs nothing at run time, since `Unit` is zero-sized.

import Prelude ()
import Std.Classes
import Data.Array (Array)
import Data.Bool.Type (Bool(..))
import Data.Option.Type (Option(..))
import Data.Map as Map
import Data.Map (Map, MapCursor)

type Set a = Set { table : Map a Unit }

fun new() -> Set a = Set { table = Map.new() }

fun add[Hash a](s : Set a, x : a) -> Unit = Map.put(s.table, x, ())

-- Answers whether the element was there, which is what `Map.delete` answers
-- and what a caller deciding whether it changed anything needs.
fun remove[Hash a](s : Set a, x : a) -> Bool = Map.delete(s.table, x)

fun contains[Hash a](s : Set a, x : a) -> Bool = Map.contains(s.table, x)

-- Insertion-ordered, for the reason `Map.keys` is.
fun elements(s : Set a) -> Array a = Map.keys(s.table)

instance Length (Set a) {
    fun len(s) = len(s.table)
}

instance Iterator (Set a) {
    type Item = a
    type Cursor = MapCursor a Unit

    fun iter(s) = iter(s.table)
    fun next(s, c) = match next(s.table, c) {
        None -> None
        Some(pair) -> Some(pair.0)
    }
}

instance Show (Set a) : Show a {
    fun show(s) {
        var out = "{"
        var rest = False
        for x in s {
            if rest { out = out + ", " }
            out = out + show(x)
            rest = True
        }
        out + "}"
    }
}
