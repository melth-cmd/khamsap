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

The dash rule is tried whenever any source line starts with a speaker dash
(hyphen, en dash, em dash), since a dash is structural wherever it
appears. Leading placeholders are looked past, because a tag can hide the
dash: {0}-Hello.

When every source line is dashed, the dashes are also repaired: a line that
came back without one gets its source line's dash back ("- Hello." keeps its
space, "-Hello." does not). This is reported through `dashes_restored`.

Blank lines in the source do not count as lines, and a split never lands inside
a placeholder, a bracket or a music marker.

More lines than the source are left alone and reported, not "fixed".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

# The dash characters subtitles use for turn-taking.
DASHES = "-\u2013\u2014"

# " -" followed by a non-space: a speaker turn that lost its line break.
# The lookbehind keeps hyphenated words like "well-known" out of it.
_DASH_BOUNDARY_RE = re.compile(rf"(?<=\s)[{DASHES}](?=\S)")

# A placeholder or an HTML tag at the start of a line can hide a speaker dash:
# {0}-Hello. or <i>-Hello.</i>. Both forms reach this module, depending on
# whether tags were restored yet.
_LEADING_PREFIX_RE = re.compile(r"(?:\{\s*\d+\s*\}|<[^<>\n]*>)+")

# A split must never land inside one of these.
_UNBREAKABLE_RE = re.compile(
    r"\{\s*\d+\s*\}"  # our placeholders
    r"|\[[^\[\]\n]*\]"  # [SIGN: ...], [door slams]
    r"|\([^()\n]*\)"  # (laughs)
    r"|\u266a[^\u266a\n]*\u266a"  # music held between notes
)


@dataclass(frozen=True)
class Layout:
    """The text after re-wrapping, and which rule was used."""

    text: str
    method: str | None = None  # None | "dash" | "proportional" | "extra_lines"
    dashes_restored: bool = False

    @property
    def rewrapped(self) -> bool:
        return self.method in {"dash", "proportional"}


def _lines(text: str) -> list[str]:
    """Non-empty lines. SRT entries sometimes carry a blank one; it is not a line."""
    return [line for line in text.split("\n") if line.strip()]


def line_count(text: str) -> int:
    """Non-blank lines, the same count rewrap compares against."""
    return len(_lines(text))


def source_line_count(text: str) -> int:
    return line_count(text)


def dash_count(text: str) -> int:
    """How many non-blank lines start with a speaker dash (behind tags or not)."""
    return sum(1 for line in _lines(text) if _dash_index(line) is not None)


def rewrap(source: str, translated: str) -> Layout:
    """Give `translated` the same number of lines as `source`."""
    source_lines = _lines(source)
    target = len(source_lines)
    if target <= 1:
        return Layout(translated)

    current = _lines(translated)
    if len(current) > target:
        return Layout(translated, "extra_lines")

    all_dashed = all(_dash_index(line) is not None for line in source_lines)
    any_dashed = any(_dash_index(line) is not None for line in source_lines)

    if len(current) == target:
        # Line count is right; only the dashes may still be missing.
        if all_dashed:
            restored = _restore_dashes(current, source_lines)
            if restored != current:
                return Layout("\n".join(restored), dashes_restored=True)
        return Layout(translated)

    if any_dashed:
        dashes = list(_DASH_BOUNDARY_RE.finditer(translated))
        if len(dashes) == target - 1:
            pieces = _split_at(translated, [match.start() for match in dashes])
            if pieces is not None:
                if not all_dashed:
                    return Layout("\n".join(pieces), "dash")
                restored = _restore_dashes(pieces, source_lines)
                return Layout(
                    "\n".join(restored), "dash", dashes_restored=restored != pieces
                )

    points = _proportional_points(source_lines, translated)
    pieces = _split_at(translated, points)
    if pieces is None:
        return Layout(translated)

    restored = _restore_dashes(pieces, source_lines) if all_dashed else pieces
    return Layout(
        "\n".join(restored),
        "proportional",
        dashes_restored=restored != pieces,
    )


def _dash_index(line: str) -> int | None:
    """Where the speaker dash sits in `line`, skipping leading placeholders."""
    text = line.strip()
    position = 0
    while True:
        match = _LEADING_PREFIX_RE.match(text[position:])
        if match is None:
            break
        position += match.end()
        while position < len(text) and text[position].isspace():
            position += 1
    if position < len(text) and text[position] in DASHES:
        return position
    return None


def _dash_prefix(line: str) -> str:
    """The dash this line uses, with a space after it only if the line had one."""
    index = _dash_index(line)
    if index is None:
        return "-"
    dash = line[index]
    return dash + " " if line[index + 1 : index + 2] == " " else dash


def _restore_dashes(pieces: list[str], source_lines: list[str]) -> list[str]:
    """Give each piece the speaker dash its source line had, if it lost one."""
    if len(pieces) != len(source_lines):
        return pieces

    fixed: list[str] = []
    for piece, source_line in zip(pieces, source_lines):
        if _dash_index(piece) is not None:
            fixed.append(piece)
            continue
        position = 0
        while True:
            match = _LEADING_PREFIX_RE.match(piece[position:])
            if match is None:
                break
            position += match.end()
        prefix = _dash_prefix(source_line)
        rest = piece[position:]
        if prefix.endswith(" ") and rest[:1].isspace():
            rest = rest.lstrip()
        fixed.append(piece[:position] + prefix + rest)
    return fixed


def _proportional_points(source_lines: list[str], translated: str) -> list[int]:
    """Character positions in `translated` matching the source's break points."""
    lengths = [len(line.strip()) for line in source_lines]
    total = sum(lengths)
    if total == 0 or len(translated) < 2:
        return []

    points: list[int] = []
    blocked = _blocked_spans(translated)
    spaces = [
        index
        for index, character in enumerate(translated)
        if character.isspace() and not _inside(index, blocked)
    ]
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


def _blocked_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges a split should not enter: tags, brackets, music."""
    return [(match.start(), match.end()) for match in _UNBREAKABLE_RE.finditer(text)]


def _inside(position: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < position < end for start, end in spans)


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
