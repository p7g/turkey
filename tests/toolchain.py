"""The C compiler that links a program, and how the linked program is run.

Two settings, both split with shell-word rules:

* `$TURKEY_CC` is the C compiler and linker, `cc` when unset. On an x86-64
  machine checking arm64 output it is a cross compiler, and it links
  statically: a dynamically linked arm64 binary under qemu-user needs an arm64
  libc to load, and a static one needs nothing.
* `$TURKEY_OPENSSL` is where OpenSSL 3 is installed, for a program that
  imports `Net.TLS`: its `lib/`, `include/` and `bin/openssl`. Unset on macOS
  it is Homebrew's `openssl@3`; unset elsewhere it is the system's, found by
  the compiler and on `$PATH`.
* `$TURKEY_RUN` is a prefix for executing anything the compiler linked -- a
  test program or a built `boot`. Empty means run it directly, which is right
  natively and also under qemu-user once binfmt_misc knows the format; the
  prefix (`qemu-aarch64-static`) is for a machine where it cannot be registered.

`scripts/build.sh` reads the same two variables. Every cached build output
is keyed on `identity()` as well as its sources: the same source linked by two
compilers is two different binaries, and the caches are shared by every session
on the machine, so a key without it serves the wrong architecture.
"""

from __future__ import annotations

import functools
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


def cc() -> list[str]:
    """The compiler, with any flags the setting carries: a command prefix."""
    return shlex.split(os.environ.get("TURKEY_CC", "cc")) or ["cc"]


def missing() -> bool:
    """Whether there is no C compiler, which skips anything that links."""
    return shutil.which(cc()[0]) is None


def command(binary: Path, *args: str) -> list[str]:
    """The argv that runs `binary` with `args`."""
    return [*shlex.split(os.environ.get("TURKEY_RUN", "")), str(binary), *args]


def _openssl_prefix() -> Path | None:
    """Where OpenSSL 3 is installed, or None for the system's default places.

    On macOS the system's own `/usr/lib/libcrypto.dylib` ends any process
    that loads it, so a Mac needs Homebrew's `openssl@3`, which is keg-only:
    not on the default search paths, so named with -L and -I.
    """
    if (prefix := os.environ.get("TURKEY_OPENSSL")):
        return Path(prefix)
    if target() != "arm64-darwin":
        return None
    for brew in ("/opt/homebrew/bin/brew", "brew"):
        if shutil.which(brew):
            result = subprocess.run([brew, "--prefix", "openssl@3"],
                                    capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                return Path(result.stdout.strip())
    return Path("/opt/homebrew/opt/openssl@3")


def openssl_libraries() -> list[str]:
    """What a program that imports `Net.TLS` adds to its link, after the
    runtime's `libraries()`. Only such a program gets these: one that does
    not links exactly as it would without them."""
    prefix = _openssl_prefix()
    found = [f"-L{prefix / 'lib'}"] if prefix else []
    return [*found, "-lssl", "-lcrypto"]


def openssl_includes() -> list[str]:
    """The flags a C file that includes OpenSSL's headers compiles with."""
    prefix = _openssl_prefix()
    return [f"-I{prefix / 'include'}"] if prefix else []


def openssl_command() -> str:
    """The `openssl` program of the same installation, which the tests make
    their certificates with. macOS's `/usr/bin/openssl` is LibreSSL's."""
    prefix = _openssl_prefix()
    return str(prefix / "bin" / "openssl") if prefix else "openssl"


@functools.lru_cache(maxsize=None)
def links_openssl() -> bool:
    """Whether a C program that calls OpenSSL 3 compiles and links here."""
    if missing():
        return False
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "probe.c"
        source.write_text("#include <openssl/ssl.h>\n"
                          "int main(void) { return SSL_CTX_new(TLS_method()) == 0; }\n")
        result = subprocess.run(
            [*cc(), *openssl_includes(), str(source), "-o", str(Path(directory) / "probe"),
             *openssl_libraries()], capture_output=True)
    return result.returncode == 0


def needs_openssl() -> None:
    """Skips the calling test unless OpenSSL 3 links, or fails it where
    `$TURKEY_REQUIRE_OPENSSL` is set, as CI sets it, so that a runner without
    OpenSSL cannot pass by skipping."""
    if links_openssl():
        return
    message = "OpenSSL 3 does not link with $TURKEY_CC" + (
        f" from {_openssl_prefix()}" if _openssl_prefix() else "")
    if os.environ.get("TURKEY_REQUIRE_OPENSSL"):
        pytest.fail(message)
    pytest.skip(message)


def identity() -> bytes:
    """The compiler, as part of a cache key. How a binary is run is not: it
    does not change what was built."""
    return shlex.join(cc()).encode() + b"\0"


@functools.lru_cache(maxsize=None)
def machine() -> str:
    """The triple `$TURKEY_CC -dumpmachine` reports, or "" if it cannot say.

    `scripts/build.sh` chooses its target from the same triple, and `boot`
    emits for the target it was built for, so this is also what the tests'
    programs are compiled for.
    """
    if missing():
        return ""
    result = subprocess.run([*cc(), "-dumpmachine"], capture_output=True,
                            text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def target() -> str:
    """The `--target` spelling for what `$TURKEY_CC` links for, which is the
    target `boot` was built for and so its default; "" for a triple that
    names neither. The same patterns as `scripts/build.sh`'s."""
    triple = machine()
    if triple.startswith(("arm64-apple-darwin", "arm64-apple-macos",
                          "aarch64-apple-darwin")):
        return "arm64-darwin"
    if triple.startswith(("aarch64", "arm64")) and "-linux" in triple:
        return "arm64-linux"
    return ""


def c_symbol(name: str) -> str:
    """How `boot`'s assembly spells the C symbol `name` for the target it
    was built for: Mach-O puts an underscore before it, and ELF does not."""
    return "_" + name if target() == "arm64-darwin" else name


def local_label(name: str) -> str:
    """How that assembly spells a label local to the file, whose prefix is
    the object format's: `L` in Mach-O and `.L` in ELF."""
    return "." + name if target() == "arm64-linux" else name


def libraries() -> list[str]:
    """What a link against the runtime needs after its objects.

    glibc keeps the maths library out of libc, and the runtime calls `log10`.
    Darwin's libSystem carries it, so a Mac link has nothing added.
    """
    return ["-lm"] if target() == "arm64-linux" else []


@functools.lru_cache(maxsize=None)
def sanitizes() -> bool:
    """Whether `$TURKEY_CC` links a program built with -fsanitize=undefined.

    Statically linked, that needs the UBSan runtime as an archive for the
    target: gcc's cross toolchain ships `libubsan.a`, and clang's needs
    compiler-rt built for the target, which a distribution's x86-64 clang
    does not carry for aarch64.
    """
    if missing():
        return False
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "probe.c"
        source.write_text("int main(void) { return 0; }\n")
        result = subprocess.run(
            [*cc(), "-fsanitize=undefined", str(source),
             "-o", str(Path(directory) / "probe")], capture_output=True)
    return result.returncode == 0


def needs_sanitizer() -> None:
    """Skips the calling test unless -fsanitize=undefined links."""
    if not sanitizes():
        pytest.skip("$TURKEY_CC cannot link -fsanitize=undefined: "
                    "no UBSan runtime for its target")
