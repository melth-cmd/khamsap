"""The whole job for one file: subtitles in, translated subtitles out.

Order of operations matters here:

  parse -> (optional HI strip) -> protect tags -> batch -> translate
        -> restore tags -> rebuild subtitles -> write

Tags are protected before batching so the model never sees markup, and restored
afterwards from the protected copy, so a dropped placeholder can never silently
corrupt the output.

Entries dropped by HI stripping are left out of the batches and out of the
finished file, which is why the output may have fewer rows than the source.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

import srt

import batcher
import hi
import llm
import runner
import srtio
import tags
from batcher import SourceEntry
from llm import Usage
from runner import EntryResult

log = logging.getLogger(__name__)

# A letter or digit; placeholders and punctuation do not count as content.
_WORD_CHAR_RE = re.compile(r"[^\W_]", re.UNICODE)


def _has_words(text: str) -> bool:
    """True if anything translatable is left once placeholders are removed."""
    return bool(_WORD_CHAR_RE.search(tags._PLACEHOLDER_RE.sub("", text)))


@dataclass
class Prepared:
    """Source entries ready for batching, plus what is needed to undo it."""

    entries: list[SourceEntry] = field(default_factory=list)
    protected: dict[int, tags.ProtectedText] = field(default_factory=dict)
    dropped: list[int] = field(default_factory=list)


@dataclass
class TranslatedFile:
    """The finished subtitles and what happened along the way."""

    subtitles: list[srt.Subtitle] = field(default_factory=list)
    results: list[EntryResult] = field(default_factory=list)
    dropped: list[int] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)

    @property
    def failures(self) -> list[EntryResult]:
        return [result for result in self.results if not result.ok]

    @property
    def translated(self) -> dict[int, str]:
        return {result.index: result.text for result in self.results}


def prepare(subtitles: Sequence[srt.Subtitle], strip_hi: bool = False) -> Prepared:
    """Turn subtitles into text-only entries, optionally dropping HI cues.

    Entries with nothing left to translate are recorded as dropped rather than
    sent as empty strings, which would fail validation forever.
    """
    prepared = Prepared()
    for subtitle in subtitles:
        text = subtitle.content
        if strip_hi:
            cleaned = hi.strip_hi(text)
            if cleaned.dropped:
                prepared.dropped.append(subtitle.index)
                continue
            text = cleaned.text

        protected = tags.protect(text)
        if not _has_words(protected.text):
            prepared.dropped.append(subtitle.index)
            continue

        prepared.protected[subtitle.index] = protected
        prepared.entries.append(SourceEntry(subtitle.index, protected.text))

    if prepared.dropped:
        log.info("%d entr%s dropped (nothing to translate)", len(prepared.dropped),
                 "y" if len(prepared.dropped) == 1 else "ies")
    return prepared


def translate_subtitles(
    client: llm.ChatClient,
    subtitles: Sequence[srt.Subtitle],
    strip_hi: bool = False,
    size: int = 60,
    context_before: int = 4,
    context_after: int = 2,
    bible: str = "",
    glossary: str = "",
    **runner_kwargs,
) -> TranslatedFile:
    """Translate a whole file, batch by batch, keeping timestamps intact."""
    prepared = prepare(subtitles, strip_hi=strip_hi)
    result = TranslatedFile(dropped=list(prepared.dropped))

    if not prepared.entries:
        log.info("Nothing to translate")
        return result

    translated: dict[int, str] = {}
    batch_count = -(-len(prepared.entries) // size)

    for number in range(1, batch_count + 1):
        # Rebuilt each round so context-before picks up Thai produced so far.
        batch = batcher.make_batches(
            prepared.entries,
            size=size,
            context_before=context_before,
            context_after=context_after,
            translated=translated,
        )[number - 1]

        log.info("Batch %d/%d: %d entries", number, batch_count, len(batch))
        batch_result: runner.BatchResult = runner.translate_batch(
            client, batch, bible=bible, glossary=glossary, **runner_kwargs
        )
        result.usage = Usage(
            prompt_tokens=result.usage.prompt_tokens + batch_result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens
            + batch_result.usage.completion_tokens,
        )

        for entry_result in batch_result.entries:
            restored = tags.restore(
                entry_result.text,
                prepared.protected[entry_result.index],
                strict=False,
            )
            restored_result = EntryResult(
                index=entry_result.index,
                text=restored,
                ok=entry_result.ok,
                reason=entry_result.reason,
                attempts=entry_result.attempts,
            )
            result.results.append(restored_result)
            if restored_result.ok:
                translated[entry_result.index] = restored

    result.results.sort(key=lambda entry: entry.index)
    result.subtitles = _rebuild(subtitles, result, prepared)
    return result


def _rebuild(
    subtitles: Sequence[srt.Subtitle], result: TranslatedFile, prepared: Prepared
) -> list[srt.Subtitle]:
    """Put translated text back into the original entries, timestamps and all."""
    by_index = {entry.index: entry for entry in result.results}
    rebuilt: list[srt.Subtitle] = []

    for subtitle in subtitles:
        if subtitle.index in prepared.dropped:
            continue
        entry = by_index.get(subtitle.index)
        if entry is None:  # pragma: no cover - every prepared entry has a result
            log.error("Entry [%d] has no result, keeping the original", subtitle.index)
            rebuilt.append(subtitle)
            continue
        rebuilt.append(
            srt.Subtitle(
                index=subtitle.index,
                start=subtitle.start,
                end=subtitle.end,
                content=entry.text,
                proprietary=subtitle.proprietary,
            )
        )
    return rebuilt


def translate_file(
    client: llm.ChatClient,
    source: str | Path,
    output: str | Path | None = None,
    encoding: str = "utf-8",
    strip_hi: bool = False,
    **kwargs,
) -> tuple[TranslatedFile, Path]:
    """Read, translate and write one file. Returns (result, output path)."""
    source = Path(source)
    loaded = srtio.load_srt(source)
    if loaded.skipped:
        log.warning("%s: %d malformed block(s) were skipped", source.name, loaded.skipped)

    result = translate_subtitles(client, loaded.subtitles, strip_hi=strip_hi, **kwargs)
    target = Path(output) if output is not None else srtio.output_path_for(source)
    srtio.write_srt(target, result.subtitles, encoding=encoding)
    return result, target
