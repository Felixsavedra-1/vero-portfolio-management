"""
prices.py — Live price fetching via yfinance.

The only module in this project that calls yfinance.
All other modules receive price data as plain dicts.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

HOLIDAY_WINDOW_DAYS = 7  # lookback window to find the prior close around a target date

_DESC_CACHE_FILE = Path.home() / '.portfolio' / 'watchlist_descriptions_cache.json'
_DESC_CACHE_TTL_DAYS = 30


def _load_desc_cache() -> dict:
    if _DESC_CACHE_FILE.exists():
        try:
            return json.loads(_DESC_CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_desc_cache(cache: dict) -> None:
    _DESC_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DESC_CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _is_cache_fresh(entry: dict) -> bool:
    ts = entry.get('cached_at')
    if not ts:
        return False
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - dt).days
    return age < _DESC_CACHE_TTL_DAYS


def _first_sentences(text: str, n: int = 3) -> str:
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return ' '.join(sentences[:n])


def _rewrite_description(raw: str, ticker: str) -> str:
    """Rewrite via Claude if ANTHROPIC_API_KEY is set; else fall back to first sentences."""
    if not os.environ.get('ANTHROPIC_API_KEY'):
        return _first_sentences(raw)
    try:
        import anthropic   # optional dependency
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model='claude-haiku-4-5',
            max_tokens=150,
            messages=[{
                'role': 'user',
                'content': (
                    f"Describe {ticker} in 2-3 sentences. "
                    "Be blunt and factual. State what they make or sell, who buys it, "
                    "and one thing that sets them apart. "
                    "No adjectives like 'leading' or 'innovative'. No sentences starting "
                    "with 'The company'. No filler. Raw facts only.\n\n"
                    f"{raw}"
                ),
            }],
        )
        return msg.content[0].text.strip()
    except Exception:
        return _first_sentences(raw)


class PriceFetchError(ValueError):
    pass


@contextmanager
def yf_warnings() -> Iterator[None]:
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=UserWarning, module='yfinance')
        yield


def _last_close(series: pd.Series, label: str) -> float:
    s = series.dropna()
    if s.empty:
        raise PriceFetchError(f"No usable price data for {label}.")
    return float(s.iloc[-1])


def _close_frame(data: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Normalize a yfinance download to a DataFrame with ticker-named columns."""
    if isinstance(data.columns, pd.MultiIndex):
        close = data['Close']
    else:
        close = data[['Close']].rename(columns={'Close': tickers[0]})
    return close


def fetch_price(ticker: str) -> float:
    with yf_warnings():
        data = yf.download(ticker, period='5d', progress=False, auto_adjust=True)

    if data.empty:
        raise PriceFetchError(
            f"No price data for '{ticker}'. Check the symbol and your connection."
        )

    close = data['Close']
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    return _last_close(close, f"'{ticker}'")


def fetch_prices_batch(tickers: list[str]) -> dict[str, float]:
    """Batch close prices. Tickers that fail fetch are silently omitted."""
    if not tickers:
        return {}

    with yf_warnings():
        data = yf.download(tickers, period='5d', progress=False, auto_adjust=True)

    if data.empty:
        return {}

    close = _close_frame(data, tickers)
    last = close.ffill().iloc[-1]
    prices = {
        t: float(last[t])
        for t in tickers
        if t in last.index and pd.notna(last[t])
    }
    missing = [t for t in tickers if t not in prices]
    if missing:
        logging.warning("price unavailable for %s", ', '.join(missing))
    return prices


def fetch_historical_price(ticker: str, date_str: str) -> float:
    """Closing price on or nearest to date_str; weekends/holidays resolve to the prior trading day."""
    target = date.fromisoformat(date_str)
    start  = (target - timedelta(days=HOLIDAY_WINDOW_DAYS)).isoformat()
    end    = (target + timedelta(days=1)).isoformat()

    with yf_warnings():
        data = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)

    if data.empty:
        raise PriceFetchError(
            f"No price data for '{ticker}' around {date_str}. "
            "Check the symbol and date, or pass --price manually."
        )

    close = data['Close']
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    return _last_close(close, f"'{ticker}' around {date_str}")


def fetch_label(ticker: str) -> str:
    """Human-readable name, falls back to ticker symbol on any failure (incl. network)."""
    try:
        info = yf.Ticker(ticker).info
        return info.get('shortName') or info.get('longName') or ticker
    except Exception:
        return ticker


def fetch_prices_with_change(tickers: list[str]) -> dict[str, dict[str, float]]:
    """
    Returns {ticker: {"price": float, "prev_close": float}} in one network call.
    prev_close is the previous trading day's close, used for day-change calculations.
    """
    if not tickers:
        return {}

    with yf_warnings():
        data = yf.download(tickers, period='5d', progress=False, auto_adjust=True)

    if data.empty:
        return {}

    close = _close_frame(data, tickers).ffill()
    result: dict[str, dict[str, float]] = {}
    for t in tickers:
        if t not in close.columns:
            continue
        s = close[t].dropna()
        if s.empty:
            continue
        result[t] = {
            'price':      round(float(s.iloc[-1]), 4),
            'prev_close': round(float(s.iloc[-2]), 4) if len(s) >= 2 else float('nan'),
        }
    return result


def fetch_watchlist_history(tickers: list[str]) -> dict[str, dict[str, list[float]]]:
    """
    Returns {ticker: {"1W": [...], "1M": [...], "3M": [...], "6M": [...], "YTD": [...]}}
    Each list is daily closing prices, oldest to newest. Single network call.
    """
    if not tickers:
        return {}

    with yf_warnings():
        data = yf.download(tickers, period='1y', progress=False, auto_adjust=True)

    if data.empty:
        return {}

    close = _close_frame(data, tickers)
    today      = date.today()
    ytd_cutoff = pd.Timestamp(date(today.year, 1, 1))

    result: dict[str, dict[str, list[float]]] = {}
    for ticker in tickers:
        if ticker not in close.columns:
            continue
        series = close[ticker].dropna()
        if series.empty:
            continue
        n     = len(series)
        all_p = [round(float(v), 4) for v in series]
        ytd_p = [round(float(v), 4) for v in series[series.index >= ytd_cutoff]]
        result[ticker] = {
            '1W':  all_p[max(0, n - 5):],
            '1M':  all_p[max(0, n - 21):],
            '3M':  all_p[max(0, n - 63):],
            '6M':  all_p[max(0, n - 126):],
            'YTD': ytd_p if ytd_p else all_p[-1:],
        }

    return result


def fetch_watchlist_info(tickers: list[str]) -> dict[str, dict[str, str]]:
    """
    Returns {ticker: {"description": str, "sector": str}} for each ticker.
    Descriptions are rewritten by Claude for concision and cached for 30 days.
    """
    cache  = _load_desc_cache()
    result: dict[str, dict[str, str]] = {}
    dirty  = False

    with yf_warnings():
        for ticker in tickers:
            cached = cache.get(ticker, {})
            if _is_cache_fresh(cached):
                result[ticker] = {'description': cached['description'], 'sector': cached['sector']}
                continue
            try:
                info   = yf.Ticker(ticker).info
                raw    = info.get('longBusinessSummary') or ''
                sector = info.get('sector') or ''
                desc   = _rewrite_description(raw, ticker) if raw else ''
                result[ticker] = {'description': desc, 'sector': sector}
                cache[ticker]  = {
                    'description': desc,
                    'sector':      sector,
                    'cached_at':   datetime.now(timezone.utc).isoformat(),
                }
                dirty = True
            except Exception:
                result[ticker] = {'description': '', 'sector': ''}

    if dirty:
        _save_desc_cache(cache)

    return result
