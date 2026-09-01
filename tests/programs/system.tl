-- The floor a program needs to be a compiler: its arguments, and a file it did
-- not have compiled into it (M18a).
--
-- Run with no arguments by the golden runner, from `tests/programs`, so the
-- file it reads is its own source under its bare name. `System.IO` and
-- `System.Env` under argument passing and a chosen exit status are
-- `tests/test_system.py`, which can hand a program an argv and a temporary
-- directory; a golden file can do neither.

import System.Env as Env
import System.IO as IO
import Data.String as S

fun main() {
    print("args " + Int.toString(len(Env.args())))

    -- Reading answers `Option String` because a file is not guaranteed to be
    -- well-formed UTF-8 and a `String` is.
    match IO.readFile("system.tl") {
        Some(text) -> {
            let ls = S.lines(text)
            print("first " + ls[0])
            print("nonempty " + Bool.toString(len(ls) > 10))
        }
        None -> print("could not read own source")
    }

    match IO.readFile("no/such/file.tl") {
        Some(_) -> print("read a file that is not there")
        None -> print("missing is None")
    }

    IO.stderr("a line on stderr\n")
}
