"""Error types shared by every stage of the pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass

# `Module#name` is what module resolution calls a top-level binding
# (`turkey/modules.py`). No diagnostic should say it: the author wrote `f`, not
# `Main#f`, and which module a name came from is not news to them. Stripping it
# here rather than at each message site means a new message cannot forget.
_INTERNAL = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*#")


def short(text: str) -> str:
    """A message with every internal name written the way the author wrote it."""
    return _INTERNAL.sub("", text)



@dataclass(frozen=True)
class Span:
    """A source location. Columns and lines are 1-based."""

    line: int
    col: int
    # Which file the location is in. `None` means "the file the driver was
    # handed", which is what every single-file program still says; a module
    # loaded through an import carries its own name so a diagnostic in it does
    # not appear to come from the entry point (M11a).
    file: str | None = None

    def __str__(self) -> str:
        return f"{self.line}:{self.col}"


class TurkeyError(Exception):
    """Base class for every error the implementation reports to the author."""

    stage = "error"

    def __init__(self, message: str, span: Span | None = None):
        message = short(message)
        super().__init__(message)
        self.message = message
        self.span = span

    def render(self, filename: str = "<input>") -> str:
        if self.span is None:
            return f"{filename}: {self.stage}: {self.message}"
        where = f"{self.span.file or filename}:{self.span}"
        return f"{where}: {self.stage}: {self.message}"


class LexError(TurkeyError):
    stage = "lex error"


class ParseError(TurkeyError):
    stage = "parse error"


class TypeError_(TurkeyError):
    stage = "type error"


class Unsupported(TurkeyError):
    """A construct design.md specifies but the v0 prototype does not implement."""

    stage = "unsupported"


@dataclass(frozen=True)
class PanicFrame:
    function: str
    span: Span | None


class TurkeyPanic(Exception):
    """A runtime panic: `error(...)`, a bounds violation, a failed match."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
        self.frames: list[PanicFrame] = []

    def add_frame(self, function: str, span: Span | None) -> TurkeyPanic:
        self.frames.append(PanicFrame(short(function), span))
        return self

    def render(self, filename: str = "<input>") -> str:
        lines = [f"panic: {self.message}"]
        for frame in self.frames:
            if frame.span is None:
                lines.append(f"  at {frame.function}")
            else:
                where = frame.span.file or filename
                lines.append(f"  at {frame.function} ({where}:{frame.span})")
        return "\n".join(lines)
