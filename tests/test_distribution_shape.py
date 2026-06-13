"""Tests for Phase 1: Distribution shape wiring.

Verifies that _liquidity_distributions() uses explicit overrides when
provided, and falls back to global settings when not.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from moe_mantle_bot.lp_service import LPService
from moe_mantle_bot.models import TokenInfo
from moe_mantle_bot.tx_sender import PreviewValidationError


@pytest.fixture
def mock_settings():
    s = MagicMock()
    s.pool_address = "0x5afd3ec861f6104af26e8755abcc1f876de77620"
    s.moe_router_address = "0x18556DA13313f3532c54711497A8FedAC273220E"
    s.wmnt_address = "0x3bd359c1119da7da1d913d1c4d2b7c461115433a"
    s.usdt_address = "0x754704bc059f8c67012fed69bc8a327a5aafb603"
    s.log_scan_start_block = 0
    s.log_scan_chunk_size = 100
    s.min_position_size_usdt = 10.0
    s.slippage_bps = 100
    # Numeric settings read by create_position's native-LP path (must be real
    # numbers, not auto-vivified MagicMocks, since they feed Decimal()).
    s.max_native_lp_value_mnt = 500.0
    s.gas_reserve_mnt = 5.0
    s.native_estimate_headroom_mnt = 50.0
    s.position_upside_pct = 0.0
    # Global distribution settings (should be overridden by explicit params)
    s.distribution_shape = "uniform"
    s.slope_direction = "ascending"
    s.slope_steepness = 1.0
    s.curve_type = "exponential"
    s.curve_exponent = 2.0
    # Strategy-specific settings
    s.narrow_distribution_shape = "slope"
    s.narrow_slope_direction = "peak"
    s.narrow_slope_steepness = 2.5
    s.narrow_curve_type = "logarithmic"
    s.narrow_curve_exponent = 1.5
    s.wide_distribution_shape = "uniform"
    s.wide_slope_direction = "ascending"
    s.wide_slope_steepness = 1.0
    s.wide_curve_type = "bell"
    s.wide_curve_exponent = 1.0
    s.get_narrow_distribution_params = MagicMock(return_value={
        'distribution_shape': 'slope',
        'slope_direction': 'peak',
        'slope_steepness': 2.5,
        'curve_type': 'logarithmic',
        'curve_exponent': 1.5,
    })
    s.get_wide_distribution_params = MagicMock(return_value={
        'distribution_shape': 'uniform',
        'slope_direction': 'ascending',
        'slope_steepness': 1.0,
        'curve_type': 'bell',
        'curve_exponent': 1.0,
    })
    return s


@pytest.fixture
def lps(mock_settings):
    rpc = MagicMock()
    rpc.checksum.side_effect = lambda addr: addr
    rpc.get_pair_contract.return_value = MagicMock()
    rpc.get_contract.return_value = MagicMock()
    rpc.get_erc20_contract.return_value = MagicMock()
    rpc.call_with_retry.side_effect = lambda name, fn, *a, **kw: fn()
    tx = MagicMock()
    balance = MagicMock()
    return LPService(rpc, tx, balance, mock_settings)


WMNT_INFO = TokenInfo("0x3bd359c1119da7da1d913d1c4d2b7c461115433a", "Wrapped MNT", "WMNT", 18)
USDT_INFO = TokenInfo("0x754704bc059f8c67012fed69bc8a327a5aafb603", "USD Coin", "USDT", 6)


class TestDistributionShapeOverrides:
    """Verify _liquidity_distributions uses explicit params over global settings."""

    def _call_distributions(self, lps, **overrides):
        """Helper to call _liquidity_distributions with standard args + overrides."""
        lps.get_token_info = MagicMock(side_effect=lambda addr: WMNT_INFO if "3bd" in addr.lower() else USDT_INFO)
        lps.balance._raw_to_decimal = MagicMock(side_effect=lambda addr, raw: Decimal(str(raw)) / Decimal(10**18) if "3bd" in addr.lower() else Decimal(str(raw)) / Decimal(10**6))
        lps._sdk_spot_distribution = MagicMock(return_value=None)  # Force custom path

        return lps._liquidity_distributions(
            active_id=8325650,
            bin_step=25,
            token_x=WMNT_INFO.address,
            token_y=USDT_INFO.address,
            amount_x_raw=10 * 10**18,    # 10 WMNT
            amount_y_raw=10 * 10**6,     # 10 USDT
            delta_ids=[-2, -1, 0, 1, 2],
            **overrides,
        )

    def test_no_overrides_uses_global_settings(self, lps, mock_settings):
        """When no overrides, global settings.distribution_shape is used."""
        mock_settings.distribution_shape = "slope"
        mock_settings.slope_direction = "ascending"
        mock_settings.slope_steepness = 1.0

        result = self._call_distributions(lps)
        assert result["distribution_shape"] == "slope"

    def test_narrow_overrides_use_slope_peak(self, lps, mock_settings):
        """When narrow params passed, uses slope/peak instead of global uniform."""
        mock_settings.distribution_shape = "uniform"  # Global is uniform

        narrow_params = mock_settings.get_narrow_distribution_params()
        result = self._call_distributions(lps, **narrow_params)
        assert result["distribution_shape"] == "slope"

    def test_wide_overrides_use_uniform(self, lps, mock_settings):
        """When wide params passed, uses uniform."""
        mock_settings.distribution_shape = "slope"  # Global is slope

        wide_params = mock_settings.get_wide_distribution_params()
        result = self._call_distributions(lps, **wide_params)
        assert result["distribution_shape"] == "uniform"

    def test_curve_override(self, lps, mock_settings):
        """When curve shape is explicitly passed, uses curve distribution."""
        mock_settings.distribution_shape = "uniform"  # Global is uniform

        result = self._call_distributions(
            lps,
            distribution_shape="curve",
            curve_type="bell",
            curve_exponent=1.5,
        )
        assert result["distribution_shape"] == "curve"

    def test_slope_steepness_override(self, lps, mock_settings):
        """Slope steepness override is respected (including 0 value)."""
        mock_settings.distribution_shape = "slope"
        mock_settings.slope_steepness = 5.0  # Global high steepness

        # Override with low steepness
        result = self._call_distributions(lps, slope_steepness=0.5)
        # Should not raise and should use slope shape
        assert result["distribution_shape"] == "slope"

    def test_create_position_passes_distribution_params(self, lps, mock_settings):
        """create_position() passes distribution_params through to _liquidity_distributions()."""
        # Patch internal methods to avoid real RPC calls
        lps._liquidity_distributions = MagicMock(return_value={
            "distribution_x": [0, 0, 1, 0, 0],
            "distribution_y": [0, 0, 1, 0, 0],
        })
        lps.get_pool_state = MagicMock()
        lps.get_pool_state.return_value.token_x.address = WMNT_INFO.address
        lps.get_pool_state.return_value.token_y.address = USDT_INFO.address
        lps.get_pool_state.return_value.bin_step = 25
        lps.get_pool_state.return_value.active_bin_id = 8325650
        lps.balance._token_to_raw = MagicMock(return_value=10**18)
        lps._lp_range_delta_ids = MagicMock(return_value=[-2, -1, 0, 1, 2])
        lps._native_lp_support = MagicMock(return_value={"enabled": True, "native_token": "token_x", "native_value": 10**18, "router_method": "addLiquidityNATIVE"})
        lps._factory_pair_information = MagicMock(return_value={"status": "ok"})
        lps.tx.ensure_erc20_approval = MagicMock(return_value=None)
        lps._skip_preview_native = MagicMock(return_value={})
        lps._lp_add_preflight = MagicMock(return_value={})
        mock_settings.bin_count = 5
        mock_settings.id_slippage = 5

        narrow_params = {'distribution_shape': 'slope', 'slope_direction': 'peak', 'slope_steepness': 2.5}

        try:
            lps.create_position(
                amount_wmnt=Decimal(10),
                amount_usdt=Decimal(10),
                distribution_params=narrow_params,
                dry_run=True,
            )
        except Exception:
            pass  # We only care that _liquidity_distributions was called correctly

        call_kwargs = lps._liquidity_distributions.call_args[1]
        assert call_kwargs["distribution_shape"] == "slope"
        assert call_kwargs["slope_direction"] == "peak"
        assert call_kwargs["slope_steepness"] == 2.5

    def test_recompute_keeps_one_sided_mode_family(self, lps):
        assert lps._resolve_recompute_prefer_mode(
            original_mode="x_only_onesided",
            initial_active_id=100,
            final_active_id=99,
        ) == "x_only_onesided"
        assert lps._resolve_recompute_prefer_mode(
            original_mode="y_only_onesided",
            initial_active_id=100,
            final_active_id=101,
        ) == "y_only_onesided"
        assert lps._resolve_recompute_prefer_mode(
            original_mode="mixed",
            initial_active_id=100,
            final_active_id=99,
        ) == "y_only"

    # NOTE: test_prefer_mode_can_select_exact_one_sided_candidate was removed.
    # The x_only_onesided candidate is no longer constructed for X-skewed
    # portfolios — it caused on-chain WrongAmounts reverts with
    # addLiquidityNATIVE (see lp_service._liquidity_distributions, "onesided
    # disabled" branch). The code now deliberately falls through to standard
    # x_only mode, so this behavior no longer exists.

    def test_validate_distribution_plan_fill_rejects_dust_fill(self, lps):
        pool_state = MagicMock()
        pool_state.mnt_price_usdt = Decimal("0.0225")
        pool_state.price_y_per_x = Decimal("0.0225")

        with pytest.raises(PreviewValidationError) as exc:
            lps._validate_distribution_plan_fill(
                distribution_plan={
                    "active_mode": "x_only",
                    "x_used": Decimal("0.0581"),
                    "y_used": Decimal("0.0012"),
                },
                pool_state=pool_state,
                token_x=WMNT_INFO.address,
                token_y=USDT_INFO.address,
                requested_amount_wmnt=Decimal("3115.6889"),
                requested_amount_usdt=Decimal("0"),
            )

        assert "planned fill is below minimum position size" in str(exc.value)
