"""
StrategyEngine — Pure-logic strategy selection, decoupled from blockchain.

Input:  MarketState + PositionSnapshot + WalletComposition + config
Output: StrategyDecision (pure data — no side effects)

This module NEVER calls RPC, executes transactions, or reads on-chain state.
All inputs must be pre-fetched and passed as data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketState:
    """Pre-fetched market snapshot for strategy decisions. No RPC calls."""
    # Keltner
    keltner_confidence: float
    keltner_is_ranging: bool
    keltner_width_pct: float
    # MTF
    regime: str  # TRENDING_UP, TRENDING_DOWN, RANGING, VOLATILE, UNKNOWN
    regime_confidence: float
    higher_tf_bias: str  # BULL, BEAR, NEUTRAL
    overbought: bool
    oversold: bool
    daily_atr_pct: float
    # Per-TF RSI
    rsi_1h: float | None = None
    rsi_4h: float | None = None

    @classmethod
    def from_keltner_and_mtf(
        cls,
        keltner: dict[str, Any] | None,
        mtf: Any | None,  # MTFAnalysis
    ) -> MarketState:
        k_conf = keltner.get("confidence", 0) if keltner else 0
        k_ranging = keltner.get("is_ranging", False) if keltner else False
        k_width = 0.0
        if keltner:
            bounds = keltner.get("bounds") or {}
            k_width = bounds.get("width_pct", keltner.get("width_pct", 0)) or 0

        if mtf is not None:
            return cls(
                keltner_confidence=k_conf,
                keltner_is_ranging=k_ranging,
                keltner_width_pct=float(k_width),
                regime=mtf.regime,
                regime_confidence=mtf.regime_confidence,
                higher_tf_bias=mtf.higher_tf_bias,
                overbought=mtf.overbought,
                oversold=mtf.oversold,
                daily_atr_pct=mtf.daily_atr_pct or 0,
                rsi_1h=mtf.tf_1h.rsi_14 if mtf.tf_1h else None,
                rsi_4h=mtf.tf_4h.rsi_14 if mtf.tf_4h else None,
            )
        return cls(
            keltner_confidence=k_conf,
            keltner_is_ranging=k_ranging,
            keltner_width_pct=float(k_width),
            regime="UNKNOWN",
            regime_confidence=0,
            higher_tf_bias="NEUTRAL",
            overbought=False,
            oversold=False,
            daily_atr_pct=0,
        )


@dataclass(frozen=True)
class PositionSnapshot:
    """Pre-fetched position state. No RPC calls."""
    exists: bool
    in_range: bool
    bin_count: int
    min_bin_id: int | None
    max_bin_id: int | None
    active_bin_id: int | None
    deployed_value_usdt: float = 0


@dataclass(frozen=True)
class WalletComposition:
    """Pre-fetched wallet balance ratios. No RPC calls."""
    mnt_weight: float  # 0.0-1.0 (fraction of total value in MNT)
    free_value_usdt: float
    total_value_usdt: float


@dataclass(frozen=True)
class StrategyDecision:
    """Pure data output from strategy engine. No side effects."""
    action: str  # "narrow", "wide", "hold", "exit_and_reenter"
    reason: str
    confidence: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


class StrategyEngine:
    """Pure-logic strategy selection. No blockchain calls.

    All state is passed in as data (MarketState, PositionSnapshot, WalletComposition).
    Output is a StrategyDecision — the caller (FarmBot) decides how to execute it.
    """

    # Position range fitness thresholds
    RANGE_TOO_WIDE = 3.0   # Only exit if position is 3x wider than optimal (was 2.0)
    RANGE_TOO_NARROW = 0.25  # Only exit if position is 4x narrower than optimal (was 0.5)
    OOR_TOLERANCE_BINS = 15  # Default; overridden by adaptive tolerance when keltner available
    OOR_FORCE_EXIT_BINS = 100  # Force exit if drift exceeds this (~5% off, no bounce expected)
    EDGE_MARGIN_BINS = -3  # Negative margin: only trigger when active bin is 3+ bins outside range (reduces churn vs OOR handler)

    def __init__(
        self,
        *,
        wide_confidence_threshold: float = 0.5,
        min_top_up_free_value_usdt: float = 20.0,
        oor_tolerance_bins: int = 15,
        oor_tolerance_cap_bins: int = 40,
    ):
        self.wide_confidence_threshold = wide_confidence_threshold
        self.min_top_up_free_value_usdt = min_top_up_free_value_usdt
        # Out-of-range tolerance floor/cap (bins) for the adaptive OOR hold.
        # Higher = more passive (fewer value-destroying re-centers at extremes).
        # NOTE: tolerance is in BINS; price-% ≈ bins × bin_step/100, so for a
        # binStep-15 pool 30 bins ≈ 4.5%, for binStep-100 ≈ 30%. Tune per pool.
        self.oor_tolerance_bins = oor_tolerance_bins
        self.oor_tolerance_cap_bins = oor_tolerance_cap_bins

    def select_strategy(
        self,
        market: MarketState,
        position: PositionSnapshot,
        wallet: WalletComposition,
        *,
        optimal_bin_count: int | None = None,
        existing_position_strategy: str | None = None,
        pool_stats: dict | None = None,
    ) -> StrategyDecision:
        """Main entry: decide what to do this cycle.

        Returns a StrategyDecision. The caller handles execution.
        """
        # ── Active position: hold, top-up, or exit ──
        if position.exists and position.in_range:
            exit_decision = self._check_position_fitness(
                position, market, optimal_bin_count,
            )
            if exit_decision is not None:
                return exit_decision

            # Top-ups disabled until addLiquidity ERC20 path is working.
            # The addLiquidityNATIVE path can't do top-ups reliably (headroom scaling).
            # The addLiquidity ERC20 path needs debugging (on-chain reverts).
            return StrategyDecision(action="hold", reason="in_range", confidence=1.0)

        if position.exists and not position.in_range:
            # Compute drift (how far active bin is from the range)
            drift = 0
            if position.active_bin_id and position.min_bin_id and position.max_bin_id:
                if position.active_bin_id < position.min_bin_id:
                    drift = position.min_bin_id - position.active_bin_id
                elif position.active_bin_id > position.max_bin_id:
                    drift = position.active_bin_id - position.max_bin_id

            # Adaptive OOR tolerance: scale with volatility.
            # Calm market (keltner 1%) → floor at 15 bins (was 5 — too tight, caused churn).
            # Volatile (keltner 4%) → wide tolerance (32 bins). More likely to bounce.
            oor_tol = self.oor_tolerance_bins
            if market.keltner_width_pct and market.keltner_width_pct > 0:
                oor_tol = max(self.oor_tolerance_bins,
                              min(self.oor_tolerance_cap_bins,
                                  int(market.keltner_width_pct / 0.05 * 0.3)))

            if drift <= oor_tol:
                return StrategyDecision(
                    action="hold",
                    reason=f"oor_tolerance (drift={drift}<={oor_tol})",
                    confidence=1.0,
                )

            # Oversold hold: only for small drift (< 30 bins) where bounce is likely.
            # At 30+ bins drift, fee opportunity cost exceeds bounce value.
            # Backtest: re-enter wins by $31 over 30d. Breakeven at 1.5h OOR.
            if market.oversold and drift <= 30:
                return StrategyDecision(
                    action="hold",
                    reason=f"oversold_hold (drift={drift}<=30, oversold=True, await_bounce)",
                    confidence=1.0,
                )

            # Extreme drift: force exit regardless of market conditions.
            # A position that's >100 bins away (>5% off) won't bounce back quickly.
            if drift > self.OOR_FORCE_EXIT_BINS:
                return StrategyDecision(
                    action="exit_and_reenter",
                    reason=f"out_of_range_extreme (drift={drift}>{self.OOR_FORCE_EXIT_BINS})",
                    confidence=1.0,
                )

            # Always exit and re-enter when drift > tolerance.
            # Dormant_oor removed: fee breakeven is 1.5h, holding idle costs $0.12/h.
            # The regime-aware rebalance policy handles bear/bull appropriately on re-entry.
            return StrategyDecision(
                action="exit_and_reenter",
                reason=f"out_of_range (drift={drift})",
                confidence=1.0,
            )

        # ── No position: select entry strategy ──
        return self._select_entry(market, wallet, pool_stats=pool_stats)

    def _check_position_fitness(
        self,
        position: PositionSnapshot,
        market: MarketState,
        optimal_bin_count: int | None,
    ) -> StrategyDecision | None:
        """Check if in-range position should be exited early."""
        # Edge check
        if (
            position.active_bin_id is not None
            and position.min_bin_id is not None
            and position.max_bin_id is not None
        ):
            at_edge = (
                position.active_bin_id <= position.min_bin_id + self.EDGE_MARGIN_BINS
                or position.active_bin_id >= position.max_bin_id - self.EDGE_MARGIN_BINS
            )
            if at_edge:
                return StrategyDecision(
                    action="exit_and_reenter",
                    reason="at_position_edge",
                    details={"active_bin": position.active_bin_id},
                )

        # Range fitness vs Keltner optimal — only resize if ratio is extreme AND
        # position has poor headroom. Don't destroy a healthy in-range position
        # just because Keltner says "wider would be better".
        if optimal_bin_count and optimal_bin_count > 0 and position.bin_count > 0:
            ratio = position.bin_count / optimal_bin_count
            # Check headroom: how far is active bin from edges?
            headroom_pct = 1.0  # default: assume good headroom
            if position.active_bin_id and position.min_bin_id and position.max_bin_id:
                total_range = position.max_bin_id - position.min_bin_id
                if total_range > 0:
                    from_edge = min(
                        position.active_bin_id - position.min_bin_id,
                        position.max_bin_id - position.active_bin_id,
                    )
                    headroom_pct = from_edge / total_range

            if ratio > self.RANGE_TOO_WIDE:
                return StrategyDecision(
                    action="exit_and_reenter",
                    reason=f"range_too_wide (ratio={ratio:.1f}x)",
                    details={"bin_count": position.bin_count, "optimal": optimal_bin_count},
                )
            if ratio < self.RANGE_TOO_NARROW and headroom_pct < 0.15:
                # Only resize narrow if we're also near the edge (< 15% headroom)
                return StrategyDecision(
                    action="exit_and_reenter",
                    reason=f"range_too_narrow (ratio={ratio:.1f}x headroom={headroom_pct:.0%})",
                    details={"bin_count": position.bin_count, "optimal": optimal_bin_count},
                )

        return None

    def _select_entry(
        self,
        market: MarketState,
        wallet: WalletComposition,
        pool_stats: dict | None = None,
    ) -> StrategyDecision:
        """Select narrow/wide/hold for a fresh entry. Pure logic."""

        # ── Gate 0: Fee profitability check ──
        # Don't enter LP if projected fee income < gas + IL cost.
        if pool_stats and isinstance(pool_stats, dict) and wallet.total_value_usdt > 0:
            pool_liq = float(pool_stats.get("liquidity_usd") or 0)
            pool_fees = float(pool_stats.get("fees_usd_24h") or 0)
            if pool_liq > 0 and pool_fees > 0:
                our_share = wallet.total_value_usdt * 0.80 / pool_liq  # 80% budget cap
                projected_daily_fee = our_share * pool_fees
                # Estimate daily cost: ~2 exits/day × $0.09 gas + IL fraction
                estimated_daily_cost = 2 * 0.09 + wallet.total_value_usdt * 0.80 * (market.daily_atr_pct or 8) / 100 * 0.02
                if projected_daily_fee < estimated_daily_cost * 0.5:
                    return StrategyDecision(
                        action="hold",
                        reason=f"unprofitable (fee=${projected_daily_fee:.2f}/d < cost=${estimated_daily_cost:.2f}/d)",
                        details={"projected_fee": projected_daily_fee, "estimated_cost": estimated_daily_cost,
                                 "pool_fees_24h": pool_fees, "pool_liquidity": pool_liq, "our_share": our_share},
                    )

        # ── Gate 1: Overbought/Oversold hold ──
        # Use 1h RSI only (not 4h) — 4h RSI stays extreme for days in trends,
        # blocking entry indefinitely. 1h recovers in hours.
        rsi_1h = market.rsi_1h or 50
        if rsi_1h > 90 and market.regime == "TRENDING_UP":
            return StrategyDecision(
                action="hold",
                reason=f"trending_up_overbought (1h_rsi={rsi_1h:.0f})",
                details={"regime": market.regime, "overbought": True},
            )
        if rsi_1h < 20 and market.regime == "TRENDING_DOWN":
            return StrategyDecision(
                action="hold",
                reason=f"trending_down_oversold (1h_rsi={rsi_1h:.0f})",
                details={"regime": market.regime, "oversold": True},
            )

        # ── Gate 2: Volatile regime → always wide ──
        if market.regime == "VOLATILE" or market.daily_atr_pct > 12:
            return StrategyDecision(
                action="wide",
                reason=f"volatile_regime (atr={market.daily_atr_pct:.1f}%)",
                confidence=0.8,
            )

        # ── Gate 3: High volatility → prefer wide ──
        # MNT daily ATR is typically 7-12%. Narrow (7 bins, 1% range) survives
        # 20min on average at this vol — backtest shows net negative.
        # Wide at ATR ≥ 6% prevents narrow from firing in normal MNT conditions.
        if market.daily_atr_pct >= 6:
            return StrategyDecision(
                action="wide",
                reason=f"high_volatility (atr={market.daily_atr_pct:.1f}%)",
                confidence=0.7,
            )

        # ── Gate 4: Ranging → wide if Keltner supports ──
        if market.regime == "RANGING":
            boosted_conf = min(1.0, market.keltner_confidence + 0.15)
            if boosted_conf > self.wide_confidence_threshold and market.keltner_is_ranging:
                return StrategyDecision(
                    action="wide",
                    reason=f"ranging_market (keltner_conf={market.keltner_confidence:.2f}→{boosted_conf:.2f})",
                    confidence=boosted_conf,
                )

        # ── Gate 5: Trending, not extreme → narrow ──
        if market.regime in ("TRENDING_UP", "TRENDING_DOWN"):
            return StrategyDecision(
                action="narrow",
                reason=f"trending_{market.regime.split('_')[-1].lower()} (atr={market.daily_atr_pct:.1f}%)",
                confidence=0.6,
            )

        # ── Default: narrow ──
        return StrategyDecision(
            action="narrow",
            reason=f"default (regime={market.regime} keltner_conf={market.keltner_confidence:.2f})",
            confidence=0.4,
        )
