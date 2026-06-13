from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable

from ..logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PreparedCycleState:
    """Normalized state loaded before single-position cycle planning."""

    pool_state: Any
    position: Any
    budget: Any
    keltner: dict[str, Any] | None


class SinglePositionCyclePreparer:
    """Load, normalize, and log single-position cycle state."""

    def __init__(
        self,
        *,
        settings,
        lp,
        balance,
        analytics,
        keltner_analyzer,
        bias_calculator,
        strategy_override: str | None,
        safe_float: Callable[[Any], float],
        calculate_rsi: Callable[..., float | None],
    ) -> None:
        self.settings = settings
        self.lp = lp
        self.balance = balance
        self.analytics = analytics
        self.keltner_analyzer = keltner_analyzer
        self.bias_calculator = bias_calculator
        self.strategy_override = strategy_override
        self._safe_float = safe_float
        self._calculate_rsi = calculate_rsi

    def prepare(self, wallet_address: str, *, dry_run: bool) -> PreparedCycleState:
        pool_state = self.lp.get_pool_state()
        position = self.lp.get_position(wallet_address, pool_state=pool_state)
        budget = self.balance.get_capital_budget(wallet_address, self.lp)
        position = self.normalize_position_for_strategy(position, budget)
        logger.info(
            f"Capital: total=${float(budget.total_value_usdt):.2f} "
            f"deployed=${float(budget.deployed_value_usdt):.2f} "
            f"free=${float(budget.free_value_usdt):.2f}"
        )
        logger.info(
            f"Position state: exists={position.position_exists} in_range={position.in_range} "
            f"bins={position.bin_count} range=[{position.min_bin_id}-{position.max_bin_id}] "
            f"active_bin={pool_state.active_bin_id}"
        )

        if not dry_run:
            self.analytics.finalize_pending_reentries(
                current_total_value_usdt=float(budget.total_value_usdt),
                current_mnt_price=float(budget.mnt_price_usdt),
                current_in_range=position.in_range,
            )
        self.record_analytics_snapshot(wallet_address, pool_state=pool_state, budget=budget)

        keltner = self.analyze_keltner()
        self.log_market_indicators(keltner=keltner)
        return PreparedCycleState(
            pool_state=pool_state,
            position=position,
            budget=budget,
            keltner=keltner,
        )

    def analyze_keltner(self) -> dict[str, Any] | None:
        try:
            analysis = self.keltner_analyzer.analyze_channel_conditions()
            return analysis.to_dict() if analysis else None
        except Exception as e:
            logger.debug(f"Keltner analysis unavailable: {e}")
            return None

    def dust_position_threshold_usdt(self) -> Decimal:
        min_size = Decimal(str(self.settings.min_position_size_usdt))
        return max(Decimal("0.10"), min(Decimal("1.00"), min_size * Decimal("0.10")))

    def normalize_position_for_strategy(self, position, budget):
        """Treat dust or sub-min residual LP as absent when free capital can deploy a real position."""
        if not getattr(position, "position_exists", False):
            return position

        deployed_value_usdt = Decimal(str(getattr(budget, "deployed_value_usdt", 0) or 0))
        free_value_usdt = Decimal(str(getattr(budget, "free_value_usdt", 0) or 0))
        dust_threshold = self.dust_position_threshold_usdt()
        min_size_usdt = Decimal(str(self.settings.min_position_size_usdt))

        reason = None
        threshold = dust_threshold
        if deployed_value_usdt <= dust_threshold:
            reason = "dust"
        elif deployed_value_usdt < min_size_usdt and free_value_usdt >= min_size_usdt:
            reason = "small_residual"
            threshold = min_size_usdt

        if reason is None:
            return position

        logger.warning(
            f"{reason.replace('_', ' ').title()} LP position detected: "
            f"range=[{getattr(position, 'min_bin_id', None)}-{getattr(position, 'max_bin_id', None)}] "
            f"bins={getattr(position, 'bin_count', 0)} "
            f"deployed_value=${float(deployed_value_usdt):.4f} <= threshold=${float(threshold):.4f} "
            f"with free_value=${float(free_value_usdt):.4f}. "
            "Treating as no active position for strategy selection."
        )
        return SimpleNamespace(
            position_exists=False,
            in_range=False,
            bin_count=0,
            min_bin_id=None,
            max_bin_id=None,
        )

    def build_market_indicator_snapshot(
        self, candles, keltner: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        closes = candles["close"].astype(float)
        close_price = float(closes.iloc[-1])
        sma_20 = float(closes.rolling(window=20, min_periods=20).mean().iloc[-1]) if len(closes) >= 20 else None
        sma_50 = float(closes.rolling(window=50, min_periods=50).mean().iloc[-1]) if len(closes) >= 50 else None
        ema_20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1]) if len(closes) >= 20 else None
        ema_50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1]) if len(closes) >= 50 else None
        rsi_14 = self._calculate_rsi(closes, period=14)

        bias = self.bias_calculator.get_combined_bias(
            closes.to_numpy(dtype=float),
            imbalance=0.0,
            short_return=0.0,
            intensity=0.0,
        )

        snapshot = {
            "price": close_price,
            "rsi_14": rsi_14,
            "sma_20": sma_20,
            "sma_50": sma_50,
            "ema_20": ema_20,
            "ema_50": ema_50,
            "bias_direction": bias["direction"],
            "bias_confidence": float(bias["confidence"]),
            "bias_score": float(bias["score"]),
            "orderflow_status": "unavailable_no_live_stream",
        }

        if keltner is not None:
            bounds = keltner.get("bounds", {}) or {}
            snapshot["keltner_width_pct"] = self._safe_float(
                bounds.get("width_pct", keltner.get("width_pct"))
            )
            snapshot["keltner_confidence"] = self._safe_float(keltner.get("confidence"))
            snapshot["keltner_is_ranging"] = bool(keltner.get("is_ranging", False))

        return snapshot

    def log_market_indicators(self, keltner: dict[str, Any] | None = None) -> None:
        try:
            candles = self.keltner_analyzer.candle_fetcher.get_candles(
                symbol="MNTUSDT",
                interval="5m",
                limit=200,
            )
            indicators = self.build_market_indicator_snapshot(candles, keltner=keltner)
            logger.info(
                "Market indicators: "
                f"price={self._fmt_metric(indicators['price'])} "
                f"rsi14={self._fmt_metric(indicators['rsi_14'], precision=2)} "
                f"sma20={self._fmt_metric(indicators['sma_20'])} "
                f"sma50={self._fmt_metric(indicators['sma_50'])} "
                f"ema20={self._fmt_metric(indicators['ema_20'])} "
                f"ema50={self._fmt_metric(indicators['ema_50'])} "
                f"bias={indicators['bias_direction']} "
                f"bias_score={self._fmt_metric(indicators['bias_score'], precision=6)} "
                f"bias_conf={self._fmt_metric(indicators['bias_confidence'], precision=3)} "
                f"orderflow={indicators['orderflow_status']} "
                f"keltner_width={self._fmt_metric(indicators.get('keltner_width_pct'), precision=2)}% "
                f"keltner_conf={self._fmt_metric(indicators.get('keltner_confidence'), precision=3)} "
                f"keltner_ranging={indicators.get('keltner_is_ranging')}"
            )
            # Multi-timeframe context
            try:
                from ..quant.mtf_analyzer import MTFAnalyzer
                mtf = MTFAnalyzer(self.keltner_analyzer.candle_fetcher)
                analysis = mtf.analyze()
                tf_1h = analysis.tf_1h
                tf_4h = analysis.tf_4h
                logger.info(
                    "MTF context: regime=%s (conf=%.2f) htf_bias=%s "
                    "overbought=%s oversold=%s daily_atr=%.2f%% "
                    "1h_rsi=%s 1h_trend=%s 4h_rsi=%s 4h_trend=%s",
                    analysis.regime, analysis.regime_confidence,
                    analysis.higher_tf_bias,
                    analysis.overbought, analysis.oversold,
                    analysis.daily_atr_pct or 0,
                    f"{tf_1h.rsi_14:.1f}" if tf_1h and tf_1h.rsi_14 else "n/a",
                    tf_1h.trend if tf_1h else "n/a",
                    f"{tf_4h.rsi_14:.1f}" if tf_4h and tf_4h.rsi_14 else "n/a",
                    tf_4h.trend if tf_4h else "n/a",
                )
            except Exception as mtf_err:
                logger.debug("MTF context unavailable: %s", mtf_err)
        except Exception as e:
            logger.debug(f"Market indicator logging unavailable: {e}")

    def record_analytics_snapshot(self, wallet: str, *, pool_state=None, budget=None) -> None:
        """Record current state to analytics DB."""
        try:
            if budget is None:
                budget = self.balance.get_capital_budget(wallet, self.lp)
            if pool_state is None:
                pool_state = self.lp.get_pool_state()

            snapshot_ranges = self.resolve_snapshot_position_ranges(wallet, pool_state)

            prev = self.analytics.get_latest_snapshot()

            pending_fees_mnt, pending_fees_usdt = self._extract_pending_fees(wallet, pool_state)
            pending_rewards_mnt = self._extract_pending_rewards(wallet)

            self.analytics.record_snapshot(
                mnt_price=float(budget.mnt_price_usdt),
                wallet_mnt=float(budget.total_mnt - budget.deployed_mnt),
                wallet_usdt=float(budget.total_usdt - budget.deployed_usdt),
                deployed_mnt=float(budget.deployed_mnt),
                deployed_usdt=float(budget.deployed_usdt),
                total_value_usdt=float(budget.total_value_usdt),
                free_value_usdt=float(budget.free_value_usdt),
                active_bin_id=pool_state.active_bin_id,
                narrow_bins=snapshot_ranges["narrow_bins"],
                narrow_min_bin=snapshot_ranges["narrow_min_bin"],
                narrow_max_bin=snapshot_ranges["narrow_max_bin"],
                wide_bins=snapshot_ranges["wide_bins"],
                wide_min_bin=snapshot_ranges["wide_min_bin"],
                wide_max_bin=snapshot_ranges["wide_max_bin"],
                pending_fees_mnt=pending_fees_mnt,
                pending_fees_usdt=pending_fees_usdt,
                pending_rewards_mnt=pending_rewards_mnt,
            )

            self.analytics.detect_external_changes(
                prev_snapshot=prev,
                current_deployed_mnt=float(budget.deployed_mnt),
                current_deployed_usdt=float(budget.deployed_usdt),
                current_bins=max(
                    snapshot_ranges["narrow_bins"],
                    snapshot_ranges["wide_bins"],
                ),
                mnt_price=float(budget.mnt_price_usdt),
            )
        except Exception as e:
            logger.debug(f"Analytics snapshot failed: {e}")

    def _extract_pending_fees(
        self, wallet: str, pool_state
    ) -> tuple[float | None, float | None]:
        """Pending fees not available (fee-only farming, no off-chain API)."""
        return (None, None)

    def _extract_pending_rewards(self, wallet: str) -> float | None:
        """Rewards not available (fee-only farming, no off-chain API)."""
        return None

    def resolve_snapshot_position_ranges(self, wallet: str, pool_state) -> dict[str, Any]:
        """Prefer on-chain position ranges for cycle snapshots when strategy ownership is clear."""
        reg = self.lp.get_registry(wallet)
        narrow = reg.get_narrow_positions()
        wide = reg.get_wide_positions()
        position = self.lp.get_position(wallet, pool_state=pool_state, include_inventory=False)

        snapshot_ranges = {
            "narrow_bins": narrow[0].bin_count if narrow else 0,
            "narrow_min_bin": narrow[0].min_bin if narrow else None,
            "narrow_max_bin": narrow[0].max_bin if narrow else None,
            "wide_bins": wide[0].bin_count if wide else 0,
            "wide_min_bin": wide[0].min_bin if wide else None,
            "wide_max_bin": wide[0].max_bin if wide else None,
        }

        if not position.position_exists:
            return snapshot_ranges

        inferred_strategy = None
        if self.strategy_override in {"narrow", "wide"}:
            inferred_strategy = self.strategy_override
        elif narrow and not wide:
            inferred_strategy = "narrow"
        elif wide and not narrow:
            inferred_strategy = "wide"

        if inferred_strategy is None:
            return snapshot_ranges

        prefix = inferred_strategy
        snapshot_ranges[f"{prefix}_bins"] = position.bin_count
        snapshot_ranges[f"{prefix}_min_bin"] = position.min_bin_id
        snapshot_ranges[f"{prefix}_max_bin"] = position.max_bin_id

        reg_position = (
            narrow[0] if inferred_strategy == "narrow" and narrow
            else wide[0] if inferred_strategy == "wide" and wide
            else None
        )
        if reg_position and (
            reg_position.min_bin != position.min_bin_id
            or reg_position.max_bin != position.max_bin_id
            or reg_position.bin_count != position.bin_count
        ):
            logger.warning(
                "Registry drift detected for snapshot telemetry: "
                f"{inferred_strategy} registry=[{reg_position.min_bin}-{reg_position.max_bin}]/{reg_position.bin_count} "
                f"onchain=[{position.min_bin_id}-{position.max_bin_id}]/{position.bin_count}"
            )

        return snapshot_ranges

    @staticmethod
    def _fmt_metric(value: Any, precision: int = 4) -> str:
        if value in (None, "", "None"):
            return "n/a"
        try:
            return f"{float(value):.{precision}f}"
        except (TypeError, ValueError):
            return "n/a"
