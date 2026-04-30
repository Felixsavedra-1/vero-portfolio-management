"""
tests/test_morning_brief.py — Unit tests for MorningBrief.

Price data is injected directly into brief._prices, so no network calls
are made. Test holdings use the actual portfolio tickers to keep things
unambiguous.
"""

import numpy as np
import pandas as pd
import pytest

from ledger import Holding, SavingsAccount
from morning_brief import MorningBrief

# ── Helpers ────────────────────────────────────────────────────────────────────

HOLDINGS: dict[str, Holding] = {
    'SWPPX':   Holding(ticker='SWPPX',   shares=5.0,    cost=500.0, first_purchase='2023-01-01', label='Schwab S&P 500'),
    'AXP':     Holding(ticker='AXP',     shares=3.0,    cost=300.0, first_purchase='2023-01-01', label='American Express'),
    'IAU':     Holding(ticker='IAU',     shares=1.0,    cost=100.0, first_purchase='2023-01-01', label='Gold (iShares)'),
    'BTC-USD': Holding(ticker='BTC-USD', shares=0.001,  cost=100.0, first_purchase='2023-01-01', label='Bitcoin'),
}


def _make_brief(**kwargs) -> MorningBrief:
    """Return a MorningBrief with default test holdings; override via kwargs."""
    defaults = dict(
        holdings=HOLDINGS,
        indices={},
        benchmark='SPY',
        risk_free_rate=0.045,
    )
    defaults.update(kwargs)
    return MorningBrief(**defaults)


def _price_series(n: int, mean: float = 0.0005, std: float = 0.012, seed: int = 0) -> np.ndarray:
    """Synthetic daily prices: 100 * cumprod(1 + r_t)."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(mean, std, n)
    return 100.0 * np.cumprod(1 + returns)


def _inject(brief: MorningBrief, data: dict, dates: pd.DatetimeIndex) -> None:
    """Bypass fetch() by setting _prices directly."""
    brief._prices = pd.DataFrame(data, index=dates)


# ── _period_return ─────────────────────────────────────────────────────────────

class TestPeriodReturn:
    def test_known_two_day_return(self):
        brief = _make_brief()
        dates = pd.date_range('2025-01-02', periods=3, freq='B')
        _inject(brief, {'AXP': [100.0, 105.0, 110.25]}, dates)
        result = brief._period_return('AXP', 2)
        assert pytest.approx(result, rel=1e-6) == 0.1025

    def test_one_day_return(self):
        brief = _make_brief()
        dates = pd.date_range('2025-01-02', periods=2, freq='B')
        _inject(brief, {'IAU': [50.0, 51.0]}, dates)
        assert pytest.approx(brief._period_return('IAU', 1), rel=1e-6) == 0.02

    def test_missing_ticker_returns_nan(self):
        brief = _make_brief()
        dates = pd.date_range('2025-01-02', periods=5, freq='B')
        _inject(brief, {'AXP': _price_series(5)}, dates)
        assert np.isnan(brief._period_return('SWPPX', 1))

    def test_insufficient_history_returns_nan(self):
        brief = _make_brief()
        dates = pd.date_range('2025-01-02', periods=3, freq='B')
        _inject(brief, {'BTC-USD': [30_000.0, 31_000.0, 32_000.0]}, dates)
        assert np.isnan(brief._period_return('BTC-USD', 5))

    def test_negative_return(self):
        brief = _make_brief()
        dates = pd.date_range('2025-01-02', periods=2, freq='B')
        _inject(brief, {'IAU': [100.0, 90.0]}, dates)
        assert pytest.approx(brief._period_return('IAU', 1), rel=1e-6) == -0.10


# ── _ytd_return ────────────────────────────────────────────────────────────────

class TestYtdReturn:
    def _make_ytd_prices(self, prior_close: float, current_close: float) -> MorningBrief:
        """Two-point price series: one in prior year, one in current year."""
        from datetime import datetime
        current_year = datetime.now().year
        brief = _make_brief()
        dates = pd.DatetimeIndex([f'{current_year - 1}-12-31', f'{current_year}-04-01'])
        _inject(brief, {'SWPPX': [prior_close, current_close]}, dates)
        return brief

    def test_positive_ytd(self):
        brief = self._make_ytd_prices(100.0, 110.0)
        assert brief._ytd_return('SWPPX') == pytest.approx(0.10, rel=1e-6)

    def test_negative_ytd(self):
        brief = self._make_ytd_prices(200.0, 180.0)
        assert brief._ytd_return('SWPPX') == pytest.approx(-0.10, rel=1e-6)

    def test_missing_ticker_returns_nan(self):
        brief = self._make_ytd_prices(100.0, 110.0)
        assert np.isnan(brief._ytd_return('AXP'))

    def test_no_prior_year_data_returns_nan(self):
        brief = _make_brief()
        dates = pd.date_range('2025-01-02', periods=5, freq='B')
        _inject(brief, {'IAU': _price_series(5)}, dates)
        assert np.isnan(brief._ytd_return('IAU'))


# ── _risk_snapshot ─────────────────────────────────────────────────────────────

class TestRiskSnapshot:
    def _make_risk_brief(self, n: int = 300) -> MorningBrief:
        """Inject synthetic price series for all four holdings."""
        brief = _make_brief()
        dates = pd.date_range('2024-01-02', periods=n, freq='B')
        data = {
            'SWPPX':   _price_series(n, seed=1),
            'AXP':     _price_series(n, seed=2),
            'IAU':     _price_series(n, seed=3),
            'BTC-USD': _price_series(n, seed=4),
        }
        _inject(brief, data, dates)
        return brief

    def test_returns_dict_with_expected_keys(self):
        brief = self._make_risk_brief()
        risk = brief._risk_snapshot()
        assert set(risk.keys()) == {'sharpe', 'sharpe_ci', 'volatility', 'max_drawdown'}

    def test_volatility_is_positive(self):
        brief = self._make_risk_brief()
        assert brief._risk_snapshot()['volatility'] > 0

    def test_max_drawdown_is_non_positive(self):
        brief = self._make_risk_brief()
        assert brief._risk_snapshot()['max_drawdown'] <= 0

    def test_sharpe_ci_lower_lt_upper(self):
        brief = self._make_risk_brief()
        ci = brief._risk_snapshot()['sharpe_ci']
        assert ci[0] < ci[1]

    def test_too_few_observations_returns_empty_dict(self):
        brief = _make_brief()
        dates = pd.date_range('2025-01-02', periods=30, freq='B')
        data = {
            'SWPPX':   _price_series(30, seed=1),
            'AXP':     _price_series(30, seed=2),
            'IAU':     _price_series(30, seed=3),
            'BTC-USD': _price_series(30, seed=4),
        }
        _inject(brief, data, dates)
        assert brief._risk_snapshot() == {}

    def test_flat_prices_returns_empty_dict(self):
        """Zero volatility → Sharpe undefined; should return {}."""
        brief = _make_brief()
        dates = pd.date_range('2025-01-02', periods=300, freq='B')
        data = {t: np.ones(300) * 100.0 for t in HOLDINGS}
        _inject(brief, data, dates)
        assert brief._risk_snapshot() == {}


# ── Formatting helpers ─────────────────────────────────────────────────────────

class TestFormatHelpers:
    def test_pct_positive(self):
        assert MorningBrief._pct(0.0110) == '+1.10%'

    def test_pct_negative(self):
        assert MorningBrief._pct(-0.032) == '-3.20%'

    def test_pct_nan(self):
        assert MorningBrief._pct(float('nan')) == 'n/a'

    def test_dollar_positive(self):
        assert MorningBrief._dollar(5.5) == '+$5.50'

    def test_dollar_negative(self):
        assert MorningBrief._dollar(-0.21) == '-$0.21'

    def test_dollar_nan(self):
        assert MorningBrief._dollar(float('nan')) == 'n/a'

    def test_arrow_positive(self):
        assert '▲' in MorningBrief._arrow(0.01)

    def test_arrow_negative(self):
        assert '▼' in MorningBrief._arrow(-0.01)

    def test_arrow_zero(self):
        assert '▲' in MorningBrief._arrow(0.0)

    def test_arrow_nan(self):
        assert MorningBrief._arrow(float('nan')) == ' '


# ── _current_portfolio_value ───────────────────────────────────────────────────

class TestCurrentPortfolioValue:
    def test_50pct_gain_reflects_in_value(self):
        """
        AXP: 3 shares bought at $100 = $300 cost.
        Price rises to $150 → current value = 3 × $150 = $450.
        """
        h = Holding(ticker='AXP', shares=3.0, cost=300.0, first_purchase='2023-01-03', label='AXP')
        brief = _make_brief(holdings={'AXP': h})
        dates = pd.date_range('2023-01-03', periods=3, freq='B')
        _inject(brief, {'AXP': [100.0, 120.0, 150.0]}, dates)
        assert brief._current_portfolio_value() == pytest.approx(450.0, rel=1e-6)

    def test_multiple_holdings_sum_correctly(self):
        """
        SWPPX: 5 shares × $110 = $550
        AXP:   3 shares × $120 = $360
        Total = $910
        """
        holdings = {
            'SWPPX': Holding(ticker='SWPPX', shares=5.0, cost=500.0, first_purchase='2023-01-01', label='SWPPX'),
            'AXP':   Holding(ticker='AXP',   shares=3.0, cost=300.0, first_purchase='2023-01-01', label='AXP'),
        }
        brief = _make_brief(holdings=holdings)
        dates = pd.date_range('2025-01-02', periods=2, freq='B')
        _inject(brief, {'SWPPX': [100.0, 110.0], 'AXP': [100.0, 120.0]}, dates)
        assert brief._current_portfolio_value() == pytest.approx(910.0, rel=1e-6)

    def test_missing_price_data_falls_back_to_cost(self):
        """Ticker with no price data contributes its cost basis to total value."""
        h = Holding(ticker='AXP', shares=3.0, cost=300.0, first_purchase='2023-01-01', label='AXP')
        brief = _make_brief(holdings={'AXP': h})
        brief._prices = pd.DataFrame()  # no price data at all
        assert brief._current_portfolio_value() == pytest.approx(300.0, rel=1e-6)


# ── _watchlist_signal ──────────────────────────────────────────────────────────

class TestWatchlistSignal:
    """
    Price series are 25 business days long — enough for _period_return(21)
    which requires iloc[-22].  Linspace prices make the expected direction
    of each period unambiguous.
    """

    def _brief_with_prices(self, ticker: str, prices) -> MorningBrief:
        brief = _make_brief(watchlist={ticker: ticker})
        dates = pd.date_range('2025-01-02', periods=len(prices), freq='B')
        brief._prices = pd.DataFrame({ticker: prices}, index=dates)
        return brief

    def test_bullish_dip_in_uptrend(self):
        """1M positive, 1D negative → dip in uptrend."""
        prices = np.linspace(95, 101, 25)
        prices[-1] = 99.0
        brief = self._brief_with_prices('AAPL', prices)
        signal, reason = brief._watchlist_signal('AAPL')
        assert signal == 'BULLISH'
        assert reason == 'dip in uptrend'

    def test_bullish_strong_momentum(self):
        """All three periods positive → strong momentum."""
        prices = np.linspace(90, 110, 25)
        brief = self._brief_with_prices('NVDA', prices)
        signal, reason = brief._watchlist_signal('NVDA')
        assert signal == 'BULLISH'
        assert reason == 'strong momentum'

    def test_bearish_downtrend(self):
        """1M, 1W, 1D all negative → downtrend."""
        prices = np.linspace(110, 90, 25)
        brief = self._brief_with_prices('GOOGL', prices)
        signal, reason = brief._watchlist_signal('GOOGL')
        assert signal == 'BEARISH'
        assert reason == 'downtrend'

    def test_bearish_bounce_in_downtrend(self):
        """1M negative but 1D positive → bounce in downtrend."""
        prices = np.linspace(110, 92, 25)
        prices[-1] = 95.0
        brief = self._brief_with_prices('JPM', prices)
        signal, reason = brief._watchlist_signal('JPM')
        assert signal == 'BEARISH'
        assert reason == 'bounce in downtrend'

    def test_neutral_flat_trend(self):
        """1M within ±1% → neutral, mixed signals."""
        prices = np.ones(25) * 100.0
        brief = self._brief_with_prices('NFLX', prices)
        signal, reason = brief._watchlist_signal('NFLX')
        assert signal == 'NEUTRAL'
        assert reason == 'mixed signals'

    def test_neutral_insufficient_data(self):
        """Fewer than 22 data points → neutral, insufficient data."""
        brief = _make_brief(watchlist={'AAPL': 'Apple'})
        dates = pd.date_range('2025-01-02', periods=10, freq='B')
        brief._prices = pd.DataFrame({'AAPL': np.linspace(100, 110, 10)}, index=dates)
        signal, reason = brief._watchlist_signal('AAPL')
        assert signal == 'NEUTRAL'
        assert reason == 'insufficient data'


# ── _render_savings ────────────────────────────────────────────────────────────

class TestRenderSavings:
    def _accounts(self, bank=''):
        return [
            SavingsAccount(name='Car Fund',     balance=12_450.00, apy=0.04, bank=bank),
            SavingsAccount(name='Housing Fund', balance=38_200.00, apy=0.04, bank=bank),
        ]

    def test_renders_account_names(self, capsys):
        brief = _make_brief(savings=self._accounts())
        brief._render_savings()
        out = capsys.readouterr().out
        assert 'Car Fund' in out
        assert 'Housing Fund' in out

    def test_renders_total_balance(self, capsys):
        brief = _make_brief(savings=self._accounts())
        brief._render_savings()
        out = capsys.readouterr().out
        assert '50,650.00' in out   # 12450 + 38200

    def test_renders_total_monthly_interest(self, capsys):
        brief = _make_brief(savings=self._accounts())
        brief._render_savings()
        out = capsys.readouterr().out
        # Car Fund: 12450*0.04/12=41.50, Housing Fund: 38200*0.04/12=127.33 → 168.83
        assert '168.83' in out

    def test_renders_apy(self, capsys):
        brief = _make_brief(savings=self._accounts())
        brief._render_savings()
        out = capsys.readouterr().out
        assert '4.00%' in out

    def test_bank_column_shown_when_set(self, capsys):
        brief = _make_brief(savings=self._accounts(bank='Amex'))
        brief._render_savings()
        out = capsys.readouterr().out
        assert 'Amex' in out
        assert 'Bank' in out

    def test_bank_column_hidden_when_not_set(self, capsys):
        brief = _make_brief(savings=self._accounts(bank=''))
        brief._render_savings()
        out = capsys.readouterr().out
        assert 'Bank' not in out

    def test_no_savings_section_when_empty(self, capsys):
        brief = _make_brief()  # no savings kwarg
        dates = pd.date_range('2025-01-02', periods=2, freq='B')
        _inject(brief, {t: [100.0, 110.0] for t in HOLDINGS}, dates)
        brief.render()
        out = capsys.readouterr().out
        assert 'Savings' not in out
