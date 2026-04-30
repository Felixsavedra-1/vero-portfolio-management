"""
morning_brief.py — Daily portfolio and market snapshot.

Usage:
    python morning_brief.py
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from config import (
    BENCHMARK,
    BRIEF_TIMEZONE,
    BRIEF_WINDOW_1D,
    BRIEF_WINDOW_1W,
    BRIEF_WINDOW_1M,
    GLOBAL_INDICES,
    HOLDINGS_FILE,
    INTEREST_PAYMENT_DAY,
    MOMENTUM_FLAT_BAND,
    MUTUAL_FUNDS,
    RISK_FREE_RATE,
    RISK_MIN_OBSERVATIONS,
    SAVINGS_FILE,
    TRANSACTIONS_FILE,
    WATCHLIST,
)
import dashboard as _dashboard
from display import _pct as _display_pct
from ledger import (
    Holding, SavingsAccount, Transaction,
    _payment_dates, accrued_interest, projected_next_payment,
    load_holdings, load_savings, load_transactions,
)
from metrics import cost_basis_weights, momentum_signal, risk_snapshot as _compute_risk_snapshot
from prices import yf_warnings

logger = logging.getLogger(__name__)

ET = ZoneInfo(BRIEF_TIMEZONE)

_GREEN  = '\033[38;2;0;213;111m'
_RED    = '\033[38;2;215;18;0m'
_ORANGE = '\033[38;2;255;128;3m'
_BRAND  = '\033[38;2;123;112;96m'
_RESET  = '\033[0m'

_SIGNAL_ARROW = {
    'BULLISH': f'{_GREEN}▲{_RESET}',
    'BEARISH': f'{_RED}▼{_RESET}',
    'NEUTRAL': f'{_ORANGE}~{_RESET}',
}


class MorningBrief:

    def __init__(
        self,
        holdings: dict[str, Holding],
        indices: dict[str, str],
        benchmark: str = BENCHMARK,
        risk_free_rate: float = RISK_FREE_RATE,
        watchlist: dict[str, str] | None = None,
        mutual_funds: frozenset[str] | None = None,
        savings: list[SavingsAccount] | None = None,
        transactions: list[Transaction] | None = None,
    ) -> None:
        self.holdings       = holdings
        self.indices        = indices
        self.benchmark      = benchmark
        self.risk_free_rate = risk_free_rate
        self.watchlist:      dict[str, str]       = watchlist or {}
        self.mutual_funds:   frozenset[str]       = mutual_funds or frozenset()
        self._savings:       list[SavingsAccount] = savings or []
        self._transactions:  list[Transaction]    = transactions or []
        self._prices:        pd.DataFrame         = pd.DataFrame()

    def fetch(self) -> None:
        """
        Starts from Dec 28 of the prior year to capture the YTD baseline close,
        and from the earliest holding start_date to cover full portfolio history.
        """
        ytd_anchor = f"{datetime.now().year - 1}-12-28"
        earliest   = min(
            (h.start_date for h in self.holdings.values()),
            default=ytd_anchor,
        )
        start = min(earliest, ytd_anchor)

        tickers = list(dict.fromkeys(
            list(self.holdings.keys())
            + list(self.indices.values())
            + [self.benchmark]
            + list(self.watchlist.keys())
        ))

        try:
            with yf_warnings():
                raw = yf.download(tickers, start=start, auto_adjust=True, progress=False)
        except Exception as exc:
            raise ValueError(f"Failed to fetch market data: {exc}") from exc

        if raw.empty:
            raise ValueError("No data returned. Check your network connection or ticker symbols.")

        close = raw['Close'] if isinstance(raw.columns, pd.MultiIndex) else raw[['Close']]
        if isinstance(close, pd.Series):
            close = close.to_frame()

        self._prices = close

    def _period_return(self, ticker: str, n_trading_days: int) -> float:
        """Uses the last n+1 data points so calendar gaps don't inflate the window."""
        if ticker not in self._prices.columns:
            return float('nan')
        s = self._prices[ticker].dropna()
        if len(s) < n_trading_days + 1:
            return float('nan')
        return float(s.iloc[-1] / s.iloc[-(n_trading_days + 1)] - 1)

    def _ytd_return(self, ticker: str) -> float:
        if ticker not in self._prices.columns:
            return float('nan')
        s = self._prices[ticker].dropna()
        current_year = datetime.now().year
        prior   = s[s.index.year < current_year]
        current = s[s.index.year == current_year]
        if prior.empty or current.empty:
            return float('nan')
        return float(current.iloc[-1] / prior.iloc[-1] - 1)

    def _data_label(self, ticker: str) -> str:
        if ticker not in self._prices.columns:
            return 'no data'
        s = self._prices[ticker].dropna()
        if s.empty:
            return 'no data'
        last: date = pd.Timestamp(s.index[-1]).date()
        today: date = datetime.now(ET).date()
        delta = (today - last).days
        if delta == 0:
            return 'today'
        if delta == 1:
            return 'yesterday'
        return f'as of {last.strftime("%b %-d")}'

    def _portfolio_return_series(self) -> pd.Series:
        """dropna(how='any') aligns to equity trading days, excluding BTC weekend sessions."""
        weights = cost_basis_weights(self.holdings)
        cols    = [t for t in weights if t in self._prices.columns]
        if not cols:
            return pd.Series(dtype=float)
        filled  = self._prices[cols].ffill()
        daily   = (filled / filled.shift(1) - 1).dropna(how='any')
        w       = pd.Series({t: weights[t] for t in cols})
        w      /= w.sum()
        return daily.mul(w, axis=1).sum(axis=1)

    def _risk_snapshot(self) -> dict:
        return _compute_risk_snapshot(
            self._portfolio_return_series(),
            self.risk_free_rate,
            RISK_MIN_OBSERVATIONS,
        )


    def _current_price(self, ticker: str) -> float:
        if ticker not in self._prices.columns:
            return float('nan')
        s = self._prices[ticker].dropna()
        return float(s.iloc[-1]) if not s.empty else float('nan')

    def latest_prices(self) -> dict[str, float]:
        """Most-recent close per holding ticker. Tickers with no data are omitted."""
        return {
            t: float(self._prices[t].dropna().iloc[-1])
            for t in self.holdings
            if t in self._prices.columns and not self._prices[t].dropna().empty
        }

    def previous_prices(self) -> dict[str, float]:
        """Penultimate close per holding ticker (for day-change). Tickers with <2 obs omitted."""
        return {
            t: float(self._prices[t].dropna().iloc[-2])
            for t in self.holdings
            if t in self._prices.columns and len(self._prices[t].dropna()) >= 2
        }

    def _current_portfolio_value(self) -> float:
        """Falls back to cost basis for any ticker missing from price data."""
        total = 0.0
        for ticker, h in self.holdings.items():
            price = self._current_price(ticker)
            total += h.shares * price if np.isfinite(price) else h.cost
        return total

    @staticmethod
    def _arrow(val: float) -> str:
        if not np.isfinite(val):
            return ' '
        return f'{_GREEN}▲{_RESET}' if val >= 0 else f'{_RED}▼{_RESET}'

    @staticmethod
    def _pct(val: float) -> str:
        return _display_pct(val)

    @staticmethod
    def _dollar(val: float) -> str:
        if not np.isfinite(val):
            return 'n/a'
        sign = '+' if val >= 0 else '-'
        return f'{sign}${abs(val):.2f}'

    _DIV = '─' * 76

    def render(self) -> None:
        bar = '═' * 68
        now = datetime.now(ET)
        print(f'\n{bar}')
        print(f"  Vero  ·  {now.strftime('%A, %B %-d, %Y  %-I:%M %p ET')}")
        print(f"  {_BRAND}@vedra&co{_RESET}")
        print(bar)
        if self._savings:
            self._render_savings()
        self._render_portfolio()
        if self.watchlist:
            self._render_watchlist()
        if self.indices:
            self._render_global_markets()
        self._render_risk()
        print(f'\n{bar}\n')

    def _render_savings(self) -> None:
        total_balance  = sum(a.balance for a in self._savings)
        total_interest = sum(a.monthly_interest for a in self._savings)
        show_bank      = any(a.bank for a in self._savings)
        show_accrual   = INTEREST_PAYMENT_DAY is not None

        today      = date.today()
        days_until = 0
        if show_accrual:
            _, next_date = _payment_dates(INTEREST_PAYMENT_DAY, today)
            days_until   = (next_date - today).days

        # Header
        print(f'\n{_BRAND}Savings{_RESET}\n')
        bank_pfx = f"  {'Bank':<10} " if show_bank else '  '
        acct_w   = 20 if show_bank else 22
        base_hdr = f"{bank_pfx}{'Account':<{acct_w}} {'Balance':>12}   {'APY':>5}   {'Interest/mo':>14}"
        accrual_hdr = f"   {'Accrued':>12}   {'Next Pmt':>16}" if show_accrual else ''
        print(base_hdr + accrual_hdr)
        print(f'  {self._DIV}')

        total_accrued = total_proj = 0.0
        for a in self._savings:
            bal_str = f'${a.balance:,.2f}'
            int_str = f'+${a.monthly_interest:,.2f}/mo'
            bank_col = f'{a.bank:<10} ' if show_bank else ''
            base_row = f'  {bank_col}{a.name:<{acct_w}} {bal_str:>12}   {a.apy:>5.2%}   {int_str:>14}'
            if show_accrual:
                acc  = accrued_interest(a, INTEREST_PAYMENT_DAY, today)
                proj = projected_next_payment(a, INTEREST_PAYMENT_DAY, today)
                total_accrued += acc
                total_proj    += proj
                print(f'{base_row}   {f"+${acc:,.2f}":>12}   {f"+${proj:,.2f} in {days_until}d":>16}')
            else:
                print(base_row)

        # Totals
        print(f'  {self._DIV}')
        bank_col   = f'{"":10} ' if show_bank else ''
        bal_str    = f'${total_balance:,.2f}'
        int_str    = f'+${total_interest:,.2f}/mo'
        total_base = f'  {bank_col}{"Total":<{acct_w}} {bal_str:>12}          {int_str:>14}'
        if show_accrual:
            print(f'{total_base}   {f"+${total_accrued:,.2f}":>12}   {f"+${total_proj:,.2f}":>16}')
        else:
            print(total_base)

    def _render_portfolio(self) -> None:
        current_value  = self._current_portfolio_value()
        total_invested = sum(h.cost for h in self.holdings.values())
        start_label    = (
            datetime.strptime(min(h.start_date for h in self.holdings.values()), '%Y-%m-%d').strftime('%b %-d, %Y')
            if self.holdings else '—'
        )

        print(f'\n{_BRAND}Portfolio{_RESET}\n')
        print(f'  Value     ${current_value:,.2f}')
        print(f'  Invested  ${total_invested:,.2f}  ·  since {start_label}')
        print()
        print(f"  {'Ticker':<9} {'Price':>9}  {'Wt':>4}  {'$P&L':>9}  {'1D':>8}  {'1W':>8}  {'1M':>8}  {'YTD':>8}")
        print(f'  {self._DIV}')

        total_dollar_pnl, components = self._render_holding_rows(current_value)

        def agg(k):
            return sum(components[k]) if components[k] else float('nan')

        p1d, p1w, p1m, pytd = agg('1d'), agg('1w'), agg('1m'), agg('ytd')

        print(f'  {self._DIV}')
        print(
            f"  {'Portfolio':<9} {'—':>9}  {'—':>4}  {self._dollar(total_dollar_pnl):>9}  "
            f'{self._pct(p1d):>8}  {self._pct(p1w):>8}  {self._pct(p1m):>8}  {self._pct(pytd):>8}'
        )
        self._render_benchmark_alpha(p1d, p1w, p1m, pytd)

        if self.mutual_funds & set(self.holdings):
            print('\n  * Mutual fund NAV updated after 4 PM ET — reflects prior close.')

    def _render_holding_rows(self, current_value: float) -> tuple:
        """Per-holding rows. Returns (total_dollar_pnl, weighted return components)."""
        components: dict[str, list] = {'1d': [], '1w': [], '1m': [], 'ytd': []}
        total_dollar_pnl = 0.0

        for ticker, h in self.holdings.items():
            r1d  = self._period_return(ticker, BRIEF_WINDOW_1D)
            r1w  = self._period_return(ticker, BRIEF_WINDOW_1W)
            r1m  = self._period_return(ticker, BRIEF_WINDOW_1M)
            rytd = self._ytd_return(ticker)

            cur_price = self._current_price(ticker)
            pos_value = h.shares * cur_price
            price_str = f'${cur_price:,.2f}' if np.isfinite(cur_price) else 'n/a'
            dollar    = pos_value * r1d
            if np.isfinite(dollar):
                total_dollar_pnl += dollar

            weight     = pos_value / current_value if np.isfinite(pos_value) and current_value > 0 else float('nan')
            weight_str = self._pct(weight) if np.isfinite(weight) else 'n/a'
            flag       = ' *' if ticker in self.mutual_funds else '  '

            print(
                f'  {ticker:<9} {price_str:>9}  {weight_str:>4}  '
                f'{self._dollar(dollar):>9}  '
                f'{self._pct(r1d):>8}  {self._pct(r1w):>8}  {self._pct(r1m):>8}  {self._pct(rytd):>8}'
                f'{flag}'
            )

            gain_dollar = pos_value - h.cost
            gain_pct    = gain_dollar / h.cost if h.cost > 0 else float('nan')
            mkt_str  = f'${pos_value:,.2f}'
            cost_str = f'${h.cost:,.2f}'
            gain_str = self._dollar(gain_dollar)
            pct_str  = self._pct(gain_pct)
            print(f'            mkt {mkt_str}  ·  cost {cost_str}  ·  gain {gain_str} ({pct_str})')

            for key, val in [('1d', r1d), ('1w', r1w), ('1m', r1m), ('ytd', rytd)]:
                if np.isfinite(val) and np.isfinite(weight):
                    components[key].append(val * weight)

        return total_dollar_pnl, components

    def _render_benchmark_alpha(self, p1d: float, p1w: float, p1m: float, pytd: float) -> None:
        if self.benchmark not in self._prices.columns:
            return
        b1d  = self._period_return(self.benchmark, BRIEF_WINDOW_1D)
        b1w  = self._period_return(self.benchmark, BRIEF_WINDOW_1W)
        b1m  = self._period_return(self.benchmark, BRIEF_WINDOW_1M)
        bytd = self._ytd_return(self.benchmark)

        def alpha(p, b):
            return p - b if np.isfinite(p) and np.isfinite(b) else float('nan')

        print(
            f"  {'S&P 500':<9} {'—':>9}  {'—':>4}  {'—':>9}  "
            f'{self._pct(b1d):>8}  {self._pct(b1w):>8}  {self._pct(b1m):>8}  {self._pct(bytd):>8}'
        )
        print(
            f"  {'Alpha':<9} {'—':>9}  {'—':>4}  {'—':>9}  "
            f'{self._pct(alpha(p1d, b1d)):>8}  {self._pct(alpha(p1w, b1w)):>8}  '
            f'{self._pct(alpha(p1m, b1m)):>8}  {self._pct(alpha(pytd, bytd)):>8}'
        )

    def _render_watchlist(self) -> None:
        print(f'\n{_BRAND}Watchlist{_RESET}\n')
        print(f"  {'Company':<20} {'Ticker':<6} {'Price':>9}  {'1D':>8}  {'1W':>8}  {'1M':>8}   Signal")
        print(f'  {self._DIV}')
        for ticker, label in self.watchlist.items():
            r1d    = self._period_return(ticker, BRIEF_WINDOW_1D)
            r1w    = self._period_return(ticker, BRIEF_WINDOW_1W)
            r1m    = self._period_return(ticker, BRIEF_WINDOW_1M)
            signal, reason = self._watchlist_signal(ticker)
            arrow  = _SIGNAL_ARROW[signal]
            price     = self._current_price(ticker)
            price_str = f'${price:,.2f}' if np.isfinite(price) else 'n/a'
            print(
                f'  {label:<20} {ticker:<6} {price_str:>9}  '
                f'{self._pct(r1d):>8}  {self._pct(r1w):>8}  {self._pct(r1m):>8}   '
                f'{arrow} {signal:<7}  {reason}'
            )

    def _render_global_markets(self) -> None:
        print(f'\n{_BRAND}Global markets{_RESET}  (local currency)\n')
        for label, ticker in self.indices.items():
            r = self._period_return(ticker, BRIEF_WINDOW_1D)
            print(f'  {label:<26}  {self._arrow(r)}  {self._pct(r):>8}   {self._data_label(ticker)}')

    def _render_risk(self) -> None:
        risk = self._risk_snapshot()
        if not risk:
            return
        ci     = risk['sharpe_ci']
        ci_str = f"[{ci[0]:.2f}, {ci[1]:.2f}]" if np.isfinite(ci[0]) else 'n/a'
        print(f'\n{_BRAND}Risk snapshot{_RESET}  (trailing 1 year)\n')
        print(
            f"  Sharpe {risk['sharpe']:.2f} {ci_str}  ·  "
            f"Volatility {risk['volatility']:.1%}  ·  "
            f"Max Drawdown {risk['max_drawdown']:.1%}"
        )

    def _watchlist_signal(self, ticker: str) -> tuple:
        return momentum_signal(
            self._period_return(ticker, BRIEF_WINDOW_1D),
            self._period_return(ticker, BRIEF_WINDOW_1W),
            self._period_return(ticker, BRIEF_WINDOW_1M),
            MOMENTUM_FLAT_BAND,
        )


def main() -> None:
    logging.basicConfig(level=logging.WARNING)

    holdings     = load_holdings(HOLDINGS_FILE)
    savings      = load_savings(SAVINGS_FILE)
    transactions = load_transactions(TRANSACTIONS_FILE)

    if not holdings:
        print("No holdings found. Run: python portfolio.py buy TICKER DOLLARS")
        return

    brief = MorningBrief(
        holdings=holdings,
        indices=GLOBAL_INDICES,
        benchmark=BENCHMARK,
        risk_free_rate=RISK_FREE_RATE,
        watchlist=WATCHLIST,
        mutual_funds=MUTUAL_FUNDS,
        savings=savings,
        transactions=transactions,
    )
    brief.fetch()
    brief.render()

    # Reuse prices already fetched for the brief — no second network call.
    out_path = _dashboard.build_html(_dashboard.build_payload(
        prices=brief.latest_prices(),
        prev_prices=brief.previous_prices(),
    ))
    try:
        import webbrowser
        webbrowser.open(out_path.as_uri())
    except Exception:
        print(f"\n  Dashboard → {out_path.as_uri()}\n")


if __name__ == '__main__':
    main()
