"""What a `foreign` call means on this host (TIX-62, PROPOSALS.md item 8).

The native backend answers a foreign call by making it. This side cannot: the
addresses a Turkey program holds come from `values.RawHeap`, which hands out
*simulated* ones from 4096, and no real `read` can be given one.

**`ctypes` is the obvious answer and it is the wrong one**, for a reason that
belongs to this project rather than to effort. `tests/test_native.py`'s premise
is that there is no byte-identical oracle below Core and that differential
execution replaces it. Back raw memory with `ctypes` and real `malloc`, and the
two arms of that differential stop being independent implementations -- they
become the host's libc, called twice, and a differential whose arms are the
same code catches nothing. `PRIMITIVES.md` 9.4 already argued the specific
case: fresh memory is poisoned rather than zeroed because a zero fill "would
make 'reads back as zero' an accidental guarantee, and since this side is the
oracle, the differential would then *enforce* the accident". Real `malloc` is
worse than a zero fill, being plausible garbage that matches often enough to be
flaky instead of wrong. Three smaller things go with it: an address stops being
a function of the program's own allocation sequence, and the goldens downstream
are byte-exact; undefined behaviour stops raising `TurkeyPanic` and starts
segfaulting a pytest worker, in a suite that runs in parallel; and
`tests/test_primitives.py`'s raw-memory tests only mean anything against a
simulation.

**So a symbol is modelled, and the model is not hand-written C.** The residue
an FFI has to cover is about ten POSIX calls and Python already has every one
of them. Each entry below is a delegation to `os` plus a buffer copied in or
out of the simulated heap, which is also why the work survives a move to raw
Linux syscalls: it is a delegation to POSIX *semantics*, not to libc.

There is one entry per symbol `lib/Unsafe/Libc.gob` declares and no more. The
rest of the residue -- `read`, `open`, `close`, `mmap`, the `pthread_*` five --
arrives with the runtime sections that need it, because a model with no caller
is a second unverified signature sitting beside the first.

A symbol with no entry here is not an error at compile time -- the declaration
is still checked, still lowered, and still runs natively. It is an error at the
moment this host is asked to *call* it, which keeps the differential covering
every stage up to execution and says plainly which symbol is missing.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from .errors import TurkeyPanic
from .prelude import BOOL_FALSE, BOOL_TRUE
from .types import BOOL
from .values import RAW_HEAP, UNIT as UNIT_VALUE, Builtin, ConValue

#: `Bool` is a library type, not a primitive one, so its internal name is what
#: a declaration's resolved type carries.
BOOL_TYPE = BOOL.name

#: Symbol -> what calling it does on this host.
_MODELS: dict[str, Callable] = {}

#: `getenv` answers a pointer into the environment block, which C promises is
#: stable for as long as nobody rewrites the variable. Nothing here rewrites
#: one, so each name is copied into the simulated heap once and the same
#: address is answered afterwards -- a program that calls `getenv("PATH")`
#: twice and compares the two pointers gets what it would get natively.
_ENVIRON: dict[str, int] = {}


def reset() -> None:
    """Forget the per-run caches. Called where the heap itself is reset, so
    that an address is a function of the program's own allocation sequence."""
    _ENVIRON.clear()


def model(symbol: str) -> Callable | None:
    return _MODELS.get(symbol)


def call(symbol: str, args: list) -> object:
    """Answer a foreign call, or say that this host cannot."""
    fn = _MODELS.get(symbol)
    if fn is None:
        raise TurkeyPanic(
            f"foreign '{symbol}' has no model on this host, so a program that "
            f"calls it can be compiled but not run here; see turkey/foreign.py")
    return fn(*args)


def _entry(symbol: str):
    def register(fn):
        _MODELS[symbol] = fn
        return fn
    return register


# -- reading and writing the simulated heap ---------------------------------
#
# Every buffer a POSIX call touches is `(pointer, length)`, so these two are
# the whole of the marshalling. A C string appears only in a path or an
# environment name, which is `PROPOSALS.md` 8.4's reason for not padding
# `TurkeyString`: the NUL-terminated argument is the exception here, not the
# rule.


def _bytes_at(address: int, length: int) -> bytes:
    if length <= 0:
        return b""
    return bytes(RAW_HEAP.bytes[
        RAW_HEAP._at(address, length, "read"):][:length])


def _store_bytes(address: int, payload: bytes) -> None:
    for offset, byte in enumerate(payload):
        RAW_HEAP.store(address + offset, 1, byte)


def _c_string(address: int) -> bytes:
    """The bytes up to the first NUL. Reading one that is not there walks off
    the end of the address space and panics, which is a debugging aid and not
    a semantics -- natively it is undefined and reads whatever follows."""
    if address == 0:
        raise TurkeyPanic("foreign: a null pointer where a C string was wanted")
    out = bytearray()
    while True:
        byte = RAW_HEAP.load(address + len(out), 1)
        if byte == 0:
            return bytes(out)
        out.append(byte)


def _new_c_string(payload: bytes) -> int:
    """A NUL-terminated copy, in a block this side owns for the rest of the
    run. What a C function that answers a `char *` into its own storage does."""
    address = RAW_HEAP.allocate(len(payload) + 1)
    _store_bytes(address, payload + b"\0")
    return address


def _errno(exc: OSError) -> int:
    """C's failure convention: -1 and `errno`. The number is the host's, which
    is the same number the native side would see, since both ask the same
    kernel."""
    return exc.errno or 0


# -- memory ------------------------------------------------------------------


@_entry("malloc")
def _malloc(size: int) -> int:
    # A negative size answers null rather than panicking: a caller can test
    # for null, and `malloc((size_t)-1)` asks for the address space and fails,
    # so the two hosts agree without either one checking.
    return RAW_HEAP.allocate(size)


@_entry("free")
def _free(address: int):
    RAW_HEAP.free(address)
    return UNIT_VALUE


@_entry("strlen")
def _strlen(address: int) -> int:
    return len(_c_string(address))


# -- the outside world -------------------------------------------------------


@_entry("getenv")
def _getenv(name: int) -> int:
    key = _c_string(name).decode("utf-8", "surrogateescape")
    if key in _ENVIRON:
        return _ENVIRON[key]
    value = os.environ.get(key)
    if value is None:
        return 0
    address = _new_c_string(value.encode("utf-8", "surrogateescape"))
    _ENVIRON[key] = address
    return address


@_entry("write")
def _write(fd: int, buf: int, count: int) -> int:
    payload = _bytes_at(buf, count)
    # stdout and stderr go through Python's own streams rather than the file
    # descriptor, so that a foreign write interleaves with `print` the way it
    # does natively instead of landing ahead of a buffer nobody flushed.
    stream = {1: sys.stdout, 2: sys.stderr}.get(fd)
    if stream is not None:
        stream.write(payload.decode("utf-8", "surrogateescape"))
        stream.flush()
        return len(payload)
    try:
        return os.write(fd, payload)
    except OSError as exc:
        return -_errno(exc)


# -- the values that cross ---------------------------------------------------
#
# A model works in the machine's terms -- an address is an integer, a length is
# an integer -- and the evaluator works in the language's. The two disagree in
# exactly three places, one per type whose Python representation is not already
# what C would hold, so the conversion is driven off the declaration rather
# than written into each model.


def _bool(flag) -> ConValue:
    return ConValue(BOOL_TRUE if flag else BOOL_FALSE, ())


def _into(ty, value):
    """One argument, as the model wants it."""
    name = getattr(ty, "name", "")
    if name == BOOL_TYPE:
        return value.con == BOOL_TRUE
    if name == "Char":
        return ord(value)
    return value


def _out_of(ty, value):
    """One result, as the language holds it."""
    name = getattr(ty, "name", "")
    if name == "Unit":
        return UNIT_VALUE
    if name == BOOL_TYPE:
        return _bool(value)
    if name == "Char":
        return chr(value)
    return value


def bindings(decls) -> dict[str, object]:
    """Every `foreign` declaration in the program, as a callable value.

    The same shape `builtins.initial_values` answers, and for the same reason:
    a foreign name is a `Prim.` entry written in source, so it reaches the
    evaluator the way one does -- a name in the global environment holding
    something with an arity.
    """
    out: dict[str, object] = {}
    for name, info in decls.foreigns.items():
        out[name] = Builtin(name, len(info.params), _dispatch(info))
    return out


def _dispatch(info):
    def invoke(*args):
        converted = [_into(ty, value)
                     for ty, value in zip(info.params, args)]
        return _out_of(info.ret, call(info.symbol, converted))
    return invoke


__all__ = ["bindings", "call", "model", "reset"]
