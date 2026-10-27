"""SHA-256 prompt cache and decision-replay helpers for Phase 6.

Deterministic, offline helpers used by ``examples/ai_research/11_deterministic_ai_replay.py``:

* ``prompt_digest`` - SHA-256 of a prompt; the content address of the cache.
* ``PromptCache`` - content-addressed prompt cache (digest -> prompt/response)
  with an append-only journal of ``JournalEntry`` records (Journal v1 compatible).
* ``verify_journal`` - replays a journal NDJSON file and re-verifies every
  digest against the cache.

Nothing here calls a model or the network: a cached prompt is never re-sent to
an LLM, and a journal can be replayed from disk alone.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def prompt_digest(prompt: str) -> str:
    """Content address of a prompt: SHA-256 of its UTF-8 encoding."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One journal row: the prompt digest, the prompt, the model's response,
    and the rationale/confidence the example recorded with it."""

    digest: str
    prompt: str
    response: str
    rationale: str
    confidence: float

    def to_record(self) -> dict[str, Any]:
        """Journal v1-compatible NDJSON record."""
        return {
            "type": "llm_decision",
            "digest": self.digest,
            "prompt": self.prompt,
            "response": self.response,
            "rationale": self.rationale,
            "confidence": self.confidence,
        }


class PromptCache:
    """Content-addressed prompt cache.

    ``store`` records ``(prompt, response)`` under ``sha256(prompt)`` and
    appends a journal entry.  A digest can be looked up with ``get``; the
    cache never re-prompts a model for a digest it already holds.
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, str]] = {}
        self.journal: list[JournalEntry] = []

    def store(
        self,
        prompt: str,
        response: str,
        rationale: str = "",
        confidence: float = 0.0,
    ) -> str:
        digest = prompt_digest(prompt)
        self._cache[digest] = (prompt, response)
        self.journal.append(JournalEntry(digest, prompt, response, rationale, confidence))
        return digest

    def get(self, digest: str) -> tuple[str, str] | None:
        return self._cache.get(digest)

    def __contains__(self, digest: str) -> bool:
        return digest in self._cache


def verify_journal(cache: PromptCache, path: Path) -> tuple[int, int, list[str]]:
    """Replay a Journal v1 NDJSON file.

    Returns ``(cached, replayed, problems)``: the number of rows whose digest
    matches the prompt and exists in the cache, the number whose cached prompt
    matches the recorded prompt exactly, and any digest/prompt problems found.
    A row is only "replayed" when both the digest verifies and the cached
    prompt matches the recorded prompt -- i.e. the decision can be reproduced
    without calling the model again.
    """
    problems: list[str] = []
    cached = replayed = 0
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            digest = record.get("digest", "")
            prompt = record.get("prompt", "")
            if prompt_digest(prompt) != digest:
                problems.append(f"line {lineno}: digest does not match prompt")
                continue
            if digest not in cache:
                problems.append(f"line {lineno}: digest not in prompt cache")
                continue
            cached += 1
            cached_prompt, _ = cache.get(digest)
            if cached_prompt != prompt:
                problems.append(f"line {lineno}: cached prompt differs from journal")
                continue
            replayed += 1
    return cached, replayed, problems


__all__ = ["JournalEntry", "PromptCache", "prompt_digest", "verify_journal"]
