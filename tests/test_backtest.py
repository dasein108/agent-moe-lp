"""Unit tests for the LP backtesting harness (pure math, no network)."""

from __future__ import annotations

import math

from moe_mantle_bot.backtest.config import BacktestConfig
from moe_mantle_bot.backtest.fee_model import step_fee_usd
from moe_mantle_bot.backtest.lb_position import (
    bin_id_from_price,
    build_position,
    price_at_bin,
)
from moe_mantle_bot.backtest.metrics import compute_metrics

BS, DX, DY = 100, 18, 6


def test_bin_price_roundtrip():
    for price in (0.1, 0.5444, 1.0, 3.7):
        b = bin_id_from_price(price, BS, DX, DY)
        assert abs(price_at_bin(b, BS, DX, DY) / price - 1.0) < BS / 10_000.0


def test_active_bin_matches_live():
    # live pool snapshot: price 0.5444 -> active bin 8385770
    assert bin_id_from_price(0.544428942621, BS, DX, DY) == 8385770


def _pos():
    return build_position(center_price=0.5444, bin_count=10, capital_usd=185.0,
                          quote_usd_target=50.0, bin_step=BS, dx=DX, dy=DY)


def test_entry_value_equals_capital():
    p = _pos()
    assert abs(p.value(0.5444) - 185.0) < 1.0


def test_inventory_all_quote_when_price_high():
    p = _pos()
    mnt, quote = p.inventory(10.0)
    assert mnt == 0.0
    assert quote > 0.0


def test_inventory_all_mnt_when_price_low():
    p = _pos()
    mnt, quote = p.inventory(0.01)
    assert quote == 0.0
    assert mnt > 0.0


def test_il_is_loss_vs_hodl_after_big_move():
    p = _pos()
    # large up move: LP sold MNT cheap -> underperforms HODL
    assert p.hodl_value(2.0) > p.value(2.0)


def test_quote_below_mnt_above_active():
    p = _pos()
    active = bin_id_from_price(0.5444, BS, DX, DY)
    # bins below active price hold quote (value frozen), above hold MNT
    mnt, quote = p.inventory(0.5444)
    assert mnt > 0 and quote > 0  # mixed at entry: both sides funded


def test_fee_zero_out_of_range():
    cfg = BacktestConfig()
    assert step_fee_usd(candle_turnover_usd=1e6, in_range=False,
                        active_value_usd=100.0, capture=1.0, cfg=cfg) == 0.0


def test_fee_monotonic_in_volume():
    cfg = BacktestConfig()
    lo = step_fee_usd(candle_turnover_usd=1000.0, in_range=True, active_value_usd=100.0, capture=1.0, cfg=cfg)
    hi = step_fee_usd(candle_turnover_usd=2000.0, in_range=True, active_value_usd=100.0, capture=1.0, cfg=cfg)
    assert hi > lo > 0


def test_fee_share_caps_at_full_liquidity():
    cfg = BacktestConfig(pool_active_liquidity_usd=0.0)
    fee = step_fee_usd(candle_turnover_usd=1000.0, in_range=True, active_value_usd=100.0, capture=1.0, cfg=cfg)
    # with no competing liquidity, LP captures full fee_rate of volume
    assert math.isclose(fee, 1000.0 * cfg.derived_lp_fee_rate(), rel_tol=1e-9)


def test_derived_fee_rate_after_protocol_share():
    cfg = BacktestConfig(base_factor=8000, bin_step=100, protocol_share_bps=2500)
    # 8000*100/1e8 = 0.008 base; *0.75 = 0.006 LP-net
    assert math.isclose(cfg.derived_lp_fee_rate(), 0.006, rel_tol=1e-9)


def test_metrics_net_pnl_math():
    m = compute_metrics(
        label="x", days=30, initial_value_usd=100.0, final_lp_value_usd=95.0,
        hodl_value_usd=98.0, total_fees_usd=10.0, gas_cost_usd=1.0,
        rebalance_cost_usd=2.0, in_range_steps=80, total_steps=100,
        rebalances=3, equity_curve=[100, 110, 90, 105])
    assert m.il_usd == 3.0                 # 98 - 95
    assert m.net_pnl_usd == 2.0            # 95 + 10 - 1 - 2 - 100
    assert m.in_range_pct == 80.0
    assert m.max_drawdown_pct > 0
