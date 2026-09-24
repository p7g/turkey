"""The C compiler that links a program, and how the linked program is run.

Two settings, both split with shell-word rules:

* `$TURKEY_CC` is the C compiler and linker, `cc` when unset. On an x86-64
  machine checking arm64 output it is a cross compiler, and it links
  statically: a dynamically linked arm64 binary under qemu-user needs an arm64
  libc to load, and a static one needs nothing.
* `$TURKEY_RUN` is a prefix for executing anything the compiler linked -- a
  test program or a built `boot`. Empty means run it directly, which is right
  natively and also under qemu-user once binfmt_misc knows the format; the
  prefix (`qemu-aarch64`) is for a machine where it cannot be registered.

`scripts/build.sh` reads the same two variables. Every cached build output
is keyed on `identity()` as well as its sources: the same source linked by two
compilers is two different binaries, and the caches are shared by every session
on the machine, so a key without it serves the wrong architecture.
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path


def cc() -> list[str]:
    """The compiler, with any flags the setting carries: a command prefix."""
    return shlex.split(os.environ.get("TURKEY_CC", "cc")) or ["cc"]


def missing() -> bool:
    """Whether there is no C compiler, which skips anything that links."""
    return shutil.which(cc()[0]) is None


def command(binary: Path, *args: str) -> list[str]:
    """The argv that runs `binary` with `args`."""
    return [*shlex.split(os.environ.get("TURKEY_RUN", "")), str(binary), *args]


def identity() -> bytes:
    """The compiler, as part of a cache key. How a binary is run is not: it
    does not change what was built."""
    return shlex.join(cc()).encode() + b"\0"
