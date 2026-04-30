"""
generate_preview.py — Write a static demo dashboard to ~/.portfolio/dashboard.html.

Run this, open the printed path in a browser, screenshot at ~1400×800px,
and save the result as docs/dashboard-preview.png.
"""

import random
import webbrowser
from dashboard import build_html

# ── Demo price histories ──────────────────────────────────────────────────────

def _trend(start, end, n):
    """Linear price series with slight noise for a realistic sparkline."""
    random.seed(42)
    step = (end - start) / (n - 1)
    return [round(start + step * i + random.uniform(-step * 0.4, step * 0.4), 2) for i in range(n)]

_JPM_YTD  = _trend(218.0, 248.3, 80)
_JPM_6M   = _trend(228.0, 248.3, 126)
_JPM_3M   = _trend(237.0, 248.3, 63)
_JPM_1M   = _trend(241.0, 248.3, 21)
_JPM_1W   = [246.10, 248.90, 247.20, 249.80, 248.30]

_GOOGL_YTD = _trend(192.0, 156.8, 80)
_GOOGL_6M  = _trend(182.0, 156.8, 126)
_GOOGL_3M  = _trend(175.0, 156.8, 63)
_GOOGL_1M  = _trend(170.0, 156.8, 21)
_GOOGL_1W  = [165.40, 163.20, 161.50, 158.90, 156.80]

_META_YTD  = _trend(484.0, 592.4, 80)
_META_6M   = _trend(510.0, 592.4, 126)
_META_3M   = _trend(548.0, 592.4, 63)
_META_1M   = _trend(565.0, 592.4, 21)
_META_1W   = [572.00, 578.40, 584.20, 589.80, 592.40]

# ── Holdings sparklines (1M window) ─────────────────────────────────────────

_NVDA_1M  = _trend(88.0,  118.0, 21)
_AAPL_1M  = _trend(192.0, 199.0, 21)
_AXP_1M   = _trend(204.0, 242.0, 21)
_SWPPX_1M = _trend(70.2,  73.4,  21)

# ── Payload ───────────────────────────────────────────────────────────────────

PAYLOAD = {
    "generated": "2026-04-20T08:02:00+00:00",
    "holdings": [
        {
            "ticker": "NVDA", "label": "NVIDIA Corporation",
            "shares": 180.00, "cost": 9720.00, "price": 118.00, "value": 21240.00,
            "gain_pct": 118.52, "gain_dollar": 11520.00,
            "day_change_dollar": 392.94, "day_change_pct": 1.85,
            "history_1m": _NVDA_1M,
        },
        {
            "ticker": "AAPL", "label": "Apple Inc.",
            "shares": 95.00, "cost": 14440.00, "price": 199.00, "value": 18905.00,
            "gain_pct": 30.92, "gain_dollar": 4465.00,
            "day_change_dollar": 85.07, "day_change_pct": 0.45,
            "history_1m": _AAPL_1M,
        },
        {
            "ticker": "AXP", "label": "American Express Company",
            "shares": 75.00, "cost": 13500.00, "price": 242.00, "value": 18150.00,
            "gain_pct": 34.44, "gain_dollar": 4650.00,
            "day_change_dollar": 223.25, "day_change_pct": 1.23,
            "history_1m": _AXP_1M,
        },
        {
            "ticker": "SWPPX", "label": "Schwab S&P 500 Index Fund",
            "shares": 240.00, "cost": 13440.00, "price": 73.40, "value": 17616.00,
            "gain_pct": 31.07, "gain_dollar": 4176.00,
            "day_change_dollar": 72.23, "day_change_pct": 0.41,
            "history_1m": _SWPPX_1M,
        },
    ],
    "savings": [
        {"name": "Car Fund",     "bank": "Amex Savings", "balance": 12450.00, "apy": 0.0400},
        {"name": "Housing Fund", "bank": "Amex Savings", "balance": 38200.00, "apy": 0.0400},
    ],
    "watchlist": [
        {
            "ticker": "JPM", "label": "JPMorgan", "price": 248.30,
            "signal": "BULLISH", "reason": "strong momentum",
            "description": "Largest US bank by assets. Runs consumer banking, investment banking, and asset management. Earns on interest rate spread and fee income from capital markets.",
            "sector": "Financial Services",
            "history": {"1W": _JPM_1W, "1M": _JPM_1M, "3M": _JPM_3M, "6M": _JPM_6M, "YTD": _JPM_YTD},
        },
        {
            "ticker": "GOOGL", "label": "Alphabet", "price": 156.80,
            "signal": "BEARISH", "reason": "downtrend",
            "description": "Parent company of Google. Sells search advertising, cloud computing (GCP), and hardware. Search and YouTube make up over 80% of revenue.",
            "sector": "Communication Services",
            "history": {"1W": _GOOGL_1W, "1M": _GOOGL_1M, "3M": _GOOGL_3M, "6M": _GOOGL_6M, "YTD": _GOOGL_YTD},
        },
        {
            "ticker": "META", "label": "Meta Platforms", "price": 592.40,
            "signal": "BULLISH", "reason": "strong momentum",
            "description": "Owns Facebook, Instagram, and WhatsApp. Revenue is almost entirely digital advertising sold against 3 billion daily active users. Also investing heavily in VR hardware.",
            "sector": "Communication Services",
            "history": {"1W": _META_1W, "1M": _META_1M, "3M": _META_3M, "6M": _META_6M, "YTD": _META_YTD},
        },
    ],
    "totals": {
        "portfolio_value": 75911.00,
        "savings_total":   50650.00,
        "total_cost":      51100.00,
        "total_gain_pct":  48.55,
        "portfolio_goal":  150000,
        "savings_goal":    75000,
        "total_accrued":   None,
        "payment_day":     None,
    },
    "chart_src": "",
}

out = build_html(PAYLOAD)

# Disable animations so the screenshot captures fully-rendered charts.
html = out.read_text()
html = html.replace(
    '</head>',
    '<style>*, *::before, *::after {'
    ' animation-duration: 0.001ms !important;'
    ' transition-duration: 0.001ms !important; }'
    '</style>\n</head>',
)
out.write_text(html)

print(f"Preview written to: {out}")
print("Screenshot at ~1400×800px and save to docs/dashboard-preview.png")
webbrowser.open(out.as_uri())
