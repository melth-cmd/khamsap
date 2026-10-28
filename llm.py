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
import os
import re
from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

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


class TransportError(LLMError):
    """Network failure or timeout; worth retrying."""


class RateLimited(LLMError):
    """HTTP 429. `retry_after` is the server's hint in seconds, if given."""

    def __init__(self, message: str, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__(message)


class AuthError(LLMError):
    """Missing or rejected API key; never worth retrying."""


class BadResponse(LLMError):
    """The reply could not be parsed at all."""


# --- wire format ------------------------------------------------------------


@dataclass(frozen=True)
class DecodeIssue:
    """A problem found while parsing a reply."""

    kind: str  # "duplicate" | "empty"
    index: int | None
    message: str


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
- Follow the glossary exactly: keep terms marked "keep", use the given spelling otherwise.
- Translate song lyrics for meaning; keep the music markers.
- Profanity: translate at equivalent strength; do not soften it.
- Idioms: translate the meaning, not the words.
- On-screen text and sound effects: translate them, keep the brackets.
- Never merge or split entries. One input ID = one output ID.
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


def build_messages(
    batch: Batch, bible: str = "", glossary: str = ""
) -> list[dict[str, str]]:
    """Build the chat messages for one batch."""
    sections: list[str] = []

    if bible.strip():
        sections.append("[STORY BIBLE]\n" + bible.strip())
    if glossary.strip():
        sections.append("[GLOSSARY]\n" + glossary.strip())
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

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
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

    def complete(self, messages: Sequence[dict[str, str]]) -> Completion: ...


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
    """Pull Retry-After off a failed response, if the server sent one."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


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

    def complete(self, messages: Sequence[dict[str, str]]) -> Completion:
        import openai

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=list(messages),
                temperature=self.temperature,
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
