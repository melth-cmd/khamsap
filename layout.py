"""Re-wrapping translated entries to match the source line count.

Models treat line breaks inside an entry as soft wrapping and routinely join
them. Line count is display formatting, so it is repaired in code rather than
left to retries: a merged line is a layout problem, never a reason to fall back
to English.

Two rules, in order:

1. Dash rule. If the source lines each begin with "-" (turn-taking), and the
   translation contains inline " -" boundaries, split there. The dash is
   structural, so this recovers the speaker boundary exactly.

2. Proportional rule. Otherwise split at the space nearest the position where
   the source line ended, as a fraction of the source length. Thai spaces mark
   clause boundaries, so this usually lands better than a literal midpoint.
   With three or more source lines it splits at every proportional point.

More lines than the source are left alone and reported, not "fixed".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

# " -" followed by a non-space: a speaker turn that lost its line break.
# The lookbehind keeps hyphenated words like "well-known" out of it.
_DASH_BOUNDARY_RE = re.compile(r"(?<=\s)-(?=\S)")


@dataclass(frozen=True)
class Layout:
    """The text after re-wrapping, and which rule was used."""

    text: str
    method: str | None = None  # None | "dash" | "proportional" | "extra_lines"

    @property
    def rewrapped(self) -> bool:
        return self.method in {"dash", "proportional"}


def source_line_count(text: str) -> int:
    return len(text.split("\n"))


def rewrap(source: str, translated: str) -> Layout:
    """Give `translated` the same number of lines as `source`."""
    source_lines = [line for line in source.split("\n")]
    target = len(source_lines)
    if target <= 1:
        return Layout(translated)

    current = translated.split("\n")
    if len(current) > target:
        return Layout(translated, "extra_lines")
    if len(current) == target:
        return Layout(translated)

    if _all_lines_dash(source_lines):
        dashes = list(_DASH_BOUNDARY_RE.finditer(translated))
        if len(dashes) == target - 1:
            points = [match.start() for match in dashes]
            pieces = _split_at(translated, points)
            if pieces is not None:
                return Layout("\n".join(pieces), "dash")

    points = _proportional_points(source_lines, translated)
    pieces = _split_at(translated, points)
    if pieces is None:
        return Layout(translated)
    return Layout("\n".join(pieces), "proportional")


def _all_lines_dash(lines: list[str]) -> bool:
    meaningful = [line.strip() for line in lines if line.strip()]
    return bool(meaningful) and all(line.startswith("-") for line in meaningful)


def _proportional_points(source_lines: list[str], translated: str) -> list[int]:
    """Character positions in `translated` matching the source's break points."""
    lengths = [len(line.strip()) for line in source_lines]
    total = sum(lengths)
    if total == 0 or len(translated) < 2:
        return []

    points: list[int] = []
    spaces = [index for index, character in enumerate(translated) if character.isspace()]
    consumed = 0
    previous = 0

    for length in lengths[:-1]:
        consumed += length
        target = consumed / total * len(translated)
        candidates = [index for index in spaces if previous < index < len(translated) - 1]
        point = (
            min(candidates, key=lambda index: abs(index - target))
            if candidates
            else int(round(target))
        )
        # Never cut immediately before whitespace: that piece would come out empty.
        while point < len(translated) - 1 and translated[point].isspace():
            point += 1
        point = max(point, previous + 1)
        points.append(point)
        previous = point

    return points


def _split_at(text: str, points: list[int]) -> list[str] | None:
    """Cut `text` before each point, trimming the pieces. None if any is empty."""
    pieces: list[str] = []
    start = 0
    for point in points:
        piece = text[start:point].strip()
        if not piece:
            return None
        pieces.append(piece)
        start = point
    tail = text[start:].strip()
    if not tail:
        return None
    pieces.append(tail)
    return pieces
