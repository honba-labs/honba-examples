"""HonbaExample CLI plumbing: every common flag must reach the attribute it names."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from honba_examples.base import HonbaExample


class _Probe(HonbaExample):
    """Probe example."""


def test_common_flags_set_the_attributes_they_name(tmp_path: Path) -> None:
    ex = _Probe()
    ex.parse_args(
        [
            "--universe",
            "nifty50",
            "--start",
            "2024-02-01",
            "--end",
            "2024-03-01",
            "--capital",
            "250000",
            "--data-dir",
            str(tmp_path),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    assert ex.universe_name == "nifty50"
    assert ex.start_date == dt.date(2024, 2, 1)
    assert ex.end_date == dt.date(2024, 3, 1)
    assert ex.initial_capital == 250000.0
    assert ex.data_dir == tmp_path
    assert ex.out_dir == tmp_path / "out"


def test_initial_capital_flag_alias() -> None:
    ex = _Probe()
    ex.parse_args(["--initial-capital", "500000"])
    assert ex.initial_capital == 500000.0


def test_store_honours_data_dir_given_on_the_command_line(tmp_path: Path) -> None:
    ex = _Probe()
    ex.parse_args(["--data-dir", str(tmp_path)])
    assert ex.store.data_dir == tmp_path.resolve()


def test_construction_has_no_filesystem_side_effects(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _Probe()
    assert list(tmp_path.iterdir()) == []


def test_default_data_root_does_not_depend_on_cwd(tmp_path: Path, monkeypatch) -> None:
    from honba.screener.store import find_data_root

    from honba_examples.base import REPO_ROOT

    monkeypatch.chdir(tmp_path)
    ex = _Probe()
    ex.parse_args([])
    assert ex.store.data_dir == find_data_root(REPO_ROOT)
