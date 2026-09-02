module Turkey.Modules (
    Scope(..), newScope, spaces,
    Module(..),
    Loader(..), newLoader, loadEntry,
    ownDeclared, ownScope,
)

-- Loading a program's modules, and deciding what each one can see
-- (design.md 9, `turkey/modules.py`).
--
-- A program is not a file; it is a graph of them. This module walks that graph
-- and produces, for each module, the two tables the rest of the compiler needs:
-- a *scope* mapping every surface name the module may write to the internal
-- name it means, and an *export* map that is the part of that scope its
-- importers get to share. `Turkey.Resolve` does the rewriting; nothing here
-- touches a tree beyond parsing it.
--
-- Three rules, in this order, later winning: what the Prelude exports, then
-- each `import`, then the module's own top-level declarations. So a local
-- definition shadows an import, and an import shadows the Prelude.
--
-- The import graph must be acyclic. Two modules that need each other are one
-- module here, which is also why this compiler's own layering is a DAG.

import Turkey.Ast
import System.IO as IO
-- `Data.Set` is not one of the modules the Prelude re-exports.
import Data.Set as Set
import Turkey.Diag as Diag
import Turkey.Parser as Parser
import Turkey.Prelude as Prelude
import Turkey.Prims as Prims
import Turkey.Source as Source
import Turkey.Source (Source, Span)

-- -- scopes -------------------------------------------------------------------------

-- One module's view of the world, in five namespaces.
--
-- They have to be at least three. A value is lowercase while a type and a
-- constructor are both uppercase, so `type Point = Point(Int, Int)` puts the
-- same spelling in two of them. Each maps a name the module may *write* --
-- bare, or qualified by an import's alias -- to the internal name it means.
--
-- Class identity is scoped even though the resulting instance table is global:
-- two modules may define different classes with the same short name. The fifth
-- is protocol-method identity, for the operator and iteration nodes the parser
-- writes itself; a method also appears in `values` for ordinary calls.
type Scope = Scope {
    values  : Map String String,
    types   : Map String String,
    cons    : Map String String,
    classes : Map String String,
    methods : Map String String,
}

fun newScope() -> Scope = Scope {
    values = Map.new(), types = Map.new(), cons = Map.new(),
    classes = Map.new(), methods = Map.new(),
}

-- The five, in a fixed order, so that a rule which applies to all of them is
-- written once. A `Map` is a reference, so these are the scope's own tables and
-- not copies of them.
fun spaces(s : Scope) -> Array (Map String String) =
    [s.values, s.types, s.cons, s.classes, s.methods]

type Module = Module {
    name    : String,
    program : Program,
    library : Bool,
    scope   : Scope,
    -- The part of that scope an importer gets.
    exports : Scope,
}

-- -- the loader ---------------------------------------------------------------------

type Loader = Loader {
    -- The entry file's own directory first, then the shipped library, which is
    -- always last and is what marks a module as one the language ships.
    search       : Array String,
    modules      : Map String Module,
    -- Every module, in the order they finished loading, which is dependency
    -- order because a module finishes after everything it imports.
    order        : Array Module,
    -- Every type name known so far, threaded into later parses: section 7's
    -- alias-versus-data pre-pass cannot see another file.
    tycons       : Array String,
    -- Internal type name -> its constructors' short names, so a `T(..)` in an
    -- export or import list can be answered for a type this module did not
    -- itself declare.
    variants     : Map String (Array String),
    -- Internal class name -> its exported methods and associated families.
    classMembers : Map String (Array String),
}

fun newLoader(search : Array String) -> Loader = Loader {
    search = search,
    modules = Map.new(),
    order = Array.new(8),
    tycons = Array.new(16),
    variants = Map.new(),
    classMembers = Map.new(),
}

-- Load the program and the effective imports it declares.
fun loadEntry(l : Loader, source : Source, name : String) -> Module =
    add(l, name, source, False, [name])

fun load(l : Loader, name : String, stack : Array String) -> Module =
    match Map.get(l.modules, name) {
        Some(existing) -> existing
        None -> {
            if Array.contains(stack, name) {
                oops("imports form a cycle: " + cycleOf(stack, name)
                     + ". Two modules that need each other are one module here.")
            }
            let (path, library) = resolvePath(l, name)
            let source = match Source.read(path) {
                Some(s) -> s
                None -> oops("cannot read module '" + name + "' at " + path)
            }
            add(l, name, source, library, Array.append(stack, [name]))
        }
    }

fun cycleOf(stack : Array String, name : String) -> String {
    let from = match Array.indexOf(stack, name) {
        Some(i) -> i
        None -> 0
    }
    String.join(Array.append(Array.slice(stack, from, len(stack)), [name]), " -> ")
}

-- `Data.String` is `Data/String.tl` under one of the search roots. The last
-- root is the library, and a module found there is a library module -- which is
-- the whole of what `library` means and the only thing that decides whether
-- `Prim.*` is spellable.
fun resolvePath(l : Loader, name : String) -> (String, Bool) {
    let relative = String.replace(name, ".", "/") + ".tl"
    for var i = 0; i < len(l.search); i = i + 1 {
        let candidate = l.search[i] + "/" + relative
        if IO.canRead(candidate) {
            return (candidate, i == len(l.search) - 1)
        }
    }
    oops("cannot find module '" + name + "' (searched "
         + String.join(l.search, ", ") + ")")
}

fun add(l : Loader, name : String, source : Source, library : Bool,
        stack : Array String) -> Module {
    var program = Parser.parse(source, l.tycons)
    let before = len(l.tycons)
    let explicitPrelude = hasPreludeImport(program)
    if name != Prelude.preludeName && !explicitPrelude {
        discard(load(l, Prelude.preludeName, stack))
    }
    for imp in program.imports {
        -- The empty Prelude import removes the otherwise implicit edge. Empty
        -- imports of every other module remain instance-only edges.
        if !(imp.name == Prelude.preludeName && isEmptyList(imp.items)) {
            discard(load(l, imp.name, stack))
        }
    }
    if len(l.tycons) != before {
        -- An imported type name changes how section 7 reads this file, and the
        -- pre-pass that decides it already ran. Read it again, knowing.
        program = Parser.parse(source, l.tycons)
    }

    for d in program.decls {
        match d {
            DType(t) -> {
                Array.push(l.tycons, t.name)
                match t.variants {
                    Some(vs) -> Map.put(l.variants, Prelude.internal(name, t.name),
                                        Array.map(vs, conName))
                    None -> {}
                }
            }
            DClass(c) -> Map.put(l.classMembers, Prelude.internal(name, c.name),
                                 Array.append(Array.map(c.methods, funName),
                                              Array.map(c.families, famName)))
            _ -> {}
        }
    }

    let m = Module {
        name = name, program = program, library = library,
        scope = newScope(), exports = newScope(),
    }
    m.scope = scopeOf(l, m)
    m.exports = exportsOf(l, m)
    Map.put(l.modules, name, m)
    Array.push(l.order, m)
    m
}

fun conName(c : ConDecl) -> String = c.name
fun funName(f : FunDecl) -> String = f.name
fun famName(f : FamDecl) -> String = f.name

fun hasPreludeImport(program : Program) -> Bool {
    for imp in program.imports {
        if imp.name == Prelude.preludeName { return True }
    }
    False
}

fun isEmptyList(items : Option (Array ExportItem)) -> Bool = match items {
    Some(xs) -> len(xs) == 0
    None -> False
}

-- -- scope --------------------------------------------------------------------------

fun scopeOf(l : Loader, m : Module) -> Scope {
    let scope = newScope()
    -- `Prim.*` is spellable only from a library module, which is what keeps the
    -- machine operations out of the language: the environment holds them for
    -- every module, and this is the stage that says no.
    if m.library {
        for name in Prims.names { Map.put(scope.values, name, name) }
        Map.put(scope.types, "Prim.Array", "Prim.Array")
    }
    -- So are the built-in type constructors, which no module declares.
    for name in Parser.builtinTycons { Map.put(scope.types, name, name) }

    let explicitPrelude = hasPreludeImport(m.program)
    if m.name != Prelude.preludeName && !explicitPrelude {
        bring(l, scope, moduleNamed(l, Prelude.preludeName), Prelude.preludeName,
              False, None, None, None)
    }
    for imp in m.program.imports {
        if !(imp.name == Prelude.preludeName && isEmptyList(imp.items)) {
            let dep = moduleNamed(l, imp.name)
            let alias = match imp.alias {
                Some(a) -> a
                None -> imp.name
            }
            bring(l, scope, dep, alias, imp.qualified, Some(imp.span),
                  imp.items, imp.hidden)
        }
    }

    -- Last, so a module's own declarations shadow everything imported.
    let own = ownScope(m.name, m.program)
    let into = spaces(scope)
    let from = spaces(own)
    for var i = 0; i < len(into); i = i + 1 {
        for (k, v) in Map.entries(from[i]) { Map.put(into[i], k, v) }
    }
    scope
}

fun moduleNamed(l : Loader, name : String) -> Module = match Map.get(l.modules, name) {
    Some(m) -> m
    None -> oops("module '" + name + "' was not loaded")
}

fun bring(l : Loader, scope : Scope, dep : Module, alias : String,
          qualified : Bool, where : Option Span,
          items : Option (Array ExportItem),
          hidden : Option (Array ExportItem)) -> Unit {
    var chosen = Array.map(spaces(dep.exports), copyMap)
    match items {
        None -> {}
        Some(wantedItems) -> {
            let wanted = Set.new()
            for item in wantedItems {
                Set.add(wanted, item.name)
                -- `T(..)` asks for constructors; `C(..)` asks for methods and
                -- associated families. The namespace decides which it is.
                for sub in subsOf(l, dep, item) { Set.add(wanted, sub) }
            }
            let missing = Array.new(2)
            for name in Array.sort(Set.elements(wanted)) {
                if !namedAnywhere(dep.exports, name) { Array.push(missing, name) }
            }
            if len(missing) > 0 {
                oopsAt("module '" + dep.name + "' does not export "
                       + String.join(Array.map(missing, quoted), ", "), where)
            }
            chosen = Array.map(chosen, fun(space) =
                keepIf(space, fun(n) = Set.contains(wanted, n)))
        }
    }
    match hidden {
        None -> {}
        Some(hiddenItems) -> {
            let out = Set.new()
            for item in hiddenItems {
                Set.add(out, item.name)
                if Option.isSome(Map.get(dep.exports.classes, item.name)) {
                    for sub in subsOf(l, dep, item) { Set.add(out, sub) }
                }
            }
            chosen = Array.map(chosen, fun(space) =
                keepIf(space, fun(n) = !Set.contains(out, n)))
        }
    }

    let into = spaces(scope)
    for var i = 0; i < len(into); i = i + 1 {
        for (short, target) in Map.entries(chosen[i]) {
            Map.put(into[i], alias + "." + short, target)
            if !qualified { Map.put(into[i], short, target) }
        }
    }
}

fun quoted(name : String) -> String = "'" + name + "'"

fun copyMap(m : Map String String) -> Map String String {
    let out = Map.new()
    for (k, v) in Map.entries(m) { Map.put(out, k, v) }
    out
}

fun keepIf(m : Map String String, keep : fun(String) -> Bool) -> Map String String {
    let out = Map.new()
    for (k, v) in Map.entries(m) {
        if keep(k) { Map.put(out, k, v) }
    }
    out
}

fun namedAnywhere(s : Scope, name : String) -> Bool {
    for space in spaces(s) {
        if Map.contains(space, name) { return True }
    }
    False
}

-- The members `T(..)` or `C(..)` names, given what `T` or `C` is here.
fun subsOf(l : Loader, dep : Module, item : ExportItem) -> Array String =
    match Map.get(dep.exports.classes, item.name) {
        Some(cls) -> subsFrom(item, membersOf(l, cls))
        None -> subsFrom(item, variantsOf(l, Map.get(dep.exports.types, item.name)))
    }

fun membersOf(l : Loader, cls : String) -> Array String =
    Map.getOr(l.classMembers, cls, Array.new(0))

fun variantsOf(l : Loader, target : Option String) -> Array String = match target {
    None -> Array.new(0)
    Some(name) -> Map.getOr(l.variants, name, Array.new(0))
}

-- `T` names the type alone; `T(..)` every constructor; `T(A, B)` those.
fun subsFrom(item : ExportItem, all : Array String) -> Array String = match item.subs {
    None -> Array.new(0)
    Some(subs) -> if isDotDot(subs) { all } else { subs }
}

fun isDotDot(subs : Array String) -> Bool =
    (len(subs) == 1 && subs[0] == "..")
    || (len(subs) == 2 && subs[0] == "." && subs[1] == ".")

-- -- exports ------------------------------------------------------------------------

fun exportsOf(l : Loader, m : Module) -> Scope {
    let mine = ownScope(m.name, m.program)
    match m.program.header {
        None -> mine
        Some(header) -> match header.exports {
            None -> mine
            Some(items) -> {
                let out = newScope()
                for item in items { exportOne(l, m, mine, item, out) }
                out
            }
        }
    }
}

fun exportOne(l : Loader, m : Module, mine : Scope, item : ExportItem,
              out : Scope) -> Unit {
    if item.kind == "module" {
        return reexport(m, item, out)
    }
    if isLower(item.name) {
        return Map.put(out.values, item.name,
                       exported(m, mine.values, m.scope.values, item))
    }
    -- A name the module *declares itself* is that declaration, whatever an
    -- import may have brought in under the same spelling. Without this,
    -- `Data.String`'s own `Index` type is invisible and its export list
    -- silently re-exports `Std.Classes`'s `Index` class instead -- the module's
    -- own declaration losing to an import, which is the opposite of the rule
    -- the scope applies everywhere else.
    if !Map.contains(mine.types, item.name)
       && (Map.contains(mine.classes, item.name)
           || Map.contains(m.scope.classes, item.name)) {
        exportClass(l, m, mine, item, out)
    } else {
        exportType(l, m, mine, item, out)
    }
}

fun exportClass(l : Loader, m : Module, mine : Scope, item : ExportItem,
                out : Scope) -> Unit {
    let target = exported(m, mine.classes, m.scope.classes, item)
    Map.put(out.classes, item.name, target)
    for member in subsFrom(item, membersOf(l, target)) {
        let value = firstOf(Map.get(mine.methods, member),
                            Map.get(m.scope.methods, member))
        let family = firstOf(Map.get(mine.types, member),
                             Map.get(m.scope.types, member))
        match value {
            Some(v) -> {
                Map.put(out.values, member, v)
                Map.put(out.methods, member, v)
            }
            None -> match family {
                Some(f) -> Map.put(out.types, member, f)
                None -> oopsAt("'" + member + "' is not a member of class '"
                               + item.name + "'", Some(item.span))
            }
        }
    }
}

fun exportType(l : Loader, m : Module, mine : Scope, item : ExportItem,
               out : Scope) -> Unit {
    let target = exported(m, mine.types, m.scope.types, item)
    Map.put(out.types, item.name, target)
    for con in subsFrom(item, variantsOf(l, Some(target))) {
        match firstOf(Map.get(mine.cons, con), Map.get(m.scope.cons, con)) {
            Some(t) -> Map.put(out.cons, con, t)
            None -> oopsAt("'" + con + "' is not a constructor of '"
                           + item.name + "'", Some(item.span))
        }
    }
}

-- `module M`: everything in scope here under that qualification, passed on
-- *still qualified*. That is what lets the Prelude hand every program
-- `Array.push` without claiming the bare `push`.
fun reexport(m : Module, item : ExportItem, out : Scope) -> Unit {
    let prefix = item.name + "."
    var found = False
    let into = spaces(out)
    let from = spaces(m.scope)
    for var i = 0; i < len(into); i = i + 1 {
        for (surface, target) in Map.entries(from[i]) {
            if String.startsWith(surface, prefix) {
                Map.put(into[i], surface, target)
                found = True
            }
        }
    }
    if !found {
        oopsAt("module '" + m.name + "' re-exports '" + item.name
               + "', which is not imported here", Some(item.span))
    }
}

fun exported(m : Module, own : Map String String, wider : Map String String,
             item : ExportItem) -> String =
    match firstOf(Map.get(own, item.name), Map.get(wider, item.name)) {
        Some(target) -> target
        None -> oopsAt("module '" + m.name + "' exports '" + item.name
                       + "', which is not defined or imported here",
                       Some(item.span))
    }

fun firstOf(a : Option v, b : Option v) -> Option v = match a {
    Some(_) -> a
    None -> b
}

fun isLower(name : String) -> Bool = match String.decode(name, String.start(name)) {
    Some(c) -> Char.isAsciiLower(c)
    None -> False
}

-- -- what a module declares ---------------------------------------------------------

-- What a module declares, in each of `Scope`'s five namespaces, before class
-- membership is taken into account.
fun ownDeclared(program : Program) -> Array (Array String) {
    let values = Array.new(8)
    let types = Array.new(4)
    let cons = Array.new(4)
    let classes = Array.new(2)
    let methods = Array.new(4)
    for decl in program.decls {
        match decl {
            DStmt(s) -> match s.kind {
                SFun(d) -> Array.push(values, d.name)
                SLet(pat, _) -> for v in Array.sort(patternVars(pat)) {
                    Array.push(values, v)
                }
                SVar(pat, _) -> for v in Array.sort(patternVars(pat)) {
                    Array.push(values, v)
                }
                _ -> {}
            }
            DType(t) -> {
                Array.push(types, t.name)
                match t.variants {
                    Some(vs) -> for v in vs { Array.push(cons, v.name) }
                    None -> {}
                }
            }
            DClass(c) -> {
                Array.push(classes, c.name)
                for meth in c.methods {
                    Array.push(values, meth.name)
                    Array.push(methods, meth.name)
                }
                for fam in c.families { Array.push(types, fam.name) }
            }
            DInstance(_) -> {}
        }
    }
    [values, types, cons, classes, methods]
}

-- The module's declarations with class members owned by their class.
--
-- A method is `M#C.method`, not `M#method`: an ordinary top-level `method` and
-- two classes that use the same member spelling are distinct bindings.
fun ownScope(name : String, program : Program) -> Scope {
    let scope = newScope()
    let into = spaces(scope)
    let declared = ownDeclared(program)
    for var i = 0; i < len(into); i = i + 1 {
        for short in declared[i] {
            Map.put(into[i], short, Prelude.internal(name, short))
        }
    }
    for decl in program.decls {
        match decl {
            DClass(c) -> {
                let cls = Prelude.internal(name, c.name)
                for meth in c.methods {
                    let target = cls + "." + meth.name
                    Map.put(scope.values, meth.name, target)
                    Map.put(scope.methods, meth.name, target)
                }
                for fam in c.families {
                    Map.put(scope.types, fam.name, cls + "." + fam.name)
                }
            }
            _ -> {}
        }
    }
    -- Ordinary top-level values shadow an explicitly called method, while the
    -- separate method namespace above keeps compiler-written operators stable.
    for decl in program.decls {
        match decl {
            DStmt(s) -> match s.kind {
                SFun(d) -> Map.put(scope.values, d.name,
                                   Prelude.internal(name, d.name))
                SLet(pat, _) -> for v in patternVars(pat) {
                    Map.put(scope.values, v, Prelude.internal(name, v))
                }
                SVar(pat, _) -> for v in patternVars(pat) {
                    Map.put(scope.values, v, Prelude.internal(name, v))
                }
                _ -> {}
            }
            _ -> {}
        }
    }
    scope
}

-- -- diagnostics --------------------------------------------------------------------

fun oops(message : String) -> a = oopsAt(message, None)

fun oopsAt(message : String, where : Option Span) -> a = match where {
    None -> Diag.fail(Diag.whole("type error", message), "")
    Some(s) -> Diag.fail(Diag.at("type error", message, s), s.file)
}

-- A block answers with its last statement, so a call made for its effect has to
-- say so. See boot/STYLE.md.
fun discard(x : a) -> Unit = {}
