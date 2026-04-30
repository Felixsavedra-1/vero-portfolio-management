"""
tests/test_display.py — Unit tests for display.py.

All tests inject static data structures — no network, no disk.
"""

import pytest

from display import render_gains, render_history, render_holdings
from ledger import Holding, Transaction


# ── Fixtures ────────────────────────────────────────────────────────────────────

def _holding(ticker='AXP', shares=2.0, cost=200.0, label='American Express') -> Holding:
    return Holding(ticker=ticker, shares=shares, cost=cost,
                   first_purchase='2023-01-03T09:30:00', label=label)


def _txn(ticker='AXP', action='buy', dollars=200.0, price=100.0,
         realized_pnl=None, timestamp='2026-04-13T14:30:22') -> Transaction:
    return Transaction(
        id=f'txn_test_{ticker}',
        timestamp=timestamp,
        action=action,
        ticker=ticker,
        shares=dollars / price,
        dollars=dollars,
        price=price,
        realized_pnl=realized_pnl,
        notes='',
    )


# ── render_holdings ─────────────────────────────────────────────────────────────

class TestRenderHoldings:
    def test_empty_holdings_returns_help_message(self):
        out = render_holdings({}, {})
        assert 'portfolio buy' in out

    def test_ticker_appears_in_output(self):
        out = render_holdings({'AXP': _holding()}, {'AXP': 120.0})
        assert 'AXP' in out

    def test_label_appears_in_output(self):
        out = render_holdings({'AXP': _holding()}, {'AXP': 120.0})
        assert 'American Express' in out

    def test_gain_shown_for_profitable_position(self):
        # 2 shares bought at $100 each ($200 cost); now $120 each → +$40
        out = render_holdings({'AXP': _holding(shares=2.0, cost=200.0)}, {'AXP': 120.0})
        assert '+$40.00' in out

    def test_loss_shown_for_losing_position(self):
        # 2 shares at $100 cost ($200); now $80 each → -$40
        out = render_holdings({'AXP': _holding(shares=2.0, cost=200.0)}, {'AXP': 80.0})
        assert '-$40.00' in out

    def test_missing_price_shows_na(self):
        out = render_holdings({'AXP': _holding()}, {})
        assert 'n/a' in out

    def test_total_row_present(self):
        out = render_holdings({'AXP': _holding()}, {'AXP': 120.0})
        assert 'TOTAL' in out

    def test_multiple_holdings(self):
        holdings = {
            'AXP': _holding('AXP', 2.0, 200.0, 'American Express'),
            'IAU': _holding('IAU', 5.0, 100.0, 'Gold'),
        }
        prices = {'AXP': 120.0, 'IAU': 25.0}
        out    = render_holdings(holdings, prices)
        assert 'AXP' in out
        assert 'IAU' in out

    def test_weights_are_fractions_of_true_total(self):
        """
        Weights must be computed against the full portfolio total — not a
        running partial sum that grows as each row is processed.

        AXP: 2 shares × $120 = $240   →  240/365 = 65.75%  → 66%
        IAU: 5 shares × $25  = $125   →  125/365 = 34.25%  → 34%
        """
        holdings = {
            'AXP': _holding('AXP', 2.0, 200.0, 'American Express'),
            'IAU': _holding('IAU', 5.0, 100.0, 'Gold'),
        }
        out = render_holdings(holdings, {'AXP': 120.0, 'IAU': 25.0})
        assert ' 66%' in out
        assert ' 34%' in out

    def test_all_prices_missing_shows_na_for_all(self):
        holdings = {
            'AXP': _holding('AXP', 2.0, 200.0),
            'IAU': _holding('IAU', 5.0, 100.0),
        }
        out = render_holdings(holdings, {})
        assert out.count('n/a') >= 2


# ── render_gains ────────────────────────────────────────────────────────────────

class TestRenderGains:
    def test_no_sells_shows_no_realized(self):
        txns = [_txn(action='buy')]
        out  = render_gains(txns, {'AXP': _holding()}, {'AXP': 120.0})
        assert 'No realized gains yet' in out

    def test_sell_pnl_appears(self):
        txns = [_txn(action='sell', realized_pnl=25.0)]
        out  = render_gains(txns, {}, {})
        assert '+$25.00' in out

    def test_unrealized_gain_shown(self):
        # 2 shares at $200 cost; price $120 → value $240, gain +$40
        out = render_gains([], {'AXP': _holding(shares=2.0, cost=200.0)}, {'AXP': 120.0})
        assert '+$40.00' in out

    def test_summary_section_present(self):
        out = render_gains([], {}, {})
        assert 'SUMMARY' in out

    def test_realized_and_unrealized_labels(self):
        out = render_gains([], {}, {})
        assert 'REALIZED' in out
        assert 'UNREALIZED' in out

    def test_ticker_filter_excludes_other_tickers(self):
        """--ticker AXP should hide IAU sells and IAU holdings entirely."""
        sells = [
            _txn('AXP', 'sell', realized_pnl=25.0),
            _txn('IAU', 'sell', realized_pnl=10.0),
        ]
        holdings = {
            'AXP': _holding('AXP'),
            'IAU': _holding('IAU', label='Gold'),
        }
        out = render_gains(sells, holdings, {'AXP': 120.0, 'IAU': 25.0}, ticker='AXP')
        assert 'AXP' in out
        assert 'IAU' not in out

    def test_ticker_filter_shows_only_matching_sell(self):
        sells = [
            _txn('AXP', 'sell', realized_pnl=25.0),
            _txn('NVDA', 'sell', realized_pnl=100.0),
        ]
        out = render_gains(sells, {}, {}, ticker='AXP')
        assert '+$25.00' in out
        assert '+$100.00' not in out

    def test_summary_totals_reflect_filter(self):
        """When filtered to one ticker, the summary combined P&L is for that ticker only."""
        sells = [
            _txn('AXP',  'sell', realized_pnl=20.0),
            _txn('NVDA', 'sell', realized_pnl=50.0),
        ]
        out_axp  = render_gains(sells, {}, {}, ticker='AXP')
        out_all  = render_gains(sells, {}, {})
        # AXP-filtered combined should be +$20, not +$70
        assert '+$20.00' in out_axp
        assert '+$70.00' not in out_axp
        # Unfiltered should show both
        assert '+$70.00' in out_all


# ── render_history ──────────────────────────────────────────────────────────────

class TestRenderHistory:
    def test_empty_returns_no_transactions_message(self):
        assert 'No transactions' in render_history([])

    def test_ticker_and_action_appear(self):
        out = render_history([_txn('AXP', 'buy')])
        assert 'AXP' in out
        assert 'buy' in out

    def test_newest_first(self):
        t1 = _txn('AXP', timestamp='2026-01-01T10:00:00')
        t2 = _txn('IAU', timestamp='2026-04-13T14:00:00')
        out = render_history([t1, t2])
        assert out.index('IAU') < out.index('AXP')

    def test_ticker_filter(self):
        out = render_history([_txn('AXP'), _txn('IAU')], ticker='AXP')
        assert 'AXP' in out
        assert 'IAU' not in out

    def test_limit(self):
        txns = [_txn('AXP', timestamp=f'2026-01-{i:02d}T00:00:00') for i in range(1, 6)]
        out  = render_history(txns, limit=2)
        assert '2 transactions' in out

    def test_sell_pnl_shown_in_history(self):
        out = render_history([_txn('AXP', 'sell', realized_pnl=15.0)])
        assert '+$15.00' in out

    def test_transaction_count_in_footer(self):
        out = render_history([_txn('AXP'), _txn('IAU')])
        assert '2 transactions' in out

    def test_empty_filter_no_match(self):
        out = render_history([_txn('AXP')], ticker='NVDA')
        assert 'No transactions' in out
