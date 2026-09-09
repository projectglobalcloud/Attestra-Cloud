"""
Console formatting helpers for the demo and attack scripts.

Kept separate so the demo scripts read as protocol narrative, not as printf
statements. No external dependencies -- plain ANSI, degrading to no-colour when
output is redirected to a file.
"""

from __future__ import annotations

import os
import sys
import time

_USE_COLOUR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text


def bold(t: str) -> str:
    return _c("1", t)


def dim(t: str) -> str:
    return _c("2", t)


def green(t: str) -> str:
    return _c("32", t)


def red(t: str) -> str:
    return _c("31", t)


def yellow(t: str) -> str:
    return _c("33", t)


def cyan(t: str) -> str:
    return _c("36", t)


WIDTH = 78


def banner(title: str, subtitle: str = "") -> None:
    print()
    print(bold("=" * WIDTH))
    print(bold(f"  {title}"))
    if subtitle:
        print(dim(f"  {subtitle}"))
    print(bold("=" * WIDTH))


def step(number: int | str, title: str) -> None:
    print()
    print(cyan(f"[{number}] {title}"))
    print(cyan("-" * WIDTH))


def note(text: str, indent: int = 4) -> None:
    """Explanatory prose -- what is happening and why it matters."""
    prefix = " " * indent
    for line in _wrap(text, WIDTH - indent):
        print(dim(prefix + line))


def item(label: str, value: str, indent: int = 4) -> None:
    print(" " * indent + f"{label:.<34} {value}")


def verdict(passed: bool, label: str, detail: str = "", indent: int = 4) -> None:
    mark = green("PASS") if passed else red("FAIL")
    line = " " * indent + f"{mark}  {label}"
    if detail:
        line += dim(f"  ({detail})")
    print(line)


def expected(passed: bool, should_pass: bool, label: str, indent: int = 4) -> bool:
    """
    Report an outcome together with whether it was the EXPECTED outcome.

    Demos are only convincing if a failure is announced as intended. Returns
    True when reality matched expectation.
    """
    ok = passed == should_pass
    mark = green("PASS") if passed else red("FAIL")
    tag = green("as expected") if ok else red("UNEXPECTED")
    print(" " * indent + f"{mark}  {label}  {dim('->')} {tag}")
    return ok


class Timer:
    """Context manager reporting how long a protocol phase took."""

    def __init__(self, label: str, indent: int = 4):
        self.label = label
        self.indent = indent

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.elapsed = time.perf_counter() - self.start
        print(" " * self.indent + dim(f"{self.label} took {self.elapsed:.2f}s"))
        return False


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines
