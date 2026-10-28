"""Verification pass: look at a finished translation and flag what looks wrong.

This module never changes the subtitles. It compares each entry with the source
it was translated from and returns flags; report.txt (the next step) is the only
place those flags are written. Fixing is a human decision, or an explicit
--fix run later.

Only the English source and the finished Thai are compared, so cached and
freshly translated entries are treated the same.

Flag kinds:
  failed               the entry was left in English after retries
  empty                nothing but whitespace, tags or placeholders came back
  short / long         the length is far from the source's (advisory)
  line_count           a different number of lines than the source
  dashes_lost          a speaker dash the source had is gone
  tag_mismatch         formatting tags are missing or invented
  glossary_missing     a glossary term is not rendered as the glossary says
  glossary_inconsistent  a term that should be Thai is still in English
  english_left         English words that are not a glossary "keep" term

Non-verbal sources ("Mm-hmm", "[MUSIC PLAYING]") are only checked for being
empty: they are allowed to come back untranslated.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

import glossary
import layout
import llm
import tags

log = logging.getLogger(__name__)

# Stable order, so a report is laid out the same way on every run.
KINDS: tuple[str, ...] = (
    "failed",
    "empty",
    "short",
    "long",
    "line_count",
    "dashes_lost",
    "tag_mismatch",
    "glossary_missing",
    "glossary_inconsistent",
    "english_left",
)

# Length heuristics. Thai is written without spaces, so it often comes out
# shorter than the English; these only catch outliers, and are advisory.
MIN_LENGTH_RATIO = 0.15
MAX_LENGTH_RATIO = 4.0
# Below this many source characters a ratio says nothing ("Yes." -> "ใช่").
MIN_COMPARE_CHARS = 4

# Latin words, keeping apostrophes inside a word ("Gandalf's", "don't").
_ENGLISH_WORD_RE = re.compile(r"[A-Za-z]+(?:['\u2019][A-Za-z]+)*")


@dataclass(frozen=True)
class Flag:
    """One thing worth a look, tied to one entry."""

    index: int
    kind: str
    message: str
    source: str = ""
    translation: str = ""


@dataclass
class Verification:
    """All flags for one file, plus how many entries were checked."""

    flags: list[Flag] = field(default_factory=list)
    checked: int = 0

    def extend(self, flags: Sequence[Flag]) -> None:
        self.flags.extend(flags)

    @property
    def by_kind(self) -> dict[str, int]:
        return dict(Counter(flag.kind for flag in self.flags))

    @property
    def entries(self) -> list[int]:
        """Entry indexes with at least one flag, in order."""
        return sorted({flag.index for flag in self.flags})

    def for_index(self, index: int) -> list[Flag]:
        return [flag for flag in self.flags if flag.index == index]


class Verifiable(Protocol):
    """What verify_file() needs: pipeline.TranslatedFile satisfies this."""

    results: Sequence[object]  # EntryResult-like: index/text/ok/reason
    sources: Mapping[int, str]


def scrub(text: str) -> str:
    """Text without formatting tags or placeholders, for word and length checks."""
    return tags.strip_markup(text)


def content_length(text: str) -> int:
    """Characters that carry meaning: tags, placeholders and spaces removed."""
    return len(re.sub(r"\s+", "", scrub(text)))


def english_words(text: str) -> list[str]:
    """Latin-script words in `text`, in order, without duplicates."""
    words: list[str] = []
    seen: set[str] = set()
    for word in _ENGLISH_WORD_RE.findall(scrub(text)):
        key = word.casefold()
        if key not in seen:
            seen.add(key)
            words.append(word)
    return words


def tag_mismatch(source: str, translated: str) -> str | None:
    """A one-line description of missing/invented tags, or None if they match.

    Comparing the tags themselves (not placeholders) is what matters here: by
    now the pipeline has restored them, and a tag the model dropped is simply
    gone. An invented {N} survives restore as literal text, so it shows up too.
    """
    wanted = tags.protect(source).counts
    got = tags.protect(translated).counts
    missing = wanted - got
    extra = got - wanted
    if not missing and not extra:
        return None

    parts: list[str] = []
    if missing:
        parts.append("missing " + _render_tags(missing))
    if extra:
        parts.append("extra " + _render_tags(extra))
    return "tag mismatch: " + "; ".join(parts)


def _render_tags(counts: Counter[str]) -> str:
    return ", ".join(
        f"{raw} x{count}" if count > 1 else raw for raw, count in sorted(counts.items())
    )


def glossary_flags(
    index: int, source: str, translated: str, terms: Sequence[glossary.Term]
) -> list[Flag]:
    """Check the terms this entry actually uses, as the glossary says to write them."""
    flags: list[Flag] = []
    for term in glossary.matching(terms, source):
        if term.action == "keep":
            if not _has_en(translated, term.en):
                flags.append(
                    Flag(
                        index,
                        "glossary_missing",
                        f"glossary term {term.en!r} (keep) is missing from the translation",
                        source,
                        translated,
                    )
                )
            continue

        if term.th in translated:
            continue
        if _has_en(translated, term.en):
            flags.append(
                Flag(
                    index,
                    "glossary_inconsistent",
                    f"glossary term {term.en!r} is still in English, expected {term.th!r}",
                    source,
                    translated,
                )
            )
        else:
            flags.append(
                Flag(
                    index,
                    "glossary_missing",
                    f"glossary term {term.en!r} expected as {term.th!r} is missing",
                    source,
                    translated,
                )
            )
    return flags


def _has_en(text: str, word: str) -> bool:
    """True if `word` appears as a whole word, case-insensitively."""
    pattern = r"(?<!\w)" + re.escape(word) + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def leftover_english(
    index: int,
    source: str,
    translated: str,
    terms: Sequence[glossary.Term],
) -> list[Flag]:
    """English words in the Thai that no "keep" term accounts for.

    Words covered by any glossary term are left to the glossary checks, so a
    name that should have been transliterated is reported once, not twice.
    """
    covered = {term.en.casefold() for term in terms}
    words = [word for word in english_words(translated) if word.casefold() not in covered]
    if not words:
        return []
    return [
        Flag(
            index,
            "english_left",
            "English left in the translation: " + ", ".join(words),
            source,
            translated,
        )
    ]


def length_flags(index: int, source: str, translated: str) -> list[Flag]:
    """Flag a translation that is implausibly short or long for its source."""
    source_length = content_length(source)
    translated_length = content_length(translated)
    if source_length < MIN_COMPARE_CHARS or translated_length == 0:
        return []

    ratio = translated_length / source_length
    detail = f"{translated_length} vs {source_length} characters"
    if ratio < MIN_LENGTH_RATIO:
        return [Flag(index, "short", f"unusually short ({detail})", source, translated)]
    if ratio > MAX_LENGTH_RATIO:
        return [Flag(index, "long", f"unusually long ({detail})", source, translated)]
    return []


def check_entry(
    index: int,
    source: str,
    translated: str,
    terms: Sequence[glossary.Term] = (),
    ok: bool = True,
    reason: str | None = None,
) -> list[Flag]:
    """Every flag one entry deserves, in KINDS order."""
    if not ok:
        message = "left in English" + (f" ({reason})" if reason else "")
        return [Flag(index, "failed", message, source, translated)]

    if content_length(translated) == 0:
        return [Flag(index, "empty", "the translation is empty", source, translated)]
    if llm.is_non_verbal(source):
        return []

    flags: list[Flag] = []

    source_lines = layout.line_count(source)
    translated_lines = layout.line_count(translated)
    if source_lines != translated_lines:
        flags.append(
            Flag(
                index,
                "line_count",
                f"{source_lines} line(s) in the source, {translated_lines} in the translation",
                source,
                translated,
            )
        )

    source_dashes = layout.dash_count(source)
    translated_dashes = layout.dash_count(translated)
    if translated_dashes < source_dashes:
        flags.append(
            Flag(
                index,
                "dashes_lost",
                f"{source_dashes - translated_dashes} speaker dash line(s) lost",
                source,
                translated,
            )
        )

    tag_problem = tag_mismatch(source, translated)
    if tag_problem:
        flags.append(Flag(index, "tag_mismatch", tag_problem, source, translated))

    flags.extend(glossary_flags(index, source, translated, terms))
    flags.extend(leftover_english(index, source, translated, terms))
    flags.extend(length_flags(index, source, translated))
    return flags


def verify_file(translated: Verifiable, terms: Sequence[glossary.Term] = ()) -> Verification:
    """Check every entry in a finished file. Never raises on bad content."""
    verification = Verification()
    results = sorted(translated.results, key=lambda result: result.index)

    for result in results:
        source = translated.sources.get(result.index)
        if source is None:
            log.warning(
                "no source text for entry [%d]; not verified", result.index
            )
            continue
        verification.checked += 1
        verification.extend(
            check_entry(
                result.index,
                source,
                result.text,
                terms,
                ok=getattr(result, "ok", True),
                reason=getattr(result, "reason", None),
            )
        )

    verification.flags.sort(key=lambda flag: (flag.index, KINDS.index(flag.kind)))
    return verification
