"""Every file of the library and the compiler parses with honest spans.

`TURKEY_CHECK_SPANS` has the compiler check each file it parses against the
file's text (`src/Turkey/ParseCheck.gob`): that every node's span covers its own
text, inside its parent's and apart from its siblings', and that the lexer's
comment and blank-line tables account for all the text the tokens leave out.

The suite sets it for everything it compiles, which reaches only the modules
some program imports. Here each file under `lib/` and `src/` is the entry of
its own `turkey check`. The entry is parsed before anything else is asked of it,
so a file that is no program, such as a library module the Prelude imports,
which cannot also be the entry, is still checked before its compile fails.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain

FILES = sorted(p.relative_to(bootc.REPO_ROOT)
               for root in ("lib", "src")
               for p in (bootc.REPO_ROOT / root).rglob("*.gob"))


@pytest.mark.parametrize("path", FILES, ids=str)
def test_the_parse_matches_the_text(path: Path) -> None:
    environment = dict(os.environ, TURKEY_CHECK_SPANS="1")
    result = subprocess.run(toolchain.command(bootc.binary(), "check", str(path)),
                            cwd=bootc.REPO_ROOT, env=environment,
                            capture_output=True, text=True, timeout=120)
    assert "internal error" not in result.stderr, result.stderr
