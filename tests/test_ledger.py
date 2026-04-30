"""
tests/test_ledger.py — Unit tests for ledger.py.

All tests are network-free. Disk I/O uses pytest's tmp_path fixture.
"""

import json

import pytest

from ledger import (
    Holding,
    SavingsAccount,
    Transaction,
    append_transaction,
    load_holdings,
    load_savings,
    load_transactions,
    save_holdings,
    save_savings,
)
from metrics import cost_basis_weights, market_value_weights


# ── Fixtures ────────────────────────────────────────────────────────────────────

def _holdings() -> dict[str, Holding]:
    return {
        'AXP': Holding(ticker='AXP', shares=3.0,   cost=300.0,
                       first_purchase='2023-01-03T09:30:00', label='American Express'),
        'IAU': Holding(ticker='IAU', shares=5.0,   cost=100.0,
                       first_purchase='2023-06-01T10:00:00', label='Gold (iShares)'),
        'BTC': Holding(ticker='BTC', shares=0.002, cost=100.0,
                       first_purchase='2024-01-01T00:00:00', label='Bitcoin'),
    }


def _txn(ticker='AXP', action='buy', dollars=50.0, price=240.0, realized_pnl=None) -> Transaction:
    ts = '2026-04-13T14:30:22.841504'
    return Transaction(
        id=f'txn_20260413143022841_{ticker}',
        timestamp=ts,
        action=action,
        ticker=ticker,
        shares=dollars / price,
        dollars=dollars,
        price=price,
        realized_pnl=realized_pnl,
        notes='',
    )


# ── Holding properties ──────────────────────────────────────────────────────────

class TestHolding:
    def test_avg_cost_per_share(self):
        h = _holdings()['AXP']  # 3 shares, $300 cost
        assert h.avg_cost_per_share == pytest.approx(100.0)

    def test_avg_cost_zero_shares(self):
        h = Holding(ticker='X', shares=0.0, cost=0.0, first_purchase='2026-01-01', label='X')
        assert h.avg_cost_per_share == 0.0

    def test_start_date_backward_compat(self):
        h = _holdings()['AXP']
        assert h.start_date == '2023-01-03'


# ── Holdings I/O ────────────────────────────────────────────────────────────────

class TestHoldingsIO:
    def test_roundtrip_preserves_all_fields(self, tmp_path):
        path     = tmp_path / 'holdings.json'
        original = _holdings()
        save_holdings(original, path)
        loaded   = load_holdings(path)

        assert set(loaded) == set(original)
        for ticker, h in original.items():
            assert loaded[ticker].shares         == pytest.approx(h.shares)
            assert loaded[ticker].cost           == pytest.approx(h.cost)
            assert loaded[ticker].first_purchase == h.first_purchase
            assert loaded[ticker].label          == h.label

    def test_load_missing_file_returns_empty(self, tmp_path):
        assert load_holdings(tmp_path / 'nope.json') == {}

    def test_atomic_write_no_tmp_left_behind(self, tmp_path):
        path = tmp_path / 'holdings.json'
        save_holdings(_holdings(), path)
        assert not (tmp_path / 'holdings.tmp').exists()
        assert path.exists()

    def test_save_empty_dict(self, tmp_path):
        path = tmp_path / 'holdings.json'
        save_holdings({}, path)
        assert load_holdings(path) == {}

    def test_overwrite_existing_file(self, tmp_path):
        """save_holdings must replace the file entirely, not append to it."""
        path = tmp_path / 'holdings.json'
        save_holdings(_holdings(), path)
        save_holdings({'AXP': _holdings()['AXP']}, path)
        loaded = load_holdings(path)
        assert list(loaded.keys()) == ['AXP']

    def test_pretty_printed_json(self, tmp_path):
        path = tmp_path / 'holdings.json'
        save_holdings(_holdings(), path)
        data = json.loads(path.read_text())
        assert 'AXP' in data
        assert data['AXP']['shares'] == pytest.approx(3.0)

    def test_creates_parent_directory(self, tmp_path):
        path = tmp_path / 'nested' / 'dir' / 'holdings.json'
        save_holdings(_holdings(), path)
        assert path.exists()

    def test_legacy_start_date_field_loaded(self, tmp_path):
        """Holdings written with 'start_date' (old format) are read correctly."""
        path = tmp_path / 'holdings.json'
        path.write_text(json.dumps({
            'AXP': {'shares': 2.0, 'cost': 200.0, 'start_date': '2023-01-03', 'label': 'AXP'}
        }))
        loaded = load_holdings(path)
        assert loaded['AXP'].first_purchase == '2023-01-03'
        assert loaded['AXP'].start_date     == '2023-01-03'

    def test_nan_shares_normalized_to_zero(self, tmp_path):
        """NaN shares (from corrupt migration) are coerced to 0.0, not raised."""
        path = tmp_path / 'holdings.json'
        # json module writes NaN as a bare token — write raw to simulate corrupt file
        path.write_text('{"AXP": {"shares": NaN, "cost": 300.0, "start_date": "2023-01-01", "label": "AXP"}}')
        loaded = load_holdings(path)
        assert loaded['AXP'].shares == 0.0


# ── Transactions I/O ────────────────────────────────────────────────────────────

class TestTransactionsIO:
    def test_append_creates_file(self, tmp_path):
        path = tmp_path / 'txns.json'
        append_transaction(_txn(), path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert len(data) == 1

    def test_append_accumulates(self, tmp_path):
        path = tmp_path / 'txns.json'
        append_transaction(_txn('AXP'), path)
        append_transaction(_txn('IAU'), path)
        data = json.loads(path.read_text())
        assert len(data) == 2
        assert data[1]['ticker'] == 'IAU'

    def test_atomic_write_no_tmp_left(self, tmp_path):
        path = tmp_path / 'txns.json'
        append_transaction(_txn(), path)
        assert not (tmp_path / 'txns.tmp').exists()

    def test_load_missing_file_returns_empty(self, tmp_path):
        assert load_transactions(tmp_path / 'nope.json') == []

    def test_roundtrip_preserves_fields(self, tmp_path):
        path = tmp_path / 'txns.json'
        original = _txn('AXP', 'sell', 50.0, 250.0, realized_pnl=10.0)
        append_transaction(original, path)
        loaded = load_transactions(path)
        assert len(loaded) == 1
        t = loaded[0]
        assert t.ticker       == 'AXP'
        assert t.action       == 'sell'
        assert t.shares       == pytest.approx(50.0 / 250.0)
        assert t.realized_pnl == pytest.approx(10.0)

    def test_legacy_date_and_type_fields(self, tmp_path):
        """Old records with 'date' and 'type' fields are normalized on load."""
        path = tmp_path / 'txns.json'
        path.write_text(json.dumps([{
            'date': '2023-01-03', 'type': 'BUY',
            'ticker': 'AXP', 'shares': 2.0, 'dollars': 200.0, 'price': 100.0,
        }]))
        loaded = load_transactions(path)
        assert loaded[0].timestamp == '2023-01-03'
        assert loaded[0].action    == 'buy'

    def test_creates_parent_directory(self, tmp_path):
        path = tmp_path / 'nested' / 'txns.json'
        append_transaction(_txn(), path)
        assert path.exists()

    def test_buy_realized_pnl_is_null(self, tmp_path):
        """BUY transactions must always store realized_pnl as None."""
        path = tmp_path / 'txns.json'
        append_transaction(_txn(action='buy', realized_pnl=None), path)
        raw = json.loads(path.read_text())
        assert raw[0]['realized_pnl'] is None

    def test_sell_realized_pnl_stored(self, tmp_path):
        """SELL transactions must persist the realized_pnl dollar amount."""
        path = tmp_path / 'txns.json'
        append_transaction(_txn(action='sell', realized_pnl=42.50), path)
        raw = json.loads(path.read_text())
        assert raw[0]['realized_pnl'] == pytest.approx(42.50)



# ── Weight helpers ──────────────────────────────────────────────────────────────

class TestCostBasisWeights:
    def test_sum_to_one(self):
        w = cost_basis_weights(_holdings())
        assert sum(w.values()) == pytest.approx(1.0)

    def test_correct_proportions(self):
        # AXP=$300, IAU=$100, BTC=$100 → 0.60, 0.20, 0.20
        w = cost_basis_weights(_holdings())
        assert w['AXP'] == pytest.approx(0.60)
        assert w['IAU'] == pytest.approx(0.20)
        assert w['BTC'] == pytest.approx(0.20)

    def test_single_holding_is_one(self):
        h = {'X': Holding('X', 1.0, 500.0, '2026-01-01', 'X')}
        assert cost_basis_weights(h)['X'] == pytest.approx(1.0)

    def test_zero_cost_returns_empty(self):
        h = {'X': Holding('X', 0.0, 0.0, '2026-01-01', 'X')}
        assert cost_basis_weights(h) == {}


# ── Savings I/O ─────────────────────────────────────────────────────────────────

class TestSavingsIO:
    def _accounts(self):
        return [
            SavingsAccount(name='Car Fund',     balance=12_450.00, apy=0.04, bank='Amex'),
            SavingsAccount(name='Housing Fund', balance=38_200.00, apy=0.04, bank='Amex'),
        ]

    def test_roundtrip_preserves_all_fields(self, tmp_path):
        path = tmp_path / 'savings.json'
        save_savings(self._accounts(), path)
        loaded = load_savings(path)
        assert len(loaded) == 2
        assert loaded[0].name    == 'Car Fund'
        assert loaded[0].balance == pytest.approx(12_450.00)
        assert loaded[0].apy     == pytest.approx(0.04)
        assert loaded[0].bank    == 'Amex'

    def test_bank_defaults_to_empty_string(self, tmp_path):
        """Accounts saved without a bank field load with bank='' — backward compatible."""
        path = tmp_path / 'savings.json'
        path.write_text('[{"name": "Emergency", "balance": 5000.0, "apy": 0.04}]')
        loaded = load_savings(path)
        assert loaded[0].bank == ''

    def test_load_missing_file_returns_empty(self, tmp_path):
        assert load_savings(tmp_path / 'nope.json') == []

    def test_atomic_write_no_tmp_left_behind(self, tmp_path):
        path = tmp_path / 'savings.json'
        save_savings(self._accounts(), path)
        assert not (tmp_path / 'savings.tmp').exists()
        assert path.exists()

    def test_save_empty_list(self, tmp_path):
        path = tmp_path / 'savings.json'
        save_savings([], path)
        assert load_savings(path) == []

    def test_monthly_interest_property(self):
        a = SavingsAccount(name='Test', balance=12_000.00, apy=0.04)
        assert a.monthly_interest == pytest.approx(40.0)  # 12000 * 0.04 / 12

    def test_creates_parent_directory(self, tmp_path):
        path = tmp_path / 'nested' / 'savings.json'
        save_savings(self._accounts(), path)
        assert path.exists()


class TestMarketValueWeights:
    def test_sum_to_one(self):
        prices = {'AXP': 100.0, 'IAU': 20.0, 'BTC': 50_000.0}
        w      = market_value_weights(_holdings(), prices)
        assert sum(w.values()) == pytest.approx(1.0)

    def test_correct_proportions(self):
        holdings = {
            'A': Holding('A', 2.0, 200.0, '2026-01-01', 'A'),
            'B': Holding('B', 1.0, 100.0, '2026-01-01', 'B'),
        }
        prices = {'A': 100.0, 'B': 100.0}  # A=$200, B=$100 → 2/3, 1/3
        w      = market_value_weights(holdings, prices)
        assert w['A'] == pytest.approx(2 / 3)
        assert w['B'] == pytest.approx(1 / 3)

    def test_missing_price_excludes_ticker(self):
        prices = {'AXP': 100.0}
        w      = market_value_weights(_holdings(), prices)
        assert 'IAU' not in w
        assert 'BTC' not in w
        assert w['AXP'] == pytest.approx(1.0)

    def test_zero_shares_excluded(self):
        holdings = {'A': Holding('A', 0.0, 0.0, '2026-01-01', 'A')}
        assert market_value_weights(holdings, {'A': 100.0}) == {}
