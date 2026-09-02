module Turkey.Diag (Diag(..), at, whole, render, fail)

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
