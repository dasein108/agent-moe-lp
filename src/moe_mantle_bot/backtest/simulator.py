"""Backtest driver: replay candles, mark positions, accrue fees, run the engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pandas as pd

from ..strategies.engine import PositionSnapshot, StrategyEngine, WalletComposition
from .candle_history import ReplayCandleFetcher, fetch_history, interval_minutes
from .config import BacktestConfig
from .engine_adapter import HistoricalMarket
from .fee_model import step_fee_usd
from .lb_position import LBPosition, bin_id_from_price, build_position
from .metrics import BacktestMetrics, compute_metrics

logger = logging.getLogger(__name__)

# Timeframes the live analyzers require (Keltner 5m; MTF 5m/1h/4h).
_REQUIRED_INTERVALS = ("5m", "1h", "4h")
_WARMUP_DAYS = 18  # 4h x 100 candles ≈ 17 days of lookback for MTF


@dataclass
class BacktestResult:
    config: dict
    static: BacktestMetrics
    strategy: BacktestMetrics
    start: str
    end: str
    final_price: float
    start_price: float
    capture: float
    daily_volume_usd: float | None
    events: list[dict]

    def to_dict(self) -> dict:
        return {
            "config": self.config,
            "window": {"start": self.start, "end": self.end,
                       "start_price": self.start_price, "final_price": self.final_price},
            "fee_model": {"effective_capture": self.capture,
                          "assumed_pool_daily_volume_usd": self.daily_volume_usd},
            "static": self.static.to_dict(),
            "strategy": self.strategy.to_dict(),
            "rebalance_events": self.events,
        }


def _mnt_weight(pos: LBPosition, price: float) -> float:
    mnt, quote = pos.inventory(price)
    total = mnt * price + quote
    return (mnt * price / total) if total > 0 else 0.0


def run_backtest(cfg: BacktestConfig, *, cache_dir: Path | None = None,
                 refresh: bool = False) -> BacktestResult:
    histories = {
        iv: fetch_history(cfg.symbol, iv, cfg.lookback_days, cache_dir=cache_dir, refresh=refresh)
        for iv in _REQUIRED_INTERVALS
    }
    base = histories[cfg.base_interval]
    if base.empty:
        raise RuntimeError("No base candles fetched")

    start_ts = base["timestamp"].iloc[0] + timedelta(days=_WARMUP_DAYS)
    sim = base[base["timestamp"] >= start_ts].reset_index(drop=True)
    if len(sim) < 50:
        raise RuntimeError(f"Backtest window too short ({len(sim)} candles). Increase --days.")

    start_price = float(sim["close"].iloc[0])

    # Derive the volume capture factor. Preferred: spread an assumed pool daily
    # volume across candles by Bybit turnover shape. Fallback: capture_ratio.
    window_days = (sim["timestamp"].iloc[-1] - sim["timestamp"].iloc[0]).total_seconds() / 86400
    total_turnover = float(sim["turnover"].sum())
    daily_volume = cfg.pool_daily_volume_usd
    if daily_volume is None and cfg.pool_tvl_usd:
        daily_volume = cfg.pool_tvl_usd  # default assumption: 1x TVL traded per day
    if daily_volume is not None and total_turnover > 0 and window_days > 0:
        capture = daily_volume * window_days / total_turnover
    else:
        capture = cfg.capture_ratio
    logger.info("fee capture=%.3e (pool_daily_volume=%s)", capture, daily_volume)

    fetcher = ReplayCandleFetcher(histories)
    hist_market = HistoricalMarket(fetcher, cfg)
    engine = StrategyEngine(wide_confidence_threshold=cfg.wide_confidence_threshold)

    dx, dy, bs = cfg.decimals_x, cfg.decimals_y, cfg.bin_step

    def make_position(center: float, bin_count: int, capital: float, quote_target: float) -> LBPosition:
        return build_position(center_price=center, bin_count=bin_count, capital_usd=capital,
                              quote_usd_target=quote_target, bin_step=bs, dx=dx, dy=dy)

    static = make_position(start_price, cfg.bin_count, cfg.capital_usd, cfg.quote_usd_target)
    strat = make_position(start_price, cfg.bin_count, cfg.capital_usd, cfg.quote_usd_target)
    strat_label = cfg.strategy_mode if cfg.strategy_mode in ("narrow", "wide") else "narrow"

    s_fees = g_fees = 0.0
    g_gas = g_rebal = 0.0
    s_inrange = g_inrange = 0
    rebalances = 0
    s_equity: list[float] = []
    g_equity: list[float] = []
    events: list[dict] = []

    decision_period = timedelta(minutes=cfg.decision_period_min)
    cooldown = timedelta(minutes=cfg.reenter_cooldown_min)
    last_decision: pd.Timestamp | None = None
    last_reenter: pd.Timestamp | None = None

    for _, row in sim.iterrows():
        ts = row["timestamp"]
        price = float(row["close"])
        turnover = float(row["turnover"])
        active = bin_id_from_price(price, bs, dx, dy)

        # ── static ──
        s_in = static.in_range(price)
        s_inrange += int(s_in)
        s_fees += step_fee_usd(candle_turnover_usd=turnover, in_range=s_in,
                               active_value_usd=static.active_bin_value(price),
                               capture=capture, cfg=cfg)
        s_equity.append(static.value(price) + s_fees)

        # ── strategy ──
        g_in = strat.in_range(price)
        g_inrange += int(g_in)
        g_fees += step_fee_usd(candle_turnover_usd=turnover, in_range=g_in,
                               active_value_usd=strat.active_bin_value(price),
                               capture=capture, cfg=cfg)

        due = last_decision is None or (ts - last_decision) >= decision_period
        cool_ok = last_reenter is None or (ts - last_reenter) >= cooldown
        if due and cool_ok:
            last_decision = ts
            fetcher.set_cursor(ts)
            market, optimal_bins = hist_market.market_state()
            gv = strat.value(price)
            wallet = WalletComposition(mnt_weight=_mnt_weight(strat, price),
                                       free_value_usdt=0.0, total_value_usdt=gv)
            pos_snap = PositionSnapshot(exists=True, in_range=g_in, bin_count=strat.bin_count,
                                        min_bin_id=strat.min_bin, max_bin_id=strat.max_bin,
                                        active_bin_id=active, deployed_value_usdt=gv)
            decision = engine.select_strategy(market, pos_snap, wallet,
                                              optimal_bin_count=optimal_bins,
                                              existing_position_strategy=strat_label)
            if decision.action == "exit_and_reenter":
                # choose re-entry width from a fresh (no-position) decision
                if cfg.strategy_mode in ("narrow", "wide"):
                    new_label = cfg.strategy_mode
                else:
                    entry = engine.select_strategy(
                        market,
                        PositionSnapshot(exists=False, in_range=False, bin_count=0,
                                         min_bin_id=None, max_bin_id=None, active_bin_id=active),
                        wallet, optimal_bin_count=optimal_bins)
                    new_label = entry.action if entry.action in ("narrow", "wide") else strat_label
                width = cfg.narrow_bin_count if new_label == "narrow" else cfg.wide_bin_count

                # execution cost: swap ~half capital toward 50/50 + gas
                swap_notional = gv * 0.5
                swap_cost = swap_notional * (cfg.slippage_bps + cfg.derived_lp_fee_rate() * 1e4) / 1e4
                gas_cost = cfg.gas_per_tx_mnt * cfg.tx_per_reenter * price
                new_capital = max(0.0, gv - swap_cost - gas_cost)
                g_rebal += swap_cost
                g_gas += gas_cost
                rebalances += 1
                strat_label = new_label
                strat = make_position(price, width, new_capital, new_capital * 0.5)
                last_reenter = ts
                events.append({"ts": ts.isoformat(), "action": "exit_and_reenter",
                               "reason": decision.reason, "new_strategy": new_label,
                               "width": width, "price": round(price, 5),
                               "value_before": round(gv, 2),
                               "swap_cost": round(swap_cost, 2), "gas_cost": round(gas_cost, 2)})

        g_equity.append(strat.value(price) + g_fees)

    end_ts = sim["timestamp"].iloc[-1]
    final_price = float(sim["close"].iloc[-1])
    days = (end_ts - sim["timestamp"].iloc[0]).total_seconds() / 86400
    total_steps = len(sim)

    static_m = compute_metrics(
        label="static", days=days, initial_value_usd=cfg.capital_usd,
        final_lp_value_usd=static.value(final_price), hodl_value_usd=static.hodl_value(final_price),
        total_fees_usd=s_fees, gas_cost_usd=0.0, rebalance_cost_usd=0.0,
        in_range_steps=s_inrange, total_steps=total_steps, rebalances=0, equity_curve=s_equity)

    strat_m = compute_metrics(
        label="strategy", days=days, initial_value_usd=cfg.capital_usd,
        final_lp_value_usd=strat.value(final_price), hodl_value_usd=strat.hodl_value(final_price),
        total_fees_usd=g_fees, gas_cost_usd=g_gas, rebalance_cost_usd=g_rebal,
        in_range_steps=g_inrange, total_steps=total_steps, rebalances=rebalances, equity_curve=g_equity)

    return BacktestResult(
        config=cfg.to_dict(), static=static_m, strategy=strat_m,
        start=sim["timestamp"].iloc[0].isoformat(), end=end_ts.isoformat(),
        final_price=final_price, start_price=start_price, events=events)
