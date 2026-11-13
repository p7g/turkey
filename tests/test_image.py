"""`src/Turkey/Image.gob`: a section written to the file and not mapped.

`src/ImageCases.gob` fills in a debug section's fields against a code section
loaded at 0x100000400. Each address in it is its target's load address, which
no loader rewrites, and a reference into another debug section is the
target's offset in it.
"""

from pathlib import Path

from tests import lang

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = REPO_ROOT / "src" / "ImageCases.gob"


def test_an_unmapped_section_holds_final_addresses_and_offsets():
    assert lang.output(DRIVER).splitlines() == [
        f"function: {0x100000404}",
        f"block: {0x10000040C}",
        "string: 5",
    ]
