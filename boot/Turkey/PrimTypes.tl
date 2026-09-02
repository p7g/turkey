module Turkey.PrimTypes (schemes)

-- The type of every machine operation (`turkey/builtins.py`'s `_PRIM`).
--
-- The environment every module is checked in. What a module may *write* is
-- settled by its scope one stage earlier, so this holds all of them for
-- everybody and `Turkey.Modules` is what says no.
--
-- Generated from the Python table rather than transcribed: eighty-five types
-- written out by hand is eighty-five chances to be wrong in a way only a deep
-- diff would catch. Regenerating is how the two stay in step, and adding a
-- primitive means adding it there and running the generator.

import Turkey.Types as Ty
import Turkey.Types (Kind(..), Scheme(..), TVar, TCon, TApp, TFun, TTuple)

fun schemes() -> Array (String, Scheme) {
    let out = Array.new(96)
    Array.push(out, ("Prim.print",
        Ty.mono(TFun([Ty.tString()], Ty.tUnit()))))
    Array.push(out, ("Prim.write",
        Ty.mono(TFun([Ty.tString()], Ty.tUnit()))))
    {
        let v0 = Ty.newVar(1, Ty.kvar())
        Array.push(out, ("Prim.error", Ty.scheme([v0],
            TFun([Ty.tString()], TVar(v0)), Array.new(0))))
    }
    Array.push(out, ("Prim.stderrWrite",
        Ty.mono(TFun([Ty.tString()], Ty.tUnit()))))
    {
        let v0 = Ty.newVar(1, Ty.kvar())
        Array.push(out, ("Prim.exit", Ty.scheme([v0],
            TFun([Ty.tInt()], TVar(v0)), Array.new(0))))
    }
    Array.push(out, ("Prim.args",
        Ty.mono(TFun([], TApp(Ty.tArray(), Ty.tString(), KStar)))))
    Array.push(out, ("Prim.fileCanRead",
        Ty.mono(TFun([Ty.tString()], Ty.tBool()))))
    Array.push(out, ("Prim.readFileBytes",
        Ty.mono(TFun([Ty.tString()], TApp(Ty.tArray(), Ty.tByte(), KStar)))))
    Array.push(out, ("Prim.writeFileBytes",
        Ty.mono(TFun([Ty.tString(), TApp(Ty.tArray(), Ty.tByte(), KStar)], Ty.tBool()))))
    {
        let v0 = Ty.newVar(1, Ty.kvar())
        Array.push(out, ("Prim.arrayNew", Ty.scheme([v0],
            TFun([Ty.tInt(), TVar(v0)], TApp(Ty.tRawArray(), TVar(v0), KStar)), Array.new(0))))
    }
    {
        let v0 = Ty.newVar(1, Ty.kvar())
        Array.push(out, ("Prim.arrayNewUninit", Ty.scheme([v0],
            TFun([Ty.tInt()], TApp(Ty.tRawArray(), TVar(v0), KStar)), Array.new(0))))
    }
    {
        let v0 = Ty.newVar(1, Ty.kvar())
        Array.push(out, ("Prim.arrayGet", Ty.scheme([v0],
            TFun([TApp(Ty.tRawArray(), TVar(v0), KStar), Ty.tInt()], TVar(v0)), Array.new(0))))
    }
    {
        let v0 = Ty.newVar(1, Ty.kvar())
        Array.push(out, ("Prim.arraySet", Ty.scheme([v0],
            TFun([TApp(Ty.tRawArray(), TVar(v0), KStar), Ty.tInt(), TVar(v0)], Ty.tUnit()), Array.new(0))))
    }
    {
        let v0 = Ty.newVar(1, Ty.kvar())
        Array.push(out, ("Prim.arrayLength", Ty.scheme([v0],
            TFun([TApp(Ty.tRawArray(), TVar(v0), KStar)], Ty.tInt()), Array.new(0))))
    }
    Array.push(out, ("Prim.stringConcat",
        Ty.mono(TFun([Ty.tString(), Ty.tString()], Ty.tString()))))
    Array.push(out, ("Prim.stringConcatAll",
        Ty.mono(TFun([TApp(Ty.tArray(), Ty.tString(), KStar)], Ty.tString()))))
    Array.push(out, ("Prim.stringByteLength",
        Ty.mono(TFun([Ty.tString()], Ty.tInt()))))
    Array.push(out, ("Prim.stringByteAt",
        Ty.mono(TFun([Ty.tString(), Ty.tInt()], Ty.tByte()))))
    Array.push(out, ("Prim.stringDecodeAt",
        Ty.mono(TFun([Ty.tString(), Ty.tInt()], Ty.tChar()))))
    Array.push(out, ("Prim.stringNextIndex",
        Ty.mono(TFun([Ty.tString(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.stringSlice",
        Ty.mono(TFun([Ty.tString(), Ty.tInt(), Ty.tInt()], Ty.tString()))))
    Array.push(out, ("Prim.stringFind",
        Ty.mono(TFun([Ty.tString(), Ty.tString(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.stringRfind",
        Ty.mono(TFun([Ty.tString(), Ty.tString()], Ty.tInt()))))
    Array.push(out, ("Prim.stringToBytes",
        Ty.mono(TFun([Ty.tString()], TApp(Ty.tArray(), Ty.tByte(), KStar)))))
    Array.push(out, ("Prim.stringFromBytes",
        Ty.mono(TFun([TApp(Ty.tArray(), Ty.tByte(), KStar)], Ty.tString()))))
    Array.push(out, ("Prim.stringIsValidUtf8",
        Ty.mono(TFun([TApp(Ty.tArray(), Ty.tByte(), KStar)], Ty.tBool()))))
    Array.push(out, ("Prim.charFromInt",
        Ty.mono(TFun([Ty.tInt()], Ty.tChar()))))
    Array.push(out, ("Prim.charIsScalar",
        Ty.mono(TFun([Ty.tInt()], Ty.tBool()))))
    Array.push(out, ("Prim.charToInt",
        Ty.mono(TFun([Ty.tChar()], Ty.tInt()))))
    Array.push(out, ("Prim.charToString",
        Ty.mono(TFun([Ty.tChar()], Ty.tString()))))
    Array.push(out, ("Prim.byteFromInt",
        Ty.mono(TFun([Ty.tInt()], Ty.tByte()))))
    Array.push(out, ("Prim.byteToInt",
        Ty.mono(TFun([Ty.tByte()], Ty.tInt()))))
    Array.push(out, ("Prim.byteEq",
        Ty.mono(TFun([Ty.tByte(), Ty.tByte()], Ty.tBool()))))
    Array.push(out, ("Prim.byteLt",
        Ty.mono(TFun([Ty.tByte(), Ty.tByte()], Ty.tBool()))))
    Array.push(out, ("Prim.intToString",
        Ty.mono(TFun([Ty.tInt()], Ty.tString()))))
    Array.push(out, ("Prim.intToFloat",
        Ty.mono(TFun([Ty.tInt()], Ty.tFloat()))))
    Array.push(out, ("Prim.floatToString",
        Ty.mono(TFun([Ty.tFloat()], Ty.tString()))))
    Array.push(out, ("Prim.floatParse",
        Ty.mono(TFun([Ty.tString()], Ty.tFloat()))))
    Array.push(out, ("Prim.floatCanParse",
        Ty.mono(TFun([Ty.tString()], Ty.tBool()))))
    Array.push(out, ("Prim.floatTruncate",
        Ty.mono(TFun([Ty.tFloat()], Ty.tInt()))))
    Array.push(out, ("Prim.floatFitsInt",
        Ty.mono(TFun([Ty.tFloat()], Ty.tBool()))))
    Array.push(out, ("Prim.intAdd",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intSub",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intMul",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intDiv",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intRem",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intNeg",
        Ty.mono(TFun([Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intAddWrapping",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intSubWrapping",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intMulWrapping",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intNegWrapping",
        Ty.mono(TFun([Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intAnd",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intOr",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intXor",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intNot",
        Ty.mono(TFun([Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intShl",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.intShr",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tInt()))))
    Array.push(out, ("Prim.not",
        Ty.mono(TFun([Ty.tBool()], Ty.tBool()))))
    Array.push(out, ("Prim.intEq",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tBool()))))
    Array.push(out, ("Prim.intLt",
        Ty.mono(TFun([Ty.tInt(), Ty.tInt()], Ty.tBool()))))
    Array.push(out, ("Prim.floatAdd",
        Ty.mono(TFun([Ty.tFloat(), Ty.tFloat()], Ty.tFloat()))))
    Array.push(out, ("Prim.floatSub",
        Ty.mono(TFun([Ty.tFloat(), Ty.tFloat()], Ty.tFloat()))))
    Array.push(out, ("Prim.floatMul",
        Ty.mono(TFun([Ty.tFloat(), Ty.tFloat()], Ty.tFloat()))))
    Array.push(out, ("Prim.floatDiv",
        Ty.mono(TFun([Ty.tFloat(), Ty.tFloat()], Ty.tFloat()))))
    Array.push(out, ("Prim.floatNeg",
        Ty.mono(TFun([Ty.tFloat()], Ty.tFloat()))))
    Array.push(out, ("Prim.floatFmod",
        Ty.mono(TFun([Ty.tFloat(), Ty.tFloat()], Ty.tFloat()))))
    Array.push(out, ("Prim.floatRemainder",
        Ty.mono(TFun([Ty.tFloat(), Ty.tFloat()], Ty.tFloat()))))
    Array.push(out, ("Prim.floatFloor",
        Ty.mono(TFun([Ty.tFloat()], Ty.tFloat()))))
    Array.push(out, ("Prim.floatCeil",
        Ty.mono(TFun([Ty.tFloat()], Ty.tFloat()))))
    Array.push(out, ("Prim.floatRound",
        Ty.mono(TFun([Ty.tFloat()], Ty.tFloat()))))
    Array.push(out, ("Prim.floatTrunc",
        Ty.mono(TFun([Ty.tFloat()], Ty.tFloat()))))
    Array.push(out, ("Prim.floatBits",
        Ty.mono(TFun([Ty.tFloat()], Ty.tInt()))))
    Array.push(out, ("Prim.floatFromBits",
        Ty.mono(TFun([Ty.tInt()], Ty.tFloat()))))
    Array.push(out, ("Prim.floatIsNaN",
        Ty.mono(TFun([Ty.tFloat()], Ty.tBool()))))
    Array.push(out, ("Prim.floatEq",
        Ty.mono(TFun([Ty.tFloat(), Ty.tFloat()], Ty.tBool()))))
    Array.push(out, ("Prim.floatLt",
        Ty.mono(TFun([Ty.tFloat(), Ty.tFloat()], Ty.tBool()))))
    Array.push(out, ("Prim.floatLte",
        Ty.mono(TFun([Ty.tFloat(), Ty.tFloat()], Ty.tBool()))))
    Array.push(out, ("Prim.floatGt",
        Ty.mono(TFun([Ty.tFloat(), Ty.tFloat()], Ty.tBool()))))
    Array.push(out, ("Prim.floatGte",
        Ty.mono(TFun([Ty.tFloat(), Ty.tFloat()], Ty.tBool()))))
    Array.push(out, ("Prim.stringEq",
        Ty.mono(TFun([Ty.tString(), Ty.tString()], Ty.tBool()))))
    Array.push(out, ("Prim.stringLt",
        Ty.mono(TFun([Ty.tString(), Ty.tString()], Ty.tBool()))))
    Array.push(out, ("Prim.charEq",
        Ty.mono(TFun([Ty.tChar(), Ty.tChar()], Ty.tBool()))))
    Array.push(out, ("Prim.charLt",
        Ty.mono(TFun([Ty.tChar(), Ty.tChar()], Ty.tBool()))))
    Array.push(out, ("Prim.boolEq",
        Ty.mono(TFun([Ty.tBool(), Ty.tBool()], Ty.tBool()))))
    Array.push(out, ("Prim.boolLt",
        Ty.mono(TFun([Ty.tBool(), Ty.tBool()], Ty.tBool()))))
    out
}
