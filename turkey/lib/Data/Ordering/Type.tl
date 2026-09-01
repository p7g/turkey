module Data.Ordering.Type (Ordering(..))

import Prelude ()

-- Declared apart from its instances for the same reason `Bool` and `Option`
-- are: `Data.Int` returns an `Ordering` from `compare`, and `Data.Ordering`'s
-- own instances are written in terms of `Int`. Splitting the declaration out
-- is what keeps that from being a cycle.
type Ordering = LT | EQ | GT
