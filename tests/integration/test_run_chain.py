"""Integration: ``screening/chains/quality_value.toml`` and ``screening/06_run_chain.py``.

Uses the same offline catalog and fundamentals store as the other pipeline tests; needs the
honba-strategies checkout (or ``$HONBA_STRATEGIES_DIR``) for chain validation and skips without it.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
from pathlib import Path

import pytest
from honba.strategies.loader import CatalogError

from honba_examples.pipeline.chain import ChainError, load_chain, run_chain, validate

EX = Path(__file__).resolve().parents[2]
CHAIN = EX / "screening" / "chains" / "quality_value.toml"


@pytest.fixture(autouse=True)
def _need_catalog() -> None:
    try:
        validate([{"uses": "size.equal_weight"}])
    except CatalogError as exc:
        pytest.skip(f"honba-strategies catalog not available: {exc}")


def _script():
    spec = importlib.util.spec_from_file_location("screening_06", EX / "screening/06_run_chain.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_quality_value_chain_runs_end_to_end() -> None:
    chain = load_chain(CHAIN)
    assert validate(chain) == []
    result = run_chain(chain)
    assert result.symbols
    assert result.summary is not None and result.summary["candidate_count"] == len(result.symbols)
    assert result.weights and abs(sum(result.weights.values()) - 1.0) < 1e-9
    for cand in result.candidates:
        assert cand["market_cap"] is None or cand["market_cap"] > 0
        assert {"dividend_yield_pct", "roe_pct"} <= set(cand)
        assert cand["roce_pct"] is None or cand["roce_pct"] >= 12.0  # the filter's own threshold


def test_report_adds_columns_without_changing_who_survives() -> None:
    chain = load_chain(CHAIN)
    bare = [dict(step, report=[]) for step in chain]
    with_report = run_chain(chain)
    without = run_chain(bare)
    assert with_report.symbols == without.symbols
    assert with_report.weights == without.weights
    assert "market_cap" not in without.candidates[0]
    assert "market_cap" in with_report.candidates[0]


def test_run_chain_refuses_an_invalid_chain() -> None:
    chain = load_chain(CHAIN)
    chain[1]["report"] = ["ev_ebitda"]
    with pytest.raises(ChainError, match="cannot report 'ev_ebitda'"):
        run_chain(chain)


def test_script_validate_describe_and_formats(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    script = _script()
    assert script.main(["--chain", str(CHAIN), "--validate-only"]) == 0
    assert "chain valid" in capsys.readouterr().out

    assert script.main(["--describe"]) == 0
    manifests = json.loads(capsys.readouterr().out)
    assert (
        "screen.value" in manifests
        and "market_cap" in (manifests["filter.fundamental_quality"]["emits"]["reportable"])
    )

    out = tmp_path / "result.json"
    assert script.main(["--chain", str(CHAIN), "--format", "csv", "--out", str(out)]) == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert rows and "market_cap" in rows[0]
    saved = json.loads(out.read_text())
    assert saved["chain"][0] == "screen.value" and saved["summary"]

    assert script.main(["--chain", str(CHAIN), "--format", "symbols"]) == 0
    assert capsys.readouterr().out.split() == saved["symbols"]


def test_script_reports_invalid_chain(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    script = _script()
    bad = tmp_path / "bad.toml"
    bad.write_text('[[steps]]\nuses = "screen.value"\nreport = ["ev_ebitda"]\n')
    assert script.main(["--chain", str(bad)]) == 1
    assert "ev_ebitda" in capsys.readouterr().err
    assert script.main(["--chain", str(tmp_path / "missing.toml")]) == 1
