"""Terminal colour that switches itself off when it would only add noise.

Colour is used for meaning (green ok, red error, yellow needs attention, dim
detail), never for decoration, and only on an interactive terminal: piped
output, ``NO_COLOR``, and ``TERM=dumb`` get plain text. ``FORCE_COLOR`` turns
it on regardless, for CI logs that render ANSI.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import TextIO

_CODES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
}


class Style:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def paint(self, text: str, *names: str) -> str:
        if not self.enabled or not names or not text:
            return text
        codes = ";".join(_CODES[name] for name in names)
        return f"\033[{codes}m{text}\033[0m"

    def ok(self, text: str) -> str:
        return self.paint(text, "green")

    def error(self, text: str) -> str:
        return self.paint(text, "red", "bold")

    def warn(self, text: str) -> str:
        return self.paint(text, "yellow")

    def dim(self, text: str) -> str:
        return self.paint(text, "dim")

    def bold(self, text: str) -> str:
        return self.paint(text, "bold")

    def accent(self, text: str) -> str:
        return self.paint(text, "cyan")


PLAIN = Style(False)


def _enable_windows_ansi() -> bool:
    """Turn on virtual-terminal processing for this console; False if impossible."""
    try:
        import ctypes  # noqa: PLC0415 - Windows-only, and only when colour is wanted

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


def wants_colour(stream: TextIO, environ: Mapping[str, str]) -> bool:
    if environ.get("NO_COLOR"):
        return False
    if environ.get("FORCE_COLOR"):
        return True
    isatty = getattr(stream, "isatty", None)
    if isatty is None or not isatty() or environ.get("TERM") == "dumb":
        return False
    return sys.platform != "win32" or _enable_windows_ansi()


def style_for(stream: TextIO, environ: Mapping[str, str] | None = None) -> Style:
    return Style(wants_colour(stream, os.environ if environ is None else environ))
