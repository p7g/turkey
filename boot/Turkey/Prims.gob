module Turkey.Prims (names)

-- The machine operations, by name.
--
-- `turkey/builtins.py` holds these as the keys of the table that also holds
-- their types and their implementations. Here only the names are needed, and
-- only for one purpose: `Turkey.Modules` puts them in a *library* module's
-- scope and in no other module's, which is the stage that says a user program
-- may not write `Prim.intAdd`. What one *means* is the Python's business until
-- the C runtime arrives.
--
-- Sorted, so that the two lists can be compared by eye. Adding a primitive
-- means adding it in both places; nothing checks that, which is the cost of
-- bootstrapping and is paid once per primitive.

let names = [
    "Prim.args", "Prim.arrayGet", "Prim.arrayLength",
    "Prim.arrayNew", "Prim.arrayNewUninit", "Prim.arraySet",
    "Prim.boolEq", "Prim.boolLt", "Prim.byteEq",
    "Prim.byteFromInt", "Prim.byteLt", "Prim.byteToInt",
    "Prim.charEq", "Prim.charFromInt", "Prim.charIsScalar",
    "Prim.charLt", "Prim.charToInt", "Prim.charToString",
    "Prim.error", "Prim.exit", "Prim.fileCanRead",
    "Prim.floatAdd", "Prim.floatBits", "Prim.floatCanParse",
    "Prim.floatCeil", "Prim.floatDiv", "Prim.floatEq",
    "Prim.floatFitsInt", "Prim.floatFloor", "Prim.floatFmod",
    "Prim.floatFromBits", "Prim.floatGt", "Prim.floatGte",
    "Prim.floatIsNaN", "Prim.floatLt", "Prim.floatLte",
    "Prim.floatMul", "Prim.floatNeg", "Prim.floatParse",
    "Prim.floatRemainder", "Prim.floatRound", "Prim.floatSub",
    "Prim.floatToString", "Prim.floatTrunc", "Prim.floatTruncate",
    "Prim.intAdd", "Prim.intAddWrapping", "Prim.intAnd",
    "Prim.intDiv", "Prim.intEq", "Prim.intLt",
    "Prim.intMul", "Prim.intMulWrapping", "Prim.intNeg",
    "Prim.intNegWrapping", "Prim.intNot", "Prim.intOr",
    "Prim.intRem", "Prim.intShl", "Prim.intShr",
    "Prim.intSub", "Prim.intSubWrapping", "Prim.intToFloat",
    "Prim.intToString", "Prim.intXor", "Prim.not",
    "Prim.print", "Prim.readFileBytes", "Prim.stderrWrite",
    "Prim.stringByteAt", "Prim.stringByteLength", "Prim.stringConcat",
    "Prim.stringConcatAll", "Prim.stringDecodeAt", "Prim.stringEq",
    "Prim.stringFind", "Prim.stringFromBytes", "Prim.stringIsValidUtf8",
    "Prim.stringLt", "Prim.stringNextIndex", "Prim.stringRfind",
    "Prim.stringSlice", "Prim.stringToBytes", "Prim.write",
    "Prim.writeFileBytes",
]
