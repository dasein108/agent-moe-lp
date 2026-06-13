"""
Keltner Channel Analyzer for Wide-Range LP Fee Farming.

Provides advanced Keltner Channel analysis optimized for MNT LP positioning
in 1-10% price ranges for consistent fee capture over longer time periods.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import time

from .candle_fetcher import CandleFetcher
from ..logging_config import get_logger

logger = get_logger(__name__)


class ChannelConfig(Enum):
    """Predefined Keltner Channel configurations."""
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    WIDE_CAPTURE = "wide_capture"


class ChannelQuality(Enum):
    """Channel quality assessment for LP positioning."""
    POOR = "poor"          # <0.4 quality score
    FAIR = "fair"          # 0.4-0.6 quality score
    GOOD = "good"          # 0.6-0.8 quality score
    EXCELLENT = "excellent"  # >0.8 quality score


@dataclass
class KeltnerBounds:
    """Keltner Channel bounds data."""
    middle_line: float
    upper_bound: float
    lower_bound: float
    channel_width: float
    current_price: float
    width_pct: float
    atr_value: float
    ema_value: float
    
    def to_dict(self) -> Dict[str, float]:
        return {
            'middle_line': self.middle_line,
            'upper_bound': self.upper_bound,
            'lower_bound': self.lower_bound,
            'channel_width': self.channel_width,
            'current_price': self.current_price,
            'width_pct': self.width_pct,
            'atr_value': self.atr_value,
            'ema_value': self.ema_value
        }


@dataclass
class ChannelAnalysis:
    """Complete channel analysis results."""
    bounds: KeltnerBounds
    quality: ChannelQuality
    quality_score: float
    stability_score: float
    width_score: float
    position_score: float
    trend_slope: float
    is_ranging: bool
    price_position: float  # 0.0 = lower bound, 1.0 = upper bound
    recommendation: str
    confidence: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            'bounds': self.bounds.to_dict(),
            'quality': self.quality.value,  # Convert enum to string
            'quality_score': self.quality_score,
            'stability_score': self.stability_score,
            'width_score': self.width_score,
            'position_score': self.position_score,
            'trend_slope': self.trend_slope,
            'is_ranging': self.is_ranging,
            'price_position': self.price_position,
            'recommendation': self.recommendation,
            'confidence': self.confidence
        }


class KeltnerChannel:
    """Core Keltner Channel calculation engine."""
    
    def __init__(self, 
                 ema_period: int = 20,
                 atr_period: int = 14,
                 multiplier: float = 2.0):
        self.ema_period = ema_period
        self.atr_period = atr_period
        self.multiplier = multiplier
    
    def calculate_bounds(self, price_data: pd.DataFrame) -> KeltnerBounds:
        """Calculate Keltner Channel bounds for LP positioning."""
        
        if len(price_data) < max(self.ema_period, self.atr_period):
            raise ValueError(f"Need at least {max(self.ema_period, self.atr_period)} candles")
        
        # Calculate EMA (middle line)
        ema = price_data['close'].ewm(span=self.ema_period).mean()
        
        # Calculate True Range
        tr = pd.DataFrame({
            'hl': price_data['high'] - price_data['low'],
            'hc': abs(price_data['high'] - price_data['close'].shift(1)),
            'lc': abs(price_data['low'] - price_data['close'].shift(1))
        }).max(axis=1)
        
        # Calculate ATR using Wilder's RMA (exponential decay, sheds stale spikes)
        atr = tr.ewm(alpha=1 / self.atr_period, adjust=False).mean()
        
        # Calculate channel bounds
        upper_bound = ema + (self.multiplier * atr)
        lower_bound = ema - (self.multiplier * atr)
        
        # Get current values
        current_price = price_data['close'].iloc[-1]
        middle_line = ema.iloc[-1]
        upper = upper_bound.iloc[-1]
        lower = lower_bound.iloc[-1]
        channel_width = upper - lower
        width_pct = (channel_width / middle_line) * 100
        
        return KeltnerBounds(
            middle_line=middle_line,
            upper_bound=upper,
            lower_bound=lower,
            channel_width=channel_width,
            current_price=current_price,
            width_pct=width_pct,
            atr_value=atr.iloc[-1],
            ema_value=middle_line
        )


class KeltnerAnalyzer:
    """Advanced Keltner Channel analysis for LP positioning."""
    
    # MNT-specific Keltner configurations
    KELTNER_CONFIGS = {
        ChannelConfig.CONSERVATIVE: {
            'ema_period': 24,      # 2-hour EMA on 5m candles
            'atr_period': 16,      # 80-minute ATR
            'multiplier': 1.5,     # ~1-3% range typically
            'target_width_pct': 2.0,   # 2% target width
            'min_quality': 0.7,    # High quality required
            'max_width_pct': 4.0   # Maximum 4% width
        },
        ChannelConfig.BALANCED: {
            'ema_period': 20,      # 100-minute EMA
            'atr_period': 14,      # 70-minute ATR (RMA/Wilder's smoothing)
            'multiplier': 1.5,     # ~1-3% range with RMA ATR
            'target_width_pct': 3.0,   # 3% target width
            'min_quality': 0.6,    # Good quality required
            'max_width_pct': 5.0   # Maximum 5% width
        },
        ChannelConfig.AGGRESSIVE: {
            'ema_period': 16,      # 80-minute EMA
            'atr_period': 12,      # 60-minute ATR
            'multiplier': 2.5,     # ~3-8% range typically
            'target_width_pct': 6.0,   # 6% target width
            'min_quality': 0.5,    # Fair quality acceptable
            'max_width_pct': 10.0  # Maximum 10% width
        },
        ChannelConfig.WIDE_CAPTURE: {
            'ema_period': 12,      # 60-minute EMA
            'atr_period': 10,      # 50-minute ATR
            'multiplier': 3.0,     # ~5-10% range typically
            'target_width_pct': 8.0,   # 8% target width
            'min_quality': 0.4,    # Any quality acceptable
            'max_width_pct': 12.0  # Maximum 12% width
        }
    }
    
    def __init__(self, candle_fetcher: Optional[CandleFetcher] = None):
        self.candle_fetcher = candle_fetcher or CandleFetcher()
        
    def analyze_channel_conditions(self,
                                 symbol: str = "MNTUSDT",
                                 config: ChannelConfig = ChannelConfig.BALANCED,
                                 timeframe: str = "5m",
                                 lookback_periods: int = 200) -> ChannelAnalysis:
        """
        Comprehensive channel analysis for LP strategy.
        
        Returns complete analysis including bounds, quality assessment,
        and LP positioning recommendations.
        """
        
        try:
            # Get market data
            candles = self.candle_fetcher.get_candles(symbol, timeframe, lookback_periods)
            
            if len(candles) < 50:
                raise ValueError(f"Insufficient data: got {len(candles)} candles, need at least 50")
            
            # Get configuration
            config_params = self.KELTNER_CONFIGS[config]
            
            # Initialize Keltner Channel with config parameters
            keltner = KeltnerChannel(
                ema_period=config_params['ema_period'],
                atr_period=config_params['atr_period'],
                multiplier=config_params['multiplier']
            )
            
            # Calculate channel bounds
            bounds = keltner.calculate_bounds(candles)
            
            # Assess channel quality
            quality_analysis = self._assess_channel_quality(candles, bounds, config_params)
            
            # Generate recommendation
            recommendation = self._generate_recommendation(bounds, quality_analysis, config_params)
            
            return ChannelAnalysis(
                bounds=bounds,
                quality=quality_analysis['quality_level'],
                quality_score=quality_analysis['overall_quality'],
                stability_score=quality_analysis['stability_score'],
                width_score=quality_analysis['width_score'],
                position_score=quality_analysis['position_score'],
                trend_slope=quality_analysis['trend_slope'],
                is_ranging=quality_analysis['is_ranging'],
                price_position=quality_analysis['price_position'],
                recommendation=recommendation['action'],
                confidence=recommendation['confidence']
            )
            
        except Exception as e:
            logger.error(f"Channel analysis failed: {e}")
            # Return default/safe analysis
            return self._get_default_analysis()
    
    def _assess_channel_quality(self, 
                              candles: pd.DataFrame,
                              bounds: KeltnerBounds,
                              config: Dict[str, Any]) -> Dict[str, Any]:
        """Assess quality of current Keltner Channel for LP strategy."""
        
        # Calculate channel stability (low volatility = stable channel)
        price_changes = candles['close'].pct_change().dropna()
        volatility = price_changes.std() * np.sqrt(len(price_changes))
        
        # Channel width analysis
        width_pct = bounds.width_pct
        target_width = config['target_width_pct']
        max_width = config['max_width_pct']
        
        # Trending vs ranging analysis
        middle_line_slope = self._calculate_trend_slope(candles['close'])
        
        # Price position within channel
        price_position = (
            (bounds.current_price - bounds.lower_bound) / bounds.channel_width
        ) if bounds.channel_width > 0 else 0.5
        
        # Quality scoring components (0.0-1.0 each)
        
        # Stability: penalize high volatility
        stability_score = max(0.0, min(1.0, 1.0 - (volatility / 0.15)))  # Penalty for >15% vol
        
        # Width: optimal around target, penalty for too narrow/wide
        width_score = self._score_channel_width(width_pct, target_width, max_width)
        
        # Position: penalty for extreme positions (near bounds)
        position_score = 1.0 - abs(price_position - 0.5) * 2  # Best at center
        position_score = max(0.0, min(1.0, position_score))
        
        # Trend: ranging markets preferred for wide-range LP
        trend_score = max(0.0, 1.0 - abs(middle_line_slope) * 1000)  # Penalty for strong trends
        
        # Overall quality (weighted combination)
        overall_quality = (
            stability_score * 0.3 +
            width_score * 0.3 +
            position_score * 0.2 +
            trend_score * 0.2
        )
        
        # Determine quality level
        if overall_quality >= 0.8:
            quality_level = ChannelQuality.EXCELLENT
        elif overall_quality >= 0.6:
            quality_level = ChannelQuality.GOOD
        elif overall_quality >= 0.4:
            quality_level = ChannelQuality.FAIR
        else:
            quality_level = ChannelQuality.POOR
        
        return {
            'overall_quality': overall_quality,
            'quality_level': quality_level,
            'stability_score': stability_score,
            'width_score': width_score,
            'position_score': position_score,
            'trend_score': trend_score,
            'channel_width_pct': width_pct,
            'price_position': price_position,
            'trend_slope': middle_line_slope,
            'is_ranging': abs(middle_line_slope) < 0.001,  # <0.1% slope = ranging
            'volatility': volatility
        }
    
    def _score_channel_width(self, 
                           current_width: float,
                           target_width: float,
                           max_width: float) -> float:
        """Score channel width for LP strategy suitability."""
        
        if current_width <= 0:
            return 0.0
        
        # Too narrow (< 1%)
        if current_width < 1.0:
            return current_width / 1.0  # Linear score 0-1
        
        # Ideal range (1% to target)
        if current_width <= target_width:
            return 1.0
        
        # Acceptable range (target to max)
        if current_width <= max_width:
            # Linear decay from 1.0 to 0.3
            excess = (current_width - target_width) / (max_width - target_width)
            return 1.0 - (excess * 0.7)
        
        # Too wide (> max)
        return max(0.0, 0.3 - (current_width - max_width) * 0.1)
    
    def _calculate_trend_slope(self, prices: pd.Series, periods: int = 20) -> float:
        """Calculate trend slope of price series."""
        
        if len(prices) < periods:
            return 0.0
        
        recent_prices = prices.tail(periods).values
        x = np.arange(len(recent_prices))
        
        # Linear regression slope
        slope = np.polyfit(x, recent_prices, 1)[0]
        
        # Normalize by current price to get percentage slope
        current_price = prices.iloc[-1]
        return slope / current_price if current_price > 0 else 0.0
    
    def _generate_recommendation(self,
                               bounds: KeltnerBounds,
                               quality_analysis: Dict[str, Any],
                               config: Dict[str, Any]) -> Dict[str, Any]:
        """Generate LP positioning recommendation based on analysis."""
        
        overall_quality = quality_analysis['overall_quality']
        min_quality = config['min_quality']
        width_pct = bounds.width_pct
        max_width = config['max_width_pct']
        
        # Entry criteria evaluation
        quality_ok = overall_quality >= min_quality
        width_ok = 1.0 <= width_pct <= max_width
        ranging_market = quality_analysis['is_ranging']
        reasonable_position = 0.2 <= quality_analysis['price_position'] <= 0.8
        
        if quality_ok and width_ok and ranging_market and reasonable_position:
            action = "ENTER_WIDE_RANGE"
            confidence = overall_quality
            reasoning = (
                f"High quality channel ({width_pct:.1f}% width, "
                f"{quality_analysis['quality_level'].value} quality)"
            )
        elif overall_quality >= 0.5 and width_pct <= max_width:
            action = "CONSIDER_ENTRY"
            confidence = overall_quality * 0.7
            reasoning = (
                f"Marginal opportunity ({width_pct:.1f}% width, "
                f"{quality_analysis['quality_level'].value} quality)"
            )
        else:
            action = "WAIT"
            confidence = 0.0
            
            # Specific reasons for waiting
            reasons = []
            if not quality_ok:
                reasons.append(f"low quality ({overall_quality:.2f} < {min_quality})")
            if not width_ok:
                if width_pct < 1.0:
                    reasons.append(f"channel too narrow ({width_pct:.1f}%)")
                else:
                    reasons.append(f"channel too wide ({width_pct:.1f}%)")
            if not ranging_market:
                reasons.append("trending market")
            if not reasonable_position:
                reasons.append("price near channel bounds")
            
            reasoning = f"Wait for better conditions: {', '.join(reasons)}"
        
        return {
            'action': action,
            'confidence': confidence,
            'reasoning': reasoning,
            'quality_check': quality_ok,
            'width_check': width_ok,
            'ranging_check': ranging_market,
            'position_check': reasonable_position
        }
    
    def _get_default_analysis(self) -> ChannelAnalysis:
        """Return safe default analysis when calculation fails."""
        
        default_bounds = KeltnerBounds(
            middle_line=2.20,  # Default MNT price
            upper_bound=2.31,  # +5%
            lower_bound=2.09,  # -5%
            channel_width=0.22,
            current_price=2.20,
            width_pct=10.0,
            atr_value=0.11,
            ema_value=2.20
        )
        
        return ChannelAnalysis(
            bounds=default_bounds,
            quality=ChannelQuality.POOR,
            quality_score=0.0,
            stability_score=0.0,
            width_score=0.0,
            position_score=0.5,
            trend_slope=0.0,
            is_ranging=True,
            price_position=0.5,
            recommendation="WAIT",
            confidence=0.0
        )
    
    def get_optimal_lp_range(self,
                           analysis: ChannelAnalysis,
                           safety_margin: float = 0.1) -> Dict[str, Any]:
        """
        Calculate optimal LP range based on channel analysis.
        
        Args:
            analysis: Channel analysis results
            safety_margin: Additional margin outside channel (0.1 = 10%)
        
        Returns:
            Dict with LP range recommendations including bin IDs
        """
        
        bounds = analysis.bounds
        
        # Apply safety margins outside channel bounds
        lower_price = bounds.lower_bound * (1 - safety_margin)
        upper_price = bounds.upper_bound * (1 + safety_margin)
        
        # Ensure reasonable range limits (1-10%)
        current_price = bounds.current_price
        min_range = current_price * 0.01  # 1%
        max_range = current_price * 0.10  # 10%
        
        range_width = upper_price - lower_price
        
        if range_width < min_range:
            # Expand to minimum range
            center = (upper_price + lower_price) / 2
            lower_price = center - min_range / 2
            upper_price = center + min_range / 2
        elif range_width > max_range:
            # Contract to maximum range
            center = (upper_price + lower_price) / 2
            lower_price = center - max_range / 2
            upper_price = center + max_range / 2
        
        # Convert to bin IDs (implementation depends on Liquidity Book bin structure)
        # For now, return prices - bin conversion would be done in LP manager
        
        range_width_pct = ((upper_price - lower_price) / current_price) * 100
        
        return {
            'lower_price': lower_price,
            'upper_price': upper_price,
            'range_width': upper_price - lower_price,
            'range_width_pct': range_width_pct,
            'center_price': (upper_price + lower_price) / 2,
            'safety_margin': safety_margin,
            'recommendation_quality': analysis.quality.value,
            'confidence': analysis.confidence
        }