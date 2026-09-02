-- The bootstrap compiler (plan.txt item 9).
--
-- Run under the Python implementation for now:
--
--     python3 -m turkey run boot/Main.tl -- tokens FILE...
--     python3 -m turkey run boot/Main.tl -- ast FILE...
--
-- and, once it emits C and compiles itself, as a native binary taking the same
-- arguments. Element zero of `args` is the first real argument under both
-- hosts, which is why the two agree about a command line at all.
--
-- One subcommand per stage, each named after the `turkey` subcommand it is
-- diffed against. `tokens` is M19; the rest arrive with their milestones.
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

fun main() {
    let args = Env.args()
    if len(args) < 2 { usage() }
    let command = args[0]
    if command != "tokens" && command != "ast" {
        die("unknown command '" + command + "'")
    }
    -- One builder for every file: a `print` per token is a flush per token,
    -- and the library is a hundred thousand of them.
    let b = String.builder()
    for var i = 1; i < len(args); i = i + 1 {
        let source = readOrDie(args[i])
        if command == "tokens" {
            dumpTokens(b, source)
        } else {
            String.push(b, AstDump.dump(Parser.parse(source, Array.new(0))))
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

fun readOrDie(path : String) -> Source = match Source.read(path) {
    Some(source) -> source
    None -> die("cannot read " + path)
}

fun usage() -> a = die("usage: boot <tokens|ast> FILE...")

fun die(message : String) -> a {
    IO.stderr("boot: " + message + "\n")
    Env.exit(2)
}
