module Turkey.Prelude (
    monadBind, monadPure,
    flowFall, flowBrk, flowCont, flowRet,
    error,
    optionType, optionNone, optionSome,
    boolTrue, boolFalse,
    preludeName, entryName, sep, internal,
)

-- The compiler's half of the Prelude (`turkey/prelude.py`).
--
-- The Prelude itself is ordinary source in `lib/Prelude.tl` and is loaded like
-- any other module. What is here is the handful of names the *compiler* has to
-- know, because it writes nodes naming them: what `?` desugars to, and what a
-- `return` crossing a `?` becomes. A program cannot change any of them by
-- shadowing, which is why they are constants here rather than lookups.
--
-- The names that reach an AST are already *internal* -- `Prelude#Fall`, not
-- `Fall` -- because desugaring runs after resolution and everything it writes
-- has to be spelled the way resolution would have spelled it.

-- What `?` is written in terms of (SPEC-DELTAS.md 46). Both are looked up in
-- the module's protocol-method scope and marked as method references, so a
-- module defining its own `bind` does not capture the one a `?` means.
let monadBind = "bind"
let monadPure = "pure"

-- What a `return`, `break` or `continue` becomes when it crosses a `?`
-- (SPEC-DELTAS.md 47). Declared in the Prelude and exported by nothing, so
-- only the lowering can write one.
let flowFall = "Prelude#Fall"
let flowBrk = "Prelude#Brk"
let flowCont = "Prelude#Cont"
let flowRet = "Prelude#Ret"

-- Diverges, so it can stand in for a `Flow` arm the language's own rules make
-- unreachable -- a `break` where there is no loop -- which the exhaustiveness
-- checker has no way to know is unreachable.
let error = "Std.Classes#error"

let optionType = "Data.Option.Type#Option"
let optionNone = "Data.Option.Type#None"
let optionSome = "Data.Option.Type#Some"

let boolTrue = "Data.Bool.Type#True"
let boolFalse = "Data.Bool.Type#False"

let preludeName = "Prelude"
let entryName = "Main"

-- What a module's top-level binding is called once resolution has run. `#` is
-- not a character any surface name may contain, so an internal name can never
-- collide with one a program could write -- and a diagnostic can strip the
-- prefix back off without having to guess where a module name ends, which an
-- ordinary `.` would leave ambiguous against `Array.push`.
let sep = "#"

fun internal(owner : String, name : String) -> String = owner + sep + name
