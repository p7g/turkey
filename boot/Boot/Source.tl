module Boot.Source (Span, Span(..), span, showSpan, Source, Source(..), read)

-- Where a thing is, and what it is in.
--
-- The bottom of the bootstrap compiler's module graph (plan.txt item 9). Turkey
-- has no mutually recursive modules, so every layer above this one may mention
-- a `Span` and nothing here may mention a token, a tree or a type.

import Prelude ()
import Std.Classes
import Data.Bool.Type (Bool(..))
import Data.Int as Int
import Data.Option.Type (Option(..))
import Data.String as String
import System.IO as IO

-- Lines and columns are 1-based, counted in *code points* rather than bytes,
-- which is what `turkey/errors.py` counts and therefore what a diagnostic has
-- to agree with.
type Span = Span {
    line : Int,
    col  : Int,
    file : String,
}

fun span(file : String, line : Int, col : Int) -> Span =
    Span { line = line, col = col, file = file }

-- `line:col`, with no file. The file is the caller's to print, because a
-- diagnostic prints it once and a token dump never prints it at all.
fun showSpan(s : Span) -> String =
    Int.toString(s.line) + ":" + Int.toString(s.col)

type Source = Source {
    name : String,
    text : String,
}

-- `None` when the file cannot be read *or* is not well-formed UTF-8. The two
-- are not distinguished here because nothing above needs to: either way there
-- is no source to compile, and the message says which file.
fun read(path : String) -> Option Source {
    match IO.readFile(path) {
        None -> None
        Some(text) -> Some(Source { name = path, text = text })
    }
}
