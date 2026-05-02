"""
ledger.py — Portfolio data model and JSON I/O.

Single source of truth for holdings and transaction history.
All reads and writes go through this module; nothing else touches the JSON files.
"""

from __future__ import annotations

import calendar
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class Holding:
    ticker:         str
    shares:         float
    cost:           float   # cumulative cost basis in dollars
    first_purchase: str
    label:          str

    @property
    def avg_cost_per_share(self) -> float:
        return self.cost / self.shares if self.shares > 0 else 0.0

    @property
    def start_date(self) -> str:
        return self.first_purchase[:10]


@dataclass
class Transaction:
    id:           str
    timestamp:    str           # ISO 8601 full datetime, e.g. "2026-04-13T14:30:22.841504"
    action:       str           # "buy" | "sell"
    ticker:       str
    shares:       float
    dollars:      float
    price:        float
    realized_pnl: float | None = None   # None on buys; dollar gain/loss on sells
    notes:        str = ""


def _atomic_write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + '.tmp')
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    tmp.rename(path)


def _coerce_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(v) else v


def load_holdings(path: Path) -> dict[str, Holding]:
    if not path.exists():
        return {}
    with open(path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            sys.exit(f"Corrupt data file: {path}\nFix or remove the file to continue.")
    result = {}
    for ticker, v in data.items():
        result[ticker] = Holding(
            ticker=ticker,
            shares=_coerce_float(v.get('shares')),
            cost=_coerce_float(v.get('cost')),
            first_purchase=v.get('first_purchase') or v.get('start_date', ''),
            label=v.get('label', ticker),
        )
    return result


def save_holdings(holdings: dict[str, Holding], path: Path) -> None:
    _atomic_write(path, {
        ticker: {
            'shares':         h.shares,
            'cost':           h.cost,
            'first_purchase': h.first_purchase,
            'label':          h.label,
        }
        for ticker, h in holdings.items()
    })


def load_transactions(path: Path) -> list[Transaction]:
    if not path.exists():
        return []
    with open(path) as f:
        try:
            raw = json.load(f)
        except json.JSONDecodeError:
            sys.exit(f"Corrupt data file: {path}\nFix or remove the file to continue.")
    result = []
    for i, r in enumerate(raw):
        timestamp = r.get('timestamp') or r.get('date', '')
        action    = (r.get('action') or r.get('type', '')).lower()
        txn_id    = r.get('id') or f"txn_{timestamp[:10]}_{r.get('ticker', '')}_{i}"
        shares    = _coerce_float(r.get('shares'))
        price     = _coerce_float(r.get('price'))
        result.append(Transaction(
            id=txn_id,
            timestamp=timestamp,
            action=action,
            ticker=r.get('ticker', ''),
            shares=shares,
            dollars=_coerce_float(r.get('dollars')),
            price=price,
            realized_pnl=r.get('realized_pnl'),
            notes=r.get('notes', ''),
        ))
    return result


def append_transaction(txn: Transaction, path: Path) -> None:
    existing = []
    if path.exists():
        with open(path) as f:
            existing = json.load(f)
    existing.append({
        'id':           txn.id,
        'timestamp':    txn.timestamp,
        'action':       txn.action,
        'ticker':       txn.ticker,
        'shares':       txn.shares,
        'dollars':      txn.dollars,
        'price':        txn.price,
        'realized_pnl': txn.realized_pnl,
        'notes':        txn.notes,
    })
    _atomic_write(path, existing)


@dataclass
class SavingsAccount:
    name:    str
    balance: float
    apy:     float   # decimal, e.g. 0.04 for 4%
    bank:    str = ''

    @property
    def monthly_interest(self) -> float:
        return self.balance * self.apy / 12


def load_savings(path: Path) -> list[SavingsAccount]:
    if not path.exists():
        return []
    with open(path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            sys.exit(f"Corrupt data file: {path}\nFix or remove the file to continue.")
    return [
        SavingsAccount(
            name=r['name'],
            balance=float(r['balance']),
            apy=float(r['apy']),
            bank=r.get('bank', ''),
        )
        for r in data
    ]


def save_savings(accounts: list[SavingsAccount], path: Path) -> None:
    _atomic_write(path, [{'name': a.name, 'balance': a.balance, 'apy': a.apy, 'bank': a.bank} for a in accounts])


def load_goals(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            sys.exit(f"Corrupt data file: {path}\nFix or remove the file to continue.")


def save_goals(goals: dict[str, float], path: Path) -> None:
    _atomic_write(path, goals)


def _payment_dates(payment_day: int, today: date) -> tuple[date, date]:
    """Returns (last_payment_date, next_payment_date) for a given day-of-month."""
    def safe_date(year: int, month: int, day: int) -> date:
        return date(year, month, min(day, calendar.monthrange(year, month)[1]))

    if today.day >= payment_day:
        last   = safe_date(today.year, today.month, payment_day)
        nm     = today.month % 12 + 1
        ny     = today.year + (1 if today.month == 12 else 0)
        next_  = safe_date(ny, nm, payment_day)
    else:
        pm     = (today.month - 2) % 12 + 1
        py     = today.year - (1 if today.month == 1 else 0)
        last   = safe_date(py, pm, payment_day)
        next_  = safe_date(today.year, today.month, payment_day)
    return last, next_


def accrued_interest(
    account: SavingsAccount,
    payment_day: int,
    today: date | None = None,
) -> float:
    """Interest accrued since the last payment date (balance × APY / 365 × days)."""
    today = today or date.today()
    last, _ = _payment_dates(payment_day, today)
    days = (today - last).days
    return account.balance * account.apy / 365 * days


def projected_next_payment(
    account: SavingsAccount,
    payment_day: int,
    today: date | None = None,
) -> float:
    """Projected interest for the full current cycle (last → next payment)."""
    today = today or date.today()
    last, next_ = _payment_dates(payment_day, today)
    days = (next_ - last).days
    return account.balance * account.apy / 365 * days
