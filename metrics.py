"""
metrics.py — Shared financial metric calculations.
"""

import math

import numpy as np
import pandas as pd
from scipy import stats

from config import TRADING_DAYS_PER_YEAR
from ledger import Holding


def annualized_sharpe(returns: pd.Series, risk_free_rate: float) -> float:
    if returns.empty:
        return float('nan')
    annual_ret = returns.mean() * TRADING_DAYS_PER_YEAR
    annual_vol = returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)
    if annual_vol == 0:
        return 0.0
    return (annual_ret - risk_free_rate) / annual_vol


def sharpe_ci(returns: pd.Series, sharpe: float, alpha: float = 0.05) -> tuple:
    """Lo (2002) asymptotic CI. SE = sqrt((1 + SR²/2) / T) where T = days/252."""
    n = len(returns)
    if n < 2 or not np.isfinite(sharpe):
        return float('nan'), float('nan')
    T  = n / TRADING_DAYS_PER_YEAR
    se = np.sqrt((1 + 0.5 * sharpe ** 2) / T)
    z  = stats.norm.ppf(1 - alpha / 2)
    return sharpe - z * se, sharpe + z * se


def max_drawdown(cumulative_returns: pd.Series) -> float:
    if cumulative_returns.empty:
        return float('nan')
    running_max = cumulative_returns.cummax()
    return float(((cumulative_returns - running_max) / running_max).min())


def momentum_signal(
    r1d: float,
    r1w: float,
    r1m: float,
    flat_band: float,
) -> tuple:
    """
    Three-horizon momentum classifier. Single source of truth — both the morning
    brief and the dashboard call this.

    1M sets the trend; 1D/1W qualify it. NaN inputs ⇒ NEUTRAL.
        BULLISH  · 1M > +band            ('strong momentum' or 'dip in uptrend')
        BEARISH  · 1M < -band            ('downtrend' or 'bounce in downtrend')
        NEUTRAL  · |1M| ≤ band, or any input is NaN
    """
    if not all(np.isfinite(v) for v in (r1d, r1w, r1m)):
        return 'NEUTRAL', 'insufficient data'
    if r1m < -flat_band:
        return 'BEARISH', 'bounce in downtrend' if (r1d > 0 or r1w > 0) else 'downtrend'
    if r1m > flat_band:
        return 'BULLISH', 'dip in uptrend' if (r1d < 0 or r1w < 0) else 'strong momentum'
    return 'NEUTRAL', 'mixed signals'


def risk_snapshot(
    returns: pd.Series,
    risk_free_rate: float,
    min_observations: int,
) -> dict:
    """Returns {} if history is insufficient."""
    trailing = returns.iloc[-TRADING_DAYS_PER_YEAR:].dropna()
    if len(trailing) < min_observations:
        return {}

    annual_vol = trailing.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)
    if annual_vol == 0:
        return {}

    sharpe     = annualized_sharpe(trailing, risk_free_rate)
    ci         = sharpe_ci(trailing, sharpe)
    cumulative = (1 + trailing).cumprod()

    return {
        'sharpe':       sharpe,
        'sharpe_ci':    ci,
        'volatility':   annual_vol,
        'max_drawdown': max_drawdown(cumulative),
    }


def cost_basis_weights(holdings: dict[str, Holding]) -> dict[str, float]:
    """Cost-basis weights — no live prices needed."""
    total = sum(h.cost for h in holdings.values())
    if total == 0:
        return {}
    return {ticker: h.cost / total for ticker, h in holdings.items()}


def market_value_weights(
    holdings: dict[str, Holding],
    prices: dict[str, float],
) -> dict[str, float]:
    """Market-value weights. Tickers missing from prices are excluded."""
    values = {
        t: h.shares * prices[t]
        for t, h in holdings.items()
        if t in prices and math.isfinite(prices[t]) and h.shares > 0
    }
    total = sum(values.values())
    if total == 0:
        return {}
    return {t: v / total for t, v in values.items()}
