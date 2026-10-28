"""Reading, parsing and writing .srt files.

This is the only module that knows about SRT structure. Indexes and timestamps
never leave the program, but they are preserved exactly on the way back out.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import srt

log = logging.getLogger(__name__)

# Tried in order. utf-8-sig first so a BOM is consumed instead of ending up as a
# stray character in the first line. latin-1 never raises, so it is the floor.
FALLBACK_ENCODINGS: tuple[str, ...] = ("utf-8", "tis-620", "cp1252", "latin-1")

# One timecode line == one subtitle block. Used to count blocks the parser had
# to throw away, since srt.parse() only reports what it kept.
_TIMECODE_RE = re.compile(
    r"^\s*\d{1,3}:\d{2}:\d{2}[,.]\d{1,3}\s*-+>\s*", re.MULTILINE
)


@dataclass
class SubtitleFile:
    """A parsed subtitle file plus what it took to read it."""

    subtitles: list[srt.Subtitle] = field(default_factory=list)
    encoding: str = "utf-8"
    skipped: int = 0

    def __len__(self) -> int:
        return len(self.subtitles)


def _detect_encoding(data: bytes) -> str | None:
    """Best-effort charset detection, or None when detection is unavailable."""
    try:
        from charset_normalizer import from_bytes
    except ImportError:  # optional dependency
        return None

    best = from_bytes(data).best()
    return best.encoding if best is not None else None


def decode_bytes(
    data: bytes, encodings: Sequence[str] = FALLBACK_ENCODINGS
) -> tuple[str, str]:
    """Decode raw bytes, returning (text, encoding_used).

    Order: UTF-8 (BOM-tolerant) -> charset detection -> the given fallbacks.
    Only raises if every fallback fails, which latin-1 never does.
    """
    try:
        return data.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        pass

    detected = _detect_encoding(data)
    if detected:
        try:
            return data.decode(detected), detected
        except (UnicodeDecodeError, LookupError) as exc:
            log.debug("Charset detection suggested unusable %r: %s", detected, exc)

    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        log.warning("File is not UTF-8, falling back to %s", encoding)
        return text, encoding

    raise UnicodeDecodeError("srtio", data, 0, len(data), "no usable encoding")


def read_text(
    path: str | Path, encodings: Sequence[str] = FALLBACK_ENCODINGS
) -> tuple[str, str]:
    """Read a subtitle file, returning (text, encoding_used)."""
    path = Path(path)
    text, encoding = decode_bytes(path.read_bytes(), encodings)
    if encoding != "utf-8-sig":
        log.info("%s decoded as %s", path.name, encoding)
    return text, encoding


def parse_subtitles(text: str) -> tuple[list[srt.Subtitle], int]:
    """Parse SRT text, tolerating malformed blocks.

    Returns (subtitles, skipped_count). Skipped blocks are logged, never fatal.
    """
    expected = len(_TIMECODE_RE.findall(text))
    subtitles = list(srt.parse(text, ignore_errors=True))
    skipped = max(expected - len(subtitles), 0)
    if skipped:
        log.warning("Skipped %d malformed subtitle block(s)", skipped)
    return subtitles, skipped


def load_srt(
    path: str | Path, encodings: Sequence[str] = FALLBACK_ENCODINGS
) -> SubtitleFile:
    """Read and parse a subtitle file in one call."""
    text, encoding = read_text(path, encodings)
    subtitles, skipped = parse_subtitles(text)
    log.info(
        "%s: %d entries, encoding %s, %d skipped",
        Path(path).name,
        len(subtitles),
        encoding,
        skipped,
    )
    return SubtitleFile(subtitles=subtitles, encoding=encoding, skipped=skipped)


def compose_subtitles(subtitles: Iterable[srt.Subtitle]) -> str:
    """Render subtitles back to SRT text, keeping the original indexes."""
    return srt.compose(list(subtitles), reindex=False, strict=False, eol="\n")


def _atomic_write(path: Path, data: bytes) -> None:
    """Write bytes via a temp file in the same directory, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def output_path_for(
    source: str | Path, suffix: str = ".th.srt", strip_en_suffix: bool = True
) -> Path:
    """Pick the output path: strip ".en" rather than appending ".th" blindly.

    Movie (2026).en.srt -> Movie (2026).th.srt
    Movie (2026).srt    -> Movie (2026).th.srt
    With strip_en_suffix off: Movie.en.srt -> Movie.en.th.srt
    """
    source = Path(source)
    stem = source.stem if source.suffix else source.name
    if strip_en_suffix and stem.lower().endswith(".en"):
        stem = stem[:-3]
    return source.with_name(stem + suffix)


def write_srt(
    path: str | Path,
    subtitles: Iterable[srt.Subtitle],
    encoding: str = "utf-8",
) -> Path:
    """Write subtitles to `path`. Pass encoding="utf-8-sig" for a BOM."""
    path = Path(path)
    data = compose_subtitles(subtitles).encode(encoding)
    _atomic_write(path, data)
    log.info("Wrote %s (%s)", path.name, encoding)
    return path
