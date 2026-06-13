"""
Re-entry inventory policy — decides how to rebalance after exiting an OOR position.

Flow: exit position → compute RSI → check regime → pick target ratio → swap → re-enter.

Five gates before any swap:
  1. Regime gate: BEAR/RANGING → continuation_safe (no swap)
  2. RSI gate: block sell when oversold (≤30), block buy when overbought (≥70)
  3. VWAP guard: only buy below 24h avg, only sell above (2% dead-zone)
  4. Cooldown: 4h between swaps in same direction
  5. Size cap: max $200 per swap + max swap pct of portfolio

Target ratio comes from regime-aware table (optimizer-calibrated on 41d real candle data).
"""

from __future__ import annotations

import json
import time as _time
from dataclasses import asdict
from decimal import Decimal
from typing import Any, Callable

from ..logging_config import get_logger

logger = get_logger(__name__)


class ReentryPolicyService:
    """Decide rebalance ratio after LP exit, gate on RSI, execute swap."""

    _CONTEXT_MAP = {"down": "exit_down", "up": "exit_up", "unknown": "neutral"}

    def __init__(
        self,
        *,
        settings,
        balance,
        analytics,
        keltner_analyzer,
        bias_calculator,
        safe_float: Callable[[Any], float],
        gas_cost_mnt: Callable[[list], float],
        calculate_rsi: Callable[..., float | None],
    ) -> None:
        self.settings = settings
        self.balance = balance
        self.analytics = analytics
        self.keltner_analyzer = keltner_analyzer
        self.bias_calculator = bias_calculator
        self._safe_float = safe_float
        self._gas_cost_mnt = gas_cost_mnt
        self._calculate_rsi = calculate_rsi
        self._last_buy_ts: float = 0.0   # epoch of last buy-MNT swap
        self._last_sell_ts: float = 0.0   # epoch of last sell-MNT swap

    # ── RSI Computation ───────────────────────────────────

    def _get_rsi(self) -> float | None:
        """Fetch current RSI-14 from 5m candles."""
        try:
            candles = self.keltner_analyzer.candle_fetcher.get_candles(
                symbol="MNTUSDT", interval="5m", limit=200,
            )
            closes = candles["close"].astype(float)
            return self._calculate_rsi(closes, period=14)
        except Exception as e:
            logger.debug(f"RSI unavailable: {e}")
            return None

    # ── RSI Gate ──────────────────────────────────────────

    def _check_rsi_gate(self, exit_direction: str, rsi: float | None) -> tuple[bool, str]:
        """Check if RSI blocks rebalancing.

        exit-DOWN + RSI ≤ 30: block (keep MNT — oversold, bounce likely)
        exit-UP + RSI ≥ 70: block (keep USDT — overbought, drop likely)

        Returns: (blocked: bool, reason: str)
        """
        if not self.settings.reentry_rsi_filter_enabled or rsi is None:
            return False, "rsi_disabled" if rsi is not None else "rsi_unavailable"

        if exit_direction == "down" and rsi <= self.settings.reentry_rsi_exit_down_threshold:
            return True, f"oversold_keep_mnt:rsi={rsi:.0f}<={self.settings.reentry_rsi_exit_down_threshold:.0f}"

        if exit_direction == "up" and rsi >= self.settings.reentry_rsi_exit_up_threshold:
            return True, f"overbought_keep_usdt:rsi={rsi:.0f}>={self.settings.reentry_rsi_exit_up_threshold:.0f}"

        return False, f"rsi_ok:{rsi:.0f}"

    # ── VWAP Guard ────────────────────────────────────────

    def _get_vwap_24h(self) -> float | None:
        """Compute 24h volume-weighted average price from 1h candles."""
        try:
            candles = self.keltner_analyzer.candle_fetcher.get_candles(
                symbol="MNTUSDT", interval="60", limit=24,
            )
            close = candles["close"].astype(float).values
            volume = candles["volume"].astype(float).values
            total_vol = volume.sum()
            if total_vol <= 0:
                return float(close.mean())
            return float((close * volume).sum() / total_vol)
        except Exception as e:
            logger.debug(f"VWAP unavailable: {e}")
            return None

    def _check_vwap_guard(
        self, is_buying_mnt: bool, current_price: float | None,
    ) -> tuple[bool, str]:
        """Block swaps at unfavorable prices vs 24h VWAP.

        Buy MNT only when price < VWAP - dead_zone (buying dip).
        Sell MNT only when price > VWAP + dead_zone (selling rally).
        Returns: (blocked: bool, reason: str)
        """
        if not self.settings.reentry_vwap_guard_enabled:
            return False, "vwap_disabled"
        if current_price is None:
            return False, "vwap_no_price"

        vwap = self._get_vwap_24h()
        if vwap is None or vwap <= 0:
            return False, "vwap_unavailable"

        dead_zone = self.settings.reentry_vwap_dead_zone_pct / 100.0
        deviation = (current_price - vwap) / vwap

        if is_buying_mnt and deviation > -dead_zone:
            return True, f"vwap_buy_blocked:price={current_price:.5f}>vwap={vwap:.5f}×{1-dead_zone:.2f}"
        if not is_buying_mnt and deviation < dead_zone:
            return True, f"vwap_sell_blocked:price={current_price:.5f}<vwap={vwap:.5f}×{1+dead_zone:.2f}"

        return False, f"vwap_ok:dev={deviation:+.1%}"

    # ── Cooldown Guard ───────────────────────────────────

    def _check_cooldown(self, is_buying_mnt: bool) -> tuple[bool, str]:
        """Block swaps if same direction was swapped recently."""
        cooldown = self.settings.reentry_swap_cooldown_seconds
        if cooldown <= 0:
            return False, "cooldown_disabled"

        now = _time.time()
        last_ts = self._last_buy_ts if is_buying_mnt else self._last_sell_ts
        if last_ts <= 0:
            return False, "cooldown_first_swap"

        elapsed = now - last_ts
        if elapsed < cooldown:
            remaining = int(cooldown - elapsed)
            direction = "buy" if is_buying_mnt else "sell"
            return True, f"cooldown_{direction}:{remaining}s_remaining"

        return False, "cooldown_ok"

    # ── Size Cap Helper ───────────────────────────────────

    @staticmethod
    def _compute_capped_ratio(
        state, target_bps: int, max_usdt: float, is_buying_mnt: bool,
    ) -> int | None:
        """Compute an adjusted target ratio that keeps the swap under max_usdt."""
        total = float(state.total_value_usdt)
        if total <= 0:
            return None
        current_mnt_pct = float(state.mnt_weight)
        current_bps = int(current_mnt_pct * 10_000)
        # How much ratio change can max_usdt buy?
        # trade_usdt ≈ |target_bps - current_bps| / 10000 * total_value
        max_delta_bps = int(max_usdt / total * 10_000)
        if is_buying_mnt:
            return min(target_bps, current_bps + max_delta_bps)
        else:
            return max(target_bps, current_bps - max_delta_bps)

    # ── Regime-Aware Target Ratio ─────────────────────────

    def _get_target_ratio(
        self, exit_direction: str, market_context: dict[str, Any] | None,
    ) -> tuple[int, str]:
        """Pick target MNT ratio (bps) based on regime.

        Ratios calibrated by Bayesian optimization on 41d real 1h MNTUSDT candles.
        See tasks/results/02_optimal_params.json.
        """
        context = self._CONTEXT_MAP.get(exit_direction, "neutral")

        # Base ratio from config
        if context == "exit_down":
            base_bps = self.settings.reentry_partial_exit_down_mnt_ratio_bps
        elif context == "exit_up":
            base_bps = self.settings.reentry_partial_exit_up_mnt_ratio_bps
        else:
            return self.settings.reentry_neutral_mnt_ratio_bps, "neutral_base"

        # Regime-aware override
        if not self.settings.reentry_regime_aware_ratio_enabled or not market_context:
            return base_bps, "base_config"

        regime = market_context.get("regime")

        if context == "exit_down":
            if regime == "TRENDING_DOWN":
                return 3_500, "regime_bear_sell"           # 35% MNT — max protection
            if regime == "TRENDING_UP":
                return 6_000, "regime_bull_keep_dip"       # 60% MNT — dip in bull, keep MNT
            if regime == "VOLATILE":
                return 6_500, "regime_volatile_keep"       # 65% MNT
            return 5_000, "regime_ranging_balanced"        # 50% MNT — was 70%, reduced for capital efficiency

        if context == "exit_up":
            if regime == "TRENDING_UP":
                return 5_500, "regime_bull_buy"
            if regime == "TRENDING_DOWN":
                return 2_000, "regime_bear_keep_usdt"
            if regime == "VOLATILE":
                return 9_000, "regime_volatile_buy"
            return 4_500, "regime_ranging_slight_bull"    # 45% MNT — slightly bullish

        return base_bps, "fallback"

    # ── Distribution Shape ────────────────────────────────

    def resolve_distribution_params(
        self, strategy: str, bias_signal: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        if strategy == "narrow":
            return self.settings.get_narrow_distribution_params(), "narrow_base"
        if strategy == "wide":
            return dict(self.settings.get_wide_distribution_params()), "wide_base"
        return None, "unsupported"

    # ── Main Entry Point ─────────────────────────────────

    def apply_inventory_policy(
        self,
        wallet: str,
        exit_direction: str,
        *,
        dry_run: bool,
        market_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Decide and execute rebalance after LP exit.

        1. Check mode (continuation_safe → skip)
        2. Compute RSI → gate check
        3. Pick regime-aware target ratio
        4. Plan swap, check guards (max_swap_pct, min_swap_usdt)
        5. Execute swap if all gates pass
        """
        context = self._CONTEXT_MAP.get(exit_direction, "neutral")
        if exit_direction == "down":
            mode = self.settings.reentry_policy_exit_down
        elif exit_direction == "up":
            mode = self.settings.reentry_policy_exit_up
        else:
            mode = self.settings.reentry_policy_neutral

        # Regime override: in BEAR or RANGING, force continuation_safe.
        # Only rebalance in BULL — backtest showed rebalancing in bear/ranging
        # amplifies IL (buy on grind-down, sell on dead cat bounce).
        regime = (market_context or {}).get("regime")
        if regime in ("TRENDING_DOWN", "RANGING") and mode != "continuation_safe":
            logger.info(f"Re-entry policy: {context} regime={regime} → override to continuation_safe")
            mode = "continuation_safe"

        # continuation_safe: no swap, keep one-sided
        if mode == "continuation_safe":
            logger.info(f"Re-entry policy: {context} mode=continuation_safe → keep one-sided")
            return {"mode": mode, "context": context, "status": "skipped", "reason": "continuation_safe"}

        # Get RSI
        rsi = self._get_rsi()
        rsi_blocked, rsi_reason = self._check_rsi_gate(exit_direction, rsi)

        # Get target ratio
        target_bps, ratio_bucket = self._get_target_ratio(exit_direction, market_context)

        # Build bias_signal dict for telemetry compatibility
        bias_signal = {
            "context": context,
            "rsi_14": rsi,
            "rsi_filter": rsi_reason,
            "alignment": "rsi_filter_blocked" if rsi_blocked else "allowed",
            "direction": "NEUTRAL",
            "effective_confidence": 0.0 if rsi_blocked else 1.0,
        }

        # RSI gate blocks
        if rsi_blocked:
            logger.info(f"Re-entry policy: {context} RSI blocked ({rsi_reason}) → keep one-sided")
            return {
                "mode": mode, "context": context, "status": "skipped",
                "reason": f"rsi_blocked:{rsi_reason}",
                "target_mnt_ratio_bps": target_bps,
                "target_ratio_bucket": ratio_bucket,
                "bias_signal": bias_signal,
            }

        # Plan the swap
        state = self.balance.get_rebalance_state(wallet)
        plan = self.balance.plan_rebalance(
            wallet,
            tolerance_bps=0,
            min_trade_usdt=Decimal(str(self.settings.min_reentry_swap_usdt)),
            target_mnt_ratio_bps=target_bps,
        )

        trade_value = Decimal(plan.trade_value_usdt)
        total_value = state.total_value_usdt
        trade_pct = float(trade_value / total_value) if total_value > 0 else 0.0

        # Nothing to do
        if plan.action == "none":
            reason = plan.details.get("reason", "already_at_target")
            logger.info(f"Re-entry policy: {context} target={target_bps}bps → {reason}")
            return {
                "mode": mode, "context": context, "status": "skipped", "reason": reason,
                "target_mnt_ratio_bps": target_bps, "target_ratio_bucket": ratio_bucket,
                "trade_value_usdt": float(trade_value), "trade_pct": trade_pct,
                "plan": asdict(plan), "bias_signal": bias_signal,
            }

        # Determine swap direction for guards
        is_buying_mnt = target_bps > 5000

        def _skip(reason: str) -> dict[str, Any]:
            logger.info(f"Re-entry policy: {context} {reason} → skip")
            return {
                "mode": mode, "context": context, "status": "skipped", "reason": reason,
                "target_mnt_ratio_bps": target_bps, "target_ratio_bucket": ratio_bucket,
                "trade_value_usdt": float(trade_value), "trade_pct": trade_pct,
                "plan": asdict(plan), "bias_signal": bias_signal,
            }

        # Guard 1: Max swap pct
        max_swap = float(self.settings.max_reentry_swap_pct)
        if trade_pct > max_swap:
            return _skip(f"swap_above_guard:{trade_pct:.1%}>{max_swap:.0%}")

        # Guard 2: VWAP — only buy below 24h avg, sell above
        current_price = (market_context or {}).get("price")
        vwap_blocked, vwap_reason = self._check_vwap_guard(is_buying_mnt, current_price)
        if vwap_blocked:
            return _skip(f"vwap_blocked:{vwap_reason}")

        # Guard 3: Cooldown — 4h between swaps in same direction
        cd_blocked, cd_reason = self._check_cooldown(is_buying_mnt)
        if cd_blocked:
            return _skip(f"cooldown_blocked:{cd_reason}")

        # Guard 4: Size cap — max $X per swap
        max_usdt = self.settings.reentry_max_swap_usdt
        if max_usdt > 0 and float(trade_value) > max_usdt:
            # Re-plan with capped amount instead of skipping entirely
            capped_ratio = self._compute_capped_ratio(
                state, target_bps, max_usdt, is_buying_mnt,
            )
            if capped_ratio is not None and capped_ratio != target_bps:
                logger.info(
                    f"Re-entry policy: swap cap ${max_usdt:.0f} — "
                    f"adjusting target {target_bps}→{capped_ratio}bps"
                )
                plan = self.balance.plan_rebalance(
                    wallet, tolerance_bps=0,
                    min_trade_usdt=Decimal(str(self.settings.min_reentry_swap_usdt)),
                    target_mnt_ratio_bps=capped_ratio,
                )
                trade_value = Decimal(plan.trade_value_usdt)
                trade_pct = float(trade_value / total_value) if total_value > 0 else 0.0
                target_bps = capped_ratio
                ratio_bucket = f"{ratio_bucket}_capped"
            if plan.action == "none":
                return _skip(f"swap_cap_too_small:${max_usdt:.0f}")

        # Execute
        logger.info(
            f"Re-entry policy: {context} target={target_bps}bps ({ratio_bucket}) "
            f"swap=${float(trade_value):.2f} ({trade_pct:.1%}) rsi={rsi_reason} vwap={vwap_reason}"
        )

        results = self.balance.execute_rebalance(wallet, plan, dry_run=dry_run, unwrap_after_buy=False)

        # Update cooldown timestamps
        if is_buying_mnt:
            self._last_buy_ts = _time.time()
        else:
            self._last_sell_ts = _time.time()
        gas = self._gas_cost_mnt(results)
        self.analytics.record_operation(
            action="rebalance", strategy="reentry",
            value_usdt=float(trade_value), gas_mnt=gas,
            details=json.dumps({"target_bps": target_bps, "bucket": ratio_bucket,
                                "rsi": rsi_reason, "regime": market_context.get("regime") if market_context else None}),
        )

        return {
            "mode": mode, "context": context, "status": "executed",
            "reason": plan.action,
            "target_mnt_ratio_bps": target_bps, "target_ratio_bucket": ratio_bucket,
            "trade_value_usdt": float(trade_value), "trade_pct": trade_pct,
            "plan": asdict(plan), "bias_signal": bias_signal,
            "gas_mnt": gas, "result_actions": [r.action for r in results],
        }
