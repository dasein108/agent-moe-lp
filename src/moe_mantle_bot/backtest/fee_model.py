"""Volume-capture fee model.

On-chain historical pool swap volume is not available, so pool volume is
approximated from Bybit quote (USD) turnover scaled by ``capture_ratio``. LP
fees accrue only while the position is in range, proportional to the
position's share of liquidity in the active bin.

    fee_rate        = LP-net swap fee fraction (after protocol share)
    step_volume_usd = candle_turnover_usd * capture_ratio
    share           = active_value_usd / (active_value_usd + pool_active_liquidity_usd)
    fee_step_usd    = step_volume_usd * fee_rate * share        (0 if out of range)

These are explicit assumptions, reported in every backtest summary so results
can be calibrated against observed on-chain fees.
"""

from __future__ import annotations

from .config import BacktestConfig


def step_fee_usd(
    *,
    candle_turnover_usd: float,
    in_range: bool,
    active_value_usd: float,
    capture: float,
    cfg: BacktestConfig,
) -> float:
    """Fee for one candle. ``capture`` maps Bybit turnover to pool volume; the
    simulator derives it from ``pool_daily_volume_usd`` when set."""
    if not in_range or active_value_usd <= 0:
        return 0.0
    step_volume = candle_turnover_usd * capture
    fee_rate = cfg.derived_lp_fee_rate()
    denom = active_value_usd + cfg.pool_active_liquidity_usd
    if denom <= 0:
        return 0.0
    share = active_value_usd / denom
    return step_volume * fee_rate * share
