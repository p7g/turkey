module System.Env (args, exit)

-- The process's own environment: what it was asked to do, and how it answers.
--
-- `main` keeps the type it has always had, `fun() -> Unit` (turkey/driver.py).
-- A program that wants a non-zero status calls `exit`, which diverges, so the
-- entry point's shape does not have to move in order to carry one.

import Prelude ()
import Std.Classes
import Data.Array (Array)

fun args() -> Array String = Prim.args()

fun exit(status : Int) -> a = Prim.exit(status)
