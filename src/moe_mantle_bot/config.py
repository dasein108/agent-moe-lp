from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

from .constants import (
    MANTLE_MAINNET_CHAIN_ID,
    MANTLE_MAINNET_RPC_URL,
    MOE_LB_FACTORY,
    MOE_LB_ROUTER,
    WMNT_USDT_POOL,
    USDT_TOKEN,
    WMNT_TOKEN,
)


def _load_env() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None or value == "" else int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None or value == "" else float(value)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    rpc_url: str = MANTLE_MAINNET_RPC_URL
    chain_id: int = MANTLE_MAINNET_CHAIN_ID
    pool_address: str = WMNT_USDT_POOL
    wmnt_address: str = WMNT_TOKEN
    usdt_address: str = USDT_TOKEN
    moe_factory_address: str = MOE_LB_FACTORY
    moe_router_address: str = MOE_LB_ROUTER
    bin_count: int = 10
    position_upside_pct: float = 0.55  # fraction of bins above active (0.5=centered, 0.7=bullish)
    # Smart MNT accumulation
    mnt_accumulation_enabled: bool = False
    accum_rsi_low: float = 45.0       # accumulate when RSI ≤ this
    accum_rsi_deep: float = 30.0      # accumulate MORE when RSI ≤ this
    accum_pct_normal: float = 0.20    # holdback 20% when oversold
    accum_pct_deep: float = 0.30      # holdback 30% when deeply oversold
    release_rsi_partial: float = 65.0 # partial release when RSI ≥ this
    release_rsi_full: float = 75.0    # full release when RSI ≥ this + profit
    release_partial_pct: float = 0.30 # sell 30% on partial
    release_profit_threshold: float = 10.0  # require 10% profit for full release
    accum_max_portfolio_pct: float = 0.30   # emergency cap 30%
    pair_version: int = 3
    slippage_bps: int = 100
    id_slippage: int = 5
    tx_deadline_seconds: int = 1800
    wallet_address: str | None = None
    private_key: str | None = None
    keystore_password: str | None = None
    wallet_file: Path = Path("wallet.json")
    debug: bool = False
    log_scan_start_block: int = 0
    log_scan_chunk_size: int = 100
    data_dir: Path = Path("data")
    # LP Distribution Configuration (Global/Legacy)
    distribution_shape: str = "uniform"  # "uniform", "slope", "curve", "custom"
    slope_direction: str = "ascending"  # "ascending", "descending", "peak", "valley"
    slope_steepness: float = 1.0  # Linear gradient multiplier (0.1-5.0)
    curve_type: str = "exponential"  # "exponential", "logarithmic", "bell", "u_curve"
    curve_exponent: float = 2.0  # Curve steepness parameter
    custom_distribution: list[float] | None = None  # User-defined weights per bin
    
    # Strategy-Specific LP Distribution Configuration
    # Narrow-Range Strategy Distribution (for reward sniping)
    narrow_distribution_shape: str = "slope"  # Shape for narrow-range positions
    narrow_slope_direction: str = "peak"      # Peak = max liquidity at center
    narrow_slope_steepness: float = 2.5       # Strong center concentration
    narrow_curve_type: str = "logarithmic"    # Alternative curve type for narrow
    narrow_curve_exponent: float = 1.5        # Curve steepness for narrow
    
    # Wide-Range Strategy Distribution (for fee farming)
    wide_distribution_shape: str = "uniform"  # Shape for wide-range positions
    wide_slope_direction: str = "ascending"   # Direction for slope-based wide distribution
    wide_slope_steepness: float = 1.0         # Slope steepness for wide
    wide_curve_type: str = "bell"             # Curve type for wide-range
    wide_curve_exponent: float = 1.0          # Moderate curve for wide
    # Portfolio Allocation Configuration
    target_mnt_ratio_bps: int = 5_000  # Target MNT allocation in basis points (5000 = 50%)
    reentry_cooldown_seconds: int = 900  # 15 min cooldown after exit before re-entering
    reentry_skip_rebalance: bool = False  # Rebalance inventory on re-entry for centered positions
    reentry_policy_exit_down: str = "continuation_safe"
    reentry_policy_exit_up: str = "continuation_safe"
    reentry_policy_neutral: str = "continuation_safe"
    reentry_partial_exit_down_mnt_ratio_bps: int = 3_000
    reentry_partial_exit_up_mnt_ratio_bps: int = 7_000
    reentry_neutral_mnt_ratio_bps: int = 5_000
    min_reentry_swap_usdt: float = 10.0
    min_reentry_confidence: float = 0.8
    max_reentry_swap_pct: float = 0.35
    reentry_rsi_filter_enabled: bool = False
    reentry_rsi_exit_down_threshold: float = 30.0
    reentry_rsi_exit_up_threshold: float = 70.0
    reentry_trend_filter_enabled: bool = False
    reentry_trend_fast_ema: int = 20
    reentry_trend_slow_ema: int = 50
    reentry_trend_flattening_lookback: int = 5
    reentry_ensemble_enabled: bool = False
    reentry_regime_aware_ratio_enabled: bool = False
    reentry_adaptive_ratio_enabled: bool = False
    reentry_adaptive_ratio_lookback: int = 5
    reentry_adaptive_ratio_min_samples: int = 3
    reentry_adaptive_ratio_step_bps: int = 500
    reentry_adaptive_low_vol_width_pct: float = 1.0
    reentry_adaptive_high_vol_width_pct: float = 2.0
    reentry_adaptive_positive_pnl_usdt: float = 1.0
    reentry_adaptive_negative_pnl_usdt: float = -1.0
    reentry_adaptive_min_in_range_ratio: float = 0.5
    reentry_adaptive_low_fill_pct: float = 60.0
    reentry_threshold_calibration_enabled: bool = False
    reentry_threshold_calibration_lookback: int = 5
    reentry_threshold_calibration_min_samples: int = 3
    reentry_threshold_confidence_step: float = 0.05
    reentry_threshold_swap_pct_step: float = 0.05
    reentry_threshold_min_swap_usdt_step: float = 2.5
    reentry_threshold_confidence_floor: float = 0.6
    reentry_threshold_confidence_ceiling: float = 0.95
    reentry_threshold_max_swap_pct_floor: float = 0.2
    reentry_threshold_max_swap_pct_ceiling: float = 0.5
    reentry_threshold_min_swap_usdt_floor: float = 5.0
    reentry_threshold_min_swap_usdt_ceiling: float = 25.0
    # VWAP guards: block swaps at unfavorable prices
    reentry_vwap_guard_enabled: bool = True
    reentry_vwap_dead_zone_pct: float = 2.0  # Only swap when price is 2%+ away from 24h VWAP
    reentry_swap_cooldown_seconds: int = 14400  # 4h between swaps in same direction
    reentry_max_swap_usdt: float = 200.0  # Max $200 per swap (size cap)

    wide_entry_inventory_gate_enabled: bool = True
    wide_entry_max_mnt_weight_bps: int = 8_500
    wide_entry_min_usdt: float = 10.0
    wide_entry_rebalance_enabled: bool = True
    wide_entry_rebalance_target_mnt_ratio_bps: int = 7_000
    wide_entry_rebalance_tolerance_bps: int = 500
    wide_entry_rebalance_min_trade_usdt: float = 10.0
    wide_entry_rebalance_max_swap_pct: float = 0.35
    adaptive_gas_reserve_enabled: bool = True
    adaptive_gas_reserve_lookback: int = 5
    adaptive_gas_reserve_multiplier: float = 3.0
    adaptive_gas_reserve_default_tx_mnt: float = 1.0
    adaptive_gas_reserve_bin_buffer_mnt: float = 0.03
    
    # Minimum position size in USDT to avoid creating uneconomical positions
    min_position_size_usdt: float = 3.0  # $3 minimum (lowered — MNT cap limits effective fill in x_only mode)
    min_top_up_fill_usdt: float = 5.0     # $5 minimum for top-up fill (lower bar since position already exists)
    min_top_up_free_value_usdt: float = 20.0  # Skip top-up planning if free value below this
    wide_confidence_threshold: float = 0.5  # Keltner confidence needed for auto-wide (was 0.8, lowered for MNT/USDT)
    # Out-of-range tolerance floor/cap (bins) for the adaptive OOR hold. Higher =
    # more passive: the bot holds through ordinary drift and only re-centers on
    # extreme sustained moves. Backtest (binStep-100 USDT0, 4mo) showed raising
    # these to ~30/60 eliminates value-destroying re-centers at price extremes.
    # Tolerance is in BINS; price-% ≈ bins × bin_step/100 — tune per pool.
    oor_tolerance_bins: int = 15
    oor_tolerance_cap_bins: int = 40
    gas_reserve_mnt: float = 2.0  # Mantle L2 gas is cheap; small native reserve suffices
    native_estimate_headroom_mnt: float = 5.0  # Extra native MNT buffer for estimateGas simulation
    max_budget_pct: float = 0.80     # Max fraction of wallet balance usable for LP
    max_native_lp_value_mnt: float = 15_000.0  # addLiquidityNATIVE safety cap for large msg.value
    mnt_min_balance: float = 0.0     # Min native MNT; if below, swap to get 2x this amount (0 = use gas_reserve_mnt)
    # Dual-mode capital split (used with --strategy dual)
    narrow_capital_pct: float = 0.5  # Fraction of capital for narrow position
    wide_capital_pct: float = 0.5    # Fraction of capital for wide position

    @classmethod
    def from_env(cls) -> "Settings":
        _load_env()
        
        # Validate target_mnt_ratio_bps if provided
        target_mnt_ratio = _env_int("TARGET_MNT_RATIO_BPS", 5_000)
        if target_mnt_ratio < 1_000 or target_mnt_ratio > 9_000:
            raise ValueError("TARGET_MNT_RATIO_BPS must be between 1000 (10%) and 9000 (90%)")

        reentry_cooldown_seconds = _env_int("REENTRY_COOLDOWN_SECONDS", 900)
        reentry_skip_rebalance = _env_bool("REENTRY_SKIP_REBALANCE", False)

        valid_reentry_policies = {"continuation_safe", "partial_rebalance", "neutral_rebalance"}
        reentry_policy_exit_down = os.getenv("REENTRY_POLICY_EXIT_DOWN", "continuation_safe")
        reentry_policy_exit_up = os.getenv("REENTRY_POLICY_EXIT_UP", "continuation_safe")
        reentry_policy_neutral = os.getenv("REENTRY_POLICY_NEUTRAL", "continuation_safe")
        for name, value in {
            "REENTRY_POLICY_EXIT_DOWN": reentry_policy_exit_down,
            "REENTRY_POLICY_EXIT_UP": reentry_policy_exit_up,
            "REENTRY_POLICY_NEUTRAL": reentry_policy_neutral,
        }.items():
            if value not in valid_reentry_policies:
                raise ValueError(f"{name} must be one of {valid_reentry_policies}")

        reentry_partial_exit_down_mnt_ratio_bps = _env_int("REENTRY_PARTIAL_EXIT_DOWN_MNT_RATIO_BPS", 3_000)
        reentry_partial_exit_up_mnt_ratio_bps = _env_int("REENTRY_PARTIAL_EXIT_UP_MNT_RATIO_BPS", 7_000)
        reentry_neutral_mnt_ratio_bps = _env_int("REENTRY_NEUTRAL_MNT_RATIO_BPS", 5_000)
        for name, value in {
            "REENTRY_PARTIAL_EXIT_DOWN_MNT_RATIO_BPS": reentry_partial_exit_down_mnt_ratio_bps,
            "REENTRY_PARTIAL_EXIT_UP_MNT_RATIO_BPS": reentry_partial_exit_up_mnt_ratio_bps,
            "REENTRY_NEUTRAL_MNT_RATIO_BPS": reentry_neutral_mnt_ratio_bps,
        }.items():
            if value < 0 or value > 10_000:
                raise ValueError(f"{name} must be between 0 and 10000")

        min_reentry_swap_usdt_env = os.getenv("MIN_REENTRY_SWAP_USDT")
        min_reentry_swap_usdt = float(min_reentry_swap_usdt_env) if min_reentry_swap_usdt_env else 10.0
        if min_reentry_swap_usdt < 0 or min_reentry_swap_usdt > 10_000:
            raise ValueError("MIN_REENTRY_SWAP_USDT must be between 0 and 10000")

        min_reentry_confidence_env = os.getenv("MIN_REENTRY_CONFIDENCE")
        min_reentry_confidence = float(min_reentry_confidence_env) if min_reentry_confidence_env else 0.8
        if min_reentry_confidence < 0 or min_reentry_confidence > 1:
            raise ValueError("MIN_REENTRY_CONFIDENCE must be between 0 and 1")

        max_reentry_swap_pct_env = os.getenv("MAX_REENTRY_SWAP_PCT")
        max_reentry_swap_pct = float(max_reentry_swap_pct_env) if max_reentry_swap_pct_env else 0.35
        if max_reentry_swap_pct < 0 or max_reentry_swap_pct > 1:
            raise ValueError("MAX_REENTRY_SWAP_PCT must be between 0 and 1")

        reentry_rsi_filter_enabled = _env_bool("REENTRY_RSI_FILTER_ENABLED", False)
        reentry_rsi_exit_down_threshold = float(
            os.getenv("REENTRY_RSI_EXIT_DOWN_THRESHOLD", "30.0")
        )
        if reentry_rsi_exit_down_threshold < 0 or reentry_rsi_exit_down_threshold > 100:
            raise ValueError("REENTRY_RSI_EXIT_DOWN_THRESHOLD must be between 0 and 100")

        reentry_rsi_exit_up_threshold = float(
            os.getenv("REENTRY_RSI_EXIT_UP_THRESHOLD", "70.0")
        )
        if reentry_rsi_exit_up_threshold < 0 or reentry_rsi_exit_up_threshold > 100:
            raise ValueError("REENTRY_RSI_EXIT_UP_THRESHOLD must be between 0 and 100")

        reentry_trend_filter_enabled = _env_bool("REENTRY_TREND_FILTER_ENABLED", False)
        reentry_trend_fast_ema = _env_int("REENTRY_TREND_FAST_EMA", 20)
        reentry_trend_slow_ema = _env_int("REENTRY_TREND_SLOW_EMA", 50)
        reentry_trend_flattening_lookback = _env_int("REENTRY_TREND_FLATTENING_LOOKBACK", 5)
        if reentry_trend_fast_ema < 2 or reentry_trend_fast_ema > 200:
            raise ValueError("REENTRY_TREND_FAST_EMA must be between 2 and 200")
        if reentry_trend_slow_ema < 3 or reentry_trend_slow_ema > 400:
            raise ValueError("REENTRY_TREND_SLOW_EMA must be between 3 and 400")
        if reentry_trend_fast_ema >= reentry_trend_slow_ema:
            raise ValueError("REENTRY_TREND_FAST_EMA must be smaller than REENTRY_TREND_SLOW_EMA")
        if reentry_trend_flattening_lookback < 1 or reentry_trend_flattening_lookback > 100:
            raise ValueError("REENTRY_TREND_FLATTENING_LOOKBACK must be between 1 and 100")

        reentry_ensemble_enabled = _env_bool("REENTRY_ENSEMBLE_ENABLED", False)
        reentry_regime_aware_ratio_enabled = _env_bool("REENTRY_REGIME_AWARE_RATIO_ENABLED", False)
        reentry_adaptive_ratio_enabled = _env_bool("REENTRY_ADAPTIVE_RATIO_ENABLED", False)
        reentry_adaptive_ratio_lookback = _env_int("REENTRY_ADAPTIVE_RATIO_LOOKBACK", 5)
        reentry_adaptive_ratio_min_samples = _env_int("REENTRY_ADAPTIVE_RATIO_MIN_SAMPLES", 3)
        reentry_adaptive_ratio_step_bps = _env_int("REENTRY_ADAPTIVE_RATIO_STEP_BPS", 500)
        reentry_adaptive_low_vol_width_pct = float(os.getenv("REENTRY_ADAPTIVE_LOW_VOL_WIDTH_PCT", "1.0"))
        reentry_adaptive_high_vol_width_pct = float(os.getenv("REENTRY_ADAPTIVE_HIGH_VOL_WIDTH_PCT", "2.0"))
        reentry_adaptive_positive_pnl_usdt = float(os.getenv("REENTRY_ADAPTIVE_POSITIVE_PNL_USDT", "1.0"))
        reentry_adaptive_negative_pnl_usdt = float(os.getenv("REENTRY_ADAPTIVE_NEGATIVE_PNL_USDT", "-1.0"))
        reentry_adaptive_min_in_range_ratio = float(os.getenv("REENTRY_ADAPTIVE_MIN_IN_RANGE_RATIO", "0.5"))
        reentry_adaptive_low_fill_pct = float(os.getenv("REENTRY_ADAPTIVE_LOW_FILL_PCT", "60.0"))
        reentry_threshold_calibration_enabled = _env_bool("REENTRY_THRESHOLD_CALIBRATION_ENABLED", False)
        reentry_threshold_calibration_lookback = _env_int("REENTRY_THRESHOLD_CALIBRATION_LOOKBACK", 5)
        reentry_threshold_calibration_min_samples = _env_int("REENTRY_THRESHOLD_CALIBRATION_MIN_SAMPLES", 3)
        reentry_threshold_confidence_step = float(os.getenv("REENTRY_THRESHOLD_CONFIDENCE_STEP", "0.05"))
        reentry_threshold_swap_pct_step = float(os.getenv("REENTRY_THRESHOLD_SWAP_PCT_STEP", "0.05"))
        reentry_threshold_min_swap_usdt_step = float(os.getenv("REENTRY_THRESHOLD_MIN_SWAP_USDT_STEP", "2.5"))
        reentry_threshold_confidence_floor = float(os.getenv("REENTRY_THRESHOLD_CONFIDENCE_FLOOR", "0.6"))
        reentry_threshold_confidence_ceiling = float(os.getenv("REENTRY_THRESHOLD_CONFIDENCE_CEILING", "0.95"))
        reentry_threshold_max_swap_pct_floor = float(os.getenv("REENTRY_THRESHOLD_MAX_SWAP_PCT_FLOOR", "0.2"))
        reentry_threshold_max_swap_pct_ceiling = float(os.getenv("REENTRY_THRESHOLD_MAX_SWAP_PCT_CEILING", "0.5"))
        reentry_threshold_min_swap_usdt_floor = float(os.getenv("REENTRY_THRESHOLD_MIN_SWAP_USDT_FLOOR", "5.0"))
        reentry_threshold_min_swap_usdt_ceiling = float(os.getenv("REENTRY_THRESHOLD_MIN_SWAP_USDT_CEILING", "25.0"))
        if reentry_adaptive_ratio_lookback < 1 or reentry_adaptive_ratio_lookback > 100:
            raise ValueError("REENTRY_ADAPTIVE_RATIO_LOOKBACK must be between 1 and 100")
        if reentry_adaptive_ratio_min_samples < 1 or reentry_adaptive_ratio_min_samples > 100:
            raise ValueError("REENTRY_ADAPTIVE_RATIO_MIN_SAMPLES must be between 1 and 100")
        if reentry_adaptive_ratio_step_bps < 0 or reentry_adaptive_ratio_step_bps > 5000:
            raise ValueError("REENTRY_ADAPTIVE_RATIO_STEP_BPS must be between 0 and 5000")
        if reentry_adaptive_low_vol_width_pct < 0 or reentry_adaptive_low_vol_width_pct > 100:
            raise ValueError("REENTRY_ADAPTIVE_LOW_VOL_WIDTH_PCT must be between 0 and 100")
        if reentry_adaptive_high_vol_width_pct < 0 or reentry_adaptive_high_vol_width_pct > 100:
            raise ValueError("REENTRY_ADAPTIVE_HIGH_VOL_WIDTH_PCT must be between 0 and 100")
        if reentry_adaptive_low_vol_width_pct >= reentry_adaptive_high_vol_width_pct:
            raise ValueError("REENTRY_ADAPTIVE_LOW_VOL_WIDTH_PCT must be smaller than REENTRY_ADAPTIVE_HIGH_VOL_WIDTH_PCT")
        if reentry_adaptive_min_in_range_ratio < 0 or reentry_adaptive_min_in_range_ratio > 1:
            raise ValueError("REENTRY_ADAPTIVE_MIN_IN_RANGE_RATIO must be between 0 and 1")
        if reentry_adaptive_low_fill_pct < 0 or reentry_adaptive_low_fill_pct > 100:
            raise ValueError("REENTRY_ADAPTIVE_LOW_FILL_PCT must be between 0 and 100")
        if reentry_threshold_calibration_lookback < 1 or reentry_threshold_calibration_lookback > 100:
            raise ValueError("REENTRY_THRESHOLD_CALIBRATION_LOOKBACK must be between 1 and 100")
        if reentry_threshold_calibration_min_samples < 1 or reentry_threshold_calibration_min_samples > 100:
            raise ValueError("REENTRY_THRESHOLD_CALIBRATION_MIN_SAMPLES must be between 1 and 100")
        if reentry_threshold_confidence_step < 0 or reentry_threshold_confidence_step > 1:
            raise ValueError("REENTRY_THRESHOLD_CONFIDENCE_STEP must be between 0 and 1")
        if reentry_threshold_swap_pct_step < 0 or reentry_threshold_swap_pct_step > 1:
            raise ValueError("REENTRY_THRESHOLD_SWAP_PCT_STEP must be between 0 and 1")
        if reentry_threshold_min_swap_usdt_step < 0 or reentry_threshold_min_swap_usdt_step > 1000:
            raise ValueError("REENTRY_THRESHOLD_MIN_SWAP_USDT_STEP must be between 0 and 1000")
        if reentry_threshold_confidence_floor < 0 or reentry_threshold_confidence_floor > 1:
            raise ValueError("REENTRY_THRESHOLD_CONFIDENCE_FLOOR must be between 0 and 1")
        if reentry_threshold_confidence_ceiling < 0 or reentry_threshold_confidence_ceiling > 1:
            raise ValueError("REENTRY_THRESHOLD_CONFIDENCE_CEILING must be between 0 and 1")
        if reentry_threshold_confidence_floor > reentry_threshold_confidence_ceiling:
            raise ValueError("REENTRY_THRESHOLD_CONFIDENCE_FLOOR must be <= REENTRY_THRESHOLD_CONFIDENCE_CEILING")
        if reentry_threshold_max_swap_pct_floor < 0 or reentry_threshold_max_swap_pct_floor > 1:
            raise ValueError("REENTRY_THRESHOLD_MAX_SWAP_PCT_FLOOR must be between 0 and 1")
        if reentry_threshold_max_swap_pct_ceiling < 0 or reentry_threshold_max_swap_pct_ceiling > 1:
            raise ValueError("REENTRY_THRESHOLD_MAX_SWAP_PCT_CEILING must be between 0 and 1")
        if reentry_threshold_max_swap_pct_floor > reentry_threshold_max_swap_pct_ceiling:
            raise ValueError("REENTRY_THRESHOLD_MAX_SWAP_PCT_FLOOR must be <= REENTRY_THRESHOLD_MAX_SWAP_PCT_CEILING")
        if reentry_threshold_min_swap_usdt_floor < 0 or reentry_threshold_min_swap_usdt_floor > 10_000:
            raise ValueError("REENTRY_THRESHOLD_MIN_SWAP_USDT_FLOOR must be between 0 and 10000")
        if reentry_threshold_min_swap_usdt_ceiling < 0 or reentry_threshold_min_swap_usdt_ceiling > 10_000:
            raise ValueError("REENTRY_THRESHOLD_MIN_SWAP_USDT_CEILING must be between 0 and 10000")
        if reentry_threshold_min_swap_usdt_floor > reentry_threshold_min_swap_usdt_ceiling:
            raise ValueError("REENTRY_THRESHOLD_MIN_SWAP_USDT_FLOOR must be <= REENTRY_THRESHOLD_MIN_SWAP_USDT_CEILING")
        reentry_vwap_guard_enabled = _env_bool("REENTRY_VWAP_GUARD_ENABLED", True)
        reentry_vwap_dead_zone_pct = float(os.getenv("REENTRY_VWAP_DEAD_ZONE_PCT", "2.0"))
        reentry_swap_cooldown_seconds = _env_int("REENTRY_SWAP_COOLDOWN_SECONDS", 14400)
        reentry_max_swap_usdt = float(os.getenv("REENTRY_MAX_SWAP_USDT", "200.0"))

        wide_entry_inventory_gate_enabled = _env_bool("WIDE_ENTRY_INVENTORY_GATE_ENABLED", True)
        wide_entry_max_mnt_weight_bps = _env_int("WIDE_ENTRY_MAX_MNT_WEIGHT_BPS", 8_500)
        wide_entry_min_usdt = float(os.getenv("WIDE_ENTRY_MIN_USDT", "10.0"))
        wide_entry_rebalance_enabled = _env_bool("WIDE_ENTRY_REBALANCE_ENABLED", True)
        wide_entry_rebalance_target_mnt_ratio_bps = _env_int("WIDE_ENTRY_REBALANCE_TARGET_MNT_RATIO_BPS", 7_000)
        wide_entry_rebalance_tolerance_bps = _env_int("WIDE_ENTRY_REBALANCE_TOLERANCE_BPS", 500)
        wide_entry_rebalance_min_trade_usdt = float(os.getenv("WIDE_ENTRY_REBALANCE_MIN_TRADE_USDT", "10.0"))
        wide_entry_rebalance_max_swap_pct = float(os.getenv("WIDE_ENTRY_REBALANCE_MAX_SWAP_PCT", "0.35"))
        adaptive_gas_reserve_enabled = _env_bool("ADAPTIVE_GAS_RESERVE_ENABLED", True)
        adaptive_gas_reserve_lookback = _env_int("ADAPTIVE_GAS_RESERVE_LOOKBACK", 5)
        adaptive_gas_reserve_multiplier = float(os.getenv("ADAPTIVE_GAS_RESERVE_MULTIPLIER", "3.0"))
        adaptive_gas_reserve_default_tx_mnt = float(os.getenv("ADAPTIVE_GAS_RESERVE_DEFAULT_TX_MNT", "1.0"))
        adaptive_gas_reserve_bin_buffer_mnt = float(os.getenv("ADAPTIVE_GAS_RESERVE_BIN_BUFFER_MNT", "0.03"))
        for name, value in {
            "WIDE_ENTRY_MAX_MNT_WEIGHT_BPS": wide_entry_max_mnt_weight_bps,
            "WIDE_ENTRY_REBALANCE_TARGET_MNT_RATIO_BPS": wide_entry_rebalance_target_mnt_ratio_bps,
            "WIDE_ENTRY_REBALANCE_TOLERANCE_BPS": wide_entry_rebalance_tolerance_bps,
        }.items():
            if value < 0 or value > 10_000:
                raise ValueError(f"{name} must be between 0 and 10000")
        if wide_entry_min_usdt < 0 or wide_entry_min_usdt > 10_000:
            raise ValueError("WIDE_ENTRY_MIN_USDT must be between 0 and 10000")
        if wide_entry_rebalance_min_trade_usdt < 0 or wide_entry_rebalance_min_trade_usdt > 10_000:
            raise ValueError("WIDE_ENTRY_REBALANCE_MIN_TRADE_USDT must be between 0 and 10000")
        if wide_entry_rebalance_max_swap_pct <= 0 or wide_entry_rebalance_max_swap_pct > 1:
            raise ValueError("WIDE_ENTRY_REBALANCE_MAX_SWAP_PCT must be between 0 and 1")
        if adaptive_gas_reserve_lookback < 1 or adaptive_gas_reserve_lookback > 100:
            raise ValueError("ADAPTIVE_GAS_RESERVE_LOOKBACK must be between 1 and 100")
        if adaptive_gas_reserve_multiplier < 0 or adaptive_gas_reserve_multiplier > 100:
            raise ValueError("ADAPTIVE_GAS_RESERVE_MULTIPLIER must be between 0 and 100")
        if adaptive_gas_reserve_default_tx_mnt < 0 or adaptive_gas_reserve_default_tx_mnt > 1000:
            raise ValueError("ADAPTIVE_GAS_RESERVE_DEFAULT_TX_MNT must be between 0 and 1000")
        if adaptive_gas_reserve_bin_buffer_mnt < 0 or adaptive_gas_reserve_bin_buffer_mnt > 100:
            raise ValueError("ADAPTIVE_GAS_RESERVE_BIN_BUFFER_MNT must be between 0 and 100")
        
        # Validate min_position_size_usdt if provided
        min_position_size_env = os.getenv("MIN_POSITION_SIZE_USDT")
        min_position_size = float(min_position_size_env) if min_position_size_env else 3.0
        if min_position_size < 1.0 or min_position_size > 1000.0:
            raise ValueError("MIN_POSITION_SIZE_USDT must be between 1.0 and 1000.0")

        min_top_up_fill_env = os.getenv("MIN_TOP_UP_FILL_USDT")
        min_top_up_fill_usdt = float(min_top_up_fill_env) if min_top_up_fill_env else 5.0

        min_top_up_free_env = os.getenv("MIN_TOP_UP_FREE_VALUE_USDT")
        min_top_up_free_value_usdt = float(min_top_up_free_env) if min_top_up_free_env else 20.0

        oor_tolerance_bins = _env_int("OOR_TOLERANCE_BINS", 15)
        oor_tolerance_cap_bins = _env_int("OOR_TOLERANCE_CAP_BINS", 40)
        if oor_tolerance_cap_bins < oor_tolerance_bins:
            raise ValueError("OOR_TOLERANCE_CAP_BINS must be >= OOR_TOLERANCE_BINS")

        wide_conf_env = os.getenv("WIDE_CONFIDENCE_THRESHOLD")
        wide_confidence_threshold = float(wide_conf_env) if wide_conf_env else 0.5
        if wide_confidence_threshold < 0.1 or wide_confidence_threshold > 1.0:
            raise ValueError("WIDE_CONFIDENCE_THRESHOLD must be between 0.1 and 1.0")

        gas_reserve_env = os.getenv("GAS_RESERVE_MNT")
        gas_reserve_mnt = float(gas_reserve_env) if gas_reserve_env else 2.0  # Mantle L2 gas is cheap; small native reserve suffices
        if gas_reserve_mnt < 1.0 or gas_reserve_mnt > 10000.0:
            raise ValueError("GAS_RESERVE_MNT must be between 1.0 and 10000.0")

        max_budget_pct_env = os.getenv("MAX_BUDGET_PCT")
        max_budget_pct = float(max_budget_pct_env) if max_budget_pct_env else 0.80
        if max_budget_pct < 0.1 or max_budget_pct > 1.0:
            raise ValueError("MAX_BUDGET_PCT must be between 0.1 and 1.0")

        mnt_min_balance_env = os.getenv("MNT_MIN_BALANCE")
        mnt_min_balance = float(mnt_min_balance_env) if mnt_min_balance_env else 0.0

        # Validate distribution_shape
        valid_shapes = {"uniform", "slope", "curve", "custom"}
        distribution_shape = os.getenv("DISTRIBUTION_SHAPE", "uniform")
        if distribution_shape not in valid_shapes:
            raise ValueError(f"DISTRIBUTION_SHAPE must be one of {valid_shapes}")
        
        # Validate slope_direction
        valid_slope_directions = {"ascending", "descending", "peak", "valley"}
        slope_direction = os.getenv("SLOPE_DIRECTION", "ascending")
        if slope_direction not in valid_slope_directions:
            raise ValueError(f"SLOPE_DIRECTION must be one of {valid_slope_directions}")
        
        # Validate curve_type
        valid_curve_types = {"exponential", "logarithmic", "bell", "u_curve"}
        curve_type = os.getenv("CURVE_TYPE", "exponential")
        if curve_type not in valid_curve_types:
            raise ValueError(f"CURVE_TYPE must be one of {valid_curve_types}")
        
        # Validate slope_steepness if provided
        slope_steepness_env = os.getenv("SLOPE_STEEPNESS")
        slope_steepness = float(slope_steepness_env) if slope_steepness_env else 1.0
        if slope_steepness < 0.1 or slope_steepness > 5.0:
            raise ValueError("SLOPE_STEEPNESS must be between 0.1 and 5.0")
        
        # Validate curve_exponent if provided
        curve_exponent_env = os.getenv("CURVE_EXPONENT")
        curve_exponent = float(curve_exponent_env) if curve_exponent_env else 2.0
        if curve_exponent < 0.1 or curve_exponent > 10.0:
            raise ValueError("CURVE_EXPONENT must be between 0.1 and 10.0")
        
        # Parse custom_distribution if provided
        custom_dist_str = os.getenv("CUSTOM_DISTRIBUTION")
        custom_distribution = None
        if custom_dist_str:
            try:
                custom_distribution = [float(x.strip()) for x in custom_dist_str.split(",")]
            except ValueError:
                raise ValueError("CUSTOM_DISTRIBUTION must be comma-separated floats")
        
        # Strategy-Specific Distribution Parameters
        # Narrow-Range Parameters
        narrow_distribution_shape = os.getenv("NARROW_DISTRIBUTION_SHAPE", "slope")
        if narrow_distribution_shape not in valid_shapes:
            raise ValueError(f"NARROW_DISTRIBUTION_SHAPE must be one of {valid_shapes}")
            
        narrow_slope_direction = os.getenv("NARROW_SLOPE_DIRECTION", "peak")
        if narrow_slope_direction not in valid_slope_directions:
            raise ValueError(f"NARROW_SLOPE_DIRECTION must be one of {valid_slope_directions}")
            
        narrow_curve_type = os.getenv("NARROW_CURVE_TYPE", "logarithmic")
        if narrow_curve_type not in valid_curve_types:
            raise ValueError(f"NARROW_CURVE_TYPE must be one of {valid_curve_types}")
            
        # Narrow steepness/exponent
        narrow_slope_steepness_env = os.getenv("NARROW_SLOPE_STEEPNESS")
        narrow_slope_steepness = float(narrow_slope_steepness_env) if narrow_slope_steepness_env else 2.5
        if narrow_slope_steepness < 0.1 or narrow_slope_steepness > 5.0:
            raise ValueError("NARROW_SLOPE_STEEPNESS must be between 0.1 and 5.0")
            
        narrow_curve_exponent_env = os.getenv("NARROW_CURVE_EXPONENT")
        narrow_curve_exponent = float(narrow_curve_exponent_env) if narrow_curve_exponent_env else 1.5
        if narrow_curve_exponent < 0.1 or narrow_curve_exponent > 10.0:
            raise ValueError("NARROW_CURVE_EXPONENT must be between 0.1 and 10.0")
        
        # Wide-Range Parameters  
        wide_distribution_shape = os.getenv("WIDE_DISTRIBUTION_SHAPE", "uniform")
        if wide_distribution_shape not in valid_shapes:
            raise ValueError(f"WIDE_DISTRIBUTION_SHAPE must be one of {valid_shapes}")
            
        wide_slope_direction = os.getenv("WIDE_SLOPE_DIRECTION", "ascending")
        if wide_slope_direction not in valid_slope_directions:
            raise ValueError(f"WIDE_SLOPE_DIRECTION must be one of {valid_slope_directions}")
            
        wide_curve_type = os.getenv("WIDE_CURVE_TYPE", "bell")
        if wide_curve_type not in valid_curve_types:
            raise ValueError(f"WIDE_CURVE_TYPE must be one of {valid_curve_types}")
            
        # Wide steepness/exponent
        wide_slope_steepness_env = os.getenv("WIDE_SLOPE_STEEPNESS")
        wide_slope_steepness = float(wide_slope_steepness_env) if wide_slope_steepness_env else 1.0
        if wide_slope_steepness < 0.1 or wide_slope_steepness > 5.0:
            raise ValueError("WIDE_SLOPE_STEEPNESS must be between 0.1 and 5.0")
            
        wide_curve_exponent_env = os.getenv("WIDE_CURVE_EXPONENT")
        wide_curve_exponent = float(wide_curve_exponent_env) if wide_curve_exponent_env else 1.0
        if wide_curve_exponent < 0.1 or wide_curve_exponent > 10.0:
            raise ValueError("WIDE_CURVE_EXPONENT must be between 0.1 and 10.0")

        # Dual-mode capital split (used with --strategy dual)
        narrow_capital_pct_env = os.getenv("NARROW_CAPITAL_PCT")
        narrow_capital_pct = float(narrow_capital_pct_env) if narrow_capital_pct_env else 0.5
        wide_capital_pct_env = os.getenv("WIDE_CAPITAL_PCT")
        wide_capital_pct = float(wide_capital_pct_env) if wide_capital_pct_env else 0.5
        return cls(
            rpc_url=os.getenv("MANTLE_RPC_URL", MANTLE_MAINNET_RPC_URL),
            chain_id=_env_int("CHAIN_ID", MANTLE_MAINNET_CHAIN_ID),
            pool_address=os.getenv("POOL_ADDRESS", WMNT_USDT_POOL),
            wmnt_address=os.getenv("WMNT_ADDRESS", WMNT_TOKEN),
            usdt_address=os.getenv("USDT_ADDRESS", USDT_TOKEN),
            moe_factory_address=os.getenv("MOE_FACTORY_ADDRESS", MOE_LB_FACTORY),
            moe_router_address=os.getenv("MOE_ROUTER_ADDRESS", MOE_LB_ROUTER),
            bin_count=_env_int("BIN_COUNT", 10),
            position_upside_pct=_env_float("POSITION_UPSIDE_PCT", 0.55),
            mnt_accumulation_enabled=_env_bool("MNT_ACCUMULATION_ENABLED", False),
            accum_rsi_low=_env_float("ACCUM_RSI_LOW", 45.0),
            accum_rsi_deep=_env_float("ACCUM_RSI_DEEP", 30.0),
            accum_pct_normal=_env_float("ACCUM_PCT_NORMAL", 0.20),
            accum_pct_deep=_env_float("ACCUM_PCT_DEEP", 0.30),
            release_rsi_partial=_env_float("RELEASE_RSI_PARTIAL", 65.0),
            release_rsi_full=_env_float("RELEASE_RSI_FULL", 75.0),
            release_partial_pct=_env_float("RELEASE_PARTIAL_PCT", 0.30),
            release_profit_threshold=_env_float("RELEASE_PROFIT_THRESHOLD", 10.0),
            accum_max_portfolio_pct=_env_float("ACCUM_MAX_PORTFOLIO_PCT", 0.30),
            pair_version=_env_int("PAIR_VERSION", 3),
            slippage_bps=_env_int("SLIPPAGE_BPS", 100),
            id_slippage=_env_int("ID_SLIPPAGE", 5),
            tx_deadline_seconds=_env_int("TX_DEADLINE_SECONDS", 1800),
            wallet_address=os.getenv("WALLET_ADDRESS") or None,
            private_key=os.getenv("PRIVATE_KEY") or None,
            keystore_password=os.getenv("KEYSTORE_PASSWORD") or None,
            wallet_file=Path(os.getenv("WALLET_FILE", "wallet.json")),
            debug=_env_bool("MOE_DEBUG", False),
            log_scan_start_block=_env_int("LOG_SCAN_START_BLOCK", 0),
            log_scan_chunk_size=_env_int("LOG_SCAN_CHUNK_SIZE", 100),
            distribution_shape=distribution_shape,
            slope_direction=slope_direction,
            slope_steepness=slope_steepness,
            curve_type=curve_type,
            curve_exponent=curve_exponent,
            custom_distribution=custom_distribution,
            # Strategy-specific distribution parameters
            narrow_distribution_shape=narrow_distribution_shape,
            narrow_slope_direction=narrow_slope_direction,
            narrow_slope_steepness=narrow_slope_steepness,
            narrow_curve_type=narrow_curve_type,
            narrow_curve_exponent=narrow_curve_exponent,
            wide_distribution_shape=wide_distribution_shape,
            wide_slope_direction=wide_slope_direction,
            wide_slope_steepness=wide_slope_steepness,
            wide_curve_type=wide_curve_type,
            wide_curve_exponent=wide_curve_exponent,
            target_mnt_ratio_bps=target_mnt_ratio,
            reentry_cooldown_seconds=reentry_cooldown_seconds,
            reentry_skip_rebalance=reentry_skip_rebalance,
            reentry_policy_exit_down=reentry_policy_exit_down,
            reentry_policy_exit_up=reentry_policy_exit_up,
            reentry_policy_neutral=reentry_policy_neutral,
            reentry_partial_exit_down_mnt_ratio_bps=reentry_partial_exit_down_mnt_ratio_bps,
            reentry_partial_exit_up_mnt_ratio_bps=reentry_partial_exit_up_mnt_ratio_bps,
            reentry_neutral_mnt_ratio_bps=reentry_neutral_mnt_ratio_bps,
            min_reentry_swap_usdt=min_reentry_swap_usdt,
            min_reentry_confidence=min_reentry_confidence,
            max_reentry_swap_pct=max_reentry_swap_pct,
            reentry_rsi_filter_enabled=reentry_rsi_filter_enabled,
            reentry_rsi_exit_down_threshold=reentry_rsi_exit_down_threshold,
            reentry_rsi_exit_up_threshold=reentry_rsi_exit_up_threshold,
            reentry_trend_filter_enabled=reentry_trend_filter_enabled,
            reentry_trend_fast_ema=reentry_trend_fast_ema,
            reentry_trend_slow_ema=reentry_trend_slow_ema,
            reentry_trend_flattening_lookback=reentry_trend_flattening_lookback,
            reentry_ensemble_enabled=reentry_ensemble_enabled,
            reentry_regime_aware_ratio_enabled=reentry_regime_aware_ratio_enabled,
            reentry_adaptive_ratio_enabled=reentry_adaptive_ratio_enabled,
            reentry_adaptive_ratio_lookback=reentry_adaptive_ratio_lookback,
            reentry_adaptive_ratio_min_samples=reentry_adaptive_ratio_min_samples,
            reentry_adaptive_ratio_step_bps=reentry_adaptive_ratio_step_bps,
            reentry_adaptive_low_vol_width_pct=reentry_adaptive_low_vol_width_pct,
            reentry_adaptive_high_vol_width_pct=reentry_adaptive_high_vol_width_pct,
            reentry_adaptive_positive_pnl_usdt=reentry_adaptive_positive_pnl_usdt,
            reentry_adaptive_negative_pnl_usdt=reentry_adaptive_negative_pnl_usdt,
            reentry_adaptive_min_in_range_ratio=reentry_adaptive_min_in_range_ratio,
            reentry_adaptive_low_fill_pct=reentry_adaptive_low_fill_pct,
            reentry_threshold_calibration_enabled=reentry_threshold_calibration_enabled,
            reentry_threshold_calibration_lookback=reentry_threshold_calibration_lookback,
            reentry_threshold_calibration_min_samples=reentry_threshold_calibration_min_samples,
            reentry_threshold_confidence_step=reentry_threshold_confidence_step,
            reentry_threshold_swap_pct_step=reentry_threshold_swap_pct_step,
            reentry_threshold_min_swap_usdt_step=reentry_threshold_min_swap_usdt_step,
            reentry_threshold_confidence_floor=reentry_threshold_confidence_floor,
            reentry_threshold_confidence_ceiling=reentry_threshold_confidence_ceiling,
            reentry_threshold_max_swap_pct_floor=reentry_threshold_max_swap_pct_floor,
            reentry_threshold_max_swap_pct_ceiling=reentry_threshold_max_swap_pct_ceiling,
            reentry_threshold_min_swap_usdt_floor=reentry_threshold_min_swap_usdt_floor,
            reentry_threshold_min_swap_usdt_ceiling=reentry_threshold_min_swap_usdt_ceiling,
            reentry_vwap_guard_enabled=reentry_vwap_guard_enabled,
            reentry_vwap_dead_zone_pct=reentry_vwap_dead_zone_pct,
            reentry_swap_cooldown_seconds=reentry_swap_cooldown_seconds,
            reentry_max_swap_usdt=reentry_max_swap_usdt,
            wide_entry_inventory_gate_enabled=wide_entry_inventory_gate_enabled,
            wide_entry_max_mnt_weight_bps=wide_entry_max_mnt_weight_bps,
            wide_entry_min_usdt=wide_entry_min_usdt,
            wide_entry_rebalance_enabled=wide_entry_rebalance_enabled,
            wide_entry_rebalance_target_mnt_ratio_bps=wide_entry_rebalance_target_mnt_ratio_bps,
            wide_entry_rebalance_tolerance_bps=wide_entry_rebalance_tolerance_bps,
            wide_entry_rebalance_min_trade_usdt=wide_entry_rebalance_min_trade_usdt,
            wide_entry_rebalance_max_swap_pct=wide_entry_rebalance_max_swap_pct,
            adaptive_gas_reserve_enabled=adaptive_gas_reserve_enabled,
            adaptive_gas_reserve_lookback=adaptive_gas_reserve_lookback,
            adaptive_gas_reserve_multiplier=adaptive_gas_reserve_multiplier,
            adaptive_gas_reserve_default_tx_mnt=adaptive_gas_reserve_default_tx_mnt,
            adaptive_gas_reserve_bin_buffer_mnt=adaptive_gas_reserve_bin_buffer_mnt,
            min_position_size_usdt=min_position_size,
            min_top_up_fill_usdt=min_top_up_fill_usdt,
            min_top_up_free_value_usdt=min_top_up_free_value_usdt,
            wide_confidence_threshold=wide_confidence_threshold,
            oor_tolerance_bins=oor_tolerance_bins,
            oor_tolerance_cap_bins=oor_tolerance_cap_bins,
            gas_reserve_mnt=gas_reserve_mnt,
            max_budget_pct=max_budget_pct,
            mnt_min_balance=mnt_min_balance,
            narrow_capital_pct=narrow_capital_pct,
            wide_capital_pct=wide_capital_pct,
        )

    def with_wallet(self, wallet_address: str | None) -> "Settings":
        return replace(self, wallet_address=wallet_address or self.wallet_address)
    
    def get_narrow_distribution_params(self) -> dict:
        """Get distribution parameters for narrow-range strategy."""
        return {
            'distribution_shape': self.narrow_distribution_shape,
            'slope_direction': self.narrow_slope_direction,
            'slope_steepness': self.narrow_slope_steepness,
            'curve_type': self.narrow_curve_type,
            'curve_exponent': self.narrow_curve_exponent,
        }
    
    def get_wide_distribution_params(self) -> dict:
        """Get distribution parameters for wide-range strategy.""" 
        return {
            'distribution_shape': self.wide_distribution_shape,
            'slope_direction': self.wide_slope_direction,
            'slope_steepness': self.wide_slope_steepness,
            'curve_type': self.wide_curve_type,
            'curve_exponent': self.wide_curve_exponent,
        }

    def with_wallet_file(self, wallet_file: Path | None) -> "Settings":
        return replace(self, wallet_file=wallet_file or self.wallet_file)

    def with_debug(self, debug: bool) -> "Settings":
        return replace(self, debug=debug or self.debug)
