"""Backtest configuration — all tunable assumptions in one frozen dataclass."""

from __future__ import annotations

from dataclasses import asdict, dataclass

# Stablecoin symbols treated as USDT 1:1 against the MNTUSDT price feed.
STABLE_SYMBOLS = frozenset({"USDT", "USDT0", "USD0", "USDC", "USDC.E", "USDE"})


@dataclass(frozen=True)
class BacktestConfig:
    """Inputs for a single backtest run.

    Market-state fields (bin_step, decimals, base_factor, protocol_share,
    pool_active_liquidity_usd) should be sourced from a live snapshot of the
    target pool — never hardcoded guesses. The CLI seeds them from ``moe
    snapshot`` by default.
    """

    # ── Price feed ──
    symbol: str = "MNTUSDT"
    base_interval: str = "5m"          # marking + fee accrual cadence
    lookback_days: int = 90            # how much history to fetch/cache
    # Optional explicit sim window (for walk-forward). When window_days is set,
    # the sim runs that many days ending window_end_days_ago before the latest
    # candle (warmup is taken from history preceding the window).
    window_days: int | None = None
    window_end_days_ago: float = 0.0

    # ── Pool / token geometry (from live snapshot) ──
    bin_step: int = 100                # basis points per bin
    decimals_x: int = 18               # WMNT
    decimals_y: int = 6                # USDT/USDT0
    base_factor: int = 8000            # LB static base-fee factor
    protocol_share_bps: int = 2500     # protocol cut of swap fee (bps of fee)

    # ── Position shape ──
    bin_count: int = 10                # static baseline + default initial width
    strat_initial_bin_count: int | None = None  # strategy's own initial width (else bin_count)
    capital_usd: float = 185.0
    quote_usd_target: float = 50.0     # USD on the quote(USDT) side; rest is MNT
    distribution: str = "uniform"      # uniform | slope | curve

    # ── Fee model (volume-capture) ──
    # Primary magnitude knob: estimated TOTAL daily swap volume through the pool
    # (all LPs). Bybit turnover is used only to distribute it across time. If
    # None, falls back to capture_ratio applied directly to Bybit turnover.
    pool_daily_volume_usd: float | None = None
    pool_tvl_usd: float | None = None  # seeded; default pool_daily_volume = 1x TVL/day
    capture_ratio: float = 1.0         # power-user fallback: pool_vol = bybit_turnover * capture_ratio
    pool_active_liquidity_usd: float = 117.0  # competing USD liquidity in active bin
    lp_fee_bps: float | None = None    # override LP-net fee rate; else derived

    # ── Execution costs (rebalances) ──
    slippage_bps: float = 100.0
    gas_per_tx_mnt: float = 0.5
    tx_per_reenter: int = 4            # wrap + swap + approve + add

    # ── Strategy replay ──
    decision_period_min: int = 60      # how often to call StrategyEngine
    reenter_cooldown_min: int = 15
    strategy_mode: str = "auto"        # auto | narrow | wide
    narrow_bin_count: int = 10
    wide_bin_count: int = 80
    wide_confidence_threshold: float = 0.5
    # Re-entry RSI/regime gate (mirrors the live reentry_policy): when exiting
    # DOWN while oversold (or in a bear/ranging regime), keep MNT instead of
    # rebalancing to 50/50 — avoids selling the local low. Off = always 50/50.
    reentry_rsi_gate: bool = True

    # ── Experimental anti-chase levers (backtest only, for now) ──
    # Mean-centered re-entry: center the new position on an EMA instead of spot,
    # so we redeploy near where price reverts to rather than chasing the extreme.
    reenter_center: str = "spot"       # spot | ema
    reenter_ema_interval: str = "4h"
    reenter_ema_period: int = 50
    # Ranging-regime hold: in a RANGING regime, do NOT re-center — hold the
    # position and earn fees on the oscillation like a static position would.
    ranging_hold: bool = False
    # Trend-confirmation gate: only re-center on STRONG confirmed continuation
    # (regime trending in the exit direction + aligned higher-TF bias + regime
    # confidence ≥ threshold). Otherwise HOLD. Hold-by-default makes round-trips
    # converge toward static (no chasing) while real trends still get followed.
    trend_confirm_gate: bool = False
    trend_confirm_min_confidence: float = 0.6

    def derived_lp_fee_rate(self) -> float:
        """LP-net fractional fee per swap (after protocol share)."""
        if self.lp_fee_bps is not None:
            return self.lp_fee_bps / 10_000.0
        base = self.base_factor * self.bin_step / 1e8  # LB base-fee fraction
        return base * (1.0 - self.protocol_share_bps / 10_000.0)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["derived_lp_fee_rate"] = self.derived_lp_fee_rate()
        return d
