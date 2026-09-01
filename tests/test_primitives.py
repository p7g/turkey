"""The primitive semantics of PRIMITIVES.md, held to directly.

These are deliberately written against the *statements* in that document
rather than against the current implementation, because the point of the
document is that the primitives no longer mean "whatever Python does". The
Python evaluator is also the differential oracle for the future native
backend, so every claim here is one both must satisfy.
"""

from __future__ import annotations

import contextlib
import io
import math

import pytest

from turkey.driver import run


def out(src: str) -> str:
    """Run a program and return everything it wrote to stdout."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        run(src)
    return buffer.getvalue()


# ---------------------------------------------------------------------- Int


def test_int_is_64_bit_and_arithmetic_traps():
    assert out("fun main() { print(Int.maxValue()) }") == "9223372036854775807\n"
    assert out("fun main() { print(Int.minValue()) }") == "-9223372036854775808\n"
    for expression in ("Int.maxValue() + 1", "Int.minValue() - 1",
                       "Int.maxValue() * 2", "-Int.minValue()"):
        with pytest.raises(Exception, match="integer overflow"):
            out(f"fun main() {{ print({expression}) }}")


def test_int_literal_out_of_range_is_a_compile_error():
    # 2^63 exactly: too big for Int, and past 2^53 so not a Float either.
    with pytest.raises(Exception, match="not representable in any numeric type"):
        out("fun main() { print(9223372036854775808) }")


def test_min_int_divided_by_minus_one_traps_and_its_remainder_does_not():
    with pytest.raises(Exception, match="integer overflow"):
        out("fun main() { print(Int.minValue() / -1) }")
    assert out("fun main() { print(Int.minValue() % -1) }") == "0\n"


def test_division_truncates_and_remainder_takes_the_dividends_sign():
    assert out("fun main() { print(-7 / 2) }") == "-3\n"
    assert out("fun main() { print(-7 % 3) }") == "-1\n"
    # a == (a / b) * b + a % b, for every sign combination.
    src = """
    fun main() {
        for a in [-7, 7, -8, 8, 0] {
            for b in [-3, 3, -2, 2] {
                if a != (a / b) * b + a % b { print("broken") }
            }
        }
        print("ok")
    }
    """
    assert out(src) == "ok\n"


def test_mod_is_floored_and_therefore_a_usable_index():
    assert out("fun main() { print(Int.mod(-7, 3)) }") == "2\n"
    assert out("fun main() { print(Int.mod(7, -3)) }") == "-2\n"


def test_wrapping_and_checked_forms():
    assert out("fun main() { print(Int.addWrapping(Int.maxValue(), 1)) }") == \
        "-9223372036854775808\n"
    assert out("fun main() { print(Int.addChecked(Int.maxValue(), 1)) }") == "None\n"
    assert out("fun main() { print(Int.addChecked(1, 2)) }") == "Some(3)\n"
    assert out("fun main() { print(Int.subChecked(Int.minValue(), 1)) }") == "None\n"
    assert out("fun main() { print(Int.mulChecked(3037000500, 3037000500)) }") == \
        "None\n"
    assert out("fun main() { print(Int.mulChecked(1000, 1000)) }") == "Some(1000000)\n"
    assert out("fun main() { print(Int.negChecked(Int.minValue())) }") == "None\n"
    # The two cases where the divide-back check would itself trap.
    assert out("fun main() { print(Int.mulChecked(-1, Int.minValue())) }") == "None\n"
    assert out("fun main() { print(Int.mulChecked(Int.minValue(), -1)) }") == "None\n"
    assert out("fun main() { print(Int.mulChecked(0, Int.minValue())) }") == "Some(0)\n"
    assert out("fun main() { print(Int.mulChecked(Int.minValue(), 1)) }") == \
        "Some(-9223372036854775808)\n"


def test_shifts_are_arithmetic_and_panic_out_of_range():
    assert out("fun main() { print(Int.shl(1, 10)) }") == "1024\n"
    assert out("fun main() { print(Int.shr(-8, 1)) }") == "-4\n"
    for by in (64, -1):
        with pytest.raises(Exception, match="shift amount"):
            out(f"fun main() {{ print(Int.shl(1, {by})) }}")


# --------------------------------------------------------------------- Byte


def test_byte_is_checked_going_in_and_total_coming_out():
    assert out("fun main() { print(Byte.fromInt(200)) }") == "Some(200)\n"
    assert out("fun main() { print(Byte.fromInt(256)) }") == "None\n"
    assert out("fun main() { print(Byte.fromInt(-1)) }") == "None\n"
    assert out("fun main() { print(Byte.truncate(300)) }") == "44\n"
    assert out("fun main() { print(Byte.maxValue()) }") == "255\n"


def test_byte_has_no_arithmetic_instances():
    # The whole point of Byte being a storage type: `+` does not resolve.
    with pytest.raises(Exception, match="[Aa]dd"):
        out("fun main() { print(Byte.maxValue() + Byte.minValue()) }")


# -------------------------------------------------------------------- Float


def test_division_by_zero_is_ieee_not_a_panic():
    assert out("fun main() { print(1.0 / 0.0) }") == "Infinity\n"
    assert out("fun main() { print(-1.0 / 0.0) }") == "-Infinity\n"
    assert out("fun main() { print(0.0 / 0.0) }") == "NaN\n"


def test_every_comparison_with_nan_is_false():
    # The bug this replaces: `gte` inherited `!lt`, so `NaN >= 1.0` was True
    # while `NaN <= 1.0` was False (PRIMITIVES.md 3.2a).
    src = """
    fun main() {
        let n = Float.nan()
        print(n == n)
        print(n != n)
        print(n < 1.0)
        print(n <= 1.0)
        print(n > 1.0)
        print(n >= 1.0)
        print(n >= n)
    }
    """
    assert out(src) == "False\nTrue\nFalse\nFalse\nFalse\nFalse\nFalse\n"


def test_signed_zero_compares_equal_but_has_a_distinct_bit_pattern():
    assert out("fun main() { print(0.0 == -0.0) }") == "True\n"
    assert out("fun main() { print(Float.bits(0.0) == Float.bits(-0.0)) }") == \
        "False\n"


def test_total_compare_is_ieee_total_order():
    src = """
    fun main() {
        print(Float.totalCompare(-0.0, 0.0))
        print(Float.totalCompare(Float.nan(), Float.infinity()))
        print(Float.totalCompare(-Float.nan(), -Float.infinity()))
        print(Float.totalCompare(1.0, 1.0))
        print(Float.totalCompare(Float.nan(), Float.nan()))
    }
    """
    assert out(src) == "LT\nGT\nLT\nEQ\nEQ\n"


def test_show_float_round_trips_and_names_the_specials():
    assert out("fun main() { print(1.5) }") == "1.5\n"
    # A whole-valued Float still shows a `.0`, so it re-lexes as a Float.
    assert out("fun main() { print(1.0 * 1.0) }") == "1.0\n"
    assert out("fun main() { print(-0.0) }") == "-0.0\n"
    assert out("fun main() { print(1.0e16) }") == "1.0e+16\n"
    assert out("fun main() { print(Float.parse(\"1.0e+16\")) }") == "Some(1.0e+16)\n"
    assert out("fun main() { print(Float.parse(\"Infinity\")) }") == \
        "Some(Infinity)\n"
    assert out("fun main() { print(Float.parse(\"nan\")) }") == "None\n"
    assert out("fun main() { print(Float.parse(\"oops\")) }") == "None\n"


def test_float_to_int_is_optional_rather_than_undefined():
    assert out("fun main() { print(Float.truncate(1.9)) }") == "Some(1)\n"
    assert out("fun main() { print(Float.truncate(-1.9)) }") == "Some(-1)\n"
    assert out("fun main() { print(Float.truncate(Float.nan())) }") == "None\n"
    assert out("fun main() { print(Float.truncate(Float.infinity())) }") == "None\n"
    assert out("fun main() { print(Float.truncate(1.0e300)) }") == "None\n"


def test_there_is_no_hash_float():
    # `Hash a : Eq a` and `Eq Float` is not reflexive, so a NaN key could be
    # inserted and never found again. Refusing the instance makes it a type
    # error instead (PRIMITIVES.md 3.2c).
    src = """
    fun main() {
        let m = Map.new()
        Map.put(m, 1.0, "one")
    }
    """
    with pytest.raises(Exception, match="Hash Float"):
        out(src)


# ------------------------------------------------------------------- String


def test_length_is_gone_and_the_two_counts_are_named():
    assert out('fun main() { print(String.byteLength("héllo")) }') == "6\n"
    assert out('fun main() { print(String.codePointCount("héllo")) }') == "5\n"
    assert out('fun main() { print(String.byteLength("\\u{1F600}")) }') == "4\n"
    assert out('fun main() { print(String.codePointCount("\\u{1F600}")) }') == "1\n"
    # No `Length String`: `len` has no single answer to give.
    with pytest.raises(Exception, match="Length"):
        out('fun main() { print(len("abc")) }')


def test_equality_does_not_normalize():
    # Both render as e-acute; one is precomposed and one is decomposed.
    assert out('fun main() { print("\\u{00E9}" == "\\u{0065}\\u{0301}") }') == \
        "False\n"


def test_ordering_is_byte_lexicographic_and_not_a_collation():
    assert out('fun main() { print("Z" < "a") }') == "True\n"
    # Byte order and code-point order agree for well-formed UTF-8.
    assert out('fun main() { print("é" > "z") }') == "True\n"


def test_views_are_lazy_and_iterate_the_encoding():
    src = """
    fun main() {
        for b in String.bytes("hé") { print(b) }
        for c in String.codePoints("hé") { print(c) }
    }
    """
    assert out(src) == "104\n195\n169\nh\né\n"


def test_bytes_are_the_only_door_and_the_way_in_is_checked():
    assert out('fun main() { print(String.toBytes("hé")) }') == "[104, 195, 169]\n"
    assert out('fun main() { print(String.fromBytes(String.toBytes("hé"))) }') == \
        "Some(hé)\n"
    # 0xC3 alone is a truncated two-byte sequence.
    src = """
    fun main() {
        let bs = Array.filled(1, Option.unwrapOr(Byte.fromInt(195), Byte.minValue()))
        print(String.fromBytes(bs))
    }
    """
    assert out(src) == "None\n"


def test_search_and_split():
    assert out('fun main() { print(String.split("a,b,c", ",")) }') == "[a, b, c]\n"
    assert out('fun main() { print(String.split("abc", "")) }') == "[a, b, c]\n"
    assert out('fun main() { print(String.splitOnce("a=b=c", "=")) }') == \
        "Some((a, b=c))\n"
    assert out('fun main() { print(String.stripPrefix("foobar", "foo")) }') == \
        "Some(bar)\n"
    assert out('fun main() { print(String.stripSuffix("foobar", "baz")) }') == \
        "None\n"
    assert out('fun main() { print(String.replace("a-b-c", "-", "+")) }') == \
        "a+b+c\n"
    assert out('fun main() { print(String.trim("  hi\\t") + "|") }') == "hi|\n"
    assert out('fun main() { print(String.contains("héllo", "é")) }') == "True\n"
    # A needle whose byte length would cut a multi-byte character in half. The
    # answer is False, not a panic and not a mangled comparison.
    assert out('fun main() { print(String.startsWith("é", "a")) }') == "False\n"
    assert out('fun main() { print(String.endsWith("é", "a")) }') == "False\n"
    assert out('fun main() { print(String.startsWith("héllo", "hé")) }') == "True\n"
    assert out('fun main() { print(String.endsWith("hé", "é")) }') == "True\n"
    assert out('fun main() { print(String.endsWith("abc", "")) }') == "True\n"
    # UTF-8 is self-synchronizing, so a search never matches mid-character:
    # 0xA9 is the trailing byte of "é" and appears in no well-formed needle.
    assert out('fun main() { print(String.contains("é", "\\u{00A9}")) }') == "False\n"
    assert out('fun main() { print(String.repeat("ab", 3)) }') == "ababab\n"


def test_builder_joins_once():
    src = """
    fun main() {
        let b = String.builder()
        String.push(b, "a")
        String.push(b, "é")
        String.push(b, "c")
        print(String.build(b))
    }
    """
    assert out(src) == "aéc\n"


# --------------------------------------------------------------------- Char


def test_char_is_a_scalar_value_so_surrogates_are_rejected():
    assert out("fun main() { print(Char.fromInt(97)) }") == "Some(a)\n"
    assert out("fun main() { print(Char.fromInt(55296)) }") == "None\n"  # D800
    assert out("fun main() { print(Char.fromInt(57343)) }") == "None\n"  # DFFF
    assert out("fun main() { print(Char.fromInt(1114112)) }") == "None\n"  # 10FFFF+1
    assert out("fun main() { print(Char.fromInt(1114111)) }") == "Some(\U0010ffff)\n"


def test_lexer_rejects_surrogate_and_oversized_escapes():
    for escape in ("\\u{D800}", "\\u{DFFF}", "\\u{110000}"):
        with pytest.raises(Exception, match="not a Unicode scalar value"):
            out(f'fun main() {{ print("{escape}") }}')


def test_unicode_escape_reaches_beyond_the_bmp():
    assert out('fun main() { print("\\u{1F600}") }') == "\U0001f600\n"
    assert out('fun main() { print("\\u{41}") }') == "A\n"
    with pytest.raises(Exception, match=r"\\u escape"):
        out('fun main() { print("\\u0041") }')


# --------------------------------------------------------------- consistency


def test_python_is_not_the_specification():
    """The four places the host would have answered differently."""
    # 1. Unbounded integers.
    with pytest.raises(Exception, match="integer overflow"):
        out("fun main() { print(Int.maxValue() + Int.maxValue()) }")
    # 2. ZeroDivisionError on float division.
    assert out("fun main() { print(1.0 / 0.0) }") == "Infinity\n"
    # 3. `repr` spellings for the specials.
    assert out("fun main() { print(0.0 / 0.0) }") == "NaN\n"
    assert "inf" not in out("fun main() { print(1.0 / 0.0) }")
    # 4. Code-point-indexed strings.
    assert out('fun main() { print(String.byteLength("é")) }') == "2\n"


def test_math_module_agreement_is_not_assumed():
    """`Float.round` is ties-away, not Python's ties-to-even."""
    assert out("fun main() { print(Float.round(0.5)) }") == "1.0\n"
    assert out("fun main() { print(Float.round(1.5)) }") == "2.0\n"
    assert out("fun main() { print(Float.round(2.5)) }") == "3.0\n"
    assert round(2.5) == 2  # what Python would have said
    assert math.floor(-1.5) == -2
    assert out("fun main() { print(Float.floor(-1.5)) }") == "-2.0\n"
