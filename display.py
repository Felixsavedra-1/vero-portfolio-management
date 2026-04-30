"""
display.py — Terminal output formatting.

Pure functions — no I/O, no side effects.
"""

from __future__ import annotations

import math

from ledger import Holding, Transaction

_W_HOLDINGS = 100
_W_GAINS    = 76
_W_HISTORY  = 88


def _pct(val: float) -> str:
    return f'{val:+.2%}' if math.isfinite(val) else 'n/a'


def _dollar(val: float, width: int = 0) -> str:
    s = f'${abs(val):,.2f}'
    return s.rjust(width) if width else s


def _signed_dollar(val: float) -> str:
    sign = '+' if val >= 0 else '-'
    return f'{sign}${abs(val):,.2f}'


def _pnl_cell(dollars: float, pct: float) -> str:
    return f'{_signed_dollar(dollars)} ({_pct(pct)})'


def _div(width: int) -> str:
    return '─' * width


def _has_price(ticker: str, prices: dict[str, float], h: Holding) -> bool:
    return ticker in prices and math.isfinite(prices[ticker]) and h.shares > 0


def render_holdings(
    holdings: dict[str, Holding],
    prices: dict[str, float],
) -> str:
    if not holdings:
        return '\n  No holdings yet. Run: portfolio buy TICKER DOLLARS\n'

    # Pre-compute market values so weights are fractions of the true total,
    # not of an incomplete running sum.
    market_values: dict[str, float] = {
        ticker: h.shares * prices[ticker]
        for ticker, h in holdings.items()
        if _has_price(ticker, prices, h)
    }
    total_value    = sum(market_values.values())
    total_invested = sum(h.cost for h in holdings.values())

    lines: list[str] = [
        '',
        _div(_W_HOLDINGS),
        f"  {'Ticker':<10} {'Name':<22} {'Shares':>9}  {'Avg $':>9}  "
        f"{'Invested':>10}  {'Value':>10}  {'P&L':>22}  {'Wt':>5}  Since",
        _div(_W_HOLDINGS),
    ]

    for ticker, h in holdings.items():
        if ticker not in market_values:
            lines.append(
                f'  {ticker:<10} {h.label[:22]:<22} {"—":>9}  {"—":>9}  '
                f'{_dollar(h.cost):>10}  {"n/a":>10}  {"n/a":>22}  {"—":>5}  {h.start_date}'
            )
            continue

        value       = market_values[ticker]
        gain_dollar = round(value - h.cost, 2)
        gain_pct    = gain_dollar / h.cost if h.cost else float('nan')
        weight      = value / total_value if total_value else 0.0

        lines.append(
            f'  {ticker:<10} {h.label[:22]:<22} {h.shares:>9.4f}  {h.avg_cost_per_share:>9.2f}  '
            f'{_dollar(h.cost):>10}  {_dollar(value):>10}  '
            f'{_pnl_cell(gain_dollar, gain_pct):>22}  '
            f'{weight:>4.0%}  {h.start_date}'
        )

    lines.append(_div(_W_HOLDINGS))

    if total_value > 0:
        total_gain     = total_value - total_invested
        total_gain_pct = total_gain / total_invested if total_invested else float('nan')
        lines.append(
            f'  {"TOTAL":<10} {"":<22} {"":>9}  {"":>9}  '
            f'{_dollar(total_invested):>10}  {_dollar(total_value):>10}  '
            f'{_pnl_cell(total_gain, total_gain_pct):>22}'
        )
    else:
        lines.append(
            f'  {"TOTAL":<10} {"":<22} {"":>9}  {"":>9}  '
            f'{_dollar(total_invested):>10}  {"n/a":>10}'
        )

    lines.append('')
    return '\n'.join(lines)


def render_gains(
    transactions: list[Transaction],
    holdings: dict[str, Holding],
    prices: dict[str, float],
    ticker: str | None = None,
) -> str:
    lines: list[str] = ['']

    filter_label = f' — {ticker}' if ticker else ''

    lines.append(f'REALIZED GAINS  (from completed sells){filter_label}')
    lines.append(_div(_W_GAINS))

    sells = [
        t for t in transactions
        if t.action == 'sell' and t.realized_pnl is not None
        and (ticker is None or t.ticker == ticker)
    ]

    total_realized = 0.0
    if not sells:
        lines.append('  No realized gains yet.')
    else:
        lines.append(
            f"  {'Date':<12} {'Ticker':<10} {'Shares':>9}  {'Proceeds':>10}  {'P&L':>14}"
        )
        lines.append(_div(_W_GAINS))
        for t in sells:
            total_realized += t.realized_pnl
            lines.append(
                f'  {t.timestamp[:10]:<12} {t.ticker:<10} {t.shares:>9.4f}  '
                f'{_dollar(t.dollars):>10}  {_signed_dollar(t.realized_pnl):>14}'
            )
        lines.append(_div(_W_GAINS))
        lines.append(f'  {"Total realized":.<30} {_signed_dollar(total_realized):>14}')

    lines += ['', f'UNREALIZED GAINS  (live prices){filter_label}', _div(_W_GAINS)]

    filtered_holdings = {
        t: h for t, h in holdings.items()
        if ticker is None or t == ticker
    }

    total_cost      = 0.0
    total_value     = 0.0
    total_unrealized = 0.0

    if not filtered_holdings:
        lines.append('  No open positions.')
    else:
        lines.append(
            f"  {'Ticker':<10} {'Name':<22} {'Shares':>9}  {'Cost Basis':>10}  "
            f"{'Value':>10}  {'Gain':>22}"
        )
        lines.append(_div(_W_GAINS))
        for t, h in filtered_holdings.items():
            price = prices.get(t)
            if not _has_price(t, prices, h):
                lines.append(
                    f'  {t:<10} {h.label[:22]:<22} {"—":>9}  '
                    f'{_dollar(h.cost):>10}  {"n/a":>10}  {"n/a":>22}'
                )
                total_cost += h.cost
                continue
            value            = h.shares * price
            gain_dollar      = round(value - h.cost, 2)
            gain_pct         = gain_dollar / h.cost if h.cost else float('nan')
            total_cost      += h.cost
            total_value     += value
            total_unrealized += gain_dollar
            lines.append(
                f'  {t:<10} {h.label[:22]:<22} {h.shares:>9.4f}  '
                f'{_dollar(h.cost):>10}  {_dollar(value):>10}  '
                f'{_pnl_cell(gain_dollar, gain_pct):>22}'
            )
        lines.append(_div(_W_GAINS))
        if total_value > 0:
            unrealized_pct = total_unrealized / total_cost if total_cost else float('nan')
            lines.append(
                f'  {"Total unrealized":.<30} {_dollar(total_cost):>10}  '
                f'{_dollar(total_value):>10}  '
                f'{_pnl_cell(total_unrealized, unrealized_pct):>22}'
            )

    combined = total_realized + total_unrealized

    lines += [
        '',
        'SUMMARY',
        _div(_W_GAINS),
        f'  Realized:    {_signed_dollar(total_realized)}',
        f'  Unrealized:  {_signed_dollar(total_unrealized)}',
        f'  Combined:    {_signed_dollar(combined)}',
        '',
    ]
    return '\n'.join(lines)


def render_history(
    transactions: list[Transaction],
    ticker: str | None = None,
    limit: int | None = None,
) -> str:
    rows = list(reversed(transactions))

    if ticker:
        rows = [t for t in rows if t.ticker == ticker.upper()]

    if not rows:
        label = f' for {ticker.upper()}' if ticker else ''
        return f'\n  No transactions{label} yet.\n'

    if limit:
        rows = rows[:limit]

    lines: list[str] = [
        '',
        _div(_W_HISTORY),
        f"  {'Timestamp':<22} {'Action':<6} {'Ticker':<10} {'Shares':>9}  "
        f"{'Dollars':>9}  {'Price':>10}  {'P&L':>12}  Notes",
        _div(_W_HISTORY),
    ]

    for t in rows:
        ts     = t.timestamp[:19].replace('T', ' ')
        pnl    = _signed_dollar(t.realized_pnl) if t.realized_pnl is not None else '—'
        lines.append(
            f'  {ts:<22} {t.action:<6} {t.ticker:<10} {t.shares:>9.4f}  '
            f'{t.dollars:>9.2f}  {t.price:>10.2f}  {pnl:>12}  {t.notes}'
        )

    lines.append(_div(_W_HISTORY))
    n = len(rows)
    lines.append(f'  {n} transaction{"s" if n != 1 else ""}')
    lines.append('')
    return '\n'.join(lines)
