-- The bootstrap compiler (plan.txt item 9).
--
-- Run under the Python implementation for now:
--
--     python3 -m turkey run boot/Main.tl -- tokens FILE...
--     python3 -m turkey run boot/Main.tl -- ast FILE...
--     python3 -m turkey run boot/Main.tl -- desugar FILE...
--
-- and, once it emits C and compiles itself, as a native binary taking the same
-- arguments. Element zero of `args` is the first real argument under both
-- hosts, which is why the two agree about a command line at all.
--
-- One subcommand per stage, each named after the `turkey` subcommand it is
-- diffed against. `tokens` is M19, `ast` M20, `desugar` M21; the rest arrive
-- with their milestones.
--
-- A subcommand takes any number of files and answers for each in turn, with no
-- separator between them: every dump ends with its own EOF, so the
-- concatenation is unambiguous. That is not a convenience for the test -- it is
-- what keeps the milestone's diff to one process, since starting this program
-- currently means compiling it.

import Data.Set as Set
import System.Env as Env
import System.IO as IO
import Turkey.Source as Source
import Turkey.Source (Source)
import Turkey.Lexer as Lexer
import Turkey.Token as Token
import Turkey.Parser as Parser
import Turkey.AstDump as AstDump
import Turkey.Modules as Modules
import Turkey.Resolve as Resolve
import Turkey.Desugar as Desugar
import Turkey.Types as Ty
import Turkey.Decls as Decls
import Turkey.Deps as Deps
import Turkey.Classes as Classes
import Turkey.Solved as Solved
import Turkey.Constraints as Constraints
import Turkey.Typed as Typed
import Turkey.Exhaustive as Exhaustive
import Turkey.PrimTypes as PrimTypes
import Turkey.Ast (Decl(..), Stmt(..), StmtKind(..), patternVars)

-- Where the language's own library lives, relative to where this is run from.
-- The Python finds it beside its own source; a compiled binary has no such
-- thing to find, so the convention is the repository root and the tests run
-- from there.
let libRoot = "lib"

fun main() {
    let args = Env.args()
    if len(args) < 2 { usage() }
    let command = args[0]
    if !Array.contains(["tokens", "ast", "desugar", "decls", "deps",
                    "classes"], command) {
        die("unknown command '" + command + "'")
    }
    -- One builder for every file: a `print` per token is a flush per token,
    -- and the library is a hundred thousand of them.
    let b = String.builder()
    for var i = 1; i < len(args); i = i + 1 {
        let path = args[i]
        match command {
            "tokens" -> dumpTokens(b, readOrDie(path))
            "ast" -> String.push(b, AstDump.dump(
                Parser.parse(readOrDie(path), Array.new(0))))
            "desugar" -> dumpDesugared(b, path)
            "decls" -> dumpDecls(b, path)
            "deps" -> dumpDeps(b, path)
            _ -> dumpClasses(b, path)
        }
    }
    write(String.build(b))
}

fun dumpTokens(b : String.Builder, source : Source) -> Unit {
    for t in Lexer.tokenize(source) {
        String.push(b, Token.canonical(t))
        String.push(b, "\n")
    }
}

-- Every module of the program, resolved and desugared -- not just the entry
-- one. What those two passes do to a module depends on what it imported, so a
-- dump of the entry alone would check the interesting half of the stage against
-- nothing.
fun dumpDesugared(b : String.Builder, path : String) -> Unit {
    for m in loadResolved(path) {
        String.push(b, "module " + m.name + "\n")
        String.push(b, AstDump.dump(m.program))
    }
}

-- Every module of the program, loaded, resolved and desugared, in dependency
-- order. What every stage from here on starts from.
fun loadResolved(path : String) -> Array Modules.Module {
    let loader = Modules.newLoader([directoryOf(path), libRoot])
    discard(Modules.loadEntry(loader, readOrDie(path), "Main"))
    for m in loader.order {
        Resolve.program(m.scope, m.program)
        Desugar.program(m.program, m.scope.methods)
    }
    loader.order
}

-- The program's declaration table, and nothing after it: every type constructor
-- with the kind inferred for it, every value constructor with the scheme it was
-- generalized to. One table for the whole program, which is what makes the
-- two-modules-claim-one-short-name rule visible at all.
fun dumpDecls(b : String.Builder, path : String) -> Unit {
    let (table, _) = registerTables(path, False)
    String.push(b, Decls.showDeclarations(table))
}

-- Everything before the first constraint is generated: types, constructors,
-- classes, their methods' schemes, their families, and the instance table. One
-- pair of tables for the whole program.
fun dumpClasses(b : String.Builder, path : String) -> Unit {
    let (_, classes) = registerTables(path, True)
    String.push(b, Classes.showClasses(classes))
}

fun registerTables(path : String,
                   withClasses : Bool) -> (Decls.DeclTable, Classes.ClassTable) {
    let table = Decls.newDeclTable()
    let classes = Classes.newClassTable(table)
    for m in loadResolved(path) {
        let types = Array.new(8)
        let classDecls = Array.new(4)
        let instDecls = Array.new(4)
        for d in m.program.decls {
            match d {
                DType(td) -> Array.push(types, td)
                DClass(cd) -> Array.push(classDecls, cd)
                DInstance(idl) -> Array.push(instDecls, idl)
                _ -> {}
            }
        }
        Decls.registerAll(table, types)
        if withClasses {
            Classes.registerAll(classes, classDecls, instDecls, m.name)
        }
    }
    (table, classes)
}

-- Each module's top-level binding groups, in the order they are checked.
--
-- The graph is keyed by *item*, not by bound name: one binding may introduce
-- several names, and keying by name would split an item across two components
-- and infer -- and evaluate -- its right-hand side twice.
fun dumpDeps(b : String.Builder, path : String) -> Unit {
    for m in loadResolved(path) {
        String.push(b, "module " + m.name + "\n")
        let items = topLevelItems(m.program.decls)
        let namesOf = Map.new()
        let owner = Map.new()
        for var i = 0; i < len(items); i = i + 1 {
            let key = "item" + Int.toString(i)
            let names = itemNames(items[i])
            Map.put(namesOf, key, names)
            for n in names {
                if !Map.contains(owner, n) { Map.put(owner, n, key) }
            }
        }
        let graph = Map.new()
        for var i = 0; i < len(items); i = i + 1 {
            let edges = Set.new()
            for n in Set.elements(itemDeps(items[i])) {
                match Map.get(owner, n) {
                    Some(key) -> Set.add(edges, key)
                    None -> {}
                }
            }
            Map.put(graph, "item" + Int.toString(i), edges)
        }
        for component in Deps.sccs(graph) {
            let bound = Array.new(4)
            for key in component {
                for n in Map.getOr(namesOf, key, Array.new(0)) {
                    Array.push(bound, n)
                }
            }
            String.push(b, "  group " + String.join(component, " ") + " : "
                           + String.join(Array.sort(bound), ", ") + "\n")
        }
    }
}

fun topLevelItems(decls : Array Decl) -> Array Stmt {
    let out = Array.new(8)
    for d in decls {
        match d {
            DStmt(s) -> Array.push(out, s)
            _ -> {}
        }
    }
    out
}

fun itemNames(item : Stmt) -> Array String = match item.kind {
    SFun(d) -> [d.name]
    SLet(pat, _) -> Array.sort(patternVars(pat))
    SVar(pat, _) -> Array.sort(patternVars(pat))
    _ -> Array.new(0)
}

fun itemDeps(item : Stmt) -> Set.Set String = match item.kind {
    SFun(d) -> Deps.freeNamesOfFun(d, Set.new())
    SLet(_, value) -> Deps.freeNames(value, Set.new())
    SVar(_, value) -> Deps.freeNames(value, Set.new())
    _ -> Set.new()
}

-- The directory a path is in, as a search root. A path with no separator is in
-- the current directory, which is what the empty prefix would mean anyway --
-- but `"" + "/" + rest` is a leading slash, so it is spelled out.
fun directoryOf(path : String) -> String {
    let parts = String.split(path, "/")
    if len(parts) < 2 { return "." }
    String.join(Array.slice(parts, 0, len(parts) - 1), "/")
}

fun readOrDie(path : String) -> Source = match Source.read(path) {
    Some(source) -> source
    None -> die("cannot read " + path)
}

fun usage() -> a =
    die("usage: boot <tokens|ast|desugar|decls|deps|classes> FILE...")

fun die(message : String) -> a {
    IO.stderr("boot: " + message + "\n")
    Env.exit(2)
}

fun discard(x : a) -> Unit = {}
