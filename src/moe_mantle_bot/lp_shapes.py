"""
LP Shape Distribution Module

Provides pure functions for calculating liquidity distribution shapes
across bins for the Merchant Moe (Mantle) farming bot.

Supported shapes:
- Uniform: Equal liquidity across all bins
- Slope: Linear gradient (ascending, descending, peak, valley)
- Curve: Non-linear distributions (exponential, logarithmic, bell, u_curve)
- Custom: User-defined weights
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Settings


def calculate_slope_weights(
    num_bins: int,
    direction: str = "ascending",
    steepness: float = 1.0,
) -> list[float]:
    """
    Generate linear slope weights across bins.
    
    Args:
        num_bins: Number of bins
        direction: "ascending", "descending", "peak", "valley"
        steepness: Gradient multiplier (0.1-5.0)
        
    Returns:
        Normalized weights that sum to 1.0
    """
    if num_bins <= 0:
        return []
    
    steepness = max(0.1, min(5.0, steepness))  # Clamp to valid range
    
    if direction == "ascending":
        # More liquidity in higher price bins (higher bin IDs)
        weights = [i * steepness for i in range(num_bins)]
    elif direction == "descending":
        # More liquidity in lower price bins (lower bin IDs)
        weights = [(num_bins - 1 - i) * steepness for i in range(num_bins)]
    elif direction == "peak":
        # Maximum liquidity at center, decreasing toward edges
        mid = num_bins // 2
        weights = [steepness * (mid - abs(i - mid)) for i in range(num_bins)]
    elif direction == "valley":
        # Minimum liquidity at center, increasing toward edges
        mid = num_bins // 2
        weights = [steepness * abs(i - mid) for i in range(num_bins)]
    else:
        raise ValueError(f"Unknown slope direction: {direction}")
    
    # Normalize to sum to 1.0
    total = sum(weights)
    if total > 0:
        return [w / total for w in weights]
    else:
        return [1.0 / num_bins] * num_bins


def calculate_curve_weights(
    num_bins: int,
    curve_type: str = "exponential",
    exponent: float = 2.0,
) -> list[float]:
    """
    Generate curve-based weights across bins.
    
    Args:
        num_bins: Number of bins
        curve_type: "exponential", "logarithmic", "bell", "u_curve"
        exponent: Curve steepness parameter (0.1-10.0)
        
    Returns:
        Normalized weights that sum to 1.0
    """
    if num_bins <= 0:
        return []
    
    exponent = max(0.1, min(10.0, exponent))  # Clamp to valid range
    
    if curve_type == "exponential":
        # Exponential growth across bins
        weights = [math.exp(i * exponent / num_bins) for i in range(num_bins)]
    elif curve_type == "logarithmic":
        # Logarithmic growth across bins
        weights = [math.log(1 + i * exponent) for i in range(num_bins)]
    elif curve_type == "bell":
        # Gaussian/normal distribution around center
        mid = num_bins / 2
        weights = [math.exp(-((i - mid) ** 2) / (2 * exponent * (num_bins / 4))) 
                   for i in range(num_bins)]
    elif curve_type == "u_curve":
        # Inverse bell (U-curve) - more at edges, less at center
        mid = num_bins / 2
        weights = [math.exp(((i - mid) ** 2) / (2 * exponent * (num_bins / 4))) 
                   for i in range(num_bins)]
    else:
        raise ValueError(f"Unknown curve type: {curve_type}")
    
    # Normalize to sum to 1.0
    total = sum(weights)
    if total > 0:
        return [w / total for w in weights]
    else:
        return [1.0 / num_bins] * num_bins


def calculate_uniform_weights(num_bins: int) -> list[float]:
    """
    Generate uniform weights (equal distribution).
    
    Args:
        num_bins: Number of bins
        
    Returns:
        Equal weights that sum to 1.0
    """
    if num_bins <= 0:
        return []
    return [1.0 / num_bins] * num_bins


def calculate_distribution_weights(
    num_bins: int,
    distribution_shape: str = "uniform",
    slope_direction: str = "ascending", 
    slope_steepness: float = 1.0,
    curve_type: str = "exponential",
    curve_exponent: float = 2.0,
    custom_distribution: list[float] | None = None,
) -> list[float]:
    """
    Calculate distribution weights based on strategy parameters.
    
    Args:
        num_bins: Number of bins
        distribution_shape: Shape type ("uniform", "slope", "curve", "custom")
        slope_direction: Direction for slopes ("ascending", "descending", "peak", "valley")
        slope_steepness: Gradient multiplier for slopes (0.1-5.0)
        curve_type: Type of curve ("exponential", "logarithmic", "bell", "u_curve")
        curve_exponent: Curve steepness parameter (0.1-10.0)
        custom_distribution: User-defined weights
        
    Returns:
        Normalized weights that sum to 1.0
    """
    if distribution_shape == "uniform":
        return calculate_uniform_weights(num_bins)
    elif distribution_shape == "slope":
        return calculate_slope_weights(num_bins, slope_direction, slope_steepness)
    elif distribution_shape == "curve":
        return calculate_curve_weights(num_bins, curve_type, curve_exponent)
    elif distribution_shape == "custom" and custom_distribution:
        return calculate_custom_weights(custom_distribution, num_bins)
    else:
        # Fallback to uniform if invalid parameters
        return calculate_uniform_weights(num_bins)


def calculate_custom_weights(
    custom_distribution: list[float],
    num_bins: int | None = None,
) -> list[float]:
    """
    Use user-provided custom weights.
    
    Args:
        custom_distribution: User-defined weights
        num_bins: Expected number of bins (for validation)
        
    Returns:
        Normalized custom weights
    """
    if not custom_distribution:
        raise ValueError("Custom distribution cannot be empty")
    
    # Validate all weights are non-negative
    if any(w < 0 for w in custom_distribution):
        raise ValueError("Custom distribution weights must be non-negative")
    
    # Validate against expected bin count if provided
    if num_bins is not None and len(custom_distribution) != num_bins:
        raise ValueError(
            f"Custom distribution has {len(custom_distribution)} weights "
            f"but expected {num_bins} bins"
        )
    
    # Check if all zeros
    total = sum(custom_distribution)
    if total == 0:
        raise ValueError("Custom distribution weights cannot all be zero")
    
    # Normalize to sum to 1.0
    return [w / total for w in custom_distribution]


def apply_shape_to_allocations(
    allocations: list[Decimal],
    shape_type: str,
    shape_params: dict[str, Any] | None = None,
) -> list[Decimal]:
    """
    Apply distribution shape to existing allocations.
    
    Args:
        allocations: Base uniform allocations (will be modified by shape)
        shape_type: "uniform", "slope", "curve", "custom"
        shape_params: Additional parameters for shape calculation
        
    Returns:
        Shape-modified allocations that preserve total amount
    """
    if not allocations:
        return []
    
    shape_params = shape_params or {}
    num_bins = len(allocations)
    total_amount = sum(allocations)
    
    if total_amount <= 0:
        return allocations
    
    # Get shape weights
    if shape_type == "uniform":
        weights = calculate_uniform_weights(num_bins)
    elif shape_type == "slope":
        weights = calculate_slope_weights(
            num_bins,
            direction=shape_params.get("direction", "ascending"),
            steepness=shape_params.get("steepness", 1.0),
        )
    elif shape_type == "curve":
        weights = calculate_curve_weights(
            num_bins,
            curve_type=shape_params.get("curve_type", "exponential"),
            exponent=shape_params.get("exponent", 2.0),
        )
    elif shape_type == "custom":
        custom_dist = shape_params.get("custom_distribution")
        weights = calculate_custom_weights(custom_dist, num_bins)
    else:
        raise ValueError(f"Unknown shape type: {shape_type}")
    
    # Apply weights while preserving total amount
    return [Decimal(str(w)) * total_amount for w in weights]


def validate_distribution(
    distribution: list[int],
    expected_sum: int = 10**18,
    min_bin_value: int = 1,
) -> tuple[bool, str]:
    """
    Validate a distribution meets requirements.
    
    Args:
        distribution: The distribution to validate
        expected_sum: Expected sum (default: 10**18 = ONE)
        min_bin_value: Minimum value per bin
        
    Returns:
        (is_valid, error_message)
    """
    if not distribution:
        return False, "Distribution is empty"
    
    actual_sum = sum(distribution)
    if actual_sum != expected_sum:
        return False, f"Distribution sum {actual_sum} != expected {expected_sum}"
    
    # Check minimum bin values
    for i, val in enumerate(distribution):
        if val < 0:
            return False, f"Bin {i} has negative value: {val}"
        if val < min_bin_value and val != 0:
            # Allow zero for certain bin types but warn - this is just info
            pass
    
    return True, ""


def distribution_to_weights(distribution: list[int]) -> list[float]:
    """
    Convert integer distribution (summing to 10**18) to float weights.
    
    Args:
        distribution: Integer distribution
        
    Returns:
        Float weights that sum to 1.0
    """
    if not distribution:
        return []
    
    total = sum(distribution)
    if total == 0:
        return []
    
    return [d / total for d in distribution]


def apply_strategy_distribution(
    allocations: list[Decimal],
    strategy_type: str,
    settings: "Settings",
) -> list[Decimal]:
    """
    Apply strategy-specific distribution to allocations.
    
    Args:
        allocations: Base uniform allocations
        strategy_type: "narrow" or "wide"
        settings: Settings object with distribution parameters
        
    Returns:
        Strategy-modified allocations
    """
    if strategy_type == "narrow":
        params = settings.get_narrow_distribution_params()
    elif strategy_type == "wide":
        params = settings.get_wide_distribution_params()
    else:
        raise ValueError(f"Unknown strategy type: {strategy_type}")
    
    # Convert to the format expected by apply_shape_to_allocations
    shape_params = {
        "direction": params["slope_direction"],
        "steepness": params["slope_steepness"],
        "curve_type": params["curve_type"],
        "exponent": params["curve_exponent"],
    }
    
    return apply_shape_to_allocations(
        allocations,
        params["distribution_shape"],
        shape_params
    )


def get_strategy_distribution_weights(
    num_bins: int,
    strategy_type: str,
    settings: "Settings",
) -> list[float]:
    """
    Get distribution weights for a specific strategy.
    
    Args:
        num_bins: Number of bins
        strategy_type: "narrow" or "wide" 
        settings: Settings object with distribution parameters
        
    Returns:
        Normalized weights that sum to 1.0
    """
    if strategy_type == "narrow":
        params = settings.get_narrow_distribution_params()
    elif strategy_type == "wide":
        params = settings.get_wide_distribution_params()
    else:
        raise ValueError(f"Unknown strategy type: {strategy_type}")
    
    return calculate_distribution_weights(
        num_bins=num_bins,
        distribution_shape=params["distribution_shape"],
        slope_direction=params["slope_direction"],
        slope_steepness=params["slope_steepness"],
        curve_type=params["curve_type"],
        curve_exponent=params["curve_exponent"],
    )


# Constants for CLI help text
SHAPE_CHOICES = ["uniform", "slope", "curve", "custom"]
SLOPE_DIRECTION_CHOICES = ["ascending", "descending", "peak", "valley"]
CURVE_TYPE_CHOICES = ["exponential", "logarithmic", "bell", "u_curve"]

# Default values
DEFAULT_SHAPE = "uniform"
DEFAULT_SLOPE_DIRECTION = "ascending"
DEFAULT_SLOPE_STEEPNESS = 1.0
DEFAULT_CURVE_TYPE = "exponential"
DEFAULT_CURVE_EXPONENT = 2.0

# Validation bounds
MIN_TARGET_MNT_RATIO_BPS = 1_000  # 10%
MAX_TARGET_MNT_RATIO_BPS = 9_000  # 90%
MIN_SLOPE_STEEPNESS = 0.1
MAX_SLOPE_STEEPNESS = 5.0
MIN_CURVE_EXPONENT = 0.1
MAX_CURVE_EXPONENT = 10.0
