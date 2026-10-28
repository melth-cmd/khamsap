"""OpenRouter client and the tagged wire format.

Text goes out as one line per entry:

    [12] Hello there.
    How are you?
    [13] Fine, thanks.

and comes back the same way. Anything that is not a line starting with [N] is a
continuation of the entry above it, so multi-line subtitles survive the trip.

Transport failures are translated into typed errors. Nothing here retries:
the caller (step 5) decides what is worth re-requesting. The API key is read
from the environment only, and never logged, stored or rendered.
"""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Mapping, Protocol, Sequence

import tags
from batcher import Batch, ContextLine, SourceEntry

log = logging.getLogger(__name__)

PROMPT_VERSION = 1
DEFAULT_KEY_ENV = "OPENROUTER_API_KEY"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# A new entry starts at "[12]"; everything else continues the one before it.
_ENTRY_LINE_RE = re.compile(r"^\[(\d+)\]\s?(.*)$")


# --- errors -----------------------------------------------------------------


class LLMError(Exception):
    """Base class for anything that went wrong talking to the model."""


class RetryableError(LLMError):
    """A failure that may go away by itself, so trying again is worth it.

    `retry_after` is the server's hint in seconds, if it sent one.
    """

    def __init__(self, message: str, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__(message)


class TransportError(RetryableError):
    """Network failure, timeout or a 5xx from the server; worth retrying."""


class RateLimited(RetryableError):
    """HTTP 429."""


class AuthError(LLMError):
    """Missing or rejected API key; never worth retrying."""


class BadResponse(LLMError):
    """The reply could not be parsed at all."""


# --- wire format ------------------------------------------------------------


@dataclass(frozen=True)
class Issue:
    """A problem found in a reply: parsing, identity or language."""

    kind: str
    # parsing:  "duplicate" | "empty"
    # identity: "missing" | "extra"
    # language: "missing_thai" | "foreign_script"
    # tags:     "tag_mismatch"
    # other:    "bad_response"
    index: int | None
    message: str


# Name kept for the parsing-only case, which reads better at the call site.
DecodeIssue = Issue


@dataclass
class Decoded:
    entries: dict[int, str] = field(default_factory=dict)
    issues: list[DecodeIssue] = field(default_factory=list)


def encode_entries(items: Sequence[SourceEntry]) -> str:
    """Render entries as tagged lines, one per entry."""
    return "\n".join(f"[{entry.index}] {entry.text}" for entry in items)


def decode_entries(text: str) -> Decoded:
    """Parse a tagged reply into {index: text}.

    Unprefixed lines continue the previous entry. Duplicate indexes keep the
    first occurrence and are reported, never merged.
    """
    decoded = Decoded()
    current: int | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current is None:
            return
        content = "\n".join(buffer).strip()
        if not content:
            decoded.issues.append(
                DecodeIssue("empty", current, f"entry [{current}] came back empty")
            )
        decoded.entries[current] = content

    for line in text.splitlines():
        match = _ENTRY_LINE_RE.match(line)
        if match:
            flush()
            index = int(match.group(1))
            if index in decoded.entries:
                decoded.issues.append(
                    DecodeIssue("duplicate", index, f"entry [{index}] appeared twice")
                )
                # Keep the first copy; swallow the second so it cannot overwrite.
                current, buffer = None, []
                continue
            current, buffer = index, [match.group(2)]
        elif current is not None:
            buffer.append(line)

    flush()

    if not decoded.entries:
        raise BadResponse("reply contained no [N] tagged lines")
    return decoded


# --- language checking ------------------------------------------------------

# Scripts the model might slip into. Thai is the target; the rest are leaks
# unless the English source already contained them (an on-screen sign, a name).
_SCRIPT_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    "thai": ((0x0E00, 0x0E7F),),
    "han": ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF)),
    "kana": ((0x3040, 0x30FF),),
    "hangul": ((0x1100, 0x11FF), (0xAC00, 0xD7AF)),
    "cyrillic": ((0x0400, 0x04FF),),
}

# Words that carry no meaning to translate: "Mm-hmm", "Shh", "Ah.".
_NON_VERBAL_WORDS = {
    "mm", "hmm", "hm", "uh", "ah", "oh", "ooh", "shh", "sh", "ha", "hey",
    "wow", "argh", "ugh", "er", "erm", "eh", "huh", "tsk", "psst", "phew",
    "ow", "ouch", "mhm", "mmm", "uhh", "ahh", "ohh", "hah", "heh", "huh",
}

_STRAY_MARKUP_RE = re.compile(r"<[^>]*>|\{\\[^}]*\}|\{[^}]*\}|\[[^\]]*\]|\([^)]*\)|[♪♫♬♩]")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def scripts_in(text: str) -> set[str]:
    """Which writing systems appear in `text`."""
    found: set[str] = set()
    for character in text:
        point = ord(character)
        for name, ranges in _SCRIPT_RANGES.items():
            if any(low <= point <= high for low, high in ranges):
                found.add(name)
                break
    return found


def has_thai(text: str) -> bool:
    return "thai" in scripts_in(text)


def is_non_verbal(text: str) -> bool:
    """True for text with nothing to translate: interjections, sounds, symbols.

    Deliberately conservative: an uncertain case is treated as verbal, so a
    real line never slips through untranslated.
    """
    stripped = _STRAY_MARKUP_RE.sub(" ", text)
    words = [word.lower().strip("'") for word in _WORD_RE.findall(stripped)]
    if not words:
        return True
    return all(
        word in _NON_VERBAL_WORDS or len(set(word)) == 1 or re.fullmatch(r"(?:ha)+|(?:mm)+|(?:h+m+)|(?:a+h+)|(?:o+h+)", word)
        for word in words
    )


def validate(batch: Batch, decoded: Decoded) -> list[Issue]:
    """Check a parsed reply against the batch that asked for it.

    Returns every problem found, so the caller can retry exactly the affected
    entries. An empty list means the reply is safe to ship.
    """
    expected = {entry.index: entry.text for entry in batch.items}
    issues: list[Issue] = list(decoded.issues)

    for index, source in expected.items():
        if index not in decoded.entries:
            issues.append(
                Issue("missing", index, f"entry [{index}] is missing from the reply")
            )
            continue

        text = decoded.entries[index]
        if not text.strip():
            continue  # decode already reported it as empty

        if not is_non_verbal(source):
            leaked = scripts_in(text) - scripts_in(source) - {"thai"}
            if leaked:
                issues.append(
                    Issue(
                        "foreign_script",
                        index,
                        f"entry [{index}] contains {'/'.join(sorted(leaked))}: {text[:40]!r}",
                    )
                )
            if not has_thai(text):
                issues.append(
                    Issue(
                        "missing_thai",
                        index,
                        f"entry [{index}] has no Thai: {text[:40]!r}",
                    )
                )

        for tag_issue in tags.check(source, text):
            issues.append(
                Issue("tag_mismatch", index, f"entry [{index}]: {tag_issue.message}")
            )

    for index in decoded.entries:
        if index not in expected:
            issues.append(
                Issue("extra", index, f"entry [{index}] was not requested")
            )

    return issues


# --- prompt -----------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a professional subtitle translator working from English into Thai.

Output only the translated lines, in the same order, each keeping its [ID] prefix.
No notes, no explanations, no romanisation, no commentary.

Rules:
- Natural, casual spoken Thai. Not literary Thai.
- Subtitles are read quickly: be concise.
- Thai subtitle convention: spaces instead of commas and periods.
- Use the story bible for pronouns, particles and register per speaker.
- Follow the glossary exactly: a term marked "keep" stays in English; for "transliterate"
  or "translate", use the Thai given after the colon every time.
- Translate song lyrics for meaning; keep the music markers.
- Profanity: translate at equivalent strength; do not soften it.
- Idioms: translate the meaning, not the words.
- On-screen text and sound effects: translate them, keep the brackets.
- Never merge or split entries. One input ID = one output ID.
- Preserve the line breaks inside an entry: if the source entry has two lines, the
  translation must have two lines. Never merge lines, and never add a line that the
  source did not have.
- Lines under CONTEXT are for understanding only and must not appear in your output.
- Placeholders such as {0} are formatting tags: keep every one, exactly once, in place.
"""


def _format_context(lines: Sequence[ContextLine], include_thai: bool) -> str:
    rendered = []
    for line in lines:
        rendered.append(f"[{line.index}] {line.text}")
        if include_thai and line.thai:
            rendered.append(f"TH: {line.thai}")
    return "\n".join(rendered)


# Added to the system prompt for --fix, where every line was flagged by the
# verification pass. It repeats the rules that matter for a flagged line,
# because a plain re-ask tends to produce the same mistake again.
FIX_RULES = """\
This is a correction pass. A checker flagged every line below in an earlier attempt,
so translate each of them again from scratch, with extra care:
- Every entry must be natural Thai. No other language, and no English except glossary
  words marked "keep".
- Use the glossary spelling exactly as given for every term that appears.
- Keep every [ID] exactly once, in the same order, and keep each entry's number of lines.
- Keep every placeholder such as {0} exactly once, in its place.
- Do not repeat the earlier wording: write a fresh translation of the source."""


# Added to the system prompt for --review. The same wire format comes back, so
# a suggestion is validated exactly like a translation.
REVIEW_RULES = """\
This is a review pass. The checker flagged the lines below, and their current translations
are shown with each one. For every entry, either write a better Thai line or repeat the
current one unchanged if it is already right. A current line still in English means the
earlier attempt failed: propose a Thai line for it.
- Output every [ID] exactly once, in the same order, with the same number of lines.
- Keep placeholders such as {0} exactly once, in their place.
- Thai only, except glossary words marked "keep"; use the glossary spelling exactly.
- Keep each line short enough to read at a glance."""


def build_messages(
    batch: Batch,
    bible: str = "",
    glossary: str = "",
    strict: bool = False,
    current: Mapping[int, str] | None = None,
) -> list[dict[str, str]]:
    """Build the chat messages for one batch.

    `strict` adds the correction rules used by --fix for flagged lines. `current`
    maps entry index -> the translation already made; it is shown to the model
    and switches the prompt to the review rules (--review).
    """
    sections: list[str] = []

    if bible.strip():
        sections.append("[STORY BIBLE]\n" + bible.strip())
    if glossary.strip():
        sections.append("[GLOSSARY]\n" + glossary.strip())
    if current:
        shown = [
            f"[{entry.index}] {current[entry.index]}"
            for entry in batch.items
            if entry.index in current
        ]
        if shown:
            sections.append(
                "[CURRENT TRANSLATIONS - flagged, improve or repeat]\n" + "\n".join(shown)
            )
    if batch.before:
        sections.append(
            "[CONTEXT BEFORE - do not translate]\n"
            + _format_context(batch.before, include_thai=True)
        )
    if batch.after:
        sections.append(
            "[CONTEXT AFTER - do not translate]\n"
            + _format_context(batch.after, include_thai=False)
        )
    sections.append("[TRANSLATE]\n" + encode_entries(batch.items))

    rules = REVIEW_RULES if current else (FIX_RULES if strict else "")
    system = SYSTEM_PROMPT + (f"\n\n{rules}" if rules else "")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


# --- client -----------------------------------------------------------------


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage = field(default_factory=Usage)


class ChatClient(Protocol):
    """What the rest of the program needs from a model."""

    def complete(
        self, messages: Sequence[dict[str, str]], temperature: float | None = None
    ) -> Completion: ...


# Anything shaped like a credential. Used to keep secrets out of error text
# when someone puts a key where a variable name belongs.
_SECRET_SHAPED_RE = re.compile(r"^sk-(or-)?[A-Za-z0-9_-]{16,}$")


def load_api_key(key_env: str = DEFAULT_KEY_ENV) -> str:
    """Read the API key from the environment. Never logs it."""
    if _SECRET_SHAPED_RE.match(key_env.strip()):
        # The user pasted the key into key_env. Say so without repeating it.
        raise AuthError(
            "config key_env holds something that looks like an API key.\n"
            "key_env must be the NAME of the variable, e.g. \"OPENROUTER_API_KEY\".\n"
            "Put the key itself in your shell environment instead."
        )

    key = os.environ.get(key_env, "").strip()
    if not key:
        raise AuthError(
            f"{key_env} is not set. Export it in your shell, e.g.\n"
            f"  export {key_env}=your-key-here"
        )
    return key


def _retry_after(exc: Exception) -> float | None:
    """Pull Retry-After off a failed response, if the server sent a usable one.

    The header is either a number of seconds or an HTTP date. Anything that is
    not a finite, non-negative delay is ignored, so a bad header can never turn
    into a weird sleep or a crash.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after")
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("latin-1", "replace")

    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = None

    if seconds is None:
        try:
            when = parsedate_to_datetime(str(raw))
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()

    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


class OpenRouterClient:
    """OpenRouter over the OpenAI SDK, with SDK retries disabled."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        temperature: float = 0.25,
        timeout_s: float = 120,
        key_env: str = DEFAULT_KEY_ENV,
    ):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise LLMError("The openai package is not installed") from exc

        self.model = model
        self.temperature = temperature
        self._client = OpenAI(
            api_key=api_key or load_api_key(key_env),
            base_url=base_url,
            timeout=timeout_s,
            max_retries=0,  # retries are decided by the caller, not the SDK
        )

    def complete(
        self, messages: Sequence[dict[str, str]], temperature: float | None = None
    ) -> Completion:
        """Send one completion request.

        `temperature` overrides the client default for this call only, which is
        how retries are made less random without rebuilding the client.
        """
        import openai

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=list(messages),
                temperature=self.temperature if temperature is None else temperature,
            )
        except openai.RateLimitError as exc:
            retry_after = _retry_after(exc)
            raise RateLimited(
                "rate limited by OpenRouter", retry_after=retry_after
            ) from None
        except openai.AuthenticationError as exc:
            raise AuthError("OpenRouter rejected the API key") from None
        except openai.APITimeoutError as exc:
            raise TransportError("request timed out") from None
        except openai.APIConnectionError as exc:
            raise TransportError(f"connection failed: {exc}") from None
        except openai.APIStatusError as exc:
            # 5xx and the "try again" 4xx codes are the gateway having a bad
            # moment, not a bad request: retry them like a timeout.
            if exc.status_code >= 500 or exc.status_code in {408, 409, 425}:
                raise TransportError(
                    f"OpenRouter returned HTTP {exc.status_code}",
                    retry_after=_retry_after(exc),
                ) from None
            raise LLMError(f"OpenRouter returned HTTP {exc.status_code}") from None

        choices = getattr(response, "choices", None) or []
        if not choices:
            raise BadResponse("reply contained no choices")
        text = choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        return Completion(
            text=text,
            usage=Usage(
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            ),
        )
