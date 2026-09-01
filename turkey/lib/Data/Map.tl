module Data.Map (Map, new, get, put, delete)

import Prelude ()
import Algorithm.Hash (fnv1a)
import Data.Int as Int
import Data.Bool (Bool(..))
import Data.Float ()
import Data.Option (Option(..))
import Data.String ()
import Std.Classes

let loadFactor = 0.6
let defaultSize = 16

type Map k v = Map {
    table          : Prim.Array (Entry k v),
    cap            : Int,
    size           : Int,
    tombstoneCount : Int,
    head           : Option (Bucket k v),
    tail           : Option (Bucket k v),
}

type Bucket k v = Bucket {
    key   : k,
    value : v,
    hash  : Int,
    next  : Option (Bucket k v),
    prev  : Option (Bucket k v),
}

type Entry k v
    = Empty
    | Tombstone
    | Active (Bucket k v)

fun hashKey(k) {
    let d = fnv1a()
    hashInto(k, d)
    digest(d)
}

fun new() -> Map k v {
    let table = Prim.arrayNew(defaultSize, Empty)
    Map {
        table,
        cap = defaultSize,
        size = 0,
        tombstoneCount = 0,
        head = None,
        tail = None,
    }
}

fun findSlot(m, h, key, isSearching) {
    -- `Int.mod`, not `%`. A 64-bit hash is signed and half of them are
    -- negative, and `%` is *remainder*: it takes the sign of the dividend, so
    -- `h % cap` would be a negative bucket index roughly half the time. This
    -- is the case PRIMITIVES.md 1.2 says floored `mod` exists for.
    var index = Int.mod(h, m.cap)
    var firstTombstone = None

    loop {
        let entry = Prim.arrayGet(m.table, index)

        match entry {
            Empty -> {
                if isSearching {
                    break index
                }
                break match firstTombstone {
                    Some(x) -> x
                    None    -> index
                }
            }
            Tombstone -> {
                if isSearching && firstTombstone == None {
                    firstTombstone = Some(index)
                }
            }
            Active(bucket) -> {
                if bucket.key == key {
                    break index
                }
            }
        }

        index = (index + 1) % m.cap
    }
}

fun put[Hash k](m : Map k v, key : k, value : v) -> Unit {
    let h = hashKey(key)
    let i = findSlot(m, h, key, False)

    match Prim.arrayGet(m.table, i) {
        Active(bucket) -> {
            bucket.value = value
            return
        }
        _ -> ()
    }

    let bucket = Bucket {
        key = key,
        value = value,
        hash = h,
        next = None,
        prev = m.tail,
    }
    Prim.arraySet(m.table, i, Active(bucket))

    match m.tail {
        Some(tl) -> {
            tl.next = Some(bucket)
        }
        None -> {
            m.head = Some(bucket)
        }
    }

    m.tail = Some(bucket)
    m.size = m.size + 1

    if Prim.intToFloat(m.size + m.tombstoneCount) > Prim.intToFloat(m.cap) * loadFactor {
        resize(m)
    }
}

fun get[Hash k](m : Map k v, key : k) -> Option v {
    let h = hashKey(key)
    let i = findSlot(m, h, key, True)
    match Prim.arrayGet(m.table, i) {
        Active(bucket) -> Some(bucket.value)
        _              -> None
    }
}

fun delete[Hash k](m : Map k v, key : k) -> Bool {
    let h = hashKey(key)
    let i = findSlot(m, h, key, True)

    let bucket = match Prim.arrayGet(m.table, i) {
        Empty          -> return False
        Tombstone      -> error("found tombstone in delete()")
        Active(bucket) -> bucket
    }

    Prim.arraySet(m.table, i, Tombstone)
    m.tombstoneCount = m.tombstoneCount + 1
    m.size = m.size - 1

    match bucket.prev {
        Some(prev) -> {
            prev.next = bucket.next
        }
        None -> {
            m.head = bucket.next
        }
    }

    match bucket.next {
        Some(next) -> {
            next.prev = bucket.prev
        }
        None -> {
            m.tail = bucket.prev
        }
    }

    True
}

fun resize(m) {
    let newCap = m.cap * 2
    m.table = Prim.arrayNew(newCap, Empty)
    m.cap = newCap
    m.tombstoneCount = 0

    var current = m.head
    loop {
        match current {
            None -> break
            Some(bucket) -> {
                var i = Int.mod(bucket.hash, newCap)
                loop {
                    match Prim.arrayGet(m.table, i) {
                        Empty -> break
                        _ -> {
                            i = (i + 1) % newCap
                        }
                    }
                }
                Prim.arraySet(m.table, i, Active(bucket))
                current = bucket.next
            }
        }
    }
}

instance Index (Map k v) : Hash k {
    type Key = k
    type Value = v

    fun get(m, k) {
        match get(m, k) {
            Some(v) -> v
            None -> error("No such key")
        }
    }

    fun set(m, k, v) = put(m, k, v)
}

instance Length (Map k v) {
    fun len(m) = m.size
}

type MapCursor k v = MapCursor { bucket : Option (Bucket k v) }

instance Iterator (Map k v) {
    type Item = (k, v)
    type Cursor = MapCursor k v

    fun iter(m) = MapCursor(m.head)
    fun next(_, c) {
        match c.bucket {
            None -> None
            Some(b) -> {
                c.bucket = b.next
                Some((b.key, b.value))
            }
        }
    }
}

instance Show (Map k v) : Show k, Show v {
    fun show(m) {
        var s = "{"
        var first = True
        for (k, v) in m {
            if first {
                first = False
            } else {
                s = s + ", "
            }
            s = s + show(k) + ": " + show(v)
        }
        s + "}"
    }
}
