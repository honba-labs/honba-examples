"""NextOpenExecution: next-session-open fills, paise cash, T+N settlement, trading gate."""

from __future__ import annotations

import datetime as dt

import pytest
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide

from honba_examples.execution import NextOpenExecution
from honba_examples.money import delivery_cost_paise, notional_paise
from tests.synthetic import bar, session_ts

A = InstrumentId("AAA", "NSE")
B = InstrumentId("BBB", "NSE")
D1, D2, D3, D4, D5 = (dt.date(2026, 6, d) for d in (1, 2, 3, 4, 5))


def zero_cost(side: OrderSide, qty: float, px: float) -> int:
    return 0


class Releases:
    def __init__(self) -> None:
        self.intents: list[OrderIntent] = []

    def __call__(self, intent: OrderIntent) -> None:
        self.intents.append(intent)


def make(cash_rupees: int = 10_000, settlement_days: int = 0, cost=zero_cost, trading=True):
    rel = Releases()
    port = NextOpenExecution(
        cash_paise=cash_rupees * 100,
        settlement_days=settlement_days,
        release=rel,
        cost_paise=cost,
        trading=trading,
    )
    return port, rel


def test_order_fills_at_next_session_open_not_decision_close() -> None:
    port, _ = make()
    port.open_session(D1, [bar("AAA", D1, 100.0, close=105.0)])
    port.submit("o-0", OrderIntent.market_buy(A, 10), session_ts(D1))
    assert port.drain_fills() == []  # nothing fills on the bar that produced the order

    port.open_session(D2, [bar("AAA", D2, 107.0, close=90.0)])
    (fill,) = port.drain_fills()
    assert fill.price == 107.0  # D2 open, not D1 close (105) nor D2 close (90)
    assert fill.ts == session_ts(D2)
    assert fill.quantity == 10
    assert port.cash_paise == 10_000 * 100 - notional_paise(10, 107.0)
    assert port.positions[A] == 10


def test_order_waits_for_the_instruments_next_bar() -> None:
    port, _ = make()
    port.open_session(D1, [bar("AAA", D1, 100.0)])
    port.submit("o-0", OrderIntent.market_buy(A, 1), session_ts(D1))
    port.open_session(D2, [bar("BBB", D2, 50.0)])  # AAA did not trade on D2
    assert port.drain_fills() == []
    port.open_session(D3, [bar("AAA", D3, 101.0), bar("BBB", D3, 50.0)])
    (fill,) = port.drain_fills()
    assert fill.price == 101.0


def test_costs_are_integer_paise_and_debited() -> None:
    port, _ = make(cost=delivery_cost_paise)
    port.open_session(D1, [bar("AAA", D1, 186.10)])
    port.submit("o-0", OrderIntent.market_buy(A, 17), session_ts(D1))
    port.open_session(D2, [bar("AAA", D2, 186.10)])
    (fill,) = port.drain_fills()
    cost = delivery_cost_paise(OrderSide.BUY, 17, 186.10)
    (record,) = port.fill_records
    assert record.cost_paise == cost
    assert record.notional_paise == notional_paise(17, 186.10)
    assert fill.costs == cost / 100
    assert port.cash_paise == 10_000 * 100 - record.notional_paise - cost
    assert port.fees_paise == cost


def test_sells_fill_before_buys_in_a_session() -> None:
    port, _ = make(cash_rupees=100)
    port.open_session(D1, [bar("AAA", D1, 100.0), bar("BBB", D1, 100.0)])
    port.submit("o-0", OrderIntent.market_buy(A, 1), session_ts(D1))
    port.open_session(D2, [bar("AAA", D2, 100.0), bar("BBB", D2, 100.0)])
    port.drain_fills()
    # Buy BBB (submitted first) and sell AAA in the same session, all cash spent.
    port.submit("o-1", OrderIntent.market_buy(B, 1), session_ts(D2))
    port.submit("o-2", OrderIntent.market_sell(A, 1), session_ts(D2))
    port.open_session(D3, [bar("AAA", D3, 100.0), bar("BBB", D3, 100.0)])
    fills = port.drain_fills()
    assert [f.side for f in fills] == [OrderSide.SELL, OrderSide.BUY]


def test_t_plus_2_buy_waits_for_settled_proceeds() -> None:
    port, rel = make(cash_rupees=100, settlement_days=2)
    sessions = [D1, D2, D3, D4, D5]
    port.open_session(D1, [bar("AAA", D1, 100.0), bar("BBB", D1, 100.0)])
    port.submit("o-0", OrderIntent.market_buy(A, 1), session_ts(D1))
    port.open_session(D2, [bar("AAA", D2, 100.0), bar("BBB", D2, 100.0)])
    assert len(port.drain_fills()) == 1  # opening buy uses capital in hand
    assert port.available_cash_paise == 0

    port.submit("o-1", OrderIntent.market_sell(A, 1), session_ts(D2))
    port.submit("o-2", OrderIntent.market_buy(B, 1), session_ts(D2))
    port.open_session(D3, [bar("AAA", D3, 100.0), bar("BBB", D3, 100.0)])
    (sell,) = port.drain_fills()  # sold on D3; proceeds settle on D5 (T+2)
    assert sell.side is OrderSide.SELL
    assert port.cash_paise == 10_000
    assert port.available_cash_paise == 0

    port.open_session(D4, [bar("AAA", D4, 100.0), bar("BBB", D4, 100.0)])
    assert port.drain_fills() == []  # T+1: still unsettled, the buy waits

    port.open_session(D5, [bar("AAA", D5, 100.0), bar("BBB", D5, 100.0)])
    (buy,) = port.drain_fills()
    assert buy.side is OrderSide.BUY and buy.ts == session_ts(sessions[4])
    assert rel.intents == []


def test_t_plus_0_spends_proceeds_next_session() -> None:
    port, _ = make(cash_rupees=100, settlement_days=0)
    port.open_session(D1, [bar("AAA", D1, 100.0), bar("BBB", D1, 100.0)])
    port.submit("o-0", OrderIntent.market_buy(A, 1), session_ts(D1))
    port.open_session(D2, [bar("AAA", D2, 100.0), bar("BBB", D2, 100.0)])
    port.drain_fills()
    port.submit("o-1", OrderIntent.market_sell(A, 1), session_ts(D2))
    port.submit("o-2", OrderIntent.market_buy(B, 1), session_ts(D2))
    port.open_session(D3, [bar("AAA", D3, 100.0), bar("BBB", D3, 100.0)])
    assert len(port.drain_fills()) == 2


def test_unfunded_buy_is_cut_to_what_cash_allows_and_rest_released() -> None:
    port, rel = make(cash_rupees=250, settlement_days=2)
    port.open_session(D1, [bar("AAA", D1, 100.0)])
    port.submit("o-0", OrderIntent.market_buy(A, 5), session_ts(D1))
    for d in (D2, D3):
        port.open_session(d, [bar("AAA", d, 100.0)])
        assert port.drain_fills() == []  # waits up to settlement_days sessions
    port.open_session(D4, [bar("AAA", D4, 100.0)])
    (fill,) = port.drain_fills()
    assert fill.quantity == 2
    assert port.cash_paise == 5_000
    (released,) = rel.intents
    assert released.quantity == 3 and released.side is OrderSide.BUY
    assert [e.kind for e in port.order_events] == ["released_unfunded"]


def test_buy_with_no_affordable_share_is_released_whole() -> None:
    port, rel = make(cash_rupees=50)
    port.open_session(D1, [bar("AAA", D1, 100.0)])
    port.submit("o-0", OrderIntent.market_buy(A, 1), session_ts(D1))
    port.open_session(D2, [bar("AAA", D2, 100.0)])
    assert port.drain_fills() == []
    assert rel.intents[0].quantity == 1
    assert port.cash_paise == 5_000


def test_sell_is_capped_at_the_position_held() -> None:
    port, rel = make()
    port.open_session(D1, [bar("AAA", D1, 100.0)])
    port.submit("o-0", OrderIntent.market_sell(A, 3), session_ts(D1))
    port.open_session(D2, [bar("AAA", D2, 100.0)])
    assert port.drain_fills() == []  # long-only: nothing held, nothing sold
    assert rel.intents[0].quantity == 3


def test_closed_gate_suppresses_orders_without_filling() -> None:
    port, rel = make(trading=False)
    port.open_session(D1, [bar("AAA", D1, 100.0)])
    port.submit("o-0", OrderIntent.market_buy(A, 1), session_ts(D1))
    assert rel.intents and port.order_events[0].kind == "suppressed_warmup"
    port.enable_trading()
    port.open_session(D2, [bar("AAA", D2, 100.0)])
    assert port.drain_fills() == []
    assert port.fill_records == []
    assert port.cash_paise == 10_000 * 100


def test_finish_reports_orders_left_unfilled() -> None:
    port, _ = make()
    port.open_session(D1, [bar("AAA", D1, 100.0)])
    port.submit("o-0", OrderIntent.market_buy(A, 1), session_ts(D1))
    port.finish()
    assert [e.kind for e in port.order_events] == ["unfilled_at_end"]


def test_rejects_negative_settlement() -> None:
    with pytest.raises(ValueError):
        make(settlement_days=-1)
