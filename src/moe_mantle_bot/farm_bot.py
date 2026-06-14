"""
Farm Bot — Single-strategy LP management for WMNT/USDT on Merchant Moe (Mantle).

Each cycle: read position → select strategy → execute (hold / exit_and_reenter / enter).
One wallet, one pool, one position at a time.
"""

import asyncio
import argparse
import json
import os
import sys
import time
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Any
from datetime import datetime, timezone, UTC

from .balance_manager import BalanceManager
from .config import Settings
from .core.wallet import load_wallet
from .logging_config import setup_logging, get_logger
from .lp_service import LPService, resolve_pool_token_roles
from .analytics import Analytics
from .execution.executor import SinglePositionIntentExecutor
from .notifications import NotificationService
from .telegram import send_farm_alert, send_error_alert
from .models import LpAllocation
from .orchestration import (
    CycleContext,
    ReentryExecutionCoordinator,
    SinglePositionCyclePlanner,
    SinglePositionCyclePreparer,
    build_cycle_context,
)
from .quant.bias_calculator import BiasCalculator
from .quant.keltner_analyzer import KeltnerAnalyzer
from .quant.wide_range_lp_manager import WideRangeLPManager
from .rpc_client import RpcClient
from .strategies import LegacySinglePositionStrategyProfile, ReentryPolicyService
from .tx_sender import PreviewValidationError, TxSender

logger = get_logger(__name__)


class FarmBot:
    """Single-strategy LP farm bot."""

    _REENTRY_CONTEXT_MAP = {
        "down": "exit_down",
        "up": "exit_up",
        "unknown": "neutral",
    }

    def __init__(self, settings: Settings, strategy_override: str | None = None):
        self.settings = settings
        self.strategy_override = strategy_override  # "narrow", "wide", or None (auto)
        self._last_strategy_state: str | None = None  # Track state changes for notifications
        self._last_exit_time: float = 0.0  # Epoch of last LP exit for cooldown
        self._exit_failures: int = 0  # Consecutive exit_and_reenter failures
        self._max_exit_failures: int = 3  # After N failures, back off and hold
        self._last_entry_time: float = 0.0  # Epoch of last position creation for min-hold
        self._min_hold_seconds: int = 1800  # 30 min minimum hold before out-of-range exit
        self._market_context: dict | None = None  # Latest MTF market state for scale-in
        # MNT accumulation state
        self._accum_mnt: float = 0.0        # accumulated MNT (not deployed)
        self._accum_avg_price: float = 0.0  # weighted avg entry price of accumulated MNT

        # Shared composable services
        self.wallet = load_wallet(settings)
        self.rpc = RpcClient(settings)
        # Resolve the pool's quote/cash token from on-chain tokenX/tokenY so an
        # arbitrary WMNT-paired pool can be managed. No-op for the default pool.
        settings = resolve_pool_token_roles(self.rpc, settings)
        self.settings = settings
        logger.info(
            "Managing pool %s (native=%s, quote=%s)",
            settings.pool_address, settings.wmnt_address, settings.usdt_address,
        )
        tx = TxSender(self.rpc, self.wallet, settings)
        self.balance = BalanceManager(self.rpc, tx, settings)
        self.lp = LPService(self.rpc, tx, self.balance, settings)

        # Startup: reconcile registry with onchain state.
        # Skip removal if a registered position was created within the last 5 minutes
        # (prevents race condition when restarting after a --once cycle).
        try:
            result = self.lp.reconcile(self.wallet.address, dry_run=False)
            unauthorized = result.get("unauthorized", [])
            if unauthorized:
                # Check if any registered position was created very recently
                from datetime import datetime, UTC, timedelta
                reg = self.lp.get_registry(self.wallet.address)
                recent_cutoff = datetime.now(tz=UTC) - timedelta(minutes=5)
                has_recent = False
                for pos in reg.get_narrow_positions() + reg.get_wide_positions():
                    try:
                        created = datetime.fromisoformat(pos.created_at)
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=UTC)
                        if created > recent_cutoff:
                            has_recent = True
                            break
                    except (ValueError, AttributeError):
                        pass
                if has_recent:
                    logger.info(
                        f"Found {len(unauthorized)} unregistered bins but registry has "
                        f"a recently created position — skipping cleanup to avoid race condition"
                    )
                else:
                    logger.info(f"Found {len(unauthorized)} unregistered onchain bins — removing")
                    try:
                        self.lp.remove_position(dry_run=False)
                        logger.info("Removed stale onchain position. Next cycle will create fresh.")
                    except Exception as e:
                        logger.error(f"Failed to remove stale position: {e}")
            else:
                matched = len(result.get("matched", []))
                logger.info(f"LP registry synced: {matched} bins matched")
        except Exception as e:
            logger.error(f"Reconciliation failed during startup: {e}")

        # Strategy components
        self.wide_range_manager = WideRangeLPManager(settings, lp_service=self.lp)
        self.keltner_analyzer = KeltnerAnalyzer()
        self.bias_calculator = BiasCalculator()
        from .quant.mtf_analyzer import MTFAnalyzer
        self.mtf_analyzer = MTFAnalyzer(self.keltner_analyzer.candle_fetcher)
        from .strategies.engine import StrategyEngine
        self.strategy_engine = StrategyEngine(
            wide_confidence_threshold=getattr(settings, "wide_confidence_threshold", 0.5),
            min_top_up_free_value_usdt=getattr(settings, "min_top_up_free_value_usdt", 20.0),
            oor_tolerance_bins=getattr(settings, "oor_tolerance_bins", 15),
            oor_tolerance_cap_bins=getattr(settings, "oor_tolerance_cap_bins", 40),
        )
        self.notifications = NotificationService(settings)
        self.analytics = Analytics(settings.data_dir / "analytics.db")
        self.single_position_executor = SinglePositionIntentExecutor(
            settings=settings,
            balance=self.balance,
            refresh_budget=lambda wallet: self.balance.get_capital_budget(wallet, self.lp),
            apply_effective_gas_reserve_to_allocation=self._apply_effective_gas_reserve_to_allocation,
            prepare_wide_entry_inventory=self._prepare_wide_entry_inventory,
            top_up_expected_fill_is_viable=self._top_up_expected_fill_is_viable,
            create_position_with_retry=self._create_position_with_retry,
        )
        self.reentry_policy = ReentryPolicyService(
            settings=settings,
            balance=self.balance,
            analytics=self.analytics,
            keltner_analyzer=self.keltner_analyzer,
            bias_calculator=self.bias_calculator,
            safe_float=self._safe_float,
            gas_cost_mnt=self._gas_cost_mnt,
            calculate_rsi=self._calculate_rsi,
        )
        self.cycle_preparer = SinglePositionCyclePreparer(
            settings=settings,
            lp=self.lp,
            balance=self.balance,
            analytics=self.analytics,
            keltner_analyzer=self.keltner_analyzer,
            bias_calculator=self.bias_calculator,
            strategy_override=self.strategy_override,
            safe_float=self._safe_float,
            calculate_rsi=self._calculate_rsi,
        )
        self.strategy_profile = LegacySinglePositionStrategyProfile(
            default_target_mnt_ratio_bps=int(self.settings.target_mnt_ratio_bps),
            get_narrow_bin_count=self._get_narrow_bin_count,
            get_wide_params=self._get_wide_params,
            target_pct_for_strategy=self._target_pct_for_strategy,
            resolve_reentry_distribution_params=self.reentry_policy.resolve_distribution_params,
        )
        self.cycle_planner = SinglePositionCyclePlanner(
            select_strategy=self.select_strategy,
            resolve_top_up_strategy=self._resolve_top_up_strategy,
            strategy_profile=self.strategy_profile,
        )
        self.reentry_coordinator = ReentryExecutionCoordinator(
            analytics=self.analytics,
            safe_float=self._safe_float,
        )
        self._warn_legacy_data_artifacts()

        logger.info("Farm bot initialized")

    def _warn_legacy_data_artifacts(self) -> None:
        """Warn when deprecated JSONL farm history artifacts are still present."""
        replacements = {
            "farm_history.jsonl": "data/analytics.db snapshots, data/latest_snapshot.json, and data/farm_bot.log",
            "farm_operations.jsonl": "data/analytics.db operations and data/farm_bot.log",
        }
        for filename, replacement in replacements.items():
            path = self.settings.data_dir / filename
            if path.exists():
                logger.warning(
                    f"Legacy data file detected: {path} is no longer written by the farm bot. "
                    f"Use {replacement} instead, then delete the stale file."
                )

    def _notify(self, message: str, is_error: bool = False) -> None:
        """Send Telegram notification. Errors are sent with error formatting."""
        try:
            if is_error:
                send_error_alert(message, urgent=False)
            else:
                send_farm_alert(message)
        except Exception as e:
            logger.debug(f"Telegram notification failed: {e}")

    def _bin_price_range(self, min_bin: int, max_bin: int, bin_step: int = 5) -> tuple[str, str]:
        """Convert bin range to human-readable USDT price range."""
        from .utils import price_from_bin_id
        lo = float(price_from_bin_id(min_bin, bin_step, 18, 6))
        hi = float(price_from_bin_id(max_bin, bin_step, 18, 6))
        # Use appropriate precision based on price magnitude
        if hi < 0.01:
            return f"${lo:.6f}", f"${hi:.6f}"
        if hi < 1:
            return f"${lo:.4f}", f"${hi:.4f}"
        return f"${lo:.2f}", f"${hi:.2f}"

    def _get_mtf_summary(self) -> str:
        """One-line MTF summary for notifications."""
        try:
            mtf = self.mtf_analyzer.analyze()
            rsi_1h = f"{mtf.tf_1h.rsi_14:.0f}" if mtf.tf_1h and mtf.tf_1h.rsi_14 else "n/a"
            rsi_4h = f"{mtf.tf_4h.rsi_14:.0f}" if mtf.tf_4h and mtf.tf_4h.rsi_14 else "n/a"
            flags = []
            if mtf.overbought:
                flags.append("OB")
            if mtf.oversold:
                flags.append("OS")
            flag_str = f" [{'/'.join(flags)}]" if flags else ""
            return (
                f"{mtf.regime} | {mtf.higher_tf_bias} | "
                f"RSI 1h:{rsi_1h} 4h:{rsi_4h} | "
                f"ATR:{mtf.daily_atr_pct:.1f}%{flag_str}"
            )
        except Exception:
            return "unavailable"

    def _notify_strategy_state_change(self, new_state: str, reason: str) -> None:
        """Send Telegram notification when strategy state changes."""
        if self._last_strategy_state == new_state:
            return
        old = self._last_strategy_state or "startup"
        self._last_strategy_state = new_state

        # Get position price range if available
        position_line = ""
        try:
            pool_state = self.lp.get_pool_state()
            reg = self.lp.get_registry(self.wallet.address)
            for positions in [reg.get_narrow_positions(), reg.get_wide_positions()]:
                if positions:
                    p = positions[0]
                    price_lo, price_hi = self._bin_price_range(p.min_bin, p.max_bin, pool_state.bin_step)
                    in_range = pool_state.active_bin_id >= p.min_bin and pool_state.active_bin_id <= p.max_bin
                    status = "in range" if in_range else "OOR"
                    position_line = f"📍 {p.strategy_type}: {price_lo}–{price_hi} ({p.bin_count} bins, {status})"
                    break
        except Exception:
            pass

        from .notification_formatter import format_strategy_change
        self._notify(format_strategy_change(
            old_state=old, new_state=new_state,
            reason=reason, mtf_summary=self._get_mtf_summary(),
            position_line=position_line,
        ))

    def _format_removal_stats(
        self, recovered_mnt: float, recovered_usdt: float, mnt_price: float,
        initial_mnt: float, initial_usdt: float, initial_value_usdt: float,
    ) -> list[str]:
        """Format removal stats with fee estimate and IL breakdown."""
        recovered_usd = recovered_mnt * mnt_price + recovered_usdt
        hodl_usd = initial_mnt * mnt_price + initial_usdt
        # Net = LP value - HODL (positive = fees > IL, negative = IL > fees)
        net_vs_hodl = recovered_usd - hodl_usd
        # IL = HODL - initial value at deposit (how much price movement cost)
        il_usd = abs(hodl_usd - initial_value_usdt)

        lines = [f"💸 Recovered: {recovered_mnt:.2f} MNT + ${recovered_usdt:.2f} USDT (${recovered_usd:.2f})"]

        if mnt_price > 0:
            # Always show HODL comparison
            lines.append(f"📊 HODL would be: ${hodl_usd:.2f}")
            if net_vs_hodl >= 0:
                lines.append(f"🏆 Fees > IL by ${net_vs_hodl:.2f}")
            else:
                lines.append(f"📉 IL > Fees by ${-net_vs_hodl:.2f}")

        return lines

    @staticmethod
    def _safe_float(value: Any) -> float:
        if value in (None, "", "None"):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _calculate_rsi(closes, period: int = 14) -> float | None:
        if len(closes) < period + 1:
            return None

        delta = closes.diff()
        gains = delta.clip(lower=0)
        losses = -delta.clip(upper=0)
        avg_gain = gains.rolling(window=period, min_periods=period).mean().iloc[-1]
        avg_loss = losses.rolling(window=period, min_periods=period).mean().iloc[-1]

        if avg_gain != avg_gain or avg_loss != avg_loss:
            return None
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        return float(100.0 - (100.0 / (1.0 + rs)))

    def _determine_exit_direction(self, position, pool_state) -> str:
        if not getattr(position, "position_exists", False):
            return "unknown"
        active_bin = getattr(pool_state, "active_bin_id", None)
        min_bin = getattr(position, "min_bin_id", None)
        max_bin = getattr(position, "max_bin_id", None)
        if active_bin is None or min_bin is None or max_bin is None:
            return "unknown"
        if active_bin < min_bin:
            return "down"
        if active_bin > max_bin:
            return "up"
        if active_bin <= min_bin + 1:
            return "down"
        if active_bin >= max_bin - 1:
            return "up"
        return "unknown"

    def _extract_add_liquidity_metrics(self, results: list) -> dict[str, Any]:
        summary = {
            "tx_hash": None,
            "lp_mode": None,
            "requested_mnt": None,
            "requested_usdt": None,
            "used_mnt": None,
            "used_usdt": None,
            "expected_refund_mnt": 0.0,
            "expected_refund_usdt": 0.0,
            "fill_pct_mnt": None,
            "fill_pct_usdt": None,
            "entry_value_usdt": 0.0,
        }

        for result in results:
            if getattr(result, "action", None) != "add_liquidity":
                continue
            summary["tx_hash"] = getattr(result, "tx_hash", None)
            details = getattr(result, "details", {}) or {}
            preflight = details.get("preflight", {})
            liquidity = preflight.get("liquidity_parameters", {})
            dist = details.get("distribution_details") or liquidity.get("distribution_details", {}) or {}
            pool = preflight.get("pool", {}) or {}

            requested_mnt = self._safe_float(details.get("amount_wmnt"))
            requested_usdt = self._safe_float(details.get("amount_usdt"))
            mnt_price = self._safe_float(pool.get("spot_price_mnt_usdt") or pool.get("price_y_per_x"))

            token_x = str(pool.get("token_x", "")).lower()
            token_y = str(pool.get("token_y", "")).lower()
            x_used = self._safe_float(dist.get("x_used"))
            y_used = self._safe_float(dist.get("y_used"))
            x_refund = self._safe_float(dist.get("x_refund"))
            y_refund = self._safe_float(dist.get("y_refund"))

            if token_x == self.settings.wmnt_address.lower():
                mnt_used, usdt_used = x_used, y_used
                mnt_refund, usdt_refund = x_refund, y_refund
            elif token_y == self.settings.wmnt_address.lower():
                mnt_used, usdt_used = y_used, x_used
                mnt_refund, usdt_refund = y_refund, x_refund
            else:
                mnt_used, usdt_used = x_used, y_used
                mnt_refund, usdt_refund = x_refund, y_refund

            summary["lp_mode"] = dist.get("active_mode")
            summary["requested_mnt"] = requested_mnt
            summary["requested_usdt"] = requested_usdt
            summary["used_mnt"] = mnt_used
            summary["used_usdt"] = usdt_used
            summary["expected_refund_mnt"] = mnt_refund
            summary["expected_refund_usdt"] = usdt_refund
            summary["fill_pct_mnt"] = (mnt_used / requested_mnt * 100.0) if requested_mnt > 0 else None
            summary["fill_pct_usdt"] = (usdt_used / requested_usdt * 100.0) if requested_usdt > 0 else None
            summary["entry_value_usdt"] = mnt_used * mnt_price + usdt_used
            break

        return summary

    @staticmethod
    def _gas_cost_mnt(results: list) -> float:
        """Extract total gas cost in MNT from a list of ExecutionResults."""
        total_gas = 0
        for r in results:
            if hasattr(r, 'details') and isinstance(r.details, dict):
                receipt = r.details.get("receipt", {})
                if isinstance(receipt, dict):
                    total_gas += receipt.get("gas_used", 0)
        # Approximate native cost using a ~100 gwei gas price assumption
        return float(total_gas * 100_000_000_000) / 10**18

    def _build_market_state(self, keltner_analysis: dict | None = None):
        """Build MarketState from live data sources. Caches MTF per call."""
        from .strategies.engine import MarketState
        mtf = None
        try:
            mtf = self.mtf_analyzer.analyze()
        except Exception as e:
            logger.debug("MTF analysis unavailable: %s", e)
        return MarketState.from_keltner_and_mtf(keltner_analysis, mtf)

    def _build_wallet_composition(self, wallet: str, budget) -> "WalletComposition":
        from .strategies.engine import WalletComposition
        mnt_value = float(budget.free_mnt * budget.mnt_price_usdt) if budget.mnt_price_usdt else 0
        usdt_value = float(budget.free_usdt)
        total = mnt_value + usdt_value
        return WalletComposition(
            mnt_weight=mnt_value / total if total > 0 else 0,
            free_value_usdt=float(budget.free_value_usdt),
            total_value_usdt=float(budget.total_value_usdt),
        )

    def _build_position_snapshot(self, position, pool_state) -> "PositionSnapshot":
        from .strategies.engine import PositionSnapshot
        exists = getattr(position, "position_exists", False)

        # Detect dust positions: if estimated value is near zero, treat as no position.
        # This prevents dust bins from triggering exit_and_reenter loops.
        # Only check when inventory data is explicitly included (include_inventory=True).
        if exists and getattr(position, "inventory_included", False):
            est_x = getattr(position, "estimated_token_x", None) or 0
            est_y = getattr(position, "estimated_token_y", None) or 0
            mnt_price = getattr(pool_state, "mnt_price_usdt", None) or getattr(pool_state, "price_y_per_x", None)
            if mnt_price:
                deployed_value = float(est_x) * float(mnt_price) + float(est_y)
                dust_threshold = float(self.settings.min_position_size_usdt)
                if deployed_value < dust_threshold:
                    logger.debug(
                        "Position snapshot: dust detected (value=$%.4f < $%.2f), treating as empty",
                        deployed_value, dust_threshold,
                    )
                    exists = False

        return PositionSnapshot(
            exists=exists,
            in_range=getattr(position, "in_range", False) if exists else False,
            bin_count=getattr(position, "bin_count", 0) or 0,
            min_bin_id=getattr(position, "min_bin_id", None),
            max_bin_id=getattr(position, "max_bin_id", None),
            active_bin_id=getattr(pool_state, "active_bin_id", None),
        )

    def _select_entry_strategy_without_position(self, keltner_analysis: dict | None = None) -> str:
        """Delegate to StrategyEngine for entry selection."""
        if self.strategy_override in {"narrow", "wide"}:
            return self.strategy_override

        market = self._build_market_state(keltner_analysis)
        from .strategies.engine import WalletComposition
        # Wallet composition not available here (no budget), use neutral default
        wallet = WalletComposition(mnt_weight=0.5, free_value_usdt=0, total_value_usdt=0)

        decision = self.strategy_engine._select_entry(market, wallet)
        logger.info("Strategy %s: %s", decision.action.upper(), decision.reason)
        return decision.action

    def _resolve_top_up_strategy(
        self,
        wallet: str,
        position,
        budget,
        keltner_analysis: dict | None = None,
    ) -> str | None:
        """Top-ups disabled — WrongAmounts on volatile pairs wastes gas."""
        return None
        if not getattr(position, "position_exists", False) or not getattr(position, "in_range", False):
            return None

        free_value_usdt = Decimal(str(getattr(budget, "free_value_usdt", 0) or 0))
        min_free = Decimal(str(getattr(self.settings, "min_top_up_free_value_usdt", 10.0)))
        if free_value_usdt < min_free:
            return None

        if self.strategy_override in {"narrow", "wide"}:
            return self.strategy_override

        reg = self.lp.get_registry(wallet)
        narrow_positions = reg.get_narrow_positions()
        wide_positions = reg.get_wide_positions()
        if wide_positions and not narrow_positions:
            return "wide"
        if narrow_positions and not wide_positions:
            return "narrow"

        return self._select_entry_strategy_without_position(keltner_analysis)

    def _build_cycle_context(
        self,
        *,
        wallet_address: str,
        timestamp: str,
        dry_run: bool,
        pool_state,
        position,
        budget,
        keltner: dict[str, Any] | None = None,
        selected_strategy: str | None = None,
        top_up_candidate: str | None = None,
        bias_signal: dict[str, Any] | None = None,
        reentry_summary: dict[str, Any] | None = None,
    ) -> CycleContext:
        """Normalize the current cycle state before strategy-specific decisions."""
        return build_cycle_context(
            wallet_address=wallet_address,
            timestamp=timestamp,
            dry_run=dry_run,
            pool_state=pool_state,
            position=position,
            budget=budget,
            keltner=keltner,
            selected_strategy=selected_strategy,
            top_up_candidate=top_up_candidate,
            bias_signal=bias_signal,
            reentry_summary=reentry_summary,
        )

    def _top_up_expected_fill_is_viable(
        self,
        *,
        strategy: str,
        alloc: LpAllocation,
        bin_count: int,
        params: dict[str, Any] | None,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Estimate whether a top-up would actually deploy enough value to be worth sending.

        Uses a lower minimum ($2 default via min_top_up_fill_usdt) than new positions
        since the position already exists and any additional liquidity is beneficial.
        """
        try:
            estimate = self.lp.estimate_position_fill(
                amount_wmnt=alloc.amount_wmnt,
                amount_usdt=alloc.amount_usdt,
                bin_count=bin_count,
                distribution_params=params,
            )
        except Exception as exc:
            logger.debug("Top-up fill estimate unavailable for %s: %s", strategy, exc)
            return True, None

        # Use the lower top-up threshold instead of min_position_size_usdt
        min_fill = Decimal(str(getattr(self.settings, "min_top_up_fill_usdt", 2.0)))
        used_value = Decimal(str(estimate.get("used_value_usdt") or 0))
        if used_value >= min_fill:
            return True, estimate

        logger.info(
            "Top-up skipped before add: strategy=%s mode=%s expected_fill=$%.4f requested=$%.4f min=$%.4f",
            strategy,
            estimate.get("active_mode"),
            float(used_value),
            float(estimate.get("requested_value_usdt") or 0),
            float(min_fill),
        )
        return False, estimate

    def _target_pct_for_strategy(self, strategy: str) -> float:
        if strategy == "wide":
            base = 1.0
        elif strategy == "narrow":
            base = 0.9
        else:
            raise ValueError(f"Unsupported strategy for allocation target: {strategy}")

        # Scale-in based on market conditions.
        # Overbought → enter 1/3 (preserve capital for top-up at better price)
        # Neutral → enter 75% (reserve for top-up opportunity)
        # Oversold → full entry (buy the dip)
        ctx = self._market_context or {}
        if ctx.get("overbought"):
            scale = 0.33
            reason = "overbought"
        elif ctx.get("oversold"):
            scale = 1.0
            reason = "oversold"
        else:
            scale = 0.75
            reason = "neutral"
        scaled = base * scale
        if scale < 1.0:
            logger.info(f"Scale-in: {reason} → target_pct={scaled:.2f} (base={base:.2f} × {scale:.2f})")
        return scaled

    def _effective_gas_reserve_mnt(self, *, strategy: str, bin_count: int) -> Decimal:
        base_reserve = Decimal(str(self.settings.gas_reserve_mnt))
        if not getattr(self.settings, "adaptive_gas_reserve_enabled", False):
            return base_reserve

        recent_avg = Decimal(
            str(
                self.analytics.get_recent_average_gas_mnt(
                    action="add",
                    strategy=strategy,
                    limit=self.settings.adaptive_gas_reserve_lookback,
                )
            )
        )
        if recent_avg <= 0:
            recent_avg = Decimal(str(self.settings.adaptive_gas_reserve_default_tx_mnt))

        dynamic_reserve = (
            recent_avg * Decimal(str(self.settings.adaptive_gas_reserve_multiplier))
            + (Decimal(str(self.settings.adaptive_gas_reserve_bin_buffer_mnt)) * Decimal(bin_count))
        )
        return max(base_reserve, dynamic_reserve)

    def _apply_effective_gas_reserve_to_allocation(
        self,
        alloc: LpAllocation,
        budget,
        *,
        strategy: str,
        bin_count: int,
    ) -> LpAllocation:
        if not alloc.is_viable:
            return alloc

        effective_reserve = self._effective_gas_reserve_mnt(strategy=strategy, bin_count=bin_count)
        base_reserve = Decimal(str(getattr(budget, "gas_reserve_mnt", 0) or 0))
        extra_reserve = max(Decimal(0), effective_reserve - base_reserve)
        if extra_reserve <= 0:
            return alloc

        adjusted_mnt = max(Decimal(0), alloc.amount_wmnt - extra_reserve)
        adjusted_value_usdt = adjusted_mnt * Decimal(str(budget.mnt_price_usdt)) + alloc.amount_usdt
        min_size_usdt = Decimal(str(self.settings.min_position_size_usdt))
        if adjusted_value_usdt < min_size_usdt:
            return LpAllocation(
                Decimal(0),
                Decimal(0),
                False,
                f"adaptive gas reserve leaves position too small: ${float(adjusted_value_usdt):.2f}",
            )

        logger.info(
            "Adaptive gas reserve: strategy=%s bins=%s base=%.2f MNT effective=%.2f MNT "
            "reducing allocation by %.4f MNT",
            strategy,
            bin_count,
            float(base_reserve),
            float(effective_reserve),
            float(extra_reserve),
        )
        return LpAllocation(adjusted_mnt, alloc.amount_usdt, True, "ok")

    def _get_wide_entry_inventory_status(self, wallet: str) -> dict[str, Any]:
        state = self.balance.get_rebalance_state(wallet)
        max_mnt_weight = Decimal(self.settings.wide_entry_max_mnt_weight_bps) / Decimal(10_000)
        min_usdt = Decimal(str(self.settings.wide_entry_min_usdt))
        # Symmetric: also detect when MNT is too LOW (USDT-heavy → can't deploy via addLiquidityNATIVE)
        min_mnt_weight = Decimal(1) - max_mnt_weight  # 1 - 0.85 = 0.15
        too_skewed = (
            state.mnt_weight > max_mnt_weight
            or state.mnt_weight < min_mnt_weight
            or state.usdt < min_usdt
        )

        reasons: list[str] = []
        if state.mnt_weight > max_mnt_weight:
            reasons.append(f"mnt_too_high={float(state.mnt_weight):.3f}>{float(max_mnt_weight):.3f}")
        if state.mnt_weight < min_mnt_weight:
            reasons.append(f"mnt_too_low={float(state.mnt_weight):.3f}<{float(min_mnt_weight):.3f}")
        if state.usdt < min_usdt:
            reasons.append(f"usdt={float(state.usdt):.2f}<min={float(min_usdt):.2f}")

        return {
            "state": state,
            "too_skewed": too_skewed,
            "reason": ",".join(reasons) if reasons else "within_threshold",
        }

    def _prepare_wide_entry_inventory(
        self,
        wallet: str,
        budget,
        *,
        bin_count: int,
        dry_run: bool,
        timestamp: str,
    ) -> tuple[Any, dict[str, Any] | None]:
        # Skip pre-entry rebalance when reentry_skip_rebalance is on
        if self.settings.reentry_skip_rebalance:
            return budget, None
        if not getattr(self.settings, "wide_entry_inventory_gate_enabled", True):
            return budget, None

        status = self._get_wide_entry_inventory_status(wallet)
        if not status["too_skewed"]:
            return budget, None

        state = status["state"]
        logger.info(
            "Wide entry inventory gate: wallet too skewed (%s), mnt_weight=%.3f usdt=%.2f",
            status["reason"],
            float(state.mnt_weight),
            float(state.usdt),
        )

        if not getattr(self.settings, "wide_entry_rebalance_enabled", True):
            return budget, {
                "action": "hold_cash_wait_rebalance",
                "reason": f"wide_inventory_gate:{status['reason']}",
                "timestamp": timestamp,
            }

        plan = self.balance.plan_rebalance(
            wallet,
            tolerance_bps=self.settings.wide_entry_rebalance_tolerance_bps,
            min_trade_usdt=Decimal(str(self.settings.wide_entry_rebalance_min_trade_usdt)),
            target_mnt_ratio_bps=self.settings.wide_entry_rebalance_target_mnt_ratio_bps,
        )
        trade_value_usdt = Decimal(plan.trade_value_usdt)
        trade_pct = (
            float(trade_value_usdt / state.total_value_usdt)
            if state.total_value_usdt > 0
            else 0.0
        )
        if plan.action == "none":
            plan_reason = str(plan.details.get("reason", "none"))
            if plan_reason in {
                "required trade below minimum threshold",
                "already within tolerance band",
            }:
                logger.info(
                    "Wide pre-entry rebalance skipped: %s (trade=$%.2f). Proceeding without swap.",
                    plan_reason,
                    float(trade_value_usdt),
                )
                return budget, None
            return budget, {
                "action": "hold_cash_wait_rebalance",
                "reason": f"wide_preentry_rebalance_none:{plan_reason}",
                "plan": asdict(plan),
                "timestamp": timestamp,
            }
        if trade_pct > self.settings.wide_entry_rebalance_max_swap_pct:
            return budget, {
                "action": "hold_cash_wait_rebalance",
                "reason": (
                    f"wide_preentry_swap_pct_above_guard:"
                    f"{trade_pct:.3f}>{self.settings.wide_entry_rebalance_max_swap_pct:.3f}"
                ),
                "plan": asdict(plan),
                "timestamp": timestamp,
            }

        # VWAP guard: block wide_entry swaps at unfavorable prices
        is_buying = plan.action == "buy_mnt"
        # Check if wallet is so skewed that one-sided entry won't work.
        # If minority token < $5, we MUST swap a small amount regardless of guards.
        # When selling MNT (is_buying=False), minority is USDT; when buying MNT, minority is MNT value
        minority_value = float(state.mnt_value_usdt) if is_buying else float(state.usdt)
        MINIMUM_VIABLE_USDT = 5.0
        guards_overridden = False
        if minority_value < MINIMUM_VIABLE_USDT:
            logger.info(
                "Wide pre-entry: minority token $%.2f < $%.0f — overriding VWAP/cooldown/cap "
                "guards for minimum viable swap",
                minority_value, MINIMUM_VIABLE_USDT,
            )
            guards_overridden = True
            # Cap the swap to just what's needed for viability ($50 worth)
            plan = self.balance.plan_rebalance(
                wallet,
                tolerance_bps=self.settings.wide_entry_rebalance_tolerance_bps,
                min_trade_usdt=Decimal(str(self.settings.wide_entry_rebalance_min_trade_usdt)),
                target_mnt_ratio_bps=self.settings.wide_entry_rebalance_target_mnt_ratio_bps,
            )
            trade_value_usdt = min(Decimal("50"), Decimal(plan.trade_value_usdt))
            # Re-plan with a closer ratio to limit swap size
            current_bps = int(float(state.mnt_weight) * 10000)
            small_step = 500 if not is_buying else -500
            capped_ratio = current_bps + small_step
            capped_ratio = max(1000, min(9000, capped_ratio))
            plan = self.balance.plan_rebalance(
                wallet,
                tolerance_bps=0,
                min_trade_usdt=Decimal("5"),
                target_mnt_ratio_bps=capped_ratio,
            )
            trade_value_usdt = Decimal(plan.trade_value_usdt)
            trade_pct = float(trade_value_usdt / state.total_value_usdt) if state.total_value_usdt > 0 else 0.0
            if plan.action == "none":
                return budget, None

        if not guards_overridden:
            vwap_blocked, vwap_reason = self.reentry_policy._check_vwap_guard(
                is_buying,
                (self._market_context or {}).get("price"),
            )
            if vwap_blocked:
                logger.info("Wide pre-entry rebalance blocked by VWAP guard: %s", vwap_reason)
                return budget, None  # proceed one-sided instead of holding

            # Cooldown guard: shared with reentry_policy
            cd_blocked, cd_reason = self.reentry_policy._check_cooldown(is_buying)
            if cd_blocked:
                logger.info("Wide pre-entry rebalance blocked by cooldown: %s", cd_reason)
                return budget, None  # proceed one-sided

            # Size cap: apply reentry_max_swap_usdt to wide_entry too
            max_usdt = self.settings.reentry_max_swap_usdt
            if max_usdt > 0 and float(trade_value_usdt) > max_usdt:
                logger.info(
                    "Wide pre-entry rebalance capped: $%.0f > $%.0f cap. Proceeding one-sided.",
                    float(trade_value_usdt), max_usdt,
                )
                return budget, None  # proceed one-sided

        if dry_run:
            return budget, {
                "action": "rebalance_before_entry",
                "reason": "wide_inventory_gate_dry_run",
                "plan": asdict(plan),
                "trade_value_usdt": float(trade_value_usdt),
                "trade_pct": trade_pct,
                "timestamp": timestamp,
            }

        logger.info(
            "Wide pre-entry rebalance: target_mnt_ratio=%sbps action=%s trade=$%.2f (%.1f%%) bins=%s",
            self.settings.wide_entry_rebalance_target_mnt_ratio_bps,
            plan.action,
            float(trade_value_usdt),
            trade_pct * 100,
            bin_count,
        )
        results = self.balance.execute_rebalance(
            wallet, plan, dry_run=False, unwrap_after_buy=False,
        )
        # Update cooldown timestamps (shared with reentry_policy)
        import time as _time_mod
        if is_buying:
            self.reentry_policy._last_buy_ts = _time_mod.time()
        else:
            self.reentry_policy._last_sell_ts = _time_mod.time()
        gas_cost = self._gas_cost_mnt(results)
        self.analytics.record_operation(
            action="rebalance",
            strategy="wide_entry",
            value_usdt=float(trade_value_usdt),
            gas_mnt=gas_cost,
            details=json.dumps(
                {
                    "stage": "pre_entry",
                    "plan": asdict(plan),
                    "result_actions": [r.action for r in results],
                    "trigger_reason": status["reason"],
                },
                sort_keys=True,
            ),
        )
        refreshed_budget = self.balance.get_capital_budget(wallet, self.lp)
        refreshed_status = self._get_wide_entry_inventory_status(wallet)
        if refreshed_status["too_skewed"]:
            return refreshed_budget, {
                "action": "hold_cash_wait_rebalance",
                "reason": f"wide_inventory_still_skewed:{refreshed_status['reason']}",
                "plan": asdict(plan),
                "trade_value_usdt": float(trade_value_usdt),
                "trade_pct": trade_pct,
                "timestamp": timestamp,
            }
        return refreshed_budget, None

    def send_status_report(self) -> None:
        """Send a status report with balances, LP positions, and fees to Telegram."""
        try:
            wallet = self.wallet.address
            balances = self.balance.get_wallet_balances(wallet)
            budget = self.balance.get_capital_budget(wallet, self.lp)
            mnt_price = balances.mnt_price_usdt or 0

            # Position info
            reg = self.lp.get_registry(wallet)
            narrow_pos = reg.get_narrow_positions()
            wide_pos = reg.get_wide_positions()

            pool_state = self.lp.get_pool_state()

            # Build position data for formatter
            pos_data = []
            for label, positions in [("🎯 Narrow", narrow_pos), ("📊 Wide", wide_pos)]:
                if positions:
                    p = positions[0]
                    price_lo = price_hi = None
                    try:
                        price_lo, price_hi = self._bin_price_range(p.min_bin, p.max_bin, pool_state.bin_step)
                    except Exception:
                        pass
                    pos_data.append({
                        "label": label, "exists": True,
                        "bin_count": p.bin_count, "min_bin": p.min_bin, "max_bin": p.max_bin,
                        "in_range": pool_state.active_bin_id >= p.min_bin and pool_state.active_bin_id <= p.max_bin,
                        "price_lo": price_lo, "price_hi": price_hi,
                    })
                else:
                    pos_data.append({"label": label, "exists": False})

            roi = None
            try:
                roi = self.analytics.get_roi()
            except Exception:
                pass

            from .notification_formatter import format_status_report
            msg = format_status_report(
                state=self._last_strategy_state or "starting",
                mtf_summary=self._get_mtf_summary(),
                native_mnt=float(balances.native_mnt.normalized),
                wmnt=float(balances.wmnt.normalized),
                usdt=float(balances.usdt.normalized),
                total_value_usdt=float(budget.total_value_usdt),
                mnt_price=float(mnt_price),
                deployed_value_usdt=float(budget.deployed_value_usdt),
                free_value_usdt=float(budget.free_value_usdt),
                positions=pos_data,
                roi=roi,
            )
            self._notify(msg)
            logger.info("Status report sent to Telegram")
        except Exception as e:
            logger.warning(f"Failed to send status report: {e}")

    def select_strategy(
        self,
        position,
        keltner_analysis: dict | None = None,
        pool_state=None,
        budget=None,
    ) -> str:
        """Delegate to StrategyEngine. Budget is passed in (no RPC inside strategy).

        Returns: "hold", "exit_and_reenter", "narrow", or "wide".
        """
        if self.strategy_override in {"narrow", "wide"}:
            if not getattr(position, "position_exists", False):
                return self.strategy_override
            if not getattr(position, "in_range", False):
                return "exit_and_reenter"

        market = self._build_market_state(keltner_analysis)
        # Cache market context for scale-in allocation decisions
        self._market_context = {
            "overbought": getattr(market, "overbought", False),
            "oversold": getattr(market, "oversold", False),
            "regime": getattr(market, "regime", None),
            "daily_atr_pct": getattr(market, "daily_atr_pct", None),
            "price": float(pool_state.mnt_price_usdt) if getattr(pool_state, "mnt_price_usdt", None) else None,
        }
        pos_snap = self._build_position_snapshot(position, pool_state)

        # Budget can be passed in (from cycle preparer) or fetched as fallback
        if budget is None:
            budget = self.balance.get_capital_budget(self.wallet.address, self.lp)
        wallet_comp = self._build_wallet_composition(self.wallet.address, budget)

        # Compute optimal bin count from Keltner + daily ATR for range fitness check
        optimal_bins = None
        if keltner_analysis is not None:
            try:
                daily_atr = market.daily_atr_pct if market.regime != "UNKNOWN" else None
                regime_str = market.regime if market.regime != "UNKNOWN" else None
                optimal = self.wide_range_manager.calculate_wide_range_params(
                    keltner_analysis, daily_atr_pct=daily_atr, pool_stats=None,
                    regime=regime_str,
                )
                optimal_bins = optimal.get("bin_count")
            except Exception:
                pass

        # Get existing position strategy from registry
        existing_strategy = None
        try:
            reg = self.lp.get_registry(self.wallet.address)
            if reg.get_wide_positions():
                existing_strategy = "wide"
            elif reg.get_narrow_positions():
                existing_strategy = "narrow"
        except Exception:
            pass

        decision = self.strategy_engine.select_strategy(
            market, pos_snap, wallet_comp,
            optimal_bin_count=optimal_bins,
            existing_position_strategy=existing_strategy,
            pool_stats=None,
        )
        logger.info("StrategyEngine: %s — %s", decision.action, decision.reason)

        # Map engine actions to legacy return values
        if decision.action == "top_up":
            return "hold"  # Cycle planner handles top-up via _resolve_top_up_strategy
        return decision.action

    @staticmethod
    def _should_exit_early(position, pool_state) -> bool:
        """Exit only when truly out of range. No early edge exit — reduces churn.
        Legacy compatibility — StrategyEngine._check_position_fitness does the same.
        """
        if not position.position_exists:
            return False
        return not position.in_range

    # ── MNT Accumulation ──────────────────────────────────

    def _check_accumulation_release(self, pool_state) -> None:
        """Check if accumulated MNT should be released (sold for USDT)."""
        if self._accum_mnt <= 0:
            return

        price = float(pool_state.mnt_price_usdt)
        accum_val = self._accum_mnt * price
        rsi = self._get_current_rsi()
        profit_pct = ((price / self._accum_avg_price) - 1) * 100 if self._accum_avg_price > 0 else 0

        # Full release: RSI overbought + significant profit
        if rsi >= self.settings.release_rsi_full and profit_pct >= self.settings.release_profit_threshold:
            logger.info(
                f"MNT accumulation FULL RELEASE: {self._accum_mnt:.2f} MNT (${accum_val:.2f}) "
                f"rsi={rsi:.0f} profit={profit_pct:+.1f}%"
            )
            self._execute_accumulation_release(1.0)
            return

        # Partial release: RSI elevated + some profit
        if rsi >= self.settings.release_rsi_partial and profit_pct > 0:
            pct = self.settings.release_partial_pct
            logger.info(
                f"MNT accumulation PARTIAL RELEASE: {self._accum_mnt * pct:.2f} MNT "
                f"({pct:.0%}) rsi={rsi:.0f} profit={profit_pct:+.1f}%"
            )
            self._execute_accumulation_release(pct)
            return

        # Emergency cap
        total_val = float(self.balance.get_wallet_balances(self.wallet.address).usdt.normalized) + accum_val
        if total_val > 0 and accum_val / total_val > self.settings.accum_max_portfolio_pct:
            logger.info(f"MNT accumulation EMERGENCY CAP: {accum_val/total_val*100:.0f}% > {self.settings.accum_max_portfolio_pct*100:.0f}%")
            self._execute_accumulation_release(0.5)

    def _execute_accumulation_release(self, pct: float) -> None:
        """Sell pct of accumulated MNT. In practice this is already in wallet as native MNT."""
        sell_mnt = self._accum_mnt * pct
        self._accum_mnt -= sell_mnt
        if self._accum_mnt < 0.01:
            self._accum_mnt = 0.0
            self._accum_avg_price = 0.0
        # The MNT is already in the wallet — on next rebalance/LP entry it will
        # be swapped to USDT as part of normal neutral_rebalance. Log for tracking.
        self.analytics.record_operation(
            action="accumulation_release",
            amount_mnt=sell_mnt,
            details=json.dumps({"pct": pct, "remaining": self._accum_mnt}),
        )

    def _accumulate_mnt_on_exit(self, recovered_mnt: float, price: float, rsi: float) -> float:
        """Conditionally hold back MNT from LP deployment. Returns MNT to hold back."""
        if not self.settings.mnt_accumulation_enabled:
            return 0.0

        regime = (self._market_context or {}).get("regime", "RANGING")
        holdback = 0.0

        if rsi <= self.settings.accum_rsi_deep:
            holdback = recovered_mnt * self.settings.accum_pct_deep
        elif rsi <= self.settings.accum_rsi_low and regime in ("TRENDING_DOWN", "RANGING"):
            holdback = recovered_mnt * self.settings.accum_pct_normal

        if holdback > 0:
            if self._accum_mnt > 0:
                self._accum_avg_price = (
                    self._accum_avg_price * self._accum_mnt + price * holdback
                ) / (self._accum_mnt + holdback)
            else:
                self._accum_avg_price = price
            self._accum_mnt += holdback
            logger.info(
                f"MNT accumulation: held back {holdback:.2f} MNT (${holdback*price:.2f}) "
                f"rsi={rsi:.0f} regime={regime} total_accum={self._accum_mnt:.2f}"
            )
            self.analytics.record_operation(
                action="accumulation_holdback",
                amount_mnt=holdback,
                details=json.dumps({"rsi": round(rsi, 1), "regime": regime,
                                    "total_accum": round(self._accum_mnt, 2)}),
            )
        return holdback

    def _get_current_rsi(self) -> float:
        """Get current 5m RSI-14."""
        try:
            candles = self.keltner_analyzer.candle_fetcher.get_candles("MNTUSDT", "5m", 200)
            closes = candles["close"].astype(float)
            rsi = self._calculate_rsi(closes, period=14)
            return rsi if rsi is not None else 50.0
        except Exception:
            return 50.0

    async def _exit_and_reenter(
        self, wallet: str, pool_state, position, dry_run: bool,
    ) -> dict[str, Any]:
        """Atomic position rotation: exit → rebalance → select → enter."""
        result: dict[str, Any] = {"action": "exit_and_reenter"}
        reentry_event_id: str | None = None
        exit_direction = self._determine_exit_direction(position, pool_state)
        reentry_policy_result: dict[str, Any] | None = None
        bal_before = None
        previous_strategy = None
        try:
            reg = self.lp.get_registry(wallet)
            if reg.get_wide_positions():
                previous_strategy = "wide"
            elif reg.get_narrow_positions():
                previous_strategy = "narrow"
        except Exception:
            previous_strategy = None

        # Step 1: Remove current position
        logger.info(
            f"Step 1/4 remove_position: direction={exit_direction} "
            f"range=[{getattr(position, 'min_bin_id', None)}-{getattr(position, 'max_bin_id', None)}] "
            f"active_bin={getattr(pool_state, 'active_bin_id', None)}"
        )
        try:
            if not dry_run:
                bal_before = self.balance.get_wallet_balances(wallet)
            self.lp.remove_position(pool_state=pool_state, dry_run=dry_run)
            result["remove"] = "ok"
            if not dry_run:
                import time as _time
                self._last_exit_time = _time.time()
            if not dry_run and bal_before is not None:
                bal_after = self.balance.get_wallet_balances(wallet)
                mnt_price = float(bal_after.mnt_price_usdt or 0)
                recovered_mnt = float(
                    bal_after.native_mnt.normalized + bal_after.wmnt.normalized
                    - bal_before.native_mnt.normalized - bal_before.wmnt.normalized
                )
                recovered_usdt = float(bal_after.usdt.normalized - bal_before.usdt.normalized)
                recovered_value = recovered_mnt * mnt_price + recovered_usdt
                # Estimate fees: compare recovered value vs initial value at current price.
                # initial_value adjusted to current price = initial_mnt * current_price + initial_usdt
                initial_value_at_current = 0.0
                fees_earned = 0.0
                try:
                    reg = self.lp.get_registry(wallet)
                    active_bin_ids = [b.bin_id for b in position.active_bins] if position.active_bins else []
                    affected_positions = reg.find_positions_by_bins(active_bin_ids) if active_bin_ids else []
                    for pos in affected_positions:
                        initial_value_at_current += pos.initial_mnt * mnt_price + pos.initial_usdt
                    if initial_value_at_current > 0:
                        fees_earned = recovered_value - initial_value_at_current
                        logger.info(
                            f"Fee estimate: recovered=${recovered_value:.2f} "
                            f"initial_at_price=${initial_value_at_current:.2f} "
                            f"fees=${fees_earned:.2f}"
                        )
                except Exception as e:
                    logger.debug(f"Fee estimation failed: {e}")
                hodl_value = initial_value_at_current if initial_value_at_current > 0 else recovered_value
                fees_vs_hodl = recovered_value - hodl_value
                reentry_event_id = self.analytics.start_reentry_event(
                    previous_strategy=previous_strategy,
                    exit_direction=exit_direction,
                    exit_active_bin=getattr(pool_state, "active_bin_id", None),
                    exit_min_bin=getattr(position, "min_bin_id", None),
                    exit_max_bin=getattr(position, "max_bin_id", None),
                    exit_mnt_price=mnt_price,
                    recovered_mnt=recovered_mnt,
                    recovered_usdt=recovered_usdt,
                    recovered_value_usdt=recovered_value,
                    hodl_value_usdt=hodl_value,
                    fees_vs_hodl_usdt=fees_vs_hodl,
                    notes=f"direction={exit_direction}",
                )
                logger.info(
                    f"Re-entry state: id={reentry_event_id} direction={exit_direction} "
                    f"recovered={recovered_mnt:.2f} MNT + ${recovered_usdt:.2f} USDT"
                )
                # Telegram notification for LP removal
                try:
                    price_lo = price_hi = ""
                    try:
                        price_lo, price_hi = self._bin_price_range(
                            getattr(position, 'min_bin_id', 0),
                            getattr(position, 'max_bin_id', 0),
                            getattr(pool_state, 'bin_step', 5),
                        )
                    except Exception:
                        pass
                    self._notify(
                        f"🔄 <b>LP removed</b> (exit {exit_direction})\n"
                        f"💰 Recovered: {recovered_mnt:.2f} MNT + ${recovered_usdt:.2f} USDT"
                        f" (${recovered_value:.2f})\n"
                        f"📍 Was: {price_lo}–{price_hi} ({previous_strategy or 'unknown'})\n"
                        f"🌐 {self._get_mtf_summary()}"
                    )
                except Exception:
                    pass
        except Exception as e:
            self._log_error(e, "Position removal failed")
            error_result: dict[str, Any] = {
                "action": "error",
                "error": f"remove failed: {e}",
            }
            if isinstance(e, PreviewValidationError):
                preview = dict(getattr(e, "preview", {}) or {})
                if preview:
                    error_result["preview_status"] = preview.get("status")
                    error_result["preview"] = preview
            else:
                stage = getattr(e, "stage", None)
                context = dict(getattr(e, "context", {}) or {})
                if stage:
                    error_result["error_stage"] = stage
                if context:
                    error_result["error_context"] = context
            return error_result

        if dry_run:
            result["dry_run"] = True
            return result

        # Step 1.5: Unwrap WMNT → native MNT for addLiquidityNATIVE.
        # removeLiquidity returns WMNT (no removeLiquidityNATIVE exists),
        # but addLiquidityNATIVE requires native MNT as msg.value.
        try:
            from decimal import Decimal as _D
            wmnt_bal = self.balance.get_erc20_balance(
                wallet, self.settings.wmnt_address,
            )
            if wmnt_bal.normalized > _D("1"):
                logger.info(f"Step 1.5 unwrap: {wmnt_bal.normalized:.2f} WMNT → native MNT")
                self.balance.unwrap_wmnt(wmnt_bal.normalized, dry_run=False)
                result["unwrap_wmnt"] = float(wmnt_bal.normalized)
        except Exception as e:
            logger.warning(f"WMNT unwrap failed (non-fatal, will retry): {e}")

        # Step 1.7: Smart MNT accumulation holdback (before rebalance)
        if self.settings.mnt_accumulation_enabled and exit_direction == "down":
            try:
                # Cost-basis tracking requires a real price. If the pool price is
                # unavailable, skip the holdback rather than accumulate against a
                # fabricated price (repo rule: no hardcoded market data).
                mnt_price = (
                    float(pool_state.mnt_price_usdt)
                    if pool_state and pool_state.mnt_price_usdt is not None
                    else None
                )
                if mnt_price is None:
                    logger.debug(
                        "Accumulation holdback skipped: no live MNT price available"
                    )
                else:
                    rsi = self._get_current_rsi()
                    native_bal = self.balance.get_native_balance(wallet)
                    available_mnt = float(native_bal.normalized)
                    holdback = self._accumulate_mnt_on_exit(available_mnt, mnt_price, rsi)
                    if holdback > 0:
                        result["accumulation_holdback"] = holdback
            except Exception as e:
                logger.debug(f"Accumulation holdback failed (non-fatal): {e}")

        # Step 2: Inventory policy — skip rebalance swap to avoid crystallizing IL.
        # Enter one-sided with whatever inventory was recovered.
        if self.settings.reentry_skip_rebalance:
            logger.info(
                "Step 2/4 reentry_policy: SKIP rebalance (reentry_skip_rebalance=True) "
                f"— entering one-sided with recovered inventory"
            )
            reentry_policy_result = {
                "mode": "continuation_safe",
                "context": f"exit_{exit_direction}",
                "status": "skipped",
                "reason": "reentry_skip_rebalance",
            }
            result["reentry_policy"] = reentry_policy_result
        else:
            try:
                logger.info("Step 2/4 reentry_policy: evaluating recovered inventory policy")
                reentry_policy_result = self.reentry_policy.apply_inventory_policy(
                    wallet, exit_direction, dry_run=False,
                    market_context=self._market_context,
                )
                result["reentry_policy"] = reentry_policy_result
                policy_status = reentry_policy_result.get('status')
                policy_mode = reentry_policy_result.get('mode')
                policy_reason = reentry_policy_result.get('reason')
                logger.info(
                    f"Step 2/4 reentry_policy: status={policy_status} "
                    f"mode={policy_mode} reason={policy_reason}"
                )
                # Notify on rebalance swaps
                if policy_status == "executed":
                    trade_val = reentry_policy_result.get("trade_value_usdt", 0)
                    target_bps = reentry_policy_result.get("target_mnt_ratio_bps", "?")
                    self._notify(
                        f"⚖️ <b>Re-entry rebalance</b>\n"
                        f"📝 {policy_mode} → {policy_reason}\n"
                        f"💱 Trade: ${trade_val:.2f} | Target: {target_bps} bps MNT\n"
                        f"⛽ Gas: {reentry_policy_result.get('gas_mnt', 0):.4f} MNT"
                    )
                if reentry_policy_result.get("status") == "skip_reentry":
                    logger.info("Step 2/4 reentry_policy: ensemble decided to skip re-entry")
                    return self.reentry_coordinator.close_exit_only(
                        result,
                        reason="ensemble_skip_reentry",
                        reentry_event_id=reentry_event_id,
                        reentry_policy_result=reentry_policy_result,
                        selected_strategy="hold",
                    )
            except Exception as e:
                logger.error(f"Re-entry inventory policy failed: {e}")
                result["reentry_policy"] = {
                    "status": "error",
                    "reason": str(e),
                }

        # Step 3: Fresh market analysis & strategy selection (pure decision)
        try:
            pool_state = self.lp.get_pool_state()
            position = self.lp.get_position(wallet, pool_state=pool_state, include_inventory=True)
            keltner = None
            try:
                analysis = self.keltner_analyzer.analyze_channel_conditions()
                keltner = analysis.to_dict() if analysis else None
            except Exception:
                pass
            logger.info(
                f"Step 3/4 refreshed_state: exists={position.position_exists} "
                f"in_range={position.in_range} bins={position.bin_count} "
                f"range=[{position.min_bin_id}-{position.max_bin_id}] "
                f"active_bin={pool_state.active_bin_id}"
            )

            budget = self.balance.get_capital_budget(wallet, self.lp)
            strategy = self.select_strategy(position, keltner, pool_state=pool_state, budget=budget)
            # After exit, always prefer wide — narrow with bin_count=None fails,
            # and narrow ranges churn too fast in volatile conditions.
            if strategy == "narrow":
                logger.info(f"Step 3/4 strategy_after_exit: {strategy} → overriding to wide (post-exit safety)")
                strategy = "wide"
            else:
                logger.info(f"Step 3/4 strategy_after_exit: {strategy}")
            if strategy == "hold":
                logger.info("No favorable entry after exit — staying in cash")
                return self.reentry_coordinator.close_exit_only(
                    result,
                    reason="No favorable entry after exit",
                    reentry_event_id=reentry_event_id,
                    reentry_policy_result=reentry_policy_result,
                    selected_strategy="hold",
                )

            # Step 4: Execute re-entry (blockchain)
            budget = self.balance.get_capital_budget(wallet, self.lp)
            reentry_ctx = self._build_cycle_context(
                wallet_address=wallet,
                timestamp="",
                dry_run=False,
                pool_state=pool_state,
                position=position,
                budget=budget,
                keltner=keltner,
                selected_strategy=strategy,
                bias_signal=reentry_policy_result.get("bias_signal") if reentry_policy_result else None,
                reentry_summary=reentry_policy_result,
            )
            intent = self.strategy_profile.build_reentry_intent(
                reentry_ctx,
                strategy=strategy,
                reentry_policy_result=reentry_policy_result,
            )
            logger.info(
                f"Step 4/4 create_position: strategy={intent.strategy_id} "
                f"bin_count={intent.range_plan.bin_count if intent.range_plan else None} "
                f"shape_bucket={intent.shape_plan.bucket if intent.shape_plan else None} "
                f"shape={intent.shape_plan.distribution_params.get('distribution_shape') if intent.shape_plan and intent.shape_plan.distribution_params else 'global_default'} "
                f"slope_dir={intent.shape_plan.distribution_params.get('slope_direction') if intent.shape_plan and intent.shape_plan.distribution_params else 'n/a'}"
            )

            if intent.action == "hold":
                return self.reentry_coordinator.close_exit_only(
                    result,
                    reason="unsupported_strategy",
                    reentry_event_id=reentry_event_id,
                    reentry_policy_result=reentry_policy_result,
                )

            create_result = self.single_position_executor.execute(reentry_ctx, intent)
            return self.reentry_coordinator.finalize_create_result(
                result=result,
                strategy=strategy,
                intent=intent,
                create_result=create_result,
                reentry_event_id=reentry_event_id,
                reentry_policy_result=reentry_policy_result,
            )

        except Exception as e:
            self._log_error(e, "Re-entry failed after successful exit")
            return self.reentry_coordinator.finalize_exception(
                result,
                error=e,
                reentry_event_id=reentry_event_id,
                reentry_policy_result=reentry_policy_result,
            )

    # Pool bin_step=5 → each bin = 0.05% price
    _BIN_PRICE_PCT = 0.05

    def _get_narrow_bin_count(self, keltner: dict | None = None) -> int | None:
        """Compute narrow bin count to cover ~half the Keltner width (6-30 bins).

        Returns None if volatility is too high for narrow (width > 8%).
        Narrow targets ~50% of the Keltner channel — enough for 1h oscillation.
        """
        if keltner is None:
            return self.settings.bin_count
        bounds = keltner.get("bounds", {})
        width_pct = bounds.get("width_pct") or keltner.get("width_pct") or 3.0
        if width_pct > 8.0:
            logger.info(f"Keltner width {width_pct:.1f}% > 8% — too volatile for narrow")
            return None
        # Target 50% of Keltner width (covers typical 1h range)
        target_pct = width_pct * 0.5
        bin_count = max(6, min(30, int(target_pct / self._BIN_PRICE_PCT)))
        return bin_count

    def _get_wide_params(self, keltner: dict | None = None) -> dict[str, Any]:
        """Get wide-range bin count and distribution from Keltner + daily ATR."""
        daily_atr = None
        try:
            mtf = self.mtf_analyzer.analyze()
            daily_atr = mtf.daily_atr_pct
        except Exception:
            pass
        return self.wide_range_manager.calculate_wide_range_params(keltner, daily_atr_pct=daily_atr)

    # ── Error decoding ────────────────────────────────────

    # Known Liquidity Book router error selectors
    _LB_ERRORS = {
        "0x9931a6ae": "LBRouter__WrongAmounts (MNT/USDT ratio doesn't match active bin)",
        "0x8a0d377b": "LBRouter__InsufficientAmountOut (slippage exceeded)",
        "0xbf9ec641": "LBRouter__MaxAmountExceeded",
        "0x1f2a2005": "LBRouter__DeadlineExceeded",
        "0x4e487b71": "Panic (arithmetic overflow/underflow)",
    }

    def _log_error(self, e: Exception, prefix: str) -> None:
        """Log error with decoded Liquidity Book details."""
        error_msg = str(e)
        logger.error(f"{prefix}: {type(e).__name__}: {error_msg}")

        # Annotate known gas/balance errors
        if "gas fee greater than reserve" in error_msg.lower():
            logger.error(f"  Cause: Position too large — not enough MNT left for gas after msg.value. Will reduce size.")
        if getattr(e, "stage", "") == "native_balance_precheck":
            logger.error(
                "  Native balance precheck: wallet_native=%s MNT required=%s MNT "
                "shortfall=%s MNT tx_value=%s MNT gas_needed=%s MNT",
                getattr(e, "context", {}).get("native_balance_mnt", "?"),
                getattr(e, "context", {}).get("required_native_mnt", "?"),
                getattr(e, "context", {}).get("shortfall_mnt", "?"),
                getattr(e, "context", {}).get("tx_value_mnt", "?"),
                getattr(e, "context", {}).get("gas_needed_mnt", "?"),
            )

        # Decode preview revert data
        if hasattr(e, 'preview') and e.preview:
            if e.preview.get("status") == "native_gas_headroom_too_low":
                if e.preview.get("gas_needed_mnt") is not None:
                    logger.error(
                        "  Native gas headroom: wallet_native=%s MNT tx_value=%s MNT "
                        "gas_needed=%s MNT total_needed=%s MNT shortfall=%s MNT phase=%s",
                        e.preview.get("native_balance_mnt", "?"),
                        e.preview.get("native_needed_mnt", "?"),
                        e.preview.get("gas_needed_mnt", "?"),
                        e.preview.get("total_needed_mnt", "?"),
                        e.preview.get("shortfall_mnt", "?"),
                        e.preview.get("phase", "?"),
                    )
                else:
                    logger.error(
                        "  Native gas headroom: wallet_native=%s MNT lp_msg_value=%s MNT "
                        "reserve=%s MNT total_needed=%s MNT shortfall=%s MNT",
                        e.preview.get("native_balance_mnt", "?"),
                        e.preview.get("native_needed_mnt", "?"),
                        e.preview.get("gas_reserve_mnt", "?"),
                        e.preview.get("total_needed_mnt", "?"),
                        e.preview.get("shortfall_mnt", "?"),
                    )
            error_str = str(e.preview.get('error', ''))
            decoded = self._decode_revert(error_str)
            if decoded:
                logger.error(f"  Revert reason: {decoded}")
            else:
                logger.error(f"  Preview: {e.preview}")

        # Log preflight context
        if hasattr(e, 'context') and e.context:
            preflight = e.context.get('preflight', {})
            if preflight:
                for key in ('wallet_mnt_native', 'wallet_wmnt', 'wallet_usdt',
                            'amount_wmnt', 'amount_usdt', 'active_bin_id',
                            'min_bin_id', 'max_bin_id', 'bin_count'):
                    if key in preflight:
                        logger.error(f"  {key}: {preflight[key]}")

        if hasattr(e, 'action'):
            logger.error(f"  Action: {e.action}")

    def _decode_revert(self, error_str: str) -> str | None:
        """Decode known Liquidity Book error selectors from revert data."""
        for selector, name in self._LB_ERRORS.items():
            if selector in error_str:
                return name
        return None

    # ── Position creation with retry ──────────────────────

    def _create_position_with_retry(
        self, *, strategy: str, alloc, bin_count: int,
        params: dict, dry_run: bool, timestamp: str,
        max_attempts: int | None = None, base_delay: float = 3.0,
    ) -> dict[str, Any]:
        """Create LP position with retry — one attempt per RPC endpoint."""
        from .constants import MANTLE_RPC_ENDPOINTS
        if max_attempts is None:
            max_attempts = len(MANTLE_RPC_ENDPOINTS)
        last_error: Exception | None = None

        try:
            estimate = self.lp.estimate_position_fill(
                amount_wmnt=alloc.amount_wmnt,
                amount_usdt=alloc.amount_usdt,
                bin_count=bin_count,
                distribution_params=params,
            )
            if not estimate.get("meets_min_fill", True):
                logger.warning(
                    "Skipping %s entry before add: mode=%s expected_fill=$%.4f requested=$%.4f min=$%.4f",
                    strategy,
                    estimate.get("active_mode"),
                    float(estimate.get("used_value_usdt") or 0),
                    float(estimate.get("requested_value_usdt") or 0),
                    float(estimate.get("min_position_size_usdt") or 0),
                )
                return {
                    "action": f"skip_{strategy}",
                    "strategy": strategy,
                    "reason": "expected_fill_below_minimum",
                    "preview_status": "insufficient_expected_fill",
                    "lp_mode": estimate.get("active_mode"),
                    "expected_fill_value_usdt": float(estimate.get("used_value_usdt") or 0),
                    "requested_value_usdt": float(estimate.get("requested_value_usdt") or 0),
                    "timestamp": timestamp,
                }
        except Exception as exc:
            logger.debug("Pre-add fill estimate unavailable for %s: %s", strategy, exc)

        # Pre-entry wallet prep: rebalance if too skewed + unwrap WMNT.
        # Prevents MNT concentration risk (e.g., 92% MNT after exits) and ensures
        # native MNT is available for addLiquidityNATIVE.
        if not dry_run:
            from decimal import Decimal as _D
            wallet = self.wallet.address

            # Step A: Rebalance if wallet MNT ratio exceeds 75%
            try:
                mnt_price = self.balance._get_mnt_price()
                if mnt_price and mnt_price > 0:
                    native_bal = self.balance.get_native_balance(wallet)
                    wmnt_bal = self.balance.get_erc20_balance(wallet, self.settings.wmnt_address)
                    usdt_bal = self.balance.get_erc20_balance(wallet, self.settings.usdt_address)
                    total_mnt = native_bal.normalized + wmnt_bal.normalized
                    mnt_usd = total_mnt * mnt_price
                    total_usd = mnt_usd + usdt_bal.normalized
                    if total_usd > 0:
                        mnt_pct = float(mnt_usd / total_usd)
                        max_mnt_ratio = 0.75  # Trigger rebalance above 75% MNT
                        target_ratio_bps = self.settings.target_mnt_ratio_bps  # Default 5000 = 50%
                        if mnt_pct > max_mnt_ratio:
                            target_pct = target_ratio_bps / 10_000
                            excess_usd = mnt_usd - total_usd * _D(str(target_pct))
                            swap_mnt = excess_usd / mnt_price
                            # Cap at available WMNT (swap uses ERC20 path)
                            swap_mnt = min(swap_mnt, wmnt_bal.normalized - _D("10"))
                            if swap_mnt > _D("100"):
                                logger.info(
                                    f"Pre-entry rebalance: MNT at {mnt_pct:.0%} (>{max_mnt_ratio:.0%}), "
                                    f"swapping {float(swap_mnt):.0f} MNT → USDT to reach {target_pct:.0%}"
                                )
                                self.balance.swap_exact_in(
                                    token_in=self.settings.wmnt_address,
                                    token_out=self.settings.usdt_address,
                                    amount_in=swap_mnt,
                                    dry_run=False,
                                )
            except Exception as e:
                logger.warning(f"Pre-entry rebalance check failed (non-fatal): {e}")

            # Step B: Unwrap WMNT → native MNT for addLiquidityNATIVE
            try:
                wmnt_bal = self.balance.get_erc20_balance(wallet, self.settings.wmnt_address)
                if wmnt_bal.normalized > _D("1"):
                    logger.info(f"Pre-entry unwrap: {wmnt_bal.normalized:.2f} WMNT → native MNT")
                    self.balance.unwrap_wmnt(wmnt_bal.normalized, dry_run=False)
            except Exception as e:
                logger.warning(f"Pre-entry WMNT unwrap failed (non-fatal): {e}")

        for attempt in range(1, max_attempts + 1):
            try:
                if attempt > 1:
                    logger.info(f"Retry {attempt}/{max_attempts}: creating {strategy} position")

                logger.info(f"Creating {strategy} position: {float(alloc.amount_wmnt):.2f} MNT + "
                            f"{float(alloc.amount_usdt):.2f} USDT ({bin_count} bins)")
                results = self.lp.create_position(
                    strategy_type=strategy,
                    amount_wmnt=alloc.amount_wmnt,
                    amount_usdt=alloc.amount_usdt,
                    bin_count=bin_count,
                    distribution_params=params,
                    dry_run=dry_run,
                )
                self.rpc._failed_endpoints.clear()
                gas_cost = self._gas_cost_mnt(results)
                add_metrics = self._extract_add_liquidity_metrics(results)
                used_mnt = add_metrics.get("used_mnt")
                used_usdt = add_metrics.get("used_usdt")
                requested_mnt = float(alloc.amount_wmnt)
                requested_usdt = float(alloc.amount_usdt)
                if used_mnt is not None and used_usdt is not None:
                    amount_line = f"💰 Used {used_mnt:.2f} MNT + {used_usdt:.2f} USDT"
                    if abs(used_mnt - requested_mnt) > 0.01 or abs(used_usdt - requested_usdt) > 0.01:
                        amount_line += (
                            f"\n📝 Requested {requested_mnt:.2f} MNT + {requested_usdt:.2f} USDT"
                        )
                else:
                    amount_line = (
                        f"💰 {requested_mnt:.2f} MNT + {requested_usdt:.2f} USDT"
                    )
                # Price range for the position
                range_line = ""
                try:
                    pool_st = self.lp.get_pool_state()
                    active_bin = pool_st.active_bin_id
                    delta_ids = self.lp._lp_range_delta_ids(bin_count)
                    pos_min = active_bin + delta_ids[0]
                    pos_max = active_bin + delta_ids[-1]
                    price_lo, price_hi = self._bin_price_range(pos_min, pos_max, pool_st.bin_step)
                    range_line = f"💲 Range: {price_lo} — {price_hi}"
                except Exception:
                    pass
                from .notification_formatter import format_lp_created
                self._notify(format_lp_created(
                    strategy=strategy, amount_line=amount_line,
                    bin_count=bin_count, mode=add_metrics.get("lp_mode", "mixed"),
                    range_line=range_line, mtf_summary=self._get_mtf_summary(),
                    gas_cost=gas_cost,
                ))
                self.analytics.record_operation(
                    action="add", strategy=strategy, bin_count=bin_count,
                    amount_mnt=used_mnt if used_mnt is not None else requested_mnt,
                    amount_usdt=used_usdt if used_usdt is not None else requested_usdt,
                    gas_mnt=gas_cost,
                    details=json.dumps(add_metrics, sort_keys=True),
                )
                logger.info(
                    f"{strategy} entry telemetry: mode={add_metrics.get('lp_mode')} "
                    f"used={add_metrics.get('used_mnt', 0):.4f} MNT + "
                    f"${add_metrics.get('used_usdt', 0):.4f} USDT "
                    f"refunds={add_metrics.get('expected_refund_mnt', 0):.4f} MNT + "
                    f"${add_metrics.get('expected_refund_usdt', 0):.4f} USDT"
                )
                # Record entry time for min-hold check
                import time as _time
                self._last_entry_time = _time.time()
                return {
                    "action": f"enter_{strategy}",
                    "strategy": strategy,
                    "bin_count": bin_count,
                    "timestamp": timestamp,
                    **add_metrics,
                }

            except Exception as e:
                last_error = e
                self._log_error(e, f"Attempt {attempt}/{max_attempts} failed")
                deterministic_stop = False
                if isinstance(e, PreviewValidationError):
                    status = str((getattr(e, "preview", {}) or {}).get("status", ""))
                    deterministic_stop = status in {
                        "native_gas_headroom_too_low",
                        "insufficient_expected_fill",
                    }
                # On-chain reverts with same params are deterministic — stop after 2
                from .tx_sender import TransactionExecutionError
                if isinstance(e, TransactionExecutionError) and attempt >= 2:
                    logger.error(
                        "Stopping retries: on-chain revert is deterministic "
                        "(same params will produce same result)"
                    )
                    deterministic_stop = True
                    status = "onchain_revert_deterministic"
                if deterministic_stop:
                    logger.error("Stopping retries for deterministic add_liquidity safety rejection")
                    if status == "insufficient_expected_fill":
                        logger.warning(
                            "Skipping %s entry because expected LP fill is below minimum size",
                            strategy,
                        )
                        return {
                            "action": f"skip_{strategy}",
                            "strategy": strategy,
                            "reason": str(e),
                            "preview_status": status,
                            "timestamp": timestamp,
                        }
                    break

                if attempt < max_attempts:
                    # Rotate RPC on retryable errors or empty reverts (node issue)
                    retryable = getattr(e, 'retryable', False)
                    empty_revert = (
                        hasattr(e, 'preview') and isinstance(e.preview, dict)
                        and "'0x'" in str(e.preview.get('error', ''))
                    )
                    if retryable or empty_revert:
                        try:
                            self.rpc.reconnect()
                            logger.info(f"Rotated RPC to {self.rpc.active_rpc_url}"
                                        f"{' (empty revert — node issue)' if empty_revert else ''}")
                        except Exception:
                            pass

                    delay = base_delay * attempt
                    logger.info(f"Retrying in {delay:.0f}s...")
                    time.sleep(delay)

        logger.error(f"All {max_attempts} attempts failed")
        from .notification_formatter import format_position_failed
        self._notify(
            format_position_failed(strategy, max_attempts, str(last_error)),
            is_error=True,
        )
        return {"action": "error", "error": str(last_error), "attempts": max_attempts,
                "timestamp": timestamp}

    async def execute_cycle(self, dry_run: bool = True) -> dict[str, Any]:
        """Execute a single-position farming cycle."""
        cycle_start = datetime.now(timezone.utc)
        wallet_addr = self.wallet.address
        logger.info(f"Starting cycle {'(DRY RUN)' if dry_run else '(LIVE)'}")

        try:
            # Ensure native MNT stays above minimum before computing budget
            if not dry_run:
                try:
                    guard_results = self.balance.ensure_mnt_min_balance(
                        wallet_addr, dry_run=dry_run,
                    )
                    if guard_results:
                        logger.info(
                            "MNT min balance guard executed %d operations",
                            len(guard_results),
                        )
                except Exception as e:
                    logger.warning("MNT min balance guard failed: %s", e)

            prepared = self.cycle_preparer.prepare(wallet_addr, dry_run=dry_run)
            pool_state = prepared.pool_state
            position = prepared.position
            budget = prepared.budget
            keltner = prepared.keltner

            planned_cycle = self.cycle_planner.plan(
                wallet_address=wallet_addr,
                timestamp=cycle_start.isoformat(),
                dry_run=dry_run,
                pool_state=pool_state,
                position=position,
                budget=budget,
                keltner=keltner,
            )
            cycle_context = planned_cycle.cycle_context
            intent = planned_cycle.intent
            logger.info(
                f"Strategy intent: profile={intent.profile_id} action={intent.action} "
                f"strategy={intent.strategy_id} reason={intent.reason}"
            )

            # Notify on strategy state changes
            state_key = f"{intent.action}:{intent.strategy_id or 'cash'}"
            if not dry_run:
                self._notify_strategy_state_change(state_key, intent.reason or "")

            if intent.action == "hold":
                # Check MNT accumulation release (runs every cycle)
                if not dry_run and self.settings.mnt_accumulation_enabled and self._accum_mnt > 0:
                    self._check_accumulation_release(pool_state)
                return {"action": "hold", "timestamp": cycle_start.isoformat()}
            if planned_cycle.top_up_candidate is not None and intent.strategy_id is not None:
                logger.info(
                    f"In-range position has deployable free capital ${float(budget.free_value_usdt):.2f} "
                    f"-> deploying additional {intent.strategy_id} capital on top"
                )
            logger.info(
                f"Strategy selected: {intent.strategy_id or planned_cycle.selected_strategy}"
            )

            if intent.action == "exit_and_reenter":
                # Minimum hold time: don't exit a position too soon after entry.
                # Nighttime (00-08 UTC): 1h hold — lower liquidity, more bounce-back.
                # Daytime: 30min hold — prevents rapid churn loops.
                import time as _time
                hour_utc = datetime.now(tz=UTC).hour
                effective_min_hold = 3600 if hour_utc < 8 else self._min_hold_seconds
                age = _time.time() - self._last_entry_time
                if self._last_entry_time > 0 and age < effective_min_hold:
                    remaining = int(effective_min_hold - age)
                    logger.info(
                        f"Min-hold: position is {int(age)}s old (<{effective_min_hold}s, "
                        f"{'night' if hour_utc < 8 else 'day'} mode). "
                        f"Holding for {remaining}s more before allowing exit_and_reenter."
                    )
                    return {"action": "hold", "reason": f"min_hold_{remaining}s",
                            "position_age": int(age),
                            "timestamp": cycle_start.isoformat()}

                # Circuit breaker: after N consecutive failures, hold and alert
                if self._exit_failures >= self._max_exit_failures:
                    logger.error(
                        f"Exit-and-reenter circuit breaker: {self._exit_failures} consecutive "
                        f"failures. Holding position to avoid wasting gas. "
                        f"Manual intervention required (moe lp remove)."
                    )
                    self._notify(
                        f"🚨 <b>Exit circuit breaker tripped</b>\n"
                        f"Failed {self._exit_failures}x to remove position. "
                        f"Holding to avoid gas waste.\n"
                        f"Run <code>moe lp remove</code> manually."
                    )
                    return {"action": "hold", "reason": "exit_failure_circuit_breaker",
                            "failures": self._exit_failures,
                            "timestamp": cycle_start.isoformat()}

                # Cooldown: don't re-enter too quickly after last exit
                import time as _time
                cooldown = self.settings.reentry_cooldown_seconds
                elapsed = _time.time() - self._last_exit_time
                if self._last_exit_time > 0 and elapsed < cooldown:
                    remaining = int(cooldown - elapsed)
                    logger.info(
                        f"Reentry cooldown: {remaining}s remaining "
                        f"(cooldown={cooldown}s, elapsed={int(elapsed)}s)"
                    )
                    return {"action": "hold", "reason": f"reentry_cooldown_{remaining}s",
                            "timestamp": cycle_start.isoformat()}

                result = await self._exit_and_reenter(wallet_addr, pool_state, position, dry_run)
                if result.get("action") == "error":
                    self._exit_failures += 1
                    logger.warning(
                        f"Exit-and-reenter failed ({self._exit_failures}/{self._max_exit_failures}): "
                        f"{result.get('error', 'unknown')}"
                    )
                else:
                    self._exit_failures = 0  # Reset on success
                result["timestamp"] = cycle_start.isoformat()
                return result

            return self.single_position_executor.execute(cycle_context, intent)

        except Exception as e:
            self._log_error(e, "Cycle failed")
            return {"action": "error", "error": str(e),
                    "timestamp": cycle_start.isoformat()}


# Keep backward-compatible alias
EnhancedFarmBotV3 = FarmBot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merchant Moe (Mantle) Farm Bot")
    parser.add_argument('--strategy', choices=['narrow', 'wide', 'auto'], default='auto',
                        help='Strategy: narrow, wide, or auto (default)')
    parser.add_argument('--pool', metavar='ADDRESS', default=None,
                        help='LB pair pool address to manage (overrides POOL_ADDRESS). '
                             'Tokens are auto-discovered; must be a WMNT-paired pool.')
    parser.add_argument('--once', action='store_true', help='Run a single cycle and exit')
    parser.add_argument('--dry-run', action='store_true', default=True, help='Dry-run mode (default)')
    parser.add_argument('--live', action='store_true', help='Live mode (overrides dry-run)')
    parser.add_argument('--poll-interval-seconds', type=int, default=300, help='Polling interval (seconds)')
    parser.add_argument('--json', action='store_true', help='JSON output')
    return parser


def cli_main():
    """CLI entry point for farm bot."""
    parser = build_parser()
    args = parser.parse_args()

    try:
        settings = Settings.from_env()
        if args.pool:
            settings = replace(settings, pool_address=args.pool)
    except Exception as e:
        setup_logging()
        logger.error(f"Failed to load settings: {e}")
        sys.exit(1)

    debug_mode = settings.debug or os.getenv("MOE_DEBUG", "").lower() in ("true", "1", "yes", "on")
    log_file = None
    if os.getenv("MOE_LOG_FILE"):
        log_file = Path(os.getenv("MOE_LOG_FILE"))
    elif not args.json:
        log_file = settings.data_dir / "farm_bot.log"
    setup_logging(debug_mode=debug_mode, json_output=args.json, log_file=log_file)

    dry_run = not args.live
    strategy_override = None if args.strategy == 'auto' else args.strategy

    try:
        bot = FarmBot(settings, strategy_override=strategy_override)
    except Exception as e:
        logger.error(f"Failed to initialize bot: {e}")
        sys.exit(1)

    if strategy_override:
        logger.info(f"Strategy forced: {strategy_override}")

    # Send status report on startup
    bot.send_status_report()

    async def run_cycle():
        try:
            result = await bot.execute_cycle(dry_run=dry_run)
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                logger.info(f"Cycle result: {result.get('action', 'unknown')}")
            # Send status report only after LP deploy/remove (not on hold cycles)
            action = result.get("action", "")
            if action not in ("hold", "error") and not dry_run:
                bot.send_status_report()
        except Exception as e:
            logger.error(f"Cycle failed: {e}")
            if args.json:
                print(json.dumps({"error": str(e)}, default=str))

    if args.once:
        asyncio.run(run_cycle())
    else:
        async def continuous():
            logger.info(f"Starting continuous farming (interval: {args.poll_interval_seconds}s)")
            while True:
                try:
                    await run_cycle()
                    await asyncio.sleep(args.poll_interval_seconds)
                except KeyboardInterrupt:
                    logger.info("Shutting down")
                    break
                except Exception as e:
                    logger.error(f"Cycle error: {e}, retrying in {args.poll_interval_seconds}s...")
                    await asyncio.sleep(args.poll_interval_seconds)

        asyncio.run(continuous())


if __name__ == '__main__':
    cli_main()
