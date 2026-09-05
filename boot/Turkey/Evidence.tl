module Turkey.Evidence (run, resolve, superPath)

-- Turning every recorded use into evidence (`turkey/evidence.py`'s elaborator).
--
-- Resolution is entailment again, but constructive: where the solver's version
-- answers a `Bool`, this one answers the derivation that produced it.
-- Assumptions first, then the instance table -- the same order, and for the same
-- reason. An assumption is evidence the caller supplied, so preferring it is
-- what keeps a polymorphic function from silently committing to one instance.
--
-- The evidence *types* live in `Turkey.Classes`, because an instance holds the
-- plan that holds them; what is here is the algorithm alone.

import Turkey.Classes as Classes
import Turkey.Classes (ClassTable(..), ClassInfo(..), InstInfo(..),
                       InstancePlan(..), Evidence(..))
import Turkey.Decls as Decls
import Turkey.Diag as Diag
import Turkey.Solved (Scope(..), Use(..), newScope)
import Turkey.Source (Span)
import Turkey.Types as Ty
import Turkey.Types (Pred(..), TBottom)

fun run(classes : ClassTable, uses : Array Use) -> Unit {
    completeInstances(classes)
    for u in uses {
        u.method = Classes.method(classes, u.name)
        u.evidence = Array.map(u.preds, fun(p) = resolve(classes, p, u.scopes, u.span))
    }
}

-- Fill in each instance's superclass dictionaries and default methods.
--
-- A default body is elaborated once, by the generator, and shared by every
-- instance that does not override it -- the class's own dictionary name is
-- rebound per instance when the dictionary is built, which is the whole of what
-- makes one body serve them all.
fun completeInstances(classes : ClassTable) -> Unit {
    for (_, insts) in Map.entries(classes.instances) {
        for inst in insts {
            if Classes.isGenerated(inst.cls) {
                -- A generated field accessor has no superclasses and no
                -- written body: the lowering builds `get` and `set` out of the
                -- primitive `CField` the class is the interface to. There is
                -- nothing here to complete.
                inst.plan = Some(Classes.newInstancePlan(Array.new(1)))
                continue
            }
            let info = match Map.get(classes.classes, inst.cls) {
                Some(i) -> i
                None -> oops("unknown class '" + inst.cls + "'", None)
            }
            let plan = match inst.plan {
                Some(p) -> p
                None -> oops("instance '" + inst.cls
                             + "' was never given a plan", Some(inst.decl.span))
            }
            let scope = newScope()
            let givens = Array.new(len(plan.params))
            for var i = 0; i < len(plan.params) && i < len(inst.context); i = i + 1 {
                Array.push(givens, (plan.params[i], inst.context[i]))
            }
            scope.givens = givens
            let scopes = [scope]
            for sup in info.supers {
                Map.put(plan.supers, sup.name, resolve(classes,
                    Ty.pred(sup.name, [inst.head]), scopes, Some(inst.decl.span)))
            }
            for (name, _) in Map.entries(info.methods) {
                if !Map.contains(plan.methods, name) {
                    match Map.get(info.defaults, name) {
                        Some(impl) -> Map.put(plan.methods, name, impl)
                        None -> {}
                    }
                }
            }
        }
    }
}

fun resolve(classes : ClassTable, p : Pred, scopes : Array Scope,
            where : Option Span) -> Evidence {
    -- Normalized, not merely pruned: `Show (Elem (Array Int))` names an instance
    -- only once the family has been reduced. This is the last place a family can
    -- appear -- evidence is over types, and a family is not one until it is.
    let target = Classes.normalize(classes, p.args[0])
    match target {
        TBottom -> return Absent(p.name)
        _ -> {}
    }
    let demand = Ty.pred(p.name, [target])
    let key = Ty.predKey(demand)

    for var i = len(scopes) - 1; i >= 0; i = i - 1 {
        for (name, given) in scopes[i].givens {
            match superPath(classes, given, key, Array.new(0)) {
                Some(path) -> return FromDict(name, path, Some(given))
                None -> {}
            }
        }
    }

    for inst in Map.getOr(classes.instances, demand.name, Array.new(0)) {
        match Classes.match_(inst.head, target) {
            None -> {}
            Some(mapping) -> return FromInstance(inst,
                Array.map(inst.context, fun(q) = resolve(classes,
                    Ty.pred(q.name, [Decls.substitute(q.args[0], mapping)]),
                    scopes, where)))
        }
    }

    -- Solving proved this predicate; failing to reconstruct *why* would be a bug
    -- in one of the two, so say so rather than reporting it as the program's
    -- fault.
    oops("internal error: no evidence for '" + Ty.showPred(demand)
         + "', which solving had already accepted", where)
}

-- How to reach a predicate from a given one by selecting superclasses.
fun superPath(classes : ClassTable, given : Pred, key : String,
              path : Array String) -> Option (Array String) {
    if Ty.predKey(given) == key { return Some(path) }
    match Map.get(classes.classes, given.name) {
        None -> None
        Some(info) -> {
            for sup in info.supers {
                match superPath(classes, Ty.pred(sup.name, given.args), key,
                                Array.append(path, [sup.name])) {
                    Some(found) -> return Some(found)
                    None -> {}
                }
            }
            None
        }
    }
}

fun oops(message : String, where : Option Span) -> a = match where {
    None -> Diag.fail(Diag.whole("type error", message), "")
    Some(s) -> Diag.fail(Diag.at("type error", message, s), s.file)
}
