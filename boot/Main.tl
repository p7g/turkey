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

-- Where the language's own library lives, relative to where this is run from.
-- The Python finds it beside its own source; a compiled binary has no such
-- thing to find, so the convention is the repository root and the tests run
-- from there.
let libRoot = "lib"

fun main() {
    let args = Env.args()
    if len(args) < 2 { usage() }
    let command = args[0]
    if !Array.contains(["tokens", "ast", "desugar"], command) {
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
            _ -> dumpDesugared(b, path)
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
    let loader = Modules.newLoader([directoryOf(path), libRoot])
    discard(Modules.loadEntry(loader, readOrDie(path), "Main"))
    for m in loader.order {
        Resolve.program(m.scope, m.program)
        String.push(b, "module " + m.name + "\n")
        String.push(b, AstDump.dump(m.program))
    }
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

fun usage() -> a = die("usage: boot <tokens|ast|desugar> FILE...")

fun die(message : String) -> a {
    IO.stderr("boot: " + message + "\n")
    Env.exit(2)
}

fun discard(x : a) -> Unit = {}
