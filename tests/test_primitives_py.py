"""The Python evaluator's simulated raw heap.

Internal: split from `test_primitives.py`, whose behavioral tests run through
`boot`. This goes with the Python compiler (TIX-96).
"""

from __future__ import annotations


import pytest


# ---------------------------------------------------------------------- Int


# --------------------------------------------------------------------- Byte


# -------------------------------------------------------------------- Float


# ------------------------------------------------------------------- String


# --------------------------------------------------------------------- Char


# --------------------------------------------------------------- consistency


def test_raw_memory_is_poisoned_and_never_reused():
    """The simulated address space stands in for `malloc`, and the one thing
    it must not do is be *nicer* than malloc.

    This side is the oracle `tests/test_native.py` diffs each compiled binary
    against, so a convenience here becomes a guarantee the differential then
    enforces. Fresh bytes are poison rather than zero, so a read-before-write
    differs between the two hosts instead of being blessed; a freed block is
    poisoned again and its address never comes back.
    """
    from turkey.values import RAW_HEAP

    RAW_HEAP.reset()
    first = RAW_HEAP.allocate(16)
    assert first != 0, "null is never a live address"
    assert first % RAW_HEAP.ALIGN == 0, "malloc's alignment, so both hosts agree"
    assert RAW_HEAP.load(first, 8) == 0xA5A5A5A5A5A5A5A5

    RAW_HEAP.store(first, 8, -5, signed=True)
    assert RAW_HEAP.load(first, 8, signed=True) == -5

    second = RAW_HEAP.allocate(16)
    assert second != first

    RAW_HEAP.free(first)
    assert RAW_HEAP.load(first, 8) == 0xDEDEDEDEDEDEDEDE
    assert RAW_HEAP.allocate(16) not in (first, second), "an address is not reused"


def test_raw_memory_checks_are_a_debugging_aid():
    """Where the native backend is undefined, this side is loud.

    Documented as an aid and not a semantics: the native backend performs none
    of these checks, and a program that trips one is undefined either way. The
    point is that it cannot quietly return a defined answer.
    """
    from turkey.errors import TurkeyPanic
    from turkey.values import RAW_HEAP

    RAW_HEAP.reset()
    block = RAW_HEAP.allocate(8)
    # Leaving the address space is caught. Leaving the *block* while staying
    # inside the address space is not, and deliberately: that is exactly what
    # malloc does not catch either, and a check here would be a guarantee the
    # native side cannot make.
    with pytest.raises(TurkeyPanic, match="raw pointer:"):
        RAW_HEAP.load(block + (1 << 20), 8)
    with pytest.raises(TurkeyPanic, match="raw pointer:"):
        RAW_HEAP.load(0, 8)
    RAW_HEAP.free(block)
    with pytest.raises(TurkeyPanic, match="not a live block"):
        RAW_HEAP.free(block)
    RAW_HEAP.free(0)  # freeing null does nothing
