module System.IO (readFile, writeFile, stderr)

-- Files, addressed as bytes.
--
-- Reading answers `Option String` rather than `String` because a file is not
-- guaranteed to be well-formed UTF-8 and a `String` is. The validation is
-- `String.fromBytes` -- the checked constructor PRIMITIVES.md 4.7 calls the
-- only door in -- rather than something hidden inside the read, so a program
-- that wants the bytes themselves is not made to go through text to get them.

import Prelude ()
import Std.Classes
import Data.Bool.Type (Bool(..))
import Data.Option.Type (Option(..))
import Data.String as String

fun readFile(path : String) -> Option String {
    if Prim.fileCanRead(path) {
        String.fromBytes(Prim.readFileBytes(path))
    } else {
        None
    }
}

fun writeFile(path : String, contents : String) -> Bool =
    Prim.writeFileBytes(path, String.toBytes(contents))

fun stderr(text : String) -> Unit = Prim.stderrWrite(text)
