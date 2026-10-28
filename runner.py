"""Translate one batch, validating each reply and retrying what failed.

Only the entries that actually failed are re-sent, so one Chinese leak in a
60-entry batch costs one small extra call rather than a full re-translation.

When the retry budget runs out the entry is left in English and marked ok=False
with a reason. English is a better failure than the wrong language: it is
visibly wrong and easy to fix by hand. Nothing here writes the report; it only
produces the data for it.

Sleep and randomness are injected so tests can assert delays without waiting.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol, Sequence

import llm
from batcher import Batch, SourceEntry
from llm import Completion, Issue, Usage

log = logging.getLogger(__name__)


@dataclass
class EntryResult:
    """What happened to one entry."""

    index: int
    text: str  # Thai on success, the original English on failure
    ok: bool = True
    reason: str | None = None
    attempts: int = 0
    from_cache: bool = False


@dataclass
class BatchResult:
    """What happened to one batch."""

    entries: list[EntryResult] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    calls: int = 0

    @property
    def failures(self) -> list[EntryResult]:
        return [entry for entry in self.entries if not entry.ok]

    def as_dict(self) -> dict[int, str]:
        return {entry.index: entry.text for entry in self.entries}


def _backoff(
    attempt: int,
    base_delay: float,
    max_delay: float,
    jitter: float,
    rand: Callable[[], float],
) -> float:
    """Exponential backoff with ±jitter, never longer than max_delay."""
    raw = min(base_delay * (2**attempt), max_delay)
    return max(0.0, raw * (1 + jitter * (2 * rand() - 1)))


def _add_usage(total: Usage, extra: Usage) -> Usage:
    return Usage(
        prompt_tokens=total.prompt_tokens + extra.prompt_tokens,
        completion_tokens=total.completion_tokens + extra.completion_tokens,
    )


def _mark_failed(
    result: BatchResult,
    batch: Batch,
    pending: Mapping[int, SourceEntry],
    attempts: Mapping[int, int],
    reasons: Mapping[int, str],
    fallback_reason: str,
) -> None:
    for index in pending:
        result.entries.append(
            EntryResult(
                index=index,
                text=pending[index].text,  # keep the English
                ok=False,
                reason=reasons.get(index, fallback_reason),
                attempts=attempts.get(index, 0),
            )
        )


def translate_batch(
    client: llm.ChatClient,
    batch: Batch,
    bible: str = "",
    glossary: str = "",
    max_retries: int = 3,
    retry_temperature: float = 0.1,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    jitter: float = 0.25,
    max_retry_after: float = 120.0,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
) -> BatchResult:
    """Translate `batch`, retrying failures up to `max_retries` times.

    The retry budget is shared: a validation failure, a 429 or a timeout all
    consume one retry. The first call uses the client's own temperature; every
    retry uses `retry_temperature` to make a repeat less likely.
    """
    result = BatchResult()
    if not batch.items:
        return result

    position = {entry.index: order for order, entry in enumerate(batch.items)}
    pending: dict[int, SourceEntry] = {entry.index: entry for entry in batch.items}
    attempts: dict[int, int] = {entry.index: 0 for entry in batch.items}
    reasons: dict[int, str] = {}
    retries_used = 0

    while pending:
        sub_batch = Batch(
            items=[entry for entry in batch.items if entry.index in pending],
            before=batch.before,
            after=batch.after,
        )
        messages = llm.build_messages(sub_batch, bible, glossary)
        temperature = None if retries_used == 0 else retry_temperature
        result.calls += 1

        try:
            completion = client.complete(messages, temperature=temperature)
        except llm.RateLimited as exc:
            if retries_used >= max_retries:
                log.error("giving up after rate limit: %s", exc)
                _mark_failed(result, batch, pending, attempts, reasons, "rate_limited")
                break
            delay = (
                min(exc.retry_after, max_retry_after)
                if exc.retry_after is not None
                else _backoff(retries_used, base_delay, max_delay, jitter, rand)
            )
            log.warning("rate limited, retrying in %.1fs", delay)
            sleep(delay)
            retries_used += 1
            continue
        except llm.TransportError as exc:
            if retries_used >= max_retries:
                log.error("giving up after transport failure: %s", exc)
                _mark_failed(result, batch, pending, attempts, reasons, "transport")
                break
            delay = _backoff(retries_used, base_delay, max_delay, jitter, rand)
            log.warning("transport failure, retrying in %.1fs", delay)
            sleep(delay)
            retries_used += 1
            continue

        result.usage = _add_usage(result.usage, completion.usage)
        for index in pending:
            attempts[index] += 1

        try:
            decoded = llm.decode_entries(completion.text)
            issues = llm.validate(sub_batch, decoded)
        except llm.BadResponse as exc:
            decoded = llm.Decoded()
            issues = [Issue("bad_response", None, str(exc))]

        broken = {issue.index for issue in issues if issue.index is not None}
        for index in list(pending):
            if index in decoded.entries and index not in broken:
                result.entries.append(
                    EntryResult(
                        index=index,
                        text=decoded.entries[index].strip(),
                        ok=True,
                        attempts=attempts[index],
                    )
                )
                pending.pop(index)

        for issue in issues:
            if issue.index in pending:
                reasons[issue.index] = issue.kind
            elif issue.index is None:
                for index in pending:
                    reasons[index] = issue.kind

        if not pending:
            break

        log.warning(
            "%d entr%s still failing (%s)",
            len(pending),
            "y" if len(pending) == 1 else "ies",
            ", ".join(sorted({reasons.get(i, "unknown") for i in pending})),
        )

        if retries_used >= max_retries:
            _mark_failed(result, batch, pending, attempts, reasons, "unknown")
            break
        retries_used += 1

    result.entries.sort(key=lambda entry: position[entry.index])
    return result
