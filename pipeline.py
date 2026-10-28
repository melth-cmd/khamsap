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
import cache as cache_module
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
    cache: cache_module.Cache | None = None,
    limit: int | None = None,
    range_: str | tuple[int, int] | None = None,
    **runner_kwargs,
) -> TranslatedFile:
    """Translate a whole file, batch by batch, keeping timestamps intact.

    With a cache, entries already translated are not sent again; they still
    count as context, so resumed batches see the same Thai they would have.

    With `limit` or `range_`, only those entries are translated and written.
    Context is still drawn from the whole file, so a slice is translated with
    the surrounding lines in view.
    """
    prepared = prepare(subtitles, strip_hi=strip_hi)
    result = TranslatedFile(dropped=list(prepared.dropped))

    if not prepared.entries:
        log.info("Nothing to translate")
        return result

    selected = batcher.select_range(prepared.entries, limit=limit, range_=range_)
    selected_indexes = {entry.index for entry in selected}
    if not selected:
        log.warning("Selection is empty; nothing to translate")
        return result
    if len(selected) < len(prepared.entries):
        log.info(
            "Translating %d of %d entr%s", len(selected), len(prepared.entries),
            "y" if len(prepared.entries) == 1 else "ies",
        )

    translated: dict[int, str] = {}
    pending: list[SourceEntry] = []

    for entry in prepared.entries:
        if entry.index not in selected_indexes:
            continue
        hit = cache.get(entry.index, entry.text) if cache is not None else None
        if hit is None:
            pending.append(entry)
            continue
        translated[entry.index] = hit
        result.results.append(
            EntryResult(index=entry.index, text=hit, ok=True, from_cache=True)
        )

    if cache is not None:
        cached = len(selected) - len(pending)
        log.info(
            "%d entr%s already cached, %d to translate",
            cached,
            "y" if cached == 1 else "ies",
            len(pending),
        )

    source_text = {entry.index: entry.text for entry in prepared.entries}
    batch_count = -(-len(pending) // size)

    for number in range(1, batch_count + 1):
        # Rebuilt each round so context-before picks up Thai produced so far.
        batch = batcher.make_batches(
            prepared.entries,
            selection=pending,
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
            result.results.append(
                EntryResult(
                    index=entry_result.index,
                    text=restored,
                    ok=entry_result.ok,
                    reason=entry_result.reason,
                    attempts=entry_result.attempts,
                )
            )
            if entry_result.ok:
                translated[entry_result.index] = restored
                if cache is not None:
                    cache.put(
                        entry_result.index, source_text[entry_result.index], restored
                    )

        # Saved per batch: an interrupted run loses at most this batch.
        if cache is not None:
            cache.save()

    result.results.sort(key=lambda entry: entry.index)
    result.subtitles = _rebuild(subtitles, result, prepared, selected_indexes)
    return result


def _rebuild(
    subtitles: Sequence[srt.Subtitle],
    result: TranslatedFile,
    prepared: Prepared,
    included: set[int],
) -> list[srt.Subtitle]:
    """Put translated text back into the original entries, timestamps and all.

    Entries outside the selection (--range / --limit) are left out entirely, so
    a slice produces a file holding just that slice.
    """
    by_index = {entry.index: entry for entry in result.results}
    rebuilt: list[srt.Subtitle] = []

    for subtitle in subtitles:
        if subtitle.index in prepared.dropped or subtitle.index not in included:
            continue
        entry = by_index.get(subtitle.index)
        if entry is None:  # pragma: no cover - every selected entry has a result
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


# Rough character-per-token ratios. English input runs about 4; Thai output is
# token-expensive, so it gets a lower number. Good enough for --dry-run, which
# only needs the order of magnitude.
CHARS_PER_PROMPT_TOKEN = 3.2
CHARS_PER_COMPLETION_TOKEN = 2.2
PROMPT_OVERHEAD_CHARS = 40  # section labels and [ID] prefixes


@dataclass
class Estimate:
    """What a run would cost, without making it."""

    entries: int = 0
    batches: int = 0
    dropped: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def estimate(
    subtitles: Sequence[srt.Subtitle],
    strip_hi: bool = False,
    size: int = 60,
    context_before: int = 4,
    context_after: int = 2,
    limit: int | None = None,
    range_: str | tuple[int, int] | None = None,
    input_usd_per_mtok: float = 0.0,
    output_usd_per_mtok: float = 0.0,
) -> Estimate:
    """Predict batch count, tokens and cost for a run, with no API calls."""
    prepared = prepare(subtitles, strip_hi=strip_hi)
    selected = batcher.select_range(prepared.entries, limit=limit, range_=range_)
    batches = batcher.make_batches(
        prepared.entries,
        selection=selected,
        size=size,
        context_before=context_before,
        context_after=context_after,
    )

    prompt_chars = completion_chars = 0
    for batch in batches:
        prompt_chars += len(llm.SYSTEM_PROMPT) + PROMPT_OVERHEAD_CHARS
        prompt_chars += sum(len(item.text) for item in batch.items)
        prompt_chars += sum(len(line.text) + len(line.thai or "") for line in batch.before)
        prompt_chars += sum(len(line.text) for line in batch.after)
        completion_chars += sum(len(item.text) for item in batch.items)

    prompt_tokens = int(prompt_chars / CHARS_PER_PROMPT_TOKEN)
    completion_tokens = int(completion_chars / CHARS_PER_COMPLETION_TOKEN)
    cost = (
        prompt_tokens / 1e6 * input_usd_per_mtok
        + completion_tokens / 1e6 * output_usd_per_mtok
    )
    return Estimate(
        entries=len(selected),
        batches=len(batches),
        dropped=len(prepared.dropped),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost,
    )


def translate_file(
    client: llm.ChatClient,
    source: str | Path,
    output: str | Path | None = None,
    encoding: str = "utf-8",
    strip_hi: bool = False,
    use_cache: bool = True,
    model: str = "",
    **kwargs,
) -> tuple[TranslatedFile, Path]:
    """Read, translate and write one file. Returns (result, output path)."""
    source = Path(source)
    loaded = srtio.load_srt(source)
    if loaded.skipped:
        log.warning("%s: %d malformed block(s) were skipped", source.name, loaded.skipped)

    cache = None
    if use_cache:
        cache = cache_module.Cache(
            srtio.sidecar_path(source, ".cache.json"), model=model
        ).load()

    result = translate_subtitles(
        client, loaded.subtitles, strip_hi=strip_hi, cache=cache, **kwargs
    )
    target = Path(output) if output is not None else srtio.output_path_for(source)
    srtio.write_srt(target, result.subtitles, encoding=encoding)
    return result, target
