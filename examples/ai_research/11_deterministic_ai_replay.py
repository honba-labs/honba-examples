"""ai_research/11_deterministic_ai_replay: cryptographic prompt replayer.

Replays historical AI decisions from a SHA-256 content-addressed prompt cache
with no external API calls. Every prompt is addressed by ``sha256(prompt)``;
replay re-verifies each journal row's digest against the cached prompt so a
decision can be reproduced without calling the model again.

Offline by default: decisions are generated deterministically from a seed, so
the same seed always yields the same journal. A real provider
(``honba.ai.llm.provider.LlmPort``) can be wired in behind an explicit flag.

Run::

    python examples/ai_research/11_deterministic_ai_replay.py --decisions 10 --out replay.json

Pitfalls::

    - Content addressing means a tampered prompt (same digest, different text)
      fails verification: ``verify_journal`` flags it as a problem.
    - The cache never re-prompts: once a digest is stored, replay returns the
      cached response without touching a model. This is the offline guarantee.
    - Journal v1 NDJSON is append-only; replay is read-only and does not mutate
      the cache or the file.
    - Determinism rests on the seed: two runs with the same seed produce
      byte-identical journals; different seeds produce different prompts and
      therefore different digests.
    - No external model is consulted here -- replay is purely local and
      deterministic, so it cannot confirm a decision the model never actually
      made. Treat a clean replay as reproducibility evidence, not as strategy
      validation.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

from honba_examples.ai_replay import JournalEntry, PromptCache, prompt_digest, verify_journal

DEFAULT_DECISIONS = 10
DEFAULT_SEED = 0

__all__ = ["DEFAULT_DECISIONS", "DEFAULT_SEED", "main", "run"]


def _scripted_decisions(decisions: int, seed: int) -> list[dict[str, Any]]:
    """Generate ``decisions`` deterministic AI-style decisions from ``seed``."""
    rng = random.Random(seed)
    regimes = ("trend", "range", "breakout")
    out: list[dict[str, Any]] = []
    for i in range(decisions):
        prompt = (
            f"decision-{i:04d}: propose a mean-reversion entry on NIFTY "
            f"under a {rng.choice(regimes)} regime at "
            f"{rng.randint(21000, 25000)}"
        )
        out.append(
            {
                "prompt": prompt,
                "response": json.dumps(
                    {
                        "decision": rng.choice(["approve", "size_down", "veto"]),
                        "decided_quantity": rng.randint(1, 100),
                        "rationale": f"seeded rationale {i}",
                        "confidence": round(rng.random(), 2),
                    }
                ),
                "rationale": f"seeded rationale {i}",
                "confidence": round(rng.random(), 2),
            }
        )
    return out


def run(
    decisions: int = DEFAULT_DECISIONS,
    seed: int = DEFAULT_SEED,
    *,
    journal_path: Path | str | None = None,
) -> dict[str, Any]:
    """Generate ``decisions`` scripted AI decisions, cache them by SHA-256
    digest, write the journal (when ``journal_path`` is given) and replay
    verify it offline."""
    if decisions < 1:
        raise ValueError(f"--decisions must be >= 1, got {decisions}")

    cache = PromptCache()
    script = _scripted_decisions(decisions, seed)

    entries: list[JournalEntry] = []
    for item in script:
        prompt = item["prompt"]
        digest = cache.store(
            prompt,
            item["response"],
            item["rationale"],
            item["confidence"],
        )
        entries.append(
            JournalEntry(digest, prompt, item["response"], item["rationale"], item["confidence"])
        )

    # Persist the journal to disk when a path is given.
    if journal_path is not None:
        journal_path = Path(journal_path)
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with journal_path.open("w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry.to_record(), default=str) + "\n")

    # Replay-verify: every cached digest must reproduce its prompt, offline.
    problems: list[str] = []
    if journal_path is not None:
        cached, replayed, problems = verify_journal(cache, journal_path)
    else:
        cached = replayed = len(entries)
        for entry in entries:
            if prompt_digest(entry.prompt) != entry.digest:
                problems.append(f"digest mismatch for {entry.prompt!r}")
            elif entry.digest not in cache:
                problems.append(f"digest {entry.digest} not in cache")
            else:
                cached_prompt, _ = cache.get(entry.digest)
                if cached_prompt != entry.prompt:
                    problems.append(f"cached prompt differs for {entry.prompt!r}")
        cached = replayed = len(entries) - len(problems)

    replay = {
        "cached": cached,
        "replayed": replayed,
        "all_verified": len(problems) == 0,
        "problems": problems,
    }
    config = {
        "decisions": decisions,
        "seed": seed,
        "journal_path": str(journal_path) if journal_path is not None else None,
    }
    return {
        "replay": replay,
        "config": config,
        "note": "No external model: replay driven by a seeded script, no live API calls",
        "journal": [e.to_record() for e in entries],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--decisions", type=int, default=DEFAULT_DECISIONS, help="number of decisions to replay"
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="deterministic seed")
    parser.add_argument(
        "--journal-path", type=Path, default=None, help="write the NDJSON journal here"
    )
    parser.add_argument("--out", type=Path, default=None, help="also write the result JSON here")
    args = parser.parse_args(argv)

    result = run(decisions=args.decisions, seed=args.seed, journal_path=args.journal_path)
    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
