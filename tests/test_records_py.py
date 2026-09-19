"""The Python implementation's primitive arrays (split from `test_records`).

Internal: exercises `turkey.values` directly, and goes with the Python
compiler (TIX-96). What it pins that a program can see -- a fixed-length
array panics past its end -- is `err_out_of_bounds.gob`'s golden.
"""

from __future__ import annotations

import pytest

from turkey.builtins import PRIM_NAMES
from turkey.errors import TurkeyPanic
from turkey.values import ArrayObj, UNINIT


def test_primitive_arrays_are_fixed_length_storage():
    filled = ArrayObj(3, 7)
    assert filled.length == 3
    assert [filled.get(i) for i in range(3)] == [7, 7, 7]
    filled.set(1, 9)
    assert filled.get(1) == 9
    with pytest.raises(TurkeyPanic, match="length 3"):
        filled.get(3)

    unsafe = ArrayObj(2)
    assert unsafe.get(0) is UNINIT
    assert "Prim.arrayNewUninit" in PRIM_NAMES
    assert "Prim.arrayPush" not in PRIM_NAMES
    assert "Prim.arrayPop" not in PRIM_NAMES
