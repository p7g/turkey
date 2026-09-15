module Turkey.Solved (
    Scope(..), newScope,
    Abstraction(..), newAbstraction,
    Use(..), newUse,
    dictName, resetDictNames,
)

-- What solving records for elaboration (`turkey/evidence.py`'s first half).
--
-- Three records, and they are here rather than with the elaborator because the
-- constraint tree holds them: a `CInstance` carries a `Use`, a `CLet` carries an
-- `Abstraction`, and a `CAssume` opens a `Scope`. Each is filled in *after* it
-- has been handed out -- what a binder makes available is not known until its
-- definition has been solved -- so what travels is the record and not its
-- contents.

import Turkey.Classes (Evidence(..), MethodInfo(..))
import Turkey.Source (Span)
import Turkey.Types as Ty
import Turkey.Types (Scheme(..), Pred(..))

-- The dictionaries a binder makes available inside its definition.
--
-- Created empty when the solver descends into a definition and filled in when it
-- comes back out. A `Use` only holds the record, so filling it late is enough.
-- The `id` is what Python compares with `is`. A scope is created once and handed
-- out by reference, and two scopes granting the same predicates are still two
-- scopes, so structure cannot stand in for identity.
type Scope = Scope {
    id     : Int,
    givens : Array (String, Pred),
}

fun newScope() -> Scope {
    dictCounter.scopes = dictCounter.scopes + 1
    Scope { id = dictCounter.scopes, givens = Array.new(2) }
}

-- The leading dictionary parameters of one binding group.
--
-- The generator hangs one of these on each declaration of the group and on the
-- group's `CLet`; the solver fills it in when it generalizes.
type Abstraction = Abstraction {
    params  : Array String,
    preds   : Array Pred,
    -- The scheme each name in the group ended up with, filled in beside the
    -- parameters and by the same loop. A *local* binding's scheme is otherwise
    -- unrecoverable: it is defined into a child environment popped as soon as
    -- the body is solved, so by the time anything lowers the tree there is
    -- nowhere left to ask what `fun go()` was generalized to.
    schemes : Map String Scheme,
    -- For a group that is one *signature-checked* declaration: which rigid
    -- constant stands for which of the signature's variables. A declared type is
    -- checked against skolems, so the body's recorded types name constants where
    -- the scheme names variables.
    skolems : Map Int Ty.Type,
}

fun newAbstraction() -> Abstraction = Abstraction {
    params = Array.new(2), preds = Array.new(2),
    schemes = Map.new(), skolems = Map.new(),
}

-- One occurrence of a name, and the evidence it turned out to need.
--
-- `typeArgs` is the other half of the same story, filled in beside the
-- predicates by the same line of the solver. A use of a polymorphic name
-- instantiates its scheme at *something*; the predicates say what evidence that
-- costs, and the type arguments say what the instantiation was. They are in the
-- scheme's `quantified` order, which is the order the definition's type
-- abstraction binds them in -- so the two need no separate agreement.
type Use = Use {
    name     : String,
    span     : Option Span,
    preds    : Array Pred,
    scopes   : Array Scope,
    evidence : Array Evidence,
    method   : Option MethodInfo,
    typeArgs : Array Ty.Type,
}

fun newUse(name : String, where : Option Span, m : Option MethodInfo) -> Use = Use {
    name = name, span = where, preds = Array.new(2), scopes = Array.new(2),
    evidence = Array.new(2), method = m, typeArgs = Array.new(2),
}

-- -- generated dictionary names ----------------------------------------------------

type Counter = Counter { n : Int, scopes : Int }

let dictCounter = Counter { n = 0, scopes = 0 }

-- A runtime name for one dictionary parameter. `%` cannot start an identifier,
-- so a generated name can never be shadowed by, or shadow, one the program
-- wrote.
fun dictName(hint : String) -> String {
    let name = "%d" + Int.toString(dictCounter.n) + "." + hint
    dictCounter.n = dictCounter.n + 1
    name
}

-- Reset per program, not per process. Python's counter is a module global and
-- never reset, which is correct there because one interpreter checks one
-- program; this compiler checks many in one process, and a drifting counter
-- would make the second program's dictionary names differ from the first's for
-- no reason in the source. See FINDINGS.md 27.
fun resetDictNames() -> Unit {
    dictCounter.n = 0
}
