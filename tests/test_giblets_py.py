"""That the Python compiler's giblet list is `boot`'s.

Internal: split from `test_giblets.py`, whose behavioral tests run through
`boot`. This goes with the Python compiler (TIX-96).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_both_compilers_keep_the_same_list():
    from turkey.giblets import GIBLET_MODULES
    source = (REPO_ROOT / "boot" / "Turkey" / "Giblets.gob").read_text(
        encoding="utf-8")
    found = re.search(r"^let giblets = \[(.*?)\]", source, re.M | re.S)
    assert found is not None
    assert set(re.findall(r'"([^"]+)"', found.group(1))) == set(GIBLET_MODULES)
