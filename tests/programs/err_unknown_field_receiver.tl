type Cell = Cell { n : Int }

-- Without an annotation there is nothing to resolve `.n` against
-- (SPEC-DELTAS.md entry 7).
fun get(r) = r.n
