"""
tests/test_portfolio.py — Unit tests for portfolio.py utilities.

Tests _trade_ts() and _make_id() without invoking any CLI commands,
network calls, or file I/O.
"""

import re

from portfolio import _make_id, _trade_ts


class TestTradeTs:
    def test_returns_iso8601_for_none(self):
        """_trade_ts(None) must return a full datetime string, not just a date."""
        ts = _trade_ts(None)
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.*', ts), (
            f"Expected ISO 8601 datetime, got: {ts!r}"
        )

    def test_includes_time_component(self):
        assert 'T' in _trade_ts(None)

    def test_is_string(self):
        assert isinstance(_trade_ts(None), str)

    def test_date_str_produces_noon_timestamp(self):
        ts = _trade_ts('2024-01-15')
        assert ts == '2024-01-15T12:00:00.000000'


class TestMakeId:
    def test_format(self):
        txn_id = _make_id('2026-04-13T14:30:22.841504', 'AXP')
        assert txn_id.startswith('txn_')
        assert txn_id.endswith('_AXP')

    def test_no_special_chars_in_middle(self):
        """The compact timestamp portion must contain only digits."""
        txn_id = _make_id('2026-04-13T14:30:22.841504', 'AXP')
        # strip prefix 'txn_' and suffix '_AXP'
        middle = txn_id[4:-4]
        assert middle.isdigit(), f"Expected digits only, got: {middle!r}"

    def test_different_tickers_produce_different_ids(self):
        ts = '2026-04-13T14:30:22.841504'
        assert _make_id(ts, 'AXP') != _make_id(ts, 'NVDA')

    def test_different_timestamps_produce_different_ids(self):
        assert _make_id('2026-04-13T14:30:22.000000', 'AXP') != \
               _make_id('2026-04-13T14:30:23.000000', 'AXP')
