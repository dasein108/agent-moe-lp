"""
Bias Calculator for directional LP positioning.

Combines slope, momentum, and order flow signals to determine
market bias for optimal LP deployment.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple
import numpy as np
from scipy import stats
from ..logging_config import get_logger

logger = get_logger(__name__)


class BiasCalculator:
    """Calculate market bias from multiple signal sources."""
    
    def __init__(self):
        """Initialize with MNT-calibrated parameters."""
        # Lookback periods for different signals
        self.lookback_periods = {
            'slope': 20,      # candles for trend analysis
            'momentum': 5,    # candles for momentum calculation  
            'orderflow': 30   # seconds for order flow analysis
        }
        
        # MNT-specific bias thresholds
        self.thresholds = {
            'bull_threshold': 0.0008,    # Positive bias threshold
            'bear_threshold': -0.0008,   # Negative bias threshold
            'confidence_min': 0.6,       # Minimum confidence for bias positioning
            'strong_bias': 0.0015,       # Strong bias signal threshold
        }
        
        # Signal weights for combined scoring
        self.signal_weights = {
            'slope': 0.4,      # Price trend momentum (most predictive)
            'momentum': 0.2,   # Recent price action (confirmation)
            'orderflow': 0.4   # Real-time market pressure (early warning)
        }
        
    def calculate_slope_bias(self, closes: np.ndarray) -> Dict[str, float]:
        """
        Calculate bias from linear regression slope.
        
        Uses price trend momentum over lookback period,
        normalized by mean price for scale independence.
        
        Args:
            closes: Array of closing prices
            
        Returns:
            Dict with slope bias metrics
        """
        if len(closes) < self.lookback_periods['slope']:
            logger.warning(f"Insufficient data for slope calculation: {len(closes)} < {self.lookback_periods['slope']}")
            return {
                'slope_raw': 0.0,
                'slope_norm': 0.0, 
                'r_squared': 0.0,
                'confidence': 0.0
            }
            
        # Use recent data for trend analysis
        recent_closes = closes[-self.lookback_periods['slope']:]
        x = np.arange(len(recent_closes))
        
        # Calculate linear regression
        slope, intercept, r_value, p_value, std_err = stats.linregress(x, recent_closes)
        
        # Normalize slope by mean price
        mean_price = np.mean(recent_closes)
        slope_norm = slope / mean_price if mean_price > 0 else 0.0
        
        # Calculate confidence based on R-squared and significance
        r_squared = r_value ** 2
        confidence = min(1.0, r_squared * (1.0 - p_value))
        
        result = {
            'slope_raw': float(slope),
            'slope_norm': float(slope_norm),
            'r_squared': float(r_squared),
            'p_value': float(p_value),
            'confidence': float(confidence)
        }
        
        logger.debug(f"Slope bias: norm={slope_norm:.6f}, R²={r_squared:.3f}, conf={confidence:.3f}")
        return result
        
    def calculate_momentum_bias(self, closes: np.ndarray, k: int = None) -> Dict[str, float]:
        """
        Calculate momentum bias from recent price action.
        
        Args:
            closes: Array of closing prices
            k: Lookback period (default from config)
            
        Returns:
            Dict with momentum bias metrics
        """
        if k is None:
            k = self.lookback_periods['momentum']
            
        if len(closes) < k + 1:
            logger.warning(f"Insufficient data for momentum calculation: {len(closes)} < {k + 1}")
            return {
                'momentum_raw': 0.0,
                'momentum_norm': 0.0,
                'volatility': 0.0,
                'confidence': 0.0
            }
            
        # Calculate momentum
        current_price = closes[-1]
        past_price = closes[-k-1]
        momentum_raw = current_price - past_price
        momentum_norm = (current_price / past_price) - 1.0 if past_price > 0 else 0.0
        
        # Calculate recent volatility for confidence adjustment
        recent_prices = closes[-k-1:]
        recent_returns = np.diff(recent_prices) / recent_prices[:-1]
        volatility = np.std(recent_returns) if len(recent_returns) > 1 else 0.0
        
        # Confidence inversely related to volatility
        confidence = max(0.0, min(1.0, 1.0 - (volatility / 0.05)))  # Scale by 5% vol
        
        result = {
            'momentum_raw': float(momentum_raw),
            'momentum_norm': float(momentum_norm),
            'volatility': float(volatility),
            'confidence': float(confidence)
        }
        
        logger.debug(f"Momentum bias: norm={momentum_norm:.6f}, vol={volatility:.6f}, conf={confidence:.3f}")
        return result
        
    def calculate_orderflow_bias(self, 
                               imbalance: float, 
                               short_return: float,
                               intensity: float = 0.0) -> Dict[str, float]:
        """
        Calculate bias from order flow analysis (Task 2 integration).
        
        Args:
            imbalance: Order flow imbalance (-1 to 1)
            short_return: Short-term return
            intensity: Trade intensity metric
            
        Returns:
            Dict with order flow bias metrics
        """
        # Combine order flow metrics with MNT-specific weighting
        flow_components = {
            'imbalance_weight': 0.6,
            'return_weight': 0.3,
            'intensity_weight': 0.1
        }
        
        # Calculate composite order flow bias
        orderflow_bias = (
            flow_components['imbalance_weight'] * imbalance +
            flow_components['return_weight'] * short_return +
            flow_components['intensity_weight'] * min(1.0, intensity / 10.0)  # Normalize intensity
        )
        
        # Calculate confidence based on signal consistency
        signal_strength = abs(imbalance) + abs(short_return) + min(1.0, intensity / 10.0)
        confidence = min(1.0, signal_strength / 2.0)  # Scale to 0-1
        
        # Check for conflicting signals (reduce confidence)
        imbalance_direction = 1 if imbalance > 0 else -1 if imbalance < 0 else 0
        return_direction = 1 if short_return > 0 else -1 if short_return < 0 else 0
        
        if imbalance_direction != 0 and return_direction != 0:
            if imbalance_direction != return_direction:
                confidence *= 0.5  # Reduce confidence for conflicting signals
                
        result = {
            'orderflow_bias': float(orderflow_bias),
            'imbalance': float(imbalance),
            'short_return': float(short_return),
            'intensity': float(intensity),
            'confidence': float(confidence)
        }
        
        logger.debug(f"Order flow bias: {orderflow_bias:.6f}, conf={confidence:.3f}")
        return result
        
    def get_combined_bias(self, 
                         closes: np.ndarray,
                         imbalance: float = 0.0,
                         short_return: float = 0.0,
                         intensity: float = 0.0) -> Dict[str, any]:
        """
        Calculate combined bias score from all signals.
        
        Formula: bias_score = 0.4*slope + 0.2*momentum + 0.4*orderflow
        
        Args:
            closes: Price array for slope/momentum calculation
            imbalance: Order flow imbalance
            short_return: Short-term return
            intensity: Trade intensity
            
        Returns:
            Complete bias analysis with direction and confidence
        """
        # Calculate individual bias components
        slope_data = self.calculate_slope_bias(closes)
        momentum_data = self.calculate_momentum_bias(closes)
        orderflow_data = self.calculate_orderflow_bias(imbalance, short_return, intensity)
        
        # Extract normalized bias values
        slope_bias = slope_data['slope_norm']
        momentum_bias = momentum_data['momentum_norm'] 
        orderflow_bias = orderflow_data['orderflow_bias']
        
        # Calculate weighted combined bias
        combined_bias = (
            self.signal_weights['slope'] * slope_bias +
            self.signal_weights['momentum'] * momentum_bias +
            self.signal_weights['orderflow'] * orderflow_bias
        )
        
        # Determine direction based on thresholds
        if combined_bias > self.thresholds['bull_threshold']:
            direction = 'BULL'
        elif combined_bias < self.thresholds['bear_threshold']:
            direction = 'BEAR'
        else:
            direction = 'NEUTRAL'
            
        # Calculate combined confidence
        confidences = [
            slope_data['confidence'],
            momentum_data['confidence'], 
            orderflow_data['confidence']
        ]
        
        # Weight confidences by signal importance
        weighted_confidence = (
            self.signal_weights['slope'] * confidences[0] +
            self.signal_weights['momentum'] * confidences[1] +
            self.signal_weights['orderflow'] * confidences[2]
        )
        
        # Boost confidence for strong directional signals
        if abs(combined_bias) > self.thresholds['strong_bias']:
            weighted_confidence = min(1.0, weighted_confidence * 1.2)
            
        # Reduce confidence if signals conflict
        signal_directions = [
            1 if slope_bias > self.thresholds['bull_threshold'] else -1 if slope_bias < self.thresholds['bear_threshold'] else 0,
            1 if momentum_bias > 0.0005 else -1 if momentum_bias < -0.0005 else 0,
            1 if orderflow_bias > 0.05 else -1 if orderflow_bias < -0.05 else 0
        ]
        
        non_zero_directions = [d for d in signal_directions if d != 0]
        if len(non_zero_directions) > 1:
            if not all(d == non_zero_directions[0] for d in non_zero_directions):
                weighted_confidence *= 0.7  # Reduce confidence for conflicting signals
        
        # Generate reasoning
        reasoning = self._generate_bias_reasoning(
            slope_bias, momentum_bias, orderflow_bias, 
            combined_bias, direction, weighted_confidence
        )
        
        result = {
            'score': float(combined_bias),
            'direction': direction,
            'confidence': float(weighted_confidence),
            'components': {
                'slope': slope_data,
                'momentum': momentum_data,
                'orderflow': orderflow_data
            },
            'weights': self.signal_weights,
            'reasoning': reasoning
        }
        
        logger.info(f"Combined bias: {direction} (score={combined_bias:.6f}, conf={weighted_confidence:.3f})")
        return result
        
    def _generate_bias_reasoning(self,
                               slope_bias: float,
                               momentum_bias: float, 
                               orderflow_bias: float,
                               combined_bias: float,
                               direction: str,
                               confidence: float) -> str:
        """Generate human-readable reasoning for bias decision."""
        
        components = []
        
        # Slope component
        if abs(slope_bias) > self.thresholds['bull_threshold']:
            trend_dir = "uptrend" if slope_bias > 0 else "downtrend"
            components.append(f"price trend shows {trend_dir} (slope={slope_bias:.6f})")
        
        # Momentum component  
        if abs(momentum_bias) > 0.0005:
            mom_dir = "positive" if momentum_bias > 0 else "negative"
            components.append(f"momentum is {mom_dir} ({momentum_bias:.6f})")
            
        # Order flow component
        if abs(orderflow_bias) > 0.05:
            flow_dir = "buying pressure" if orderflow_bias > 0 else "selling pressure"
            components.append(f"order flow shows {flow_dir} ({orderflow_bias:.6f})")
        
        if not components:
            return f"{direction} bias with neutral signals (combined score={combined_bias:.6f})"
            
        reasoning = f"{direction} bias from: " + ", ".join(components)
        reasoning += f" (combined={combined_bias:.6f}, confidence={confidence:.2f})"
        
        return reasoning
        
    def get_bias_strength(self, bias_score: float) -> str:
        """
        Classify bias strength.
        
        Args:
            bias_score: Combined bias score
            
        Returns:
            Strength classification
        """
        abs_score = abs(bias_score)
        
        if abs_score > self.thresholds['strong_bias']:
            return 'STRONG'
        elif abs_score > self.thresholds['bull_threshold']:
            return 'MODERATE'
        else:
            return 'WEAK'
            
    def should_use_bias_positioning(self, bias_result: Dict[str, any]) -> bool:
        """
        Determine if bias is strong enough for asymmetric positioning.
        
        Args:
            bias_result: Result from get_combined_bias()
            
        Returns:
            True if should use biased positioning
        """
        return (
            bias_result['direction'] != 'NEUTRAL' and
            bias_result['confidence'] >= self.thresholds['confidence_min']
        )