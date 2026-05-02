"""
tests/test_portfolio.py — Unit tests for portfolio.py.

All tests are network-free and disk-isolated via tmp_path.
File paths are patched per test so no test touches ~/.portfolio.
"""

import argparse
import json
import re
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

import pytest

from portfolio import (
    SHARE_DECIMALS,
    _make_id,
    _parse_date,
    _resolve_price,
    _trade_ts,
    cmd_buy,
    cmd_gains,
    cmd_goal_remove,
    cmd_goal_set,
    cmd_goal_show,
    cmd_history,
    cmd_remove,
    cmd_savings_interest,
    cmd_savings_remove,
    cmd_savings_set,
    cmd_sell,
    cmd_show,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def fs(tmp_path):
    """Isolated data file paths — not connected to ~/.portfolio."""
    return argparse.Namespace(
        holdings=tmp_path / 'holdings.json',
        transactions=tmp_path / 'transactions.json',
        savings=tmp_path / 'savings.json',
        goals=tmp_path / 'goals.json',
    )


@contextmanager
def _files(fs):
    with patch.multiple(
        'portfolio',
        HOLDINGS_FILE=fs.holdings,
        TRANSACTIONS_FILE=fs.transactions,
        SAVINGS_FILE=fs.savings,
        GOALS_FILE=fs.goals,
    ):
        yield


def _args(**kwargs) -> argparse.Namespace:
    defaults = dict(ticker='AXP', dollars=300.0, date=None, price=150.0, notes='')
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _buy(args, fs, *, confirm='y'):
    """Execute cmd_buy with a patched confirm prompt and fetch_label."""
    with _files(fs), patch('portfolio.fetch_label', return_value='American Express'):
        cmd_buy(args, prompt=lambda _: confirm)


# ── _trade_ts ──────────────────────────────────────────────────────────────────

class TestTradeTs:
    def test_returns_iso8601_for_none(self):
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.*', _trade_ts(None))

    def test_includes_time_component(self):
        assert 'T' in _trade_ts(None)

    def test_is_string(self):
        assert isinstance(_trade_ts(None), str)

    def test_date_str_produces_noon_timestamp(self):
        assert _trade_ts('2024-01-15') == '2024-01-15T12:00:00.000000'


# ── _make_id ───────────────────────────────────────────────────────────────────

class TestMakeId:
    def test_format(self):
        txn_id = _make_id('2026-04-13T14:30:22.841504', 'AXP')
        assert txn_id.startswith('txn_')
        assert txn_id.endswith('_AXP')

    def test_no_special_chars_in_middle(self):
        txn_id = _make_id('2026-04-13T14:30:22.841504', 'AXP')
        middle = txn_id[4:-4]
        assert middle.isdigit()

    def test_different_tickers_produce_different_ids(self):
        ts = '2026-04-13T14:30:22.841504'
        assert _make_id(ts, 'AXP') != _make_id(ts, 'NVDA')

    def test_different_timestamps_produce_different_ids(self):
        assert _make_id('2026-04-13T14:30:22.000000', 'AXP') != \
               _make_id('2026-04-13T14:30:23.000000', 'AXP')


# ── _parse_date ────────────────────────────────────────────────────────────────

class TestParseDate:
    def test_valid_date_returned(self):
        assert _parse_date('2023-01-15') == '2023-01-15'

    def test_invalid_format_exits(self):
        with pytest.raises(SystemExit, match="Invalid date"):
            _parse_date('15-01-2023')

    def test_future_date_exits(self):
        with pytest.raises(SystemExit, match="future"):
            _parse_date('2099-01-01')

    def test_today_accepted(self):
        assert _parse_date(date.today().isoformat()) == date.today().isoformat()


# ── _resolve_price ─────────────────────────────────────────────────────────────

class TestResolvePrice:
    def test_explicit_price_returned_directly(self):
        assert _resolve_price('AXP', explicit=123.45) == 123.45

    def test_fetches_historical_when_date_given(self):
        with patch('portfolio.fetch_historical_price', return_value=99.0) as m:
            price = _resolve_price('AXP', explicit=None, date_str='2024-01-15')
        m.assert_called_once_with('AXP', '2024-01-15')
        assert price == 99.0

    def test_fetches_live_price_when_no_date(self):
        with patch('portfolio.fetch_price', return_value=88.0) as m:
            price = _resolve_price('AXP', explicit=None, date_str=None)
        m.assert_called_once_with('AXP')
        assert price == 88.0


# ── cmd_buy ────────────────────────────────────────────────────────────────────

class TestCmdBuy:
    def test_creates_holding_with_correct_shares_and_cost(self, fs):
        _buy(_args(dollars=300.0, price=150.0), fs)
        h = json.loads(fs.holdings.read_text())['AXP']
        assert h['cost'] == pytest.approx(300.0)
        assert h['shares'] == pytest.approx(round(300.0 / 150.0, SHARE_DECIMALS))

    def test_appends_buy_transaction(self, fs):
        _buy(_args(dollars=300.0, price=150.0), fs)
        txns = json.loads(fs.transactions.read_text())
        assert len(txns) == 1
        t = txns[0]
        assert t['action'] == 'buy'
        assert t['ticker'] == 'AXP'
        assert t['dollars'] == pytest.approx(300.0)
        assert t['price'] == pytest.approx(150.0)
        assert t['realized_pnl'] is None

    def test_cancel_writes_no_files(self, fs):
        _buy(_args(), fs, confirm='n')
        assert not fs.holdings.exists()
        assert not fs.transactions.exists()

    def test_adds_to_existing_holding(self, fs):
        _buy(_args(dollars=300.0, price=150.0), fs)           # 2.0 shares, $300 cost
        with _files(fs):
            cmd_buy(_args(dollars=150.0, price=150.0))         # 1.0 share, $150 cost
        h = json.loads(fs.holdings.read_text())['AXP']
        assert h['cost'] == pytest.approx(450.0)
        assert h['shares'] == pytest.approx(3.0)

    def test_second_buy_appends_second_transaction(self, fs):
        _buy(_args(), fs)
        with _files(fs):
            cmd_buy(_args(dollars=150.0, price=150.0))
        txns = json.loads(fs.transactions.read_text())
        assert len(txns) == 2

    def test_zero_dollars_exits(self, fs):
        with pytest.raises(SystemExit, match="positive"):
            with _files(fs):
                cmd_buy(_args(dollars=0.0))

    def test_negative_dollars_exits(self, fs):
        with pytest.raises(SystemExit, match="positive"):
            with _files(fs):
                cmd_buy(_args(dollars=-50.0))

    def test_notes_stored_in_transaction(self, fs):
        _buy(_args(notes='test note'), fs)
        txns = json.loads(fs.transactions.read_text())
        assert txns[0]['notes'] == 'test note'


# ── cmd_sell ───────────────────────────────────────────────────────────────────

class TestCmdSell:
    def _setup(self, fs, dollars=300.0, price=150.0):
        _buy(_args(dollars=dollars, price=price), fs)

    def test_partial_sell_reduces_shares(self, fs):
        self._setup(fs, dollars=300.0, price=150.0)     # 2.0 shares
        with _files(fs):
            cmd_sell(_args(dollars=150.0, price=150.0)) # sell 1.0 share
        h = json.loads(fs.holdings.read_text())['AXP']
        assert h['shares'] == pytest.approx(1.0)

    def test_partial_sell_reduces_cost_proportionally(self, fs):
        self._setup(fs, dollars=300.0, price=150.0)     # $300 cost for 2 shares
        with _files(fs):
            cmd_sell(_args(dollars=150.0, price=150.0)) # sell half
        h = json.loads(fs.holdings.read_text())['AXP']
        assert h['cost'] == pytest.approx(150.0)

    def test_full_sell_removes_holding(self, fs):
        self._setup(fs, dollars=300.0, price=150.0)
        with _files(fs):
            cmd_sell(_args(dollars=300.0, price=150.0))
        assert 'AXP' not in json.loads(fs.holdings.read_text())

    def test_realized_pnl_average_cost_method(self, fs):
        # Buy 2 shares @ $150 = $300 cost.
        # Sell 1 share @ $200 = $200 proceeds.
        # Cost of 1 share (avg) = 300 / 2 = $150. P&L = 200 - 150 = $50.
        self._setup(fs, dollars=300.0, price=150.0)
        with _files(fs):
            cmd_sell(_args(dollars=200.0, price=200.0))
        txns = json.loads(fs.transactions.read_text())
        sell = next(t for t in txns if t['action'] == 'sell')
        assert sell['realized_pnl'] == pytest.approx(50.0)

    def test_sell_loss_produces_negative_pnl(self, fs):
        # Buy 2 shares @ $150 = $300 cost. Sell 1 @ $100 proceeds.
        # Cost of 1 share = $150. P&L = 100 - 150 = -$50.
        self._setup(fs, dollars=300.0, price=150.0)
        with _files(fs):
            cmd_sell(_args(dollars=100.0, price=100.0))
        txns = json.loads(fs.transactions.read_text())
        sell = next(t for t in txns if t['action'] == 'sell')
        assert sell['realized_pnl'] == pytest.approx(-50.0)

    def test_sell_appends_transaction(self, fs):
        self._setup(fs)
        with _files(fs):
            cmd_sell(_args(dollars=150.0, price=150.0))
        txns = json.loads(fs.transactions.read_text())
        assert any(t['action'] == 'sell' for t in txns)

    def test_oversell_exits(self, fs):
        self._setup(fs, dollars=300.0, price=150.0)   # 2 shares
        with pytest.raises(SystemExit):
            with _files(fs):
                cmd_sell(_args(dollars=600.0, price=150.0))  # 4 shares

    def test_ticker_not_in_portfolio_exits(self, fs):
        with pytest.raises(SystemExit, match="not in your portfolio"):
            with _files(fs):
                cmd_sell(_args(ticker='NVDA', dollars=100.0, price=100.0))

    def test_zero_dollars_exits(self, fs):
        self._setup(fs)
        with pytest.raises(SystemExit, match="positive"):
            with _files(fs):
                cmd_sell(_args(dollars=0.0))


# ── cmd_show ───────────────────────────────────────────────────────────────────

class TestCmdShow:
    def test_no_holdings_prints_help(self, fs, capsys):
        with _files(fs):
            cmd_show(_args())
        assert 'portfolio buy' in capsys.readouterr().out

    def test_with_holdings_prints_ticker(self, fs, capsys):
        _buy(_args(), fs)
        with _files(fs), patch('portfolio.fetch_prices_batch', return_value={'AXP': 160.0}):
            cmd_show(_args())
        assert 'AXP' in capsys.readouterr().out


# ── cmd_gains ──────────────────────────────────────────────────────────────────

class TestCmdGains:
    def test_shows_summary_section(self, fs, capsys):
        with _files(fs), patch('portfolio.fetch_prices_batch', return_value={}):
            cmd_gains(_args(ticker=None))
        assert 'SUMMARY' in capsys.readouterr().out

    def test_realized_pnl_appears_after_sell(self, fs, capsys):
        _buy(_args(dollars=300.0, price=150.0), fs)
        with _files(fs):
            cmd_sell(_args(dollars=200.0, price=200.0))
        with _files(fs), patch('portfolio.fetch_prices_batch', return_value={}):
            cmd_gains(_args(ticker=None))
        assert '+$50.00' in capsys.readouterr().out


# ── cmd_history ────────────────────────────────────────────────────────────────

class TestCmdHistory:
    def test_no_transactions_says_so(self, fs, capsys):
        with _files(fs):
            cmd_history(_args(ticker=None, limit=None))
        assert 'No transactions' in capsys.readouterr().out

    def test_buy_appears_after_purchase(self, fs, capsys):
        _buy(_args(), fs)
        with _files(fs):
            cmd_history(_args(ticker=None, limit=None))
        assert 'buy' in capsys.readouterr().out.lower()

    def test_ticker_filter_excludes_other_tickers(self, fs, capsys):
        _buy(_args(ticker='AXP'), fs)
        with _files(fs), patch('portfolio.fetch_label', return_value='Gold'):
            cmd_buy(_args(ticker='IAU'), prompt=lambda _: 'y')
        capsys.readouterr()  # discard buy confirmation output
        with _files(fs):
            cmd_history(_args(ticker='AXP', limit=None))
        out = capsys.readouterr().out
        assert 'AXP' in out
        assert 'IAU' not in out


# ── cmd_remove ─────────────────────────────────────────────────────────────────

class TestCmdRemove:
    def test_removes_holding_after_confirm(self, fs):
        _buy(_args(), fs)
        with _files(fs):
            cmd_remove(_args(), prompt=lambda _: 'y')
        assert 'AXP' not in json.loads(fs.holdings.read_text())

    def test_cancel_leaves_holding_intact(self, fs):
        _buy(_args(), fs)
        with _files(fs):
            cmd_remove(_args(), prompt=lambda _: 'n')
        assert 'AXP' in json.loads(fs.holdings.read_text())

    def test_not_in_portfolio_exits(self, fs):
        with pytest.raises(SystemExit, match="not in your portfolio"):
            with _files(fs):
                cmd_remove(_args(ticker='NVDA'), prompt=lambda _: 'y')


# ── cmd_savings_set ─────────────────────────────────────────────────────────────

class TestCmdSavingsSet:
    def _sargs(self, **kwargs) -> argparse.Namespace:
        defaults = dict(name='Car Fund', balance=10_000.0, apy=4.0, bank='Amex')
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_creates_account_with_correct_fields(self, fs):
        with _files(fs):
            cmd_savings_set(self._sargs())
        accs = json.loads(fs.savings.read_text())
        assert len(accs) == 1
        assert accs[0]['name'] == 'Car Fund'
        assert accs[0]['balance'] == pytest.approx(10_000.0)
        assert accs[0]['apy'] == pytest.approx(0.04)
        assert accs[0]['bank'] == 'Amex'

    def test_updates_existing_account_balance(self, fs):
        with _files(fs):
            cmd_savings_set(self._sargs())
            cmd_savings_set(self._sargs(balance=20_000.0, apy=None, bank=None))
        accs = json.loads(fs.savings.read_text())
        assert len(accs) == 1
        assert accs[0]['balance'] == pytest.approx(20_000.0)

    def test_requires_apy_for_new_account(self, fs):
        with pytest.raises(SystemExit, match="--apy is required"):
            with _files(fs):
                cmd_savings_set(self._sargs(apy=None))

    def test_requires_balance_for_new_account(self, fs):
        with pytest.raises(SystemExit, match="BALANCE is required"):
            with _files(fs):
                cmd_savings_set(self._sargs(balance=None))

    def test_negative_balance_exits(self, fs):
        with pytest.raises(SystemExit, match="non-negative"):
            with _files(fs):
                cmd_savings_set(self._sargs(balance=-500.0))


# ── cmd_savings_remove ──────────────────────────────────────────────────────────

class TestCmdSavingsRemove:
    def test_removes_account(self, fs):
        with _files(fs):
            cmd_savings_set(argparse.Namespace(name='Car Fund', balance=5_000.0, apy=4.0, bank=''))
            cmd_savings_remove(argparse.Namespace(name='Car Fund'))
        assert json.loads(fs.savings.read_text()) == []

    def test_not_found_exits(self, fs):
        with pytest.raises(SystemExit, match="not a savings account"):
            with _files(fs):
                cmd_savings_remove(argparse.Namespace(name='Ghost'))


# ── cmd_savings_interest ────────────────────────────────────────────────────────

class TestCmdSavingsInterest:
    def test_no_payment_day_prints_setup_message(self, fs, capsys):
        with _files(fs), patch('portfolio.INTEREST_PAYMENT_DAY', None):
            cmd_savings_interest(argparse.Namespace())
        assert 'INTEREST_PAYMENT_DAY' in capsys.readouterr().out

    def test_no_accounts_prints_message(self, fs, capsys):
        with _files(fs), patch('portfolio.INTEREST_PAYMENT_DAY', 15):
            cmd_savings_interest(argparse.Namespace())
        assert 'No savings accounts' in capsys.readouterr().out

    def test_shows_account_row_when_configured(self, fs, capsys):
        with _files(fs):
            cmd_savings_set(argparse.Namespace(name='Emergency', balance=10_000.0, apy=4.0, bank=''))
        with _files(fs), patch('portfolio.INTEREST_PAYMENT_DAY', 15):
            cmd_savings_interest(argparse.Namespace())
        assert 'Emergency' in capsys.readouterr().out


# ── cmd_goal_set / remove / show ───────────────────────────────────────────────

class TestCmdGoals:
    def test_sets_portfolio_goal(self, fs):
        with _files(fs):
            cmd_goal_set(argparse.Namespace(target='portfolio', amount=100_000.0))
        assert json.loads(fs.goals.read_text())['__portfolio__'] == pytest.approx(100_000.0)

    def test_sets_savings_goal(self, fs):
        with _files(fs):
            cmd_goal_set(argparse.Namespace(target='savings', amount=50_000.0))
        assert json.loads(fs.goals.read_text())['__savings__'] == pytest.approx(50_000.0)

    def test_removes_goal(self, fs):
        with _files(fs):
            cmd_goal_set(argparse.Namespace(target='portfolio', amount=100_000.0))
            cmd_goal_remove(argparse.Namespace(target='portfolio'))
        assert '__portfolio__' not in json.loads(fs.goals.read_text())

    def test_remove_nonexistent_goal_is_silent_noop(self, fs, capsys):
        with _files(fs):
            cmd_goal_remove(argparse.Namespace(target='portfolio'))
        assert 'No' in capsys.readouterr().out

    def test_goal_show_displays_amounts(self, fs, capsys):
        with _files(fs):
            cmd_goal_set(argparse.Namespace(target='portfolio', amount=100_000.0))
            cmd_goal_show(argparse.Namespace())
        assert '100,000' in capsys.readouterr().out

    def test_goal_show_displays_not_set_when_empty(self, fs, capsys):
        with _files(fs):
            cmd_goal_show(argparse.Namespace())
        assert 'not set' in capsys.readouterr().out
