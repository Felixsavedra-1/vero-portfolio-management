"""
portfolio_analyzer.py — Deep portfolio analysis and 6-panel chart.

Three layers, separated:

    1.  Pure data model:       AssetMetrics, RollingMetrics, AnalysisResult
    2.  Pure compute:          compute_asset_metrics, compute_rolling_metrics,
                               compute_analysis  (no I/O, take returns, return dataclass)
    3.  Pure render:           print_results, plot_dashboard  (take dataclass, write output)
    4.  Thin coordinator:      PortfolioAnalyzer  (validates inputs, fetches prices,
                               hands off to the pure layer)

Produces a console tearsheet (CAGR, Sharpe with Lo 2002 CI, volatility,
max drawdown) and saves a chart to ~/.portfolio/portfolio_analysis.png.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import yfinance as yf

from config import BENCHMARK, DATA_DIR, HOLDINGS_FILE, RISK_FREE_RATE, TRADING_DAYS_PER_YEAR, TRANSACTION_COST
from ledger import load_holdings
from metrics import annualized_sharpe, cost_basis_weights, max_drawdown, sharpe_ci
from prices import yf_warnings

logger = logging.getLogger(__name__)

WEIGHT_TOLERANCE = 1e-6


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AssetMetrics:
    """Risk/return metrics for a single asset, portfolio, or benchmark."""
    annual_return:            float                # CAGR
    annual_return_arithmetic: float
    annual_volatility:        float
    sharpe_ratio:             float
    sharpe_ci:                tuple[float, float]  # (low, high), Lo (2002) 95% CI
    total_return:             float
    max_drawdown:             float
    cumulative_returns:       pd.Series            # held for plotting


@dataclass(frozen=True)
class RollingMetrics:
    """Rolling-window risk metrics for portfolio + benchmark."""
    window:              int
    portfolio_sharpe:    pd.Series
    benchmark_sharpe:    pd.Series
    portfolio_drawdown:  pd.Series
    benchmark_drawdown:  pd.Series


@dataclass(frozen=True)
class AnalysisResult:
    """Everything print_results and plot_dashboard need — the full analysis frozen."""
    portfolio:         AssetMetrics
    benchmark:         AssetMetrics
    individual_assets: dict[str, AssetMetrics]
    weights:           dict[str, float]
    benchmark_ticker:  str
    risk_free_rate:    float
    transaction_cost:  float
    rolling:           RollingMetrics | None = field(default=None)


# ── Pure compute ──────────────────────────────────────────────────────────────

def compute_asset_metrics(returns: pd.Series, risk_free_rate: float) -> AssetMetrics:
    """Compute risk/return for a single return series. Pure."""
    returns = returns.dropna()
    if returns.empty:
        raise ValueError("Insufficient return observations to compute metrics.")
    cumulative   = (1 + returns).cumprod()
    total_return = float(cumulative.iloc[-1] - 1)
    num_years    = len(returns) / TRADING_DAYS_PER_YEAR
    if num_years > 0 and total_return > -1:
        cagr = (1 + total_return) ** (1 / num_years) - 1
    else:
        cagr = float('nan')
    sharpe = annualized_sharpe(returns, risk_free_rate)
    return AssetMetrics(
        annual_return            = cagr,
        annual_return_arithmetic = float(returns.mean() * TRADING_DAYS_PER_YEAR),
        annual_volatility        = float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)),
        sharpe_ratio             = sharpe,
        sharpe_ci                = sharpe_ci(returns, sharpe),
        total_return             = total_return,
        max_drawdown             = max_drawdown(cumulative),
        cumulative_returns       = cumulative,
    )


def compute_rolling_metrics(
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series,
    risk_free_rate: float,
    window: int = TRADING_DAYS_PER_YEAR,
) -> RollingMetrics:
    """Rolling Sharpe + drawdown for portfolio and benchmark. Pure."""
    def sharpe(r: pd.Series) -> pd.Series:
        ann_mean = r.rolling(window).mean() * TRADING_DAYS_PER_YEAR
        ann_std  = r.rolling(window).std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)
        return (ann_mean - risk_free_rate) / ann_std

    def drawdown(r: pd.Series) -> pd.Series:
        cum     = (1 + r).cumprod()
        peak    = cum.cummax()
        return (cum - peak) / peak

    return RollingMetrics(
        window             = window,
        portfolio_sharpe   = sharpe(portfolio_returns),
        benchmark_sharpe   = sharpe(benchmark_returns),
        portfolio_drawdown = drawdown(portfolio_returns),
        benchmark_drawdown = drawdown(benchmark_returns),
    )


def compute_analysis(
    returns: pd.DataFrame,
    weights: dict[str, float],
    benchmark: str,
    risk_free_rate: float,
    transaction_cost: float = 0.0,
    rolling_window: int | None = TRADING_DAYS_PER_YEAR,
) -> AnalysisResult:
    """`returns` must have one column per portfolio ticker plus the benchmark column,
    indexed by date with no missing values (use `PortfolioAnalyzer.fetch_returns`
    or align upstream).
    """
    if returns.empty:
        raise ValueError(
            "Insufficient overlapping data to compute returns. "
            "Increase date range or use assets with longer history."
        )
    missing = (set(weights) | {benchmark}) - set(returns.columns)
    if missing:
        raise ValueError(f"Returns DataFrame is missing columns: {sorted(missing)}")

    weights_series    = pd.Series(weights)
    portfolio_returns = returns[list(weights)].mul(weights_series, axis=1).sum(axis=1)
    benchmark_returns = returns[benchmark]

    # One-time entry transaction cost as a NAV haircut at inception. This
    # models a single buy-and-hold entry; see README for the DCA caveat.
    portfolio_input = portfolio_returns.copy()
    if transaction_cost > 0 and not portfolio_input.empty:
        portfolio_input.iloc[0] = (1 + portfolio_input.iloc[0]) * (1 - transaction_cost) - 1

    rolling = (
        compute_rolling_metrics(portfolio_returns, benchmark_returns, risk_free_rate, rolling_window)
        if rolling_window else None
    )

    return AnalysisResult(
        portfolio         = compute_asset_metrics(portfolio_input, risk_free_rate),
        benchmark         = compute_asset_metrics(benchmark_returns, risk_free_rate),
        individual_assets = {t: compute_asset_metrics(returns[t], risk_free_rate) for t in weights},
        weights           = dict(weights),
        benchmark_ticker  = benchmark,
        risk_free_rate    = risk_free_rate,
        transaction_cost  = transaction_cost,
        rolling           = rolling,
    )


# ── Pure render: console ──────────────────────────────────────────────────────

def print_results(result: AnalysisResult) -> None:
    """Tearsheet to stdout. Pure: depends only on `result`."""
    def fmt(val: float, fmt_str: str) -> str:
        return "n/a" if pd.isna(val) else fmt_str.format(val)

    bench_name = result.benchmark_ticker
    print("\nSummary")
    print('─' * 64)
    print(f"{'Metric':<26} {'Portfolio':>12} {bench_name:>12} {'Diff':>12}")
    print('─' * 64)

    metrics_map = [
        ('Annual Return (CAGR)',       'annual_return',            '{:.2%}'),
        ('Annual Return (Arithmetic)', 'annual_return_arithmetic', '{:.2%}'),
        ('Annual Volatility',          'annual_volatility',        '{:.2%}'),
        ('Sharpe Ratio',               'sharpe_ratio',             '{:.3f}'),
        ('Total Return',               'total_return',             '{:.2%}'),
        ('Max Drawdown',               'max_drawdown',             '{:.2%}'),
    ]
    for label, attr, value_fmt in metrics_map:
        port  = getattr(result.portfolio, attr)
        bench = getattr(result.benchmark, attr)
        diff  = port - bench
        print(f"{label:<26} {fmt(port, value_fmt):>12} "
              f"{fmt(bench, value_fmt):>12} {fmt(diff, value_fmt):>12}")

    sharpe_diff = result.portfolio.sharpe_ratio - result.benchmark.sharpe_ratio
    print(f"\nSharpe vs {bench_name}: {sharpe_diff:+.3f}")

    pl, ph = result.portfolio.sharpe_ci
    bl, bh = result.benchmark.sharpe_ci
    print("\nSharpe Ratio 95% CI (Lo 2002):")
    print(f"  {'Portfolio:':<16} [{pl:.3f}, {ph:.3f}]")
    print(f"  {bench_name + ':':<16} [{bl:.3f}, {bh:.3f}]")

    if result.transaction_cost > 0:
        print(f"\nTransaction cost: {result.transaction_cost:.2%} one-way entry — "
              "portfolio metrics reflect net-of-cost returns.")

    print("\nAssets")
    print('─' * 64)
    print(f"{'Ticker':<10} {'Weight':>10} {'Return':>15} {'Volatility':>12} {'Sharpe':>10}")
    print('─' * 64)
    for ticker, m in result.individual_assets.items():
        weight = result.weights[ticker]
        print(f"{ticker:<10} {weight:>9.1%} {m.annual_return:>14.2%} "
              f"{m.annual_volatility:>11.2%} {m.sharpe_ratio:>10.3f}")


# ── Pure render: matplotlib ───────────────────────────────────────────────────

def _plot_cumulative(ax, result: AnalysisResult) -> None:
    p = result.portfolio.cumulative_returns
    b = result.benchmark.cumulative_returns
    ax.plot(p.index, (p - 1) * 100, label='Portfolio', linewidth=2, color='#2E86AB')
    ax.plot(b.index, (b - 1) * 100, label=result.benchmark_ticker,
            linewidth=2, color='#A23B72', linestyle='--')
    ax.set_title('Cumulative Returns', fontweight='bold')
    ax.set_ylabel('Return (%)')
    ax.legend()


def _plot_risk_return(ax, result: AnalysisResult) -> None:
    if not result.individual_assets:
        return
    rf_pct = result.risk_free_rate * 100
    for ticker, m in result.individual_assets.items():
        ax.scatter(m.annual_volatility * 100, m.annual_return * 100,
                   s=result.weights[ticker] * 1000, alpha=0.6, label=ticker)
    ax.scatter(result.portfolio.annual_volatility * 100,
               result.portfolio.annual_return * 100,
               s=300, marker='*', color='gold', edgecolors='black',
               linewidth=2, label='Portfolio', zorder=5)
    ax.scatter(result.benchmark.annual_volatility * 100,
               result.benchmark.annual_return * 100,
               s=300, marker='D', color='red', edgecolors='black',
               linewidth=2, label=result.benchmark_ticker, zorder=5)
    ax.scatter(0, rf_pct, s=200, marker='^', color='#2CA02C', edgecolors='black',
               linewidth=1.5, label=f'Risk-Free ({result.risk_free_rate:.1%})', zorder=5)
    bench_vol = result.benchmark.annual_volatility * 100
    bench_ret = result.benchmark.annual_return * 100
    if bench_vol > 0:
        max_vol = max(m.annual_volatility * 100 for m in result.individual_assets.values()) * 1.2
        slope   = (bench_ret - rf_pct) / bench_vol
        cml_x   = np.linspace(0, max(max_vol, bench_vol * 1.2), 100)
        ax.plot(cml_x, rf_pct + slope * cml_x, color='#2CA02C',
                linewidth=1, linestyle=':', alpha=0.7, label='CML')
    ax.set_title('Risk-Return Profile', fontweight='bold')
    ax.set_xlabel('Volatility (Annual %)')
    ax.set_ylabel('CAGR (%)')
    ax.legend(loc='best')


def _plot_sharpe(ax, result: AnalysisResult) -> None:
    sharpe_data = {
        'Portfolio':              result.portfolio.sharpe_ratio,
        result.benchmark_ticker:  result.benchmark.sharpe_ratio,
    }
    cis = {
        'Portfolio':              result.portfolio.sharpe_ci,
        result.benchmark_ticker:  result.benchmark.sharpe_ci,
    }
    colors = ['#2E86AB' if v == max(sharpe_data.values()) else '#A23B72'
              for v in sharpe_data.values()]
    bars = ax.bar(sharpe_data.keys(), sharpe_data.values(), color=colors, alpha=0.7)
    for i, key in enumerate(sharpe_data):
        ci_lo, ci_hi = cis[key]
        sr           = sharpe_data[key]
        if np.isfinite(ci_lo) and np.isfinite(ci_hi):
            ax.errorbar(i, sr, yerr=[[sr - ci_lo], [ci_hi - sr]],
                        fmt='none', color='black', capsize=6, linewidth=1.5)
    ax.set_title('Sharpe Ratio with 95% CI (Lo 2002)', fontweight='bold')
    ax.set_ylabel('Sharpe Ratio')
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
    for bar in bars:
        height       = bar.get_height()
        va, offset   = ('bottom', 0.02) if height >= 0 else ('top', -0.02)
        ax.text(bar.get_x() + bar.get_width() / 2., height + offset,
                f'{height:.3f}', ha='center', va=va, fontweight='bold')


def _plot_allocation(ax, result: AnalysisResult) -> None:
    weights = list(result.weights.values())
    labels  = [f"{t}\n({w:.1%})" for t, w in result.weights.items()]
    ax.pie(weights, labels=labels, autopct='',
           colors=plt.cm.Set3(range(len(result.weights))), startangle=90)
    ax.set_title('Portfolio Allocation', fontweight='bold')


def _plot_rolling_sharpe(ax, result: AnalysisResult) -> None:
    r = result.rolling
    if r is None:
        return
    ax.plot(r.portfolio_sharpe.index, r.portfolio_sharpe,
            label='Portfolio', linewidth=1.5, color='#2E86AB')
    ax.plot(r.benchmark_sharpe.index, r.benchmark_sharpe,
            label=result.benchmark_ticker, linewidth=1.5, color='#A23B72', linestyle='--')
    ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
    ax.axhline(y=1, color='gray',  linestyle=':', linewidth=0.8, alpha=0.7)
    ax.set_title(f'Rolling Sharpe Ratio ({r.window // TRADING_DAYS_PER_YEAR}Y window)',
                 fontweight='bold')
    ax.set_ylabel('Sharpe Ratio')
    ax.legend()


def _plot_drawdown(ax, result: AnalysisResult) -> None:
    r = result.rolling
    if r is None:
        return
    p = r.portfolio_drawdown * 100
    b = r.benchmark_drawdown * 100
    ax.fill_between(p.index, p, 0, alpha=0.4, color='#2E86AB')
    ax.fill_between(b.index, b, 0, alpha=0.3, color='#A23B72')
    ax.plot(p.index, p, color='#2E86AB', linewidth=1, label='Portfolio')
    ax.plot(b.index, b, color='#A23B72', linewidth=1, linestyle='--',
            label=result.benchmark_ticker)
    ax.set_title('Underwater Chart (Drawdown from Peak)', fontweight='bold')
    ax.set_ylabel('Drawdown (%)')
    ax.legend()


def plot_dashboard(result: AnalysisResult, output_path: Path) -> Path:
    """Pure render — depends only on result and output_path."""
    sns.set_theme(style="whitegrid")
    fig, axes = plt.subplots(3, 2, figsize=(16, 18))
    fig.suptitle('Vero — Analysis Dashboard', fontsize=16, fontweight='bold')

    _plot_cumulative   (axes[0, 0], result)
    _plot_risk_return  (axes[0, 1], result)
    _plot_sharpe       (axes[1, 0], result)
    _plot_allocation   (axes[1, 1], result)
    if result.rolling is not None:
        _plot_rolling_sharpe(axes[2, 0], result)
        _plot_drawdown      (axes[2, 1], result)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close(fig)
    logger.info("Chart saved: %s", output_path)
    return output_path


# ── Coordinator ───────────────────────────────────────────────────────────────

class PortfolioAnalyzer:

    def __init__(self,
                 weights: dict[str, float],
                 start_date:       str | datetime | None = None,
                 end_date:         str | datetime | None = None,
                 benchmark:        str   = BENCHMARK,
                 risk_free_rate:   float = RISK_FREE_RATE,
                 transaction_cost: float = TRANSACTION_COST):
        self.weights          = self._normalize_portfolio(weights)
        self.benchmark        = self._normalize_benchmark(benchmark, self.weights)
        self.risk_free_rate   = float(risk_free_rate)
        self.transaction_cost = float(transaction_cost)
        self.start_date, self.end_date = self._resolve_date_range(start_date, end_date)

    # — Normalizers (pure, static) —

    @staticmethod
    def _normalize_portfolio(raw: dict[str, float]) -> dict[str, float]:
        if not raw:
            raise ValueError("Portfolio is empty. Provide at least one ticker with a weight.")
        cleaned: dict[str, float] = {}
        for ticker, raw_weight in raw.items():
            t = str(ticker).strip().upper()
            if not t:
                raise ValueError("Ticker symbols must be non-empty strings.")
            if t in cleaned:
                raise ValueError(f"Duplicate ticker after normalization: '{t}'")
            try:
                weight = float(raw_weight)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid weight for ticker '{t}': {raw_weight}") from exc
            if not np.isfinite(weight):
                raise ValueError(f"Weight for ticker '{t}' must be a finite number.")
            if weight < 0:
                raise ValueError("Portfolio weights must be non-negative.")
            cleaned[t] = weight
        total = sum(cleaned.values())
        if not np.isclose(total, 1.0, atol=WEIGHT_TOLERANCE):
            raise ValueError(f"Portfolio weights sum to {total:.2%}, must equal 100%")
        return cleaned

    @staticmethod
    def _normalize_benchmark(benchmark: str, weights: dict[str, float]) -> str:
        if not isinstance(benchmark, str):
            raise ValueError("Benchmark ticker must be a string.")
        b = benchmark.strip().upper()
        if not b:
            raise ValueError("Benchmark ticker cannot be empty.")
        if b in weights:
            raise ValueError(f"Benchmark ticker '{b}' cannot also be a portfolio holding.")
        return b

    @staticmethod
    def _resolve_date_range(
        start: str | datetime | None,
        end:   str | datetime | None,
    ) -> tuple[str, str]:
        end_dt   = PortfolioAnalyzer._coerce_date(end,   default=datetime.now())
        start_dt = PortfolioAnalyzer._coerce_date(start, default=end_dt - timedelta(days=365 * 3))
        if start_dt >= end_dt:
            raise ValueError(f"Start date {start_dt.date()} must be before end date {end_dt.date()}")
        return start_dt.strftime('%Y-%m-%d'), end_dt.strftime('%Y-%m-%d')

    @staticmethod
    def _coerce_date(value: str | datetime | None, default: datetime) -> datetime:
        if value is None:
            return default
        if isinstance(value, datetime):
            return value
        try:
            return datetime.strptime(value, '%Y-%m-%d')
        except ValueError as exc:
            raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD format.") from exc

    # — Fetch (the only impure operation in this class) —

    def fetch_returns(self) -> pd.DataFrame:
        """Download and align daily returns. Forward-fills single-day gaps then
        drops rows that any asset lacks, so all columns share a common index."""
        tickers = list(dict.fromkeys(list(self.weights) + [self.benchmark]))
        with yf_warnings():
            data = yf.download(
                tickers,
                start=self.start_date, end=self.end_date,
                progress=False, auto_adjust=True,
            )

        prices = data['Close'] if isinstance(data.columns, pd.MultiIndex) else data[['Close']]
        if isinstance(prices, pd.Series):
            prices = prices.to_frame()

        missing = set(tickers) - set(prices.columns)
        if missing:
            raise ValueError(f"Missing data for tickers: {', '.join(sorted(missing))}")

        prices = prices.ffill(limit=5)

        original_len = len(prices)
        first_valid  = {col: prices[col].first_valid_index() for col in prices.columns}
        limiting     = {col: dt for col, dt in first_valid.items()
                        if dt is not None and dt > prices.index[0]}
        prices = prices.dropna()

        if prices.empty:
            raise ValueError("No overlapping data found for the selected tickers and date range.")

        dropped = original_len - len(prices)
        if dropped > 0 and limiting:
            detail = ', '.join(f"{t} (from {d.date()})" for t, d in limiting.items())
            logger.info("Aligned to shortest common history: %d rows dropped. Limiting: %s", dropped, detail)
        logger.info("Data fetched: %d trading days (%s to %s)",
                    len(prices), prices.index[0].date(), prices.index[-1].date())

        returns = prices.pct_change().dropna(how='any')
        if returns.empty:
            raise ValueError(
                "Insufficient overlapping data to compute returns. "
                "Increase date range or use assets with longer history."
            )
        return returns

    # — Public entry point —

    def run_analysis(self) -> AnalysisResult:
        returns = self.fetch_returns()
        result  = compute_analysis(
            returns          = returns,
            weights          = self.weights,
            benchmark        = self.benchmark,
            risk_free_rate   = self.risk_free_rate,
            transaction_cost = self.transaction_cost,
        )
        print_results(result)
        plot_dashboard(result, DATA_DIR / 'portfolio_analysis.png')
        return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    holdings = load_holdings(HOLDINGS_FILE)
    if not holdings:
        print("No holdings found. Run: python portfolio.py buy TICKER DOLLARS")
        return

    PortfolioAnalyzer(
        weights          = cost_basis_weights(holdings),
        start_date       = min(h.start_date for h in holdings.values()),
        benchmark        = BENCHMARK,
        risk_free_rate   = RISK_FREE_RATE,
        transaction_cost = TRANSACTION_COST,
    ).run_analysis()


if __name__ == "__main__":
    main()
