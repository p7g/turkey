module Turkey.Diag (Diag(..), at, whole, render, fail, short)

-- A diagnostic is a value.
--
-- The Python implementation raises `TurkeyError`, which the CLI catches and
-- renders. Turkey has no exceptions, so a diagnostic here is an ordinary record
-- and a stage that cannot continue answers with one. `fail` is the exception to
-- that and is deliberately the only one: it prints and exits, which is what the
-- driver would have done with a caught error anyway, and it keeps the lexer
-- from threading a result type through every character.
--
-- The rendering matches `turkey/errors.py` exactly -- `file:line:col: stage:
-- message` -- because two implementations that disagree about how an error
-- looks cannot be diffed against each other.

import System.Env as Env
import System.IO as IO
import Turkey.Source (Span, showSpan)

type Diag = Diag {
    stage   : String,
    message : String,
    span    : Option Span,
}

fun at(stage : String, message : String, where : Span) -> Diag =
    Diag { stage = stage, message = message, span = Some(where) }

-- A diagnostic about a whole file rather than a place in it: a file that cannot
-- be read has no line to point at.
fun whole(stage : String, message : String) -> Diag =
    Diag { stage = stage, message = message, span = None }

fun render(d : Diag, filename : String) -> String = match d.span {
    None -> filename + ": " + d.stage + ": " + d.message
    Some(where) ->
        where.file + ":" + showSpan(where) + ": " + d.stage + ": " + d.message
}

-- Diverges, so it may be used where a value is expected.
fun fail(d : Diag, filename : String) -> a {
    IO.stderr(render(d, filename) + "\n")
    Env.exit(1)
}

-- A message with every internal name written the way the author wrote it.
--
-- `Module#name` is what module resolution calls a top-level binding. No
-- diagnostic should say it: the author wrote `f`, not `Main#f`, and which
-- module a name came from is not news to them. Python strips it with one
-- regular expression, `[A-Za-z_][A-Za-z0-9_.]*#`; this is that expression as a
-- scan, which is what there is without a regex engine.
fun short(text : String) -> String {
    let out = String.builder()
    -- The run of characters that could be the prefix of an internal name, held
    -- back until the next one says whether a `#` follows.
    let pending = Array.new(16)
    for c in String.codePoints(text) {
        if c == '#' && len(pending) > 0 {
            -- The run was a module qualification. Drop it and the `#` with it.
            Array.clear(pending)
        } else if len(pending) > 0 && isNameRest(c) {
            Array.push(pending, c)
        } else {
            flush(out, pending)
            if isNameStart(c) { Array.push(pending, c) }
            else { String.push(out, Char.toString(c)) }
        }
    }
    flush(out, pending)
    String.build(out)
}

fun flush(out : String.Builder, pending : Array Char) -> Unit {
    if len(pending) == 0 { return {} }
    String.push(out, String.fromCodePoints(pending))
    Array.clear(pending)
}

fun isNameStart(c : Char) -> Bool = Char.isAsciiAlpha(c) || c == '_'

fun isNameRest(c : Char) -> Bool =
    Char.isAsciiAlphanumeric(c) || c == '_' || c == '.'

fun discard(x : a) -> Unit = {}
