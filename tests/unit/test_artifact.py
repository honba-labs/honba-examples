from __future__ import annotations
import datetime as dt
from pathlib import Path

from honba_examples import artifact


class DummyBar:
    def __init__(self, ts, symbol):
        self.ts = ts
        self.symbol = symbol


def test_bars_digest_deterministic():
    b1 = [DummyBar(1, "A"), DummyBar(2, "B")]
    b2 = [DummyBar(1, "A"), DummyBar(2, "B")]
    assert artifact.bars_digest(b1) == artifact.bars_digest(b2)


def test_bars_digest_differs():
    b1 = [DummyBar(1, "A")]
    b2 = [DummyBar(1, "B")]
    assert artifact.bars_digest(b1) != artifact.bars_digest(b2)


def test_write_run_json_writes_file(tmp_path: Path):
    class R:
        def metrics(self):
            return {"total_return_pct": 1.0}

    cfg = {"test_start": "2026-01-01"}
    art = artifact.write_run_json(
        schema="s/v1",
        config=cfg,
        universe=["A"],
        coverage={"covered": 1, "missing": 0},
        run_result=R(),
        bars=[DummyBar(1, "A")],
        out_dir=tmp_path,
    )
    p = tmp_path / "run.json"
    assert p.exists()
    assert art["schema"] == "s/v1"
    assert art["run_hash"]
    assert art["bars_digest"]
