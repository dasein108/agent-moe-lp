"""Parameter sweep: find strategy configs that beat the static baseline.

Static + hold are a FIXED reference (10-bin current position). Only the
strategy's levers vary: initial/re-entry width, narrow-vs-wide mode, decision
cadence, RSI gate. Uses cached candles — run a normal `moe-backtest` first so
data/candles/ is populated for the desired --days.

Run:  .venv/bin/python scripts/backtest_sweep.py
"""

from __future__ import annotations

from moe_mantle_bot.backtest.config import BacktestConfig
from moe_mantle_bot.backtest.simulator import run_backtest

# Fixed market/pool context (seeded earlier from the live WMNT/USDT0 pool).
BASE = dict(
    symbol="MNTUSDT",
    base_interval="1h",        # 1h marking → fast sweep (IL/in-range unaffected materially)
    lookback_days=120,
    bin_step=100, decimals_x=18, decimals_y=6,
    base_factor=8000, protocol_share_bps=2500,
    capital_usd=124.35, quote_usd_target=87.57,
    pool_active_liquidity_usd=156.08,
    pool_daily_volume_usd=2000.0,
    reentry_rsi_gate=True,
)

# (label, overrides) — testing the anti-chase levers vs the baseline.
_AUTO = dict(strategy_mode="auto", narrow_bin_count=10, decision_period_min=240)
GRID = [
    ("baseline auto narrow10",          {**_AUTO}),
    ("+ ranging_hold",                  {**_AUTO, "ranging_hold": True}),
    ("+ ema-center",                    {**_AUTO, "reenter_center": "ema"}),
    ("+ ranging_hold + ema",            {**_AUTO, "ranging_hold": True, "reenter_center": "ema"}),
    ("+ rh + ema + narrow7",            {**_AUTO, "ranging_hold": True, "reenter_center": "ema", "narrow_bin_count": 7}),
    ("+ rh + ema + narrow5",            {**_AUTO, "ranging_hold": True, "reenter_center": "ema", "narrow_bin_count": 5}),
    ("+ rh + ema + narrow7 d12h",       {**_AUTO, "ranging_hold": True, "reenter_center": "ema", "narrow_bin_count": 7, "decision_period_min": 720}),
    ("+ rh + ema-1h-period20",          {**_AUTO, "ranging_hold": True, "reenter_center": "ema", "reenter_ema_interval": "1h", "reenter_ema_period": 20}),
    ("ranging_hold only narrow7",       {**_AUTO, "ranging_hold": True, "narrow_bin_count": 7}),
    ("ema-center only narrow7",         {**_AUTO, "reenter_center": "ema", "narrow_bin_count": 7}),
]


def main() -> None:
    rows = []
    static_ref = hold_ref = None
    for label, ov in GRID:
        cfg = BacktestConfig(**{**BASE, **ov})
        r = run_backtest(cfg)
        if static_ref is None:
            static_ref, hold_ref = r.static, r.hold
        g = r.strategy
        rows.append((label, g.net_pnl_pct, g.total_fees_usd, g.il_usd,
                     g.in_range_pct, g.rebalances))
        print(f"  done: {label:30} strat net {g.net_pnl_pct:+6.2f}%")

    print("\n" + "=" * 92)
    print(f"window {static_ref.days:.0f}d   "
          f"HOLD net {hold_ref.net_pnl_pct:+.2f}%   "
          f"STATIC net {static_ref.net_pnl_pct:+.2f}% "
          f"(fees ${static_ref.total_fees_usd}, IL ${static_ref.il_usd}, "
          f"inRange {static_ref.in_range_pct}%)")
    print("=" * 92)
    print(f"{'strategy config':32} {'net%':>8} {'fees$':>8} {'IL$':>7} {'inRange':>8} {'rebal':>6}  vs static")
    print("-" * 92)
    for label, net, fees, il, inr, reb in sorted(rows, key=lambda x: -x[1]):
        beat = "WIN " if net > static_ref.net_pnl_pct else "    "
        print(f"{label:32} {net:>+8.2f} {fees:>8.2f} {il:>7.2f} {inr:>7.1f}% {reb:>6} {beat}"
              f"{net - static_ref.net_pnl_pct:+.2f}")


if __name__ == "__main__":
    main()
