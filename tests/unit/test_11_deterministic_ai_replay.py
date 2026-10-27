"""tests/unit/test_11_deterministic_ai_replay: cryptographic prompt replay."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from honba_examples.ai_replay import JournalEntry, verify_journal

_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "ai_research"
    / "11_deterministic_ai_replay.py"
)


@pytest.fixture(scope="module")
def example():
    spec = importlib.util.spec_from_file_location("deterministic_ai_replay", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_replay_verifies_every_decision(example, tmp_path: Path) -> None:
    journal = tmp_path / "journal.ndjson"
    result = example.run(decisions=40, seed=7, journal_path=journal)
    replay = result["replay"]
    assert replay["cached"] == 40
    assert replay["replayed"] == 40
    assert replay["all_verified"] is True
    assert replay["problems"] == []
    assert journal.is_file()
    lines = journal.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 40
    for line in lines:
        record = json.loads(line)
        assert set(record) == {
            "type",
            "digest",
            "prompt",
            "response",
            "rationale",
            "confidence",
        }
        assert record["type"] == "llm_decision"
        assert record["digest"] == hashlib.sha256(record["prompt"].encode()).hexdigest()


def test_tampered_prompt_is_detected(example, tmp_path: Path) -> None:
    from honba_examples.ai_replay import JournalEntry, PromptCache

    cache = PromptCache()
    entries: list[JournalEntry] = []
    for i in range(3):
        prompt = f"prompt number {i}"
        digest = cache.store(prompt, f"response {i}", f"rationale {i}", 0.9)
        entries.append(JournalEntry(digest, prompt, f"response {i}", f"rationale {i}", 0.9))
    journal = tmp_path / "tampered.ndjson"
    with journal.open("w", encoding="utf-8") as f:
        for i, entry in enumerate(entries):
            if i == 1:
                # Tamper: prompt no longer matches its digest.
                entry = JournalEntry(
                    entry.digest,
                    "TAMPERED: " + entry.prompt,
                    entry.response,
                    entry.rationale,
                    entry.confidence,
                )
            f.write(json.dumps(entry.to_record(), default=str) + "\n")
    cached, replayed, problems = verify_journal(cache, journal)
    assert cached == 2  # only the two untouched rows verify
    assert replayed == 2
    assert problems
    assert any("digest" in p for p in problems)


def test_run_is_deterministic(example, tmp_path: Path) -> None:
    a = example.run(decisions=20, seed=11, journal_path=tmp_path / "a.ndjson")
    b = example.run(decisions=20, seed=11, journal_path=tmp_path / "b.ndjson")
    a_cfg, b_cfg = dict(a["config"]), dict(b["config"])
    a_cfg.pop("journal_path")
    b_cfg.pop("journal_path")
    a.pop("config")
    b.pop("config")
    assert a == b
    assert a_cfg == b_cfg


def test_main_writes_same_json_to_out_and_stdout(example, tmp_path, capsys) -> None:
    out = tmp_path / "replay.json"
    assert example.main(["--decisions", "10", "--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert json.loads(out.read_text()) == json.loads(stdout)
    assert json.loads(stdout)["replay"]["all_verified"] is True


def test_main_happy_path_returns_zero(example, capsys) -> None:
    assert example.main([]) == 0
    assert "No external model" in capsys.readouterr().out
