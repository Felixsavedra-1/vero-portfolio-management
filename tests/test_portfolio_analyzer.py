import os

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

from config import TRADING_DAYS_PER_YEAR
from portfolio_analyzer import (
    AnalysisResult,
    AssetMetrics,
    PortfolioAnalyzer,
    compute_analysis,
    compute_asset_metrics,
)


# ── Constructor / input validation ───────────────────────────────────────────

def test_invalid_non_numeric_weight_raises_clear_error():
    with pytest.raises(ValueError, match="Invalid weight"):
        PortfolioAnalyzer({'SWPPX': 0.4, 'AXP': 'bad', 'IAU': 0.6})


def test_duplicate_ticker_after_normalization_raises():
    with pytest.raises(ValueError, match="Duplicate ticker"):
        PortfolioAnalyzer({'axp': 0.5, 'AXP': 0.5})


def test_benchmark_collision_raises():
    with pytest.raises(ValueError, match="cannot also be a portfolio holding"):
        PortfolioAnalyzer({'SPY': 1.0})


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="must equal 100%"):
        PortfolioAnalyzer({'AXP': 0.5, 'IAU': 0.4})


def test_start_must_precede_end():
    with pytest.raises(ValueError, match="must be before end date"):
        PortfolioAnalyzer({'AXP': 1.0}, start_date='2024-06-01', end_date='2024-01-01')


# ── compute_asset_metrics: pure compute ───────────────────────────────────────

def test_asset_metrics_cagr_arithmetic_and_total():
    returns = pd.Series(
        [0.01, 0.00, -0.005],
        index=pd.to_datetime(['2024-01-02', '2024-01-03', '2024-01-04']),
    )
    m = compute_asset_metrics(returns, risk_free_rate=0.045)

    assert isinstance(m, AssetMetrics)
    expected_arith = np.mean([0.01, 0.00, -0.005]) * TRADING_DAYS_PER_YEAR
    expected_total = (1.01 * 1.0 * 0.995) - 1
    expected_years = 3 / TRADING_DAYS_PER_YEAR
    expected_cagr  = (1 + expected_total) ** (1 / expected_years) - 1

    assert m.annual_return_arithmetic == pytest.approx(expected_arith)
    assert m.total_return             == pytest.approx(expected_total)
    assert m.annual_return            == pytest.approx(expected_cagr)
    assert m.cumulative_returns.iloc[-1] == pytest.approx(1 + expected_total)


def test_asset_metrics_rejects_empty_input():
    with pytest.raises(ValueError, match="Insufficient return observations"):
        compute_asset_metrics(pd.Series(dtype=float), risk_free_rate=0.045)


# ── compute_analysis: end-to-end pure path ────────────────────────────────────

def _synthetic_returns() -> pd.DataFrame:
    """Three trading days of returns for a single-asset portfolio + benchmark."""
    return pd.DataFrame(
        {'AXP': [0.01, 0.00, -0.005], 'SPY': [0.002, 0.001, -0.001]},
        index=pd.to_datetime(['2024-01-02', '2024-01-03', '2024-01-04']),
    )


def test_compute_analysis_returns_frozen_dataclass_with_expected_shape():
    result = compute_analysis(
        returns          = _synthetic_returns(),
        weights          = {'AXP': 1.0},
        benchmark        = 'SPY',
        risk_free_rate   = 0.045,
        transaction_cost = 0.0,
        rolling_window   = None,   # window > obs would be all-NaN; skip for the unit test
    )

    assert isinstance(result, AnalysisResult)
    assert result.benchmark_ticker == 'SPY'
    assert set(result.individual_assets) == {'AXP'}
    assert result.weights == {'AXP': 1.0}
    assert result.rolling is None

    expected_total = (1.01 * 1.0 * 0.995) - 1
    assert result.portfolio.total_return == pytest.approx(expected_total)


def test_compute_analysis_rejects_missing_columns():
    with pytest.raises(ValueError, match="missing columns"):
        compute_analysis(
            returns        = _synthetic_returns().drop(columns=['SPY']),
            weights        = {'AXP': 1.0},
            benchmark      = 'SPY',
            risk_free_rate = 0.045,
            rolling_window = None,
        )


def test_compute_analysis_rejects_empty_returns():
    with pytest.raises(ValueError, match="Insufficient overlapping data"):
        compute_analysis(
            returns        = pd.DataFrame(columns=['AXP', 'SPY']),
            weights        = {'AXP': 1.0},
            benchmark      = 'SPY',
            risk_free_rate = 0.045,
            rolling_window = None,
        )


def test_transaction_cost_haircuts_only_inception_return():
    """A 1% entry cost should reduce only the first day's return."""
    base = compute_analysis(
        returns=_synthetic_returns(), weights={'AXP': 1.0},
        benchmark='SPY', risk_free_rate=0.045,
        transaction_cost=0.0, rolling_window=None,
    )
    haircut = compute_analysis(
        returns=_synthetic_returns(), weights={'AXP': 1.0},
        benchmark='SPY', risk_free_rate=0.045,
        transaction_cost=0.01, rolling_window=None,
    )
    assert haircut.portfolio.total_return < base.portfolio.total_return
    # Benchmark is unaffected
    assert haircut.benchmark.total_return == pytest.approx(base.benchmark.total_return)


def test_individual_asset_metrics_match_standalone_compute():
    returns = _synthetic_returns()
    result  = compute_analysis(
        returns=returns, weights={'AXP': 1.0}, benchmark='SPY',
        risk_free_rate=0.045, rolling_window=None,
    )
    standalone = compute_asset_metrics(returns['AXP'], risk_free_rate=0.045)
    assert result.individual_assets['AXP'].sharpe_ratio == pytest.approx(standalone.sharpe_ratio)
    assert result.individual_assets['AXP'].annual_volatility == pytest.approx(standalone.annual_volatility)
