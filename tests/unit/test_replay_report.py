"""replay_report: the shared {"strategy", "config", "result"} shape of the strategy examples."""

from __future__ import annotations

import datetime as dt
import json

from honba.strategies.base import Strategy

from honba_examples.replay_report import replay_report
from honba_examples.synthetic import bar, weekdays


class _Buyer(Strategy):
    name = "buyer"
    warmup_bars = 2

    def on_bar(self, bar) -> None:
        if self.position(bar.instrument_id) <= 0:
            self.buy(bar.instrument_id, 1)


def _bars(n: int = 5):
    return [bar("RELIANCE", d, 100.0 + i) for i, d in enumerate(weekdays(dt.date(2026, 1, 1), n))]


def test_shape_and_key_order():
    report = replay_report(_Buyer(), _bars(), {"a": 1, "b": 2.0}, config_extra={"bars": 5})
    assert list(report) == ["strategy", "config", "result"]
    assert list(report["strategy"]) == ["name", "warmup_bars", "params"]
    assert report["strategy"] == {
        "name": "buyer",
        "warmup_bars": 2,
        "params": {"a": 1, "b": 2.0},
    }
    assert list(report["config"]) == ["a", "b", "bars"]


def test_config_extra_defaults_to_empty():
    report = replay_report(_Buyer(), _bars(), {"a": 1})
    assert report["config"] == {"a": 1}


def test_params_are_copied_not_shared():
    params = {"a": 1}
    report = replay_report(_Buyer(), _bars(), params)
    report["strategy"]["params"]["a"] = 9
    assert report["config"]["a"] == 1 and params["a"] == 1


def test_result_is_json_serialisable_and_has_intents():
    report = replay_report(_Buyer(), _bars(), {})
    assert json.loads(json.dumps(report))["result"]["intents"]
