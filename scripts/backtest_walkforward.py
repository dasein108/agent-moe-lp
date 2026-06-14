"""Walk-forward: strategy vs static across many rolling windows.

Single-window results are anecdotal (a fixed static range can be lucky). This
rolls a fixed-length window across history and reports, per window and in
aggregate, whether the managed strategy beats a static position.

Run:  .venv/bin/python scripts/backtest_walkforward.py
"""

from __future__ import annotations

from statistics import mean

from moe_mantle_bot.backtest.config import BacktestConfig
from moe_mantle_bot.backtest.simulator import run_backtest

BASE = dict(
    symbol="MNTUSDT", base_interval="1h", lookback_days=110,  # uses cached candles
    bin_step=100, decimals_x=18, decimals_y=6,
    base_factor=8000, protocol_share_bps=2500,
    capital_usd=124.35, quote_usd_target=87.57,
    pool_active_liquidity_usd=156.08, pool_daily_volume_usd=2000.0,
    reentry_rsi_gate=True, strategy_mode="auto", narrow_bin_count=10,
    decision_period_min=240,
)

WINDOW_DAYS = 30
OFFSETS = [0, 15, 30, 45, 60, 72]  # window_end_days_ago

VARIANTS = [
    ("baseline", dict()),
    ("trend-gate", dict(trend_confirm_gate=True)),
]


def main() -> None:
    agg = {label: {"net": [], "vs": [], "wins": 0} for label, _ in VARIANTS}
    static_nets = []
    print(f"window={WINDOW_DAYS}d   (price%/static% then each variant net% and Δ vs static)")
    print("=" * 92)
    print(f"{'window end':12} {'price%':>7} {'static%':>8} | " +
          " | ".join(f"{l:>10}" for l, _ in VARIANTS))
    print("-" * 92)
    for off in OFFSETS:
        ref = None
        results = {}
        for label, ov in VARIANTS:
            cfg = BacktestConfig(**{**BASE, "window_days": WINDOW_DAYS, "window_end_days_ago": off, **ov})
            r = run_backtest(cfg)
            ref = ref or r
            results[label] = r.strategy
        s = ref.static
        static_nets.append(s.net_pnl_pct)
        price_pct = (ref.final_price / ref.start_price - 1) * 100
        cells = []
        for label, _ in VARIANTS:
            m = results[label]
            d = m.net_pnl_pct - s.net_pnl_pct
            agg[label]["net"].append(m.net_pnl_pct)
            agg[label]["vs"].append(d)
            agg[label]["wins"] += int(m.net_pnl_pct >= s.net_pnl_pct)
            cells.append(f"{m.net_pnl_pct:+6.1f}({d:+5.1f})")
        print(f"{ref.end[:10]:12} {price_pct:>+7.1f} {s.net_pnl_pct:>+8.1f} | " + " | ".join(cells))

    n = len(OFFSETS)
    print("=" * 92)
    print(f"{'MEAN':12} {'':>7} {mean(static_nets):>+8.1f} | " +
          " | ".join(f"{mean(agg[l]['net']):+6.1f}({mean(agg[l]['vs']):+5.1f})" for l, _ in VARIANTS))
    print(f"{'WINS vs static':12} {'':>7} {'':>8} | " +
          " | ".join(f"{agg[l]['wins']:>2}/{n:<7}" for l, _ in VARIANTS))


if __name__ == "__main__":
    main()
