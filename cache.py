"""Per-entry JSON cache, so a rerun does not pay for work already done.

The file sits next to the subtitles (Movie.cache.json) and is rewritten after
each batch, atomically, so an interrupted run loses at most the batch it was on.

What makes an entry reusable:
  - same version of this file format
  - same model
  - same prompt version
  - same source text for that index

Changing the model or the prompt invalidates everything, because the Thai it
produced is no longer the Thai this prompt would produce. Editing one line of a
subtitle only invalidates that line.

Step 11 replaces the blunt "same model + prompt" check with a per-entry key that
also accounts for the glossary entries that applied to each line.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import llm
import srtio

log = logging.getLogger(__name__)

FORMAT_VERSION = 1


@dataclass(frozen=True)
class CacheEntry:
    """One remembered translation."""

    source: str  # the protected source text the model was given
    text: str  # the finished Thai, tags already restored


class Cache:
    """A JSON file mapping subtitle index to a remembered translation."""

    def __init__(self, path: str | Path, model: str = "", prompt_version: int = llm.PROMPT_VERSION):
        self.path = Path(path)
        self.model = model
        self.prompt_version = prompt_version
        self._entries: dict[int, CacheEntry] = {}
        self._dirty = False

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, index: int) -> bool:
        return index in self._entries

    def load(self) -> "Cache":
        """Read the file if it exists. A broken cache is a warning, never fatal."""
        if not self.path.exists():
            return self

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Ignoring unreadable cache %s: %s", self.path.name, exc)
            return self

        if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
            log.warning("Ignoring cache %s: unknown format", self.path.name)
            return self

        if data.get("model") != self.model or data.get("prompt_version") != self.prompt_version:
            log.info(
                "Cache %s came from a different model or prompt, ignoring it",
                self.path.name,
            )
            return self

        raw_entries = data.get("entries") or {}
        if not isinstance(raw_entries, dict):
            log.warning("Ignoring cache %s: entries are not a table", self.path.name)
            return self

        for key, value in raw_entries.items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                log.debug("Ignoring cache key %r", key)
                continue
            if not isinstance(value, dict):
                continue
            source, text = value.get("source"), value.get("text")
            if isinstance(source, str) and isinstance(text, str):
                self._entries[index] = CacheEntry(source=source, text=text)

        log.info("Loaded %d cached entr%s from %s",
                 len(self._entries), "y" if len(self._entries) == 1 else "ies",
                 self.path.name)
        return self

    def get(self, index: int, source: str) -> str | None:
        """The cached Thai for this entry, or None if it is missing or stale."""
        entry = self._entries.get(index)
        if entry is None or entry.source != source:
            return None
        return entry.text

    def put(self, index: int, source: str, text: str) -> None:
        existing = self._entries.get(index)
        if existing is not None and existing.source == source and existing.text == text:
            return
        self._entries[index] = CacheEntry(source=source, text=text)
        self._dirty = True

    def save(self) -> None:
        """Write the cache atomically, but only if something changed."""
        if not self._dirty:
            return
        payload = {
            "version": FORMAT_VERSION,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "entries": {
                str(index): {"source": entry.source, "text": entry.text}
                for index, entry in sorted(self._entries.items())
            },
        }
        data = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
        srtio._atomic_write(self.path, data)
        self._dirty = False
