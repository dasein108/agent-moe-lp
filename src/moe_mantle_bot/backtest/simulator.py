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
from .lb_position import LBPosition, bin_id_from_price, build_position, price_at_bin
from .metrics import BacktestMetrics, compute_metrics

logger = logging.getLogger(__name__)

# Timeframes the live analyzers require (Keltner 5m; MTF 5m/1h/4h).
_REQUIRED_INTERVALS = ("5m", "1h", "4h")
_WARMUP_DAYS = 18  # 4h x 100 candles ≈ 17 days of lookback for MTF


@dataclass
class BacktestResult:
    config: dict
    hold: BacktestMetrics
    static: BacktestMetrics
    strategy: BacktestMetrics
    start: str
    end: str
    final_price: float
    start_price: float
    capture: float
    daily_volume_usd: float | None
    events: list[dict]
    series: dict | None = None  # per-step arrays for plotting (not serialized)

    def to_dict(self) -> dict:
        return {
            "config": self.config,
            "window": {"start": self.start, "end": self.end,
                       "start_price": self.start_price, "final_price": self.final_price},
            "fee_model": {"effective_capture": self.capture,
                          "assumed_pool_daily_volume_usd": self.daily_volume_usd},
            "hold": self.hold.to_dict(),
            "static": self.static.to_dict(),
            "strategy": self.strategy.to_dict(),
            "rebalance_events": self.events,
        }


def _mnt_weight(pos: LBPosition, price: float) -> float:
    mnt, quote = pos.inventory(price)
    total = mnt * price + quote
    return (mnt * price / total) if total > 0 else 0.0


def _ema(fetcher: ReplayCandleFetcher, symbol: str, interval: str,
         period: int, fallback: float) -> float:
    try:
        df = fetcher.get_candles(symbol, interval, period * 3)
        if len(df) >= 5:
            v = float(df["close"].ewm(span=period, adjust=False).mean().iloc[-1])
            return v if v > 0 else fallback
    except (RuntimeError, KeyError, ValueError):
        pass
    return fallback


def _reenter_center(fetcher: ReplayCandleFetcher, cfg: BacktestConfig, spot: float) -> float:
    """Re-entry center price: spot, or an EMA (mean-reversion anchor)."""
    if cfg.reenter_center != "ema":
        return spot
    return _ema(fetcher, cfg.symbol, cfg.reenter_ema_interval, cfg.reenter_ema_period, spot)


def run_backtest(cfg: BacktestConfig, *, cache_dir: Path | None = None,
                 refresh: bool = False) -> BacktestResult:
    histories = {
        iv: fetch_history(cfg.symbol, iv, cfg.lookback_days, cache_dir=cache_dir, refresh=refresh)
        for iv in _REQUIRED_INTERVALS
    }
    base = histories[cfg.base_interval]
    if base.empty:
        raise RuntimeError("No base candles fetched")

    first_ts, last_ts = base["timestamp"].iloc[0], base["timestamp"].iloc[-1]
    if cfg.window_days:
        win_end = last_ts - timedelta(days=cfg.window_end_days_ago)
        win_start = win_end - timedelta(days=cfg.window_days)
        min_start = first_ts + timedelta(days=_WARMUP_DAYS)
        if win_start < min_start:
            win_start = min_start
        sim = base[(base["timestamp"] >= win_start) & (base["timestamp"] <= win_end)].reset_index(drop=True)
    else:
        win_start = first_ts + timedelta(days=_WARMUP_DAYS)
        sim = base[base["timestamp"] >= win_start].reset_index(drop=True)
    if len(sim) < 50:
        raise RuntimeError(f"Backtest window too short ({len(sim)} candles). Increase history or window_days.")

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
    strat_bins0 = cfg.strat_initial_bin_count or cfg.bin_count
    strat = make_position(start_price, strat_bins0, cfg.capital_usd, cfg.quote_usd_target)
    strat_label = cfg.strategy_mode if cfg.strategy_mode in ("narrow", "wide") else "narrow"

    s_fees = g_fees = 0.0
    g_gas = g_rebal = 0.0
    s_inrange = g_inrange = 0
    rebalances = 0
    s_equity: list[float] = []
    g_equity: list[float] = []
    hold_equity: list[float] = []
    events: list[dict] = []
    ts_series: list[str] = []
    price_series: list[float] = []
    g_lo_series: list[float] = []
    g_hi_series: list[float] = []
    s_lo = price_at_bin(static.min_bin, bs, dx, dy)
    s_hi = price_at_bin(static.max_bin, bs, dx, dy)

    decision_period = timedelta(minutes=cfg.decision_period_min)
    cooldown = timedelta(minutes=cfg.reenter_cooldown_min)
    last_decision: pd.Timestamp | None = None
    last_reenter: pd.Timestamp | None = None
    awaiting_stab = False
    await_since: pd.Timestamp | None = None

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
        hold_equity.append(static.hodl_value(price))

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
            # In forced narrow/wide mode, suppress the width-fitness exit (only
            # true OOR should re-center) — otherwise a fixed wide position that
            # is N× the Keltner-optimal thrashes (exit→re-enter wide→repeat).
            forced = cfg.strategy_mode in ("narrow", "wide")
            optimal_arg = None if forced else optimal_bins
            decision = engine.select_strategy(market, pos_snap, wallet,
                                              optimal_bin_count=optimal_arg,
                                              existing_position_strategy=strat_label)
            # Ranging-hold: don't chase in a RANGING regime — hold and earn fees
            # on the oscillation like a static position.
            if (decision.action == "exit_and_reenter" and cfg.ranging_hold
                    and market.regime == "RANGING"):
                decision = type(decision)(action="hold", reason="ranging_hold", confidence=1.0)

            # Trend-confirmation gate: hold unless this is a STRONG confirmed
            # continuation in the exit direction. Avoids chasing round-trip legs.
            if decision.action == "exit_and_reenter" and cfg.trend_confirm_gate:
                ex_down = active < strat.min_bin
                ex_up = active > strat.max_bin
                conf_ok = market.regime_confidence >= cfg.trend_confirm_min_confidence
                confirmed = conf_ok and (
                    (ex_down and market.regime == "TRENDING_DOWN" and market.higher_tf_bias == "BEAR")
                    or (ex_up and market.regime == "TRENDING_UP" and market.higher_tf_bias == "BULL")
                )
                if not confirmed:
                    decision = type(decision)(
                        action="hold", reason="trend_unconfirmed_hold", confidence=1.0)

            # Stabilization-hold: don't redeploy at an extreme. Wait until price
            # retraces near its EMA, RSI normalizes, or a max wait elapses.
            if decision.action == "exit_and_reenter" and cfg.stabilization_hold:
                ema = _ema(fetcher, cfg.symbol, cfg.stab_ema_interval, cfg.stab_ema_period, price)
                band_ok = abs(price - ema) / ema <= cfg.stab_ema_band_pct / 100.0
                rsi_norm = not market.overbought and not market.oversold
                if not awaiting_stab:
                    awaiting_stab = True
                    await_since = ts
                timed_out = (ts - await_since) >= timedelta(minutes=cfg.stab_max_wait_min)
                if band_ok or rsi_norm or timed_out:
                    awaiting_stab = False  # stabilized → proceed to re-center
                else:
                    decision = type(decision)(
                        action="hold", reason="stabilization_wait", confidence=1.0)
            elif decision.action != "exit_and_reenter":
                awaiting_stab = False  # back in range / holding → clear wait

            if decision.action == "exit_and_reenter":
                # choose re-entry width from a fresh (no-position) decision
                if forced:
                    new_label = cfg.strategy_mode
                    width = cfg.narrow_bin_count if new_label == "narrow" else cfg.wide_bin_count
                else:
                    entry = engine.select_strategy(
                        market,
                        PositionSnapshot(exists=False, in_range=False, bin_count=0,
                                         min_bin_id=None, max_bin_id=None, active_bin_id=active),
                        wallet, optimal_bin_count=optimal_bins)
                    new_label = entry.action if entry.action in ("narrow", "wide") else strat_label
                    base_w = cfg.narrow_bin_count if new_label == "narrow" else cfg.wide_bin_count
                    # snap to the engine's optimal width so we don't immediately
                    # re-trigger the range_too_wide/narrow fitness exit.
                    width = optimal_bins if optimal_bins else base_w

                # Re-entry inventory policy (mirrors the live reentry_policy RSI
                # gate). exit-DOWN + oversold, exit-UP + overbought, or a
                # bear/ranging regime → keep current inventory (no swap), so we
                # don't sell the low / buy the high. Otherwise rebalance to 50/50.
                exit_down = active < strat.min_bin
                exit_up = active > strat.max_bin
                mnt_val, quote_val = strat.inventory(price)
                keep_inventory = cfg.reentry_rsi_gate and (
                    (exit_down and market.oversold)
                    or (exit_up and market.overbought)
                    or market.regime in ("TRENDING_DOWN", "RANGING")
                )
                gas_cost = cfg.gas_per_tx_mnt * cfg.tx_per_reenter * price
                if keep_inventory:
                    swap_cost = 0.0
                    reentry_mode = "continuation_safe"
                    new_capital = max(0.0, gv - gas_cost)
                    quote_target = quote_val * (new_capital / gv if gv else 0.0)
                else:
                    # only swap the gap needed to reach ~50/50
                    swap_notional = abs(gv * 0.5 - quote_val)
                    swap_cost = swap_notional * (cfg.slippage_bps + cfg.derived_lp_fee_rate() * 1e4) / 1e4
                    reentry_mode = "rebalance_50_50"
                    new_capital = max(0.0, gv - swap_cost - gas_cost)
                    quote_target = new_capital * 0.5
                g_rebal += swap_cost
                g_gas += gas_cost
                rebalances += 1
                strat_label = new_label
                center = _reenter_center(fetcher, cfg, price)
                strat = make_position(center, width, new_capital, quote_target)
                last_reenter = ts
                events.append({"ts": ts.isoformat(), "action": "exit_and_reenter",
                               "reason": decision.reason, "new_strategy": new_label,
                               "reentry_mode": reentry_mode, "center": round(center, 5),
                               "exit_dir": "down" if exit_down else "up" if exit_up else "edge",
                               "oversold": market.oversold, "regime": market.regime,
                               "width": width, "price": round(price, 5),
                               "value_before": round(gv, 2),
                               "swap_cost": round(swap_cost, 2), "gas_cost": round(gas_cost, 2)})

        g_equity.append(strat.value(price) + g_fees)
        ts_series.append(ts.isoformat())
        price_series.append(price)
        g_lo_series.append(price_at_bin(strat.min_bin, bs, dx, dy))
        g_hi_series.append(price_at_bin(strat.max_bin, bs, dx, dy))

    end_ts = sim["timestamp"].iloc[-1]
    final_price = float(sim["close"].iloc[-1])
    days = (end_ts - sim["timestamp"].iloc[0]).total_seconds() / 86400
    total_steps = len(sim)

    hold_end = static.hodl_value(final_price)
    hold_m = compute_metrics(
        label="hold", days=days, initial_value_usd=cfg.capital_usd,
        final_lp_value_usd=hold_end, hodl_value_usd=hold_end,
        total_fees_usd=0.0, gas_cost_usd=0.0, rebalance_cost_usd=0.0,
        in_range_steps=0, total_steps=total_steps, rebalances=0, equity_curve=hold_equity)

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

    series = {
        "ts": ts_series, "price": price_series,
        "static_lo": s_lo, "static_hi": s_hi,
        "strat_lo": g_lo_series, "strat_hi": g_hi_series,
        "static_equity": s_equity, "strategy_equity": g_equity,
        "hold_equity": hold_equity,
    }
    return BacktestResult(
        config=cfg.to_dict(), hold=hold_m, static=static_m, strategy=strat_m,
        start=sim["timestamp"].iloc[0].isoformat(), end=end_ts.isoformat(),
        final_price=final_price, start_price=start_price,
        capture=capture, daily_volume_usd=daily_volume, events=events, series=series)
