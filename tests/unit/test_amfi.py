"""honba_examples.amfi: NAVAll.txt parsed into rows and stamped as daily NAV bars."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest
from honba.domain.instrument import InstrumentId

from honba_examples import amfi
from honba_examples.amfi import NavRow, nav_bars, parse_navall
from tests.synthetic import session_ts

NAVALL = """Applic Date: 02-Jun-2025

100001;Sample Flexi Cap Fund - Growth;123.4567;02-Jun-2025
100002;Sample Index Fund - Direct Plan;89.1011;02-Jun-2025
100003;Sample Debt Fund - Growth;-;02-Jun-2025
"""

MALFORMED = """Applic Date: 04-Jun-2025

100001;Sample Flexi Cap Fund - Growth;124.5000;04-Jun-2025
100002;Sample Index Fund - Direct Plan;88.9000
"""


def test_parses_every_well_formed_row_and_skips_the_header_and_blank_lines():
    rows = parse_navall(NAVALL)
    assert [row.code for row in rows] == ["100001", "100002"]
    assert rows[0] == NavRow(
        code="100001",
        name="Sample Flexi Cap Fund - Growth",
        nav=123.4567,
        date=dt.date(2025, 6, 2),
    )
    assert rows[1].nav == 89.1011


def test_a_publication_without_data_rows_parses_to_nothing():
    assert parse_navall("Applic Date: 02-Jun-2025\n\n") == []
    assert parse_navall("") == []


def test_a_missing_nav_is_skipped_and_counted_with_line_code_and_reason():
    skipped = []
    rows = parse_navall(NAVALL, skipped=skipped)
    assert len(rows) == 2
    assert len(skipped) == 1
    (skip,) = skipped
    assert skip.line == 5
    assert skip.code == "100003"
    assert "'-'" in skip.reason


@pytest.mark.parametrize("nav", ["-", "", "0", "-1.5", "nan", "inf"])
def test_any_nav_that_is_not_a_positive_finite_number_is_skipped(nav):
    skipped = []
    rows = parse_navall(f"Applic Date: 02-Jun-2025\n100004;Any Fund;{nav};02-Jun-2025\n", skipped)
    assert rows == []
    assert [skip.code for skip in skipped] == ["100004"]
    assert skipped[0].line == 2
    assert nav in skipped[0].reason


def test_a_row_with_the_wrong_number_of_fields_raises_naming_the_offending_line():
    with pytest.raises(ValueError) as excinfo:
        parse_navall(MALFORMED)
    message = str(excinfo.value)
    assert "line 4" in message
    assert "88.9000" in message


def test_structural_damage_fails_even_when_it_follows_valid_rows():
    text = "Applic Date: 02-Jun-2025\n100001;Any Fund;1.0;02-Jun-2025\n100002;Any Fund;1.0\n"
    with pytest.raises(ValueError, match="line 3"):
        parse_navall(text)


def test_a_date_outside_the_documented_format_raises_naming_the_line():
    text = "Applic Date: 02-Jun-2025\n100001;Any Fund;1.0;2025-06-02\n"
    with pytest.raises(ValueError) as excinfo:
        parse_navall(text)
    assert "line 2" in str(excinfo.value)
    assert "2025-06-02" in str(excinfo.value)


def test_rows_come_back_sorted_by_code_then_date():
    text = (
        "Applic Date: 03-Jun-2025\n"
        "100002;Second;10.0;03-Jun-2025\n"
        "100001;First;2.0;03-Jun-2025\n"
        "100001;First;1.0;02-Jun-2025\n"
        "000010;Zero;9.0;02-Jun-2025\n"
    )
    assert [(row.code, row.date.day) for row in parse_navall(text)] == [
        ("000010", 2),
        ("100001", 2),
        ("100001", 3),
        ("100002", 3),
    ]


def test_rows_are_frozen():
    row = parse_navall(NAVALL)[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.nav = 0.0  # type: ignore[misc]


def test_nav_bars_are_stamped_at_the_documented_session_open():
    rows = parse_navall(NAVALL)
    assert [bar.ts for bar in nav_bars(rows)] == [session_ts(row.date) for row in rows]
    assert nav_bars(rows)[0].ts == 1_748_835_900_000_000_000  # 2025-06-02 03:45 UTC = 09:15 IST


def test_nav_bars_carry_the_nav_as_flat_ohlc_with_no_volume():
    rows = parse_navall(NAVALL)
    bars = nav_bars(rows)
    assert len(bars) == len(rows)
    for row, bar in zip(rows, bars, strict=True):
        assert bar.instrument_id == InstrumentId(row.code, "AMFI")
        assert (bar.open, bar.high, bar.low, bar.close) == (row.nav,) * 4
        assert bar.volume == 0.0


def test_the_bars_of_a_publication_keep_its_order():
    rows = parse_navall(NAVALL)
    assert [bar.instrument_id for bar in nav_bars(rows)] == [
        InstrumentId(row.code, "AMFI") for row in rows
    ]


def test_instrument_id_accepts_the_amfi_exchange():
    instrument = InstrumentId("100001", "AMFI")
    assert instrument.exchange == "AMFI"
    assert str(instrument) == "100001.AMFI"


def test_public_surface():
    assert set(amfi.__all__) == {"EXCHANGE", "NavRow", "NavSkip", "nav_bars", "parse_navall"}
