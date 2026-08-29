"""Error types shared by every stage of the pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    """A source location. Columns and lines are 1-based."""

    line: int
    col: int

    def __str__(self) -> str:
        return f"{self.line}:{self.col}"


class TurkeyError(Exception):
    """Base class for every error the implementation reports to the author."""

    stage = "error"

    def __init__(self, message: str, span: Span | None = None):
        super().__init__(message)
        self.message = message
        self.span = span

    def render(self, filename: str = "<input>") -> str:
        where = f"{filename}:{self.span}" if self.span else filename
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


class TurkeyPanic(Exception):
    """A runtime panic: `error(...)`, a bounds violation, a failed match."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
