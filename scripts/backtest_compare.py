"""Validate the trend-confirmation gate across BOTH regimes.

Goal: a config that is >= static in the 90d trend-V window AND the 120d
round-trip window. Static/hold are the fixed reference per window.

Run:  .venv/bin/python scripts/backtest_compare.py
"""

from __future__ import annotations

from moe_mantle_bot.backtest.config import BacktestConfig
from moe_mantle_bot.backtest.simulator import run_backtest

BASE = dict(
    symbol="MNTUSDT", base_interval="1h",
    bin_step=100, decimals_x=18, decimals_y=6,
    base_factor=8000, protocol_share_bps=2500,
    capital_usd=124.35, quote_usd_target=87.57,
    pool_active_liquidity_usd=156.08, pool_daily_volume_usd=2000.0,
    reentry_rsi_gate=True, strategy_mode="auto", narrow_bin_count=10,
    decision_period_min=240,
)

VARIANTS = [
    ("baseline (chase)",          dict()),
    ("trend-gate",                dict(trend_confirm_gate=True)),
    ("trend-gate + narrow7",      dict(trend_confirm_gate=True, narrow_bin_count=7)),
    ("trend-gate conf0.5",        dict(trend_confirm_gate=True, trend_confirm_min_confidence=0.5)),
]


def main() -> None:
    for days in (90, 120):
        print(f"\n{'='*78}\nWINDOW days={days}")
        ref = None
        rows = []
        for label, ov in VARIANTS:
            cfg = BacktestConfig(**{**BASE, "lookback_days": days, **ov})
            r = run_backtest(cfg)
            ref = ref or r
            rows.append((label, r.strategy))
        s, h = ref.static, ref.hold
        print(f"  {ref.start[:10]} → {ref.end[:10]} ({s.days:.0f}d)  "
              f"price {ref.start_price:.4f}→{ref.final_price:.4f} "
              f"({(ref.final_price/ref.start_price-1)*100:+.1f}%)")
        print(f"  {'HOLD':24} net {h.net_pnl_pct:+7.2f}%")
        print(f"  {'STATIC':24} net {s.net_pnl_pct:+7.2f}%  fees ${s.total_fees_usd:.0f}  "
              f"inRange {s.in_range_pct:.0f}%")
        print(f"  {'-'*70}")
        for label, m in rows:
            flag = "  >= static" if m.net_pnl_pct >= s.net_pnl_pct else ""
            print(f"  {label:24} net {m.net_pnl_pct:+7.2f}%  fees ${m.total_fees_usd:>4.0f}  "
                  f"inRange {m.in_range_pct:>3.0f}%  rebal {m.rebalances:>2}{flag}")


if __name__ == "__main__":
    main()
