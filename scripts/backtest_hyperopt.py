"""Hyperparameter search for the LP strategy, scored on the walk-forward.

Random-searches the strategy parameter space; each candidate is evaluated across
several rolling windows (robust, not overfit to one window) and ranked by mean
net PnL. Static/hold are the fixed per-window reference. Uses cached candles.

Run:  .venv/bin/python scripts/backtest_hyperopt.py
"""

from __future__ import annotations

import random
from statistics import mean, pstdev

from moe_mantle_bot.backtest.config import BacktestConfig
from moe_mantle_bot.backtest.simulator import run_backtest

BASE = dict(
    symbol="MNTUSDT", base_interval="1h", lookback_days=110,
    bin_step=100, decimals_x=18, decimals_y=6,
    base_factor=8000, protocol_share_bps=2500,
    capital_usd=124.35, quote_usd_target=87.57,
    pool_active_liquidity_usd=156.08, pool_daily_volume_usd=2000.0,
    bin_count=10,  # fixes the static baseline
)

WINDOW_DAYS = 30
OFFSETS = [0, 15, 30, 45, 60, 72]
N_SAMPLES = 45

SPACE = {
    "oor_tolerance_bins": [0, 15, 25, 35, 50],  # 0 = engine default (no passive override)
    "narrow_bin_count": [7, 10, 15],
    "strat_initial_bin_count": [None, 10, 20],
    "decision_period_min": [240, 720, 1440],
    "reentry_rsi_gate": [True, False],
    "strategy_mode": ["auto", "narrow"],
    "stabilization_hold": [False, True],
}


def sample(rng) -> dict:
    c = {k: rng.choice(v) for k, v in SPACE.items()}
    if c["oor_tolerance_bins"] == 0:
        c["oor_tolerance_bins"] = None  # let engine adaptive tolerance decide
    return c


def evaluate(cfg_over: dict, static_cache: dict) -> dict:
    nets, deltas, wins = [], [], 0
    for off in OFFSETS:
        cfg = BacktestConfig(**{**BASE, "window_days": WINDOW_DAYS, "window_end_days_ago": off, **cfg_over})
        r = run_backtest(cfg)
        if off not in static_cache:
            static_cache[off] = r.static.net_pnl_pct
        s = static_cache[off]
        nets.append(r.strategy.net_pnl_pct)
        deltas.append(r.strategy.net_pnl_pct - s)
        wins += int(r.strategy.net_pnl_pct >= s)
    return {"mean": mean(nets), "min": min(nets), "std": pstdev(nets),
            "mean_delta": mean(deltas), "wins": wins}


def main() -> None:
    rng = random.Random(42)
    static_cache: dict = {}

    # de-dup samples
    seen, candidates = set(), []
    while len(candidates) < N_SAMPLES:
        c = sample(rng)
        key = tuple(sorted(c.items()))
        if key not in seen:
            seen.add(key)
            candidates.append(c)

    results = []
    for i, over in enumerate(candidates):
        m = evaluate(over, static_cache)
        results.append((over, m))
        print(f"  [{i+1}/{len(candidates)}] mean {m['mean']:+6.2f}  min {m['min']:+6.2f}  "
              f"wins {m['wins']}/{len(OFFSETS)}  {over}")

    static_mean = mean(static_cache.values())
    print("\n" + "=" * 100)
    print(f"STATIC mean net across {len(OFFSETS)} windows: {static_mean:+.2f}%   "
          f"(per-window: {', '.join(f'{static_cache[o]:+.1f}' for o in OFFSETS)})")
    print("=" * 100)
    print("TOP 10 by mean net (then by min / consistency):")
    results.sort(key=lambda x: (x[1]["mean"], x[1]["min"]), reverse=True)
    for over, m in results[:10]:
        beat = "BEATS" if m["mean"] > static_mean else "ties " if abs(m["mean"] - static_mean) < 0.05 else "below"
        print(f"  mean {m['mean']:+6.2f} ({beat} static {m['mean_delta']:+.2f})  "
              f"min {m['min']:+6.2f}  std {m['std']:4.1f}  wins {m['wins']}/{len(OFFSETS)}  | "
              + ", ".join(f"{k}={v}" for k, v in over.items()))


if __name__ == "__main__":
    main()
