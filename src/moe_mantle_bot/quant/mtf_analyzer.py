"""
Multi-Timeframe (MTF) market analysis for LP strategy decisions.

Combines 5m (execution), 1h (trend), and 4h (regime) timeframes
to provide a complete market view for strategy selection.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimeframeSignal:
    """Analysis for a single timeframe."""
    interval: str
    price: float
    rsi_14: float | None
    sma_20: float | None
    sma_50: float | None
    ema_20: float | None
    ema_50: float | None
    trend: str  # BULL, BEAR, NEUTRAL
    atr_pct: float | None  # ATR as % of price
    volatility_pct: float  # std of returns annualized to this TF


@dataclass(frozen=True)
class MTFAnalysis:
    """Combined multi-timeframe market view."""
    tf_5m: TimeframeSignal
    tf_1h: TimeframeSignal | None
    tf_4h: TimeframeSignal | None
    regime: str  # TRENDING_UP, TRENDING_DOWN, RANGING, VOLATILE
    regime_confidence: float  # 0.0-1.0
    higher_tf_bias: str  # BULL, BEAR, NEUTRAL
    overbought: bool  # RSI > 70 on 1h or 4h
    oversold: bool  # RSI < 30 on 1h or 4h
    daily_atr_pct: float | None  # 4h ATR extrapolated to daily

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "regime": self.regime,
            "regime_confidence": round(self.regime_confidence, 3),
            "higher_tf_bias": self.higher_tf_bias,
            "overbought": self.overbought,
            "oversold": self.oversold,
            "daily_atr_pct": round(self.daily_atr_pct, 2) if self.daily_atr_pct else None,
        }
        for label, tf in [("5m", self.tf_5m), ("1h", self.tf_1h), ("4h", self.tf_4h)]:
            if tf is None:
                continue
            d[f"tf_{label}"] = {
                "trend": tf.trend,
                "rsi_14": round(tf.rsi_14, 1) if tf.rsi_14 is not None else None,
                "atr_pct": round(tf.atr_pct, 3) if tf.atr_pct is not None else None,
                "volatility_pct": round(tf.volatility_pct, 3),
            }
        return d


class MTFAnalyzer:
    """Multi-timeframe market analyzer using Bybit candle data."""

    def __init__(self, candle_fetcher):
        self.cf = candle_fetcher

    def analyze(self, symbol: str = "MNTUSDT") -> MTFAnalysis:
        """Run full multi-timeframe analysis."""
        tf_5m = self._analyze_timeframe(symbol, "5m", 200)
        tf_1h = self._analyze_timeframe(symbol, "1h", 100)
        tf_4h = self._analyze_timeframe(symbol, "4h", 100)

        regime, regime_confidence = self._classify_regime(tf_5m, tf_1h, tf_4h)
        higher_tf_bias = self._higher_tf_bias(tf_1h, tf_4h)

        overbought = False
        oversold = False
        for tf in [tf_1h, tf_4h]:
            if tf is not None and tf.rsi_14 is not None:
                if tf.rsi_14 > 70:
                    overbought = True
                if tf.rsi_14 < 30:
                    oversold = True

        daily_atr_pct = None
        if tf_4h is not None and tf_4h.atr_pct is not None:
            daily_atr_pct = tf_4h.atr_pct * math.sqrt(6)  # 6 x 4h candles per day

        result = MTFAnalysis(
            tf_5m=tf_5m,
            tf_1h=tf_1h,
            tf_4h=tf_4h,
            regime=regime,
            regime_confidence=regime_confidence,
            higher_tf_bias=higher_tf_bias,
            overbought=overbought,
            oversold=oversold,
            daily_atr_pct=daily_atr_pct,
        )
        logger.info(
            "MTF analysis: regime=%s (conf=%.2f) bias=%s overbought=%s oversold=%s daily_atr=%.2f%%",
            regime, regime_confidence, higher_tf_bias, overbought, oversold,
            daily_atr_pct or 0,
        )
        return result

    def _analyze_timeframe(
        self, symbol: str, interval: str, limit: int,
    ) -> TimeframeSignal | None:
        try:
            candles = self.cf.get_candles(symbol, interval, limit)
        except Exception as e:
            logger.warning("MTF: failed to fetch %s candles: %s", interval, e)
            return None

        close = candles["close"].astype(float).values
        high = candles["high"].astype(float).values
        low = candles["low"].astype(float).values

        if len(close) < 20:
            return None

        price = float(close[-1])

        # Moving averages
        sma_20 = float(np.mean(close[-20:])) if len(close) >= 20 else None
        sma_50 = float(np.mean(close[-50:])) if len(close) >= 50 else None
        ema_20 = self._ema(close, 20)
        ema_50 = self._ema(close, 50) if len(close) >= 50 else None

        # RSI
        rsi_14 = self._rsi(close, 14)

        # Trend from EMA cross
        if ema_20 is not None and ema_50 is not None:
            if ema_20 > ema_50 * 1.001:
                trend = "BULL"
            elif ema_20 < ema_50 * 0.999:
                trend = "BEAR"
            else:
                trend = "NEUTRAL"
        else:
            trend = "NEUTRAL"

        # ATR as % of price
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1]),
            ),
        )
        atr_14 = float(np.mean(tr[-14:])) if len(tr) >= 14 else None
        atr_pct = (atr_14 / price * 100) if atr_14 and price > 0 else None

        # Volatility (std of returns)
        returns = np.diff(close) / close[:-1]
        vol = float(np.std(returns)) * 100

        return TimeframeSignal(
            interval=interval,
            price=price,
            rsi_14=rsi_14,
            sma_20=sma_20,
            sma_50=sma_50,
            ema_20=ema_20,
            ema_50=ema_50,
            trend=trend,
            atr_pct=atr_pct,
            volatility_pct=vol,
        )

    def _classify_regime(
        self,
        tf_5m: TimeframeSignal | None,
        tf_1h: TimeframeSignal | None,
        tf_4h: TimeframeSignal | None,
    ) -> tuple[str, float]:
        """Classify market regime from multi-TF signals."""
        trends = []
        for tf in [tf_5m, tf_1h, tf_4h]:
            if tf is not None:
                trends.append(tf.trend)

        if not trends:
            return "RANGING", 0.0

        bull_count = trends.count("BULL")
        bear_count = trends.count("BEAR")
        n = len(trends)

        # All aligned = strong trend
        if bull_count == n:
            return "TRENDING_UP", min(1.0, 0.5 + 0.2 * n)
        if bear_count == n:
            return "TRENDING_DOWN", min(1.0, 0.5 + 0.2 * n)

        # Mixed signals — check if higher TFs agree
        if tf_4h is not None and tf_1h is not None:
            if tf_4h.trend == tf_1h.trend == "BULL":
                return "TRENDING_UP", 0.7
            if tf_4h.trend == tf_1h.trend == "BEAR":
                return "TRENDING_DOWN", 0.7

        # Check for high volatility regime
        if tf_1h is not None and tf_1h.atr_pct is not None and tf_1h.atr_pct > 3.0:
            return "VOLATILE", 0.6

        # Default: ranging
        confidence = 1.0 - (bull_count + bear_count) / max(n, 1) * 0.5
        return "RANGING", max(0.3, confidence)

    def _higher_tf_bias(
        self,
        tf_1h: TimeframeSignal | None,
        tf_4h: TimeframeSignal | None,
    ) -> str:
        """Determine the dominant higher-timeframe bias."""
        if tf_4h is not None and tf_4h.trend != "NEUTRAL":
            return tf_4h.trend
        if tf_1h is not None and tf_1h.trend != "NEUTRAL":
            return tf_1h.trend
        return "NEUTRAL"

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> float | None:
        if len(data) < period:
            return None
        alpha = 2.0 / (period + 1)
        ema = float(data[0])
        for v in data[1:]:
            ema = alpha * float(v) + (1 - alpha) * ema
        return ema

    @staticmethod
    def _rsi(close: np.ndarray, period: int = 14) -> float | None:
        if len(close) < period + 1:
            return None
        deltas = np.diff(close)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = float(np.mean(gains[-period:]))
        avg_loss = float(np.mean(losses[-period:]))
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
