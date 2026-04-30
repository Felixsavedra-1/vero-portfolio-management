"""
portfolio.py — Investment portfolio tracker.

Usage
-----
  python portfolio.py buy    TICKER DOLLARS [--date YYYY-MM-DD] [--price P] [--notes "..."]
  python portfolio.py sell   TICKER DOLLARS [--date YYYY-MM-DD] [--price P] [--notes "..."]
  python portfolio.py show
  python portfolio.py gains  [--ticker TICKER]
  python portfolio.py history [--ticker TICKER] [--limit N]
  python portfolio.py remove TICKER

Data is stored in ~/.portfolio/
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import BRIEF_TIMEZONE, GOALS_FILE, HOLDINGS_FILE, INTEREST_PAYMENT_DAY, SAVINGS_FILE, TRANSACTIONS_FILE
from display import render_gains, render_history, render_holdings
from ledger import (
    Holding, SavingsAccount, Transaction,
    _payment_dates, accrued_interest, projected_next_payment,
    append_transaction, load_goals, load_holdings, load_savings,
    load_transactions, save_goals, save_holdings, save_savings,
)
from prices import PriceFetchError, fetch_historical_price, fetch_label, fetch_price, fetch_prices_batch

EPSILON = 1e-9


def _trade_ts(date_str: str | None) -> str:
    return f'{date_str}T12:00:00.000000' if date_str else datetime.now(ZoneInfo(BRIEF_TIMEZONE)).isoformat()


def _make_id(timestamp: str, ticker: str) -> str:
    compact = timestamp[:23].translate(str.maketrans('', '', '-:T.'))
    return f'txn_{compact}_{ticker}'


def _parse_date(date_str: str) -> str:
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        sys.exit(f"Invalid date '{date_str}'. Use YYYY-MM-DD format (e.g. 2024-01-15).")
    if d > date.today():
        sys.exit(f"Date {date_str} is in the future. Use a past or present date.")
    return date_str


def _resolve_price(ticker: str, explicit: float | None, date_str: str | None = None) -> float:
    if explicit is not None:
        return explicit
    if date_str is not None:
        print(f'  Fetching {ticker} price for {date_str}...', end=' ', flush=True)
        price = fetch_historical_price(ticker, date_str)
        print(f'${price:,.2f}')
        return price
    print(f'  Fetching price for {ticker}...', end=' ', flush=True)
    price = fetch_price(ticker)
    print(f'${price:,.2f}')
    return price


def cmd_buy(args: argparse.Namespace, prompt: Callable[[str], str] = input) -> None:
    ticker   = args.ticker
    dollars  = args.dollars
    date_str = _parse_date(args.date) if args.date else None

    if dollars <= 0:
        sys.exit('Amount must be positive.')

    holdings = load_holdings(HOLDINGS_FILE)
    is_new   = ticker not in holdings

    label = None
    if is_new:
        label   = fetch_label(ticker)   # falls back to ticker symbol on failure
        confirm = prompt(
            f'\n  Opening new position: {ticker}  [{label}]\n'
            f'  Invest ${dollars:,.2f} — confirm? [y/N] '
        ).strip().lower()
        if confirm != 'y':
            print('  Cancelled.\n')
            return

    try:
        price = _resolve_price(ticker, args.price, date_str)
    except PriceFetchError as e:
        sys.exit(str(e))

    shares    = round(dollars / price, 10)
    timestamp = _trade_ts(date_str)

    if is_new:
        holdings[ticker] = Holding(
            ticker=ticker, shares=shares, cost=dollars,
            first_purchase=timestamp, label=label,
        )
    else:
        h         = holdings[ticker]
        h.shares += shares
        h.cost   += dollars

    save_holdings(holdings, HOLDINGS_FILE)
    append_transaction(
        Transaction(
            id=_make_id(timestamp, ticker),
            timestamp=timestamp,
            action='buy',
            ticker=ticker,
            shares=shares,
            dollars=dollars,
            price=price,
            realized_pnl=None,
            notes=args.notes or '',
        ),
        TRANSACTIONS_FILE,
    )

    h = holdings[ticker]
    if is_new:
        print(f'\n  Opened  {ticker}  {shares:.4f} shares @ ${price:,.2f}  |  ${dollars:,.2f} invested\n')
    else:
        print(
            f'\n  Bought  {ticker}  +{shares:.4f} shares @ ${price:,.2f}  |  +${dollars:,.2f}\n'
            f'          Total: {h.shares:.4f} shares  |  ${h.cost:,.2f} invested\n'
        )


def cmd_sell(args: argparse.Namespace) -> None:
    ticker   = args.ticker
    dollars  = args.dollars
    date_str = _parse_date(args.date) if args.date else None

    if dollars <= 0:
        sys.exit('Amount must be positive.')

    holdings = load_holdings(HOLDINGS_FILE)
    if ticker not in holdings:
        sys.exit(f'{ticker} is not in your portfolio.')

    h = holdings[ticker]
    if h.shares <= 0:
        sys.exit(f'{ticker} has no shares to sell.')

    try:
        price = _resolve_price(ticker, args.price, date_str)
    except PriceFetchError as e:
        sys.exit(str(e))

    shares = round(dollars / price, 10)

    if shares > h.shares + EPSILON:
        sys.exit(
            f'Cannot sell {shares:.4f} shares — only {h.shares:.4f} held.\n'
            f'To sell everything: portfolio sell {ticker} {h.shares * price:.2f}'
        )

    cost_sold    = round((shares / h.shares) * h.cost, 2)
    realized_pnl = round(dollars - cost_sold, 2)

    h.shares  -= shares
    h.cost    -= cost_sold
    timestamp  = _trade_ts(date_str)

    if h.shares < EPSILON:
        del holdings[ticker]
        status = 'position closed'
    else:
        status = f'{h.shares:.4f} shares remaining'

    save_holdings(holdings, HOLDINGS_FILE)
    append_transaction(
        Transaction(
            id=_make_id(timestamp, ticker),
            timestamp=timestamp,
            action='sell',
            ticker=ticker,
            shares=shares,
            dollars=dollars,
            price=price,
            realized_pnl=realized_pnl,
            notes=args.notes or '',
        ),
        TRANSACTIONS_FILE,
    )

    sign = '+' if realized_pnl >= 0 else '-'
    print(
        f'\n  Sold    {ticker}  -{shares:.4f} shares @ ${price:,.2f}  |  proceeds ${dollars:,.2f}\n'
        f'          Realized P&L: {sign}${abs(realized_pnl):,.2f}  |  {status}\n'
    )


def cmd_show(_args: argparse.Namespace) -> None:
    holdings = load_holdings(HOLDINGS_FILE)
    if not holdings:
        print('\n  No holdings yet. Run: portfolio buy TICKER DOLLARS\n')
        return

    tickers = [t for t, h in holdings.items() if h.shares > 0]
    prices  = fetch_prices_batch(tickers) if tickers else {}
    print(render_holdings(holdings, prices))


def cmd_gains(args: argparse.Namespace) -> None:
    holdings     = load_holdings(HOLDINGS_FILE)
    transactions = load_transactions(TRANSACTIONS_FILE)
    tickers = [t for t, h in holdings.items() if h.shares > 0]
    prices  = fetch_prices_batch(tickers) if tickers else {}
    print(render_gains(transactions, holdings, prices, ticker=args.ticker))


def cmd_history(args: argparse.Namespace) -> None:
    transactions = load_transactions(TRANSACTIONS_FILE)
    print(render_history(transactions, ticker=args.ticker, limit=args.limit))


def cmd_remove(args: argparse.Namespace, prompt: Callable[[str], str] = input) -> None:
    ticker   = args.ticker
    holdings = load_holdings(HOLDINGS_FILE)

    if ticker not in holdings:
        sys.exit(f'{ticker} is not in your portfolio.')

    h = holdings[ticker]
    confirm = prompt(
        f'  Remove {ticker} ({h.shares:.4f} shares, ${h.cost:,.2f} invested)? [y/N] '
    ).strip().lower()

    if confirm != 'y':
        print('  Cancelled.')
        return

    del holdings[ticker]
    save_holdings(holdings, HOLDINGS_FILE)
    print(f'  Removed {ticker}.')


def _ordinal(n: int) -> str:
    suffix = {1: 'st', 2: 'nd', 3: 'rd'}
    return suffix.get(n % 10 if n not in (11, 12, 13) else 0, 'th')


def cmd_savings_set(args: argparse.Namespace) -> None:
    accounts = load_savings(SAVINGS_FILE)
    name     = args.name
    existing = next((a for a in accounts if a.name == name), None)
    if args.balance is not None and args.balance < 0:
        sys.exit('Balance must be non-negative.')
    if args.apy is not None and args.apy < 0:
        sys.exit('APY must be non-negative.')
    if existing:
        if args.balance is not None: existing.balance = args.balance
        if args.apy     is not None: existing.apy     = args.apy / 100
        if args.bank    is not None: existing.bank    = args.bank
        save_savings(accounts, SAVINGS_FILE)
        print(f'\n  Updated  {name}  ${existing.balance:,.2f}  ({existing.apy:.2%} APY)\n')
        return
    if args.balance is None:
        sys.exit('BALANCE is required when adding a new savings account.')
    if args.apy is None:
        sys.exit('--apy is required when adding a new savings account.')
    accounts.append(SavingsAccount(
        name=name, balance=args.balance, apy=args.apy / 100, bank=args.bank or '',
    ))
    save_savings(accounts, SAVINGS_FILE)
    print(f'\n  Added  {name}  ${args.balance:,.2f}  ({args.apy:.2f}% APY)\n')


def cmd_savings_remove(args: argparse.Namespace) -> None:
    accounts = load_savings(SAVINGS_FILE)
    filtered = [a for a in accounts if a.name != args.name]
    if len(filtered) == len(accounts):
        sys.exit(f"'{args.name}' is not a savings account.")
    save_savings(filtered, SAVINGS_FILE)
    print(f'\n  Removed  {args.name}.\n')


def cmd_savings_interest(_args: argparse.Namespace) -> None:
    if INTEREST_PAYMENT_DAY is None:
        print('\n  Set INTEREST_PAYMENT_DAY in config.py to track interest accrual.\n')
        return
    accounts = load_savings(SAVINGS_FILE)
    if not accounts:
        print('\n  No savings accounts found.\n')
        return

    today        = date.today()
    _, next_date = _payment_dates(INTEREST_PAYMENT_DAY, today)
    days_until   = (next_date - today).days
    print(f'\n  Savings interest  ·  payment day: {INTEREST_PAYMENT_DAY}{_ordinal(INTEREST_PAYMENT_DAY)} of every month\n')

    col_w = max(len(a.name) for a in accounts)
    hdr = (f"  {'Account':<{col_w}}   {'Balance':>12}   {'APY':>5}   "
           f"{'Daily':>12}   {'Accrued':>12}   {'Next Pmt':>12}   {'In':>6}")
    div = '  ' + '─' * (len(hdr) - 2)
    print(hdr)
    print(div)

    tot_daily = tot_accrued = tot_proj = 0.0
    for a in accounts:
        daily        = a.balance * a.apy / 365
        accrued      = accrued_interest(a, INTEREST_PAYMENT_DAY, today)
        proj         = projected_next_payment(a, INTEREST_PAYMENT_DAY, today)
        tot_daily   += daily
        tot_accrued += accrued
        tot_proj    += proj
        print(f"  {a.name:<{col_w}}   ${a.balance:>11,.2f}   {a.apy:>5.2%}   "
              f"+${daily:>9,.2f}/d   +${accrued:>9,.2f}   +${proj:>9,.2f}   {days_until:>4}d")

    print(div)
    print(f"  {'Total':<{col_w}}   {'':>12}   {'':>5}   "
          f"+${tot_daily:>9,.2f}/d   +${tot_accrued:>9,.2f}   +${tot_proj:>9,.2f}")
    print()


def cmd_goal_set(args: argparse.Namespace) -> None:
    goals = load_goals(GOALS_FILE)
    goals[f'__{args.target}__'] = args.amount
    save_goals(goals, GOALS_FILE)
    print(f'\n  Goal set: {args.target} → ${args.amount:,.0f}\n')


def cmd_goal_remove(args: argparse.Namespace) -> None:
    goals = load_goals(GOALS_FILE)
    key   = f'__{args.target}__'
    if key in goals:
        del goals[key]
        save_goals(goals, GOALS_FILE)
        print(f'\n  Removed {args.target} goal.\n')
    else:
        print(f'\n  No {args.target} goal was set.\n')


def cmd_goal_show(_args: argparse.Namespace) -> None:
    goals = load_goals(GOALS_FILE)
    pg    = goals.get('__portfolio__')
    sg    = goals.get('__savings__')
    print()
    print(f'  Portfolio goal:  {f"${pg:,.0f}" if pg is not None else "not set"}')
    print(f'  Savings goal:    {f"${sg:,.0f}" if sg is not None else "not set"}')
    print()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='portfolio',
        description='Track your investment portfolio from the terminal.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='command', metavar='COMMAND')
    sub.required = True

    p_buy = sub.add_parser('buy', help='Buy a stock (opens new position or adds to existing)')
    p_buy.add_argument('ticker',  type=str,   help='Ticker symbol, e.g. AAPL')
    p_buy.add_argument('dollars', type=float, help='Dollar amount to invest')
    p_buy.add_argument('--date',  type=str,   default=None, metavar='YYYY-MM-DD',
                       help='Trade date — fetches the closing price on that day (default: today/live)')
    p_buy.add_argument('--price', type=float, default=None,
                       help='Override price instead of fetching (combine with --date to set both)')
    p_buy.add_argument('--notes', type=str, default='')
    p_buy.set_defaults(func=cmd_buy)

    p_sell = sub.add_parser('sell', help='Sell a position (partial or full)')
    p_sell.add_argument('ticker',  type=str,   help='Ticker symbol')
    p_sell.add_argument('dollars', type=float, help='Dollar value to sell')
    p_sell.add_argument('--date',  type=str,   default=None, metavar='YYYY-MM-DD',
                        help='Trade date — fetches the closing price on that day (default: today/live)')
    p_sell.add_argument('--price', type=float, default=None,
                        help='Override price instead of fetching (combine with --date to set both)')
    p_sell.add_argument('--notes', type=str, default='')
    p_sell.set_defaults(func=cmd_sell)

    sub.add_parser('show', help='Show current holdings with live prices and P&L'
                   ).set_defaults(func=cmd_show)

    p_gains = sub.add_parser('gains', help='Show realized and unrealized P&L breakdown')
    p_gains.add_argument('--ticker', type=str, default=None, help='Filter to a single ticker')
    p_gains.set_defaults(func=cmd_gains)

    p_hist = sub.add_parser('history', help='Show transaction log')
    p_hist.add_argument('--ticker', type=str, default=None, help='Filter to a single ticker')
    p_hist.add_argument('--limit', type=int, default=None,
                        help='Show only the N most recent transactions')
    p_hist.set_defaults(func=cmd_history)

    p_rem = sub.add_parser('remove', help='Remove a holding (data correction, no transaction logged)')
    p_rem.add_argument('ticker', type=str)
    p_rem.set_defaults(func=cmd_remove)

    p_sav = sub.add_parser('savings', help='Manage savings accounts')
    sav_sub = p_sav.add_subparsers(dest='savings_command', metavar='SUBCOMMAND')
    sav_sub.required = True

    p_sav_set = sav_sub.add_parser('set', help='Add or update a savings account')
    p_sav_set.add_argument('name',    type=str,            help='Account name, e.g. "Car Fund"')
    p_sav_set.add_argument('balance', type=float, nargs='?', default=None,
                           help='Current balance in dollars (required when creating)')
    p_sav_set.add_argument('--apy',  type=float, default=None,
                           help='Annual percentage yield, e.g. 4.0 for 4%% (required when creating)')
    p_sav_set.add_argument('--bank', type=str,   default=None,
                           help='Institution name shown in the morning brief, e.g. "Amex"')
    p_sav_set.set_defaults(func=cmd_savings_set)

    p_sav_rem = sav_sub.add_parser('remove', help='Remove a savings account')
    p_sav_rem.add_argument('name', type=str, help='Account name to remove')
    p_sav_rem.set_defaults(func=cmd_savings_remove)

    sav_sub.add_parser('interest', help='Show accrued interest and next payment projections'
                       ).set_defaults(func=cmd_savings_interest)

    p_goal = sub.add_parser('goal', help='Set or view portfolio and savings goals')
    goal_sub = p_goal.add_subparsers(dest='goal_command', metavar='SUBCOMMAND')
    goal_sub.required = True

    p_goal_set = goal_sub.add_parser('set', help='Set a goal')
    p_goal_set.add_argument('target', choices=['portfolio', 'savings'],
                            help='"portfolio" or "savings"')
    p_goal_set.add_argument('amount', type=float, help='Target dollar amount')
    p_goal_set.set_defaults(func=cmd_goal_set)

    p_goal_rem = goal_sub.add_parser('remove', help='Remove a goal')
    p_goal_rem.add_argument('target', choices=['portfolio', 'savings'])
    p_goal_rem.set_defaults(func=cmd_goal_remove)

    goal_sub.add_parser('show', help='Show current goals'
                        ).set_defaults(func=cmd_goal_show)

    return parser


def main() -> None:
    args = _build_parser().parse_args()
    if hasattr(args, 'ticker') and args.ticker:
        args.ticker = args.ticker.upper()
    args.func(args)


if __name__ == '__main__':
    main()
