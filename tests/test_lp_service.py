"""Tests for LPService — pool/position reads, validation, bin discovery."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from moe_mantle_bot.lp_service import LPService
from moe_mantle_bot.tx_sender import PreviewValidationError
from moe_mantle_bot.models import (
    BinState,
    PoolState,
    PositionState,
    TokenInfo,
)


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
    return s


@pytest.fixture
def mock_rpc():
    rpc = MagicMock()
    rpc.checksum.side_effect = lambda addr: addr
    rpc.get_pair_contract.return_value = MagicMock()
    rpc.get_contract.return_value = MagicMock()
    rpc.get_erc20_contract.return_value = MagicMock()
    rpc.call_with_retry.side_effect = lambda name, fn, *a, **kw: fn()
    return rpc


@pytest.fixture
def lps(mock_rpc, mock_settings):
    tx = MagicMock()
    balance = MagicMock()
    return LPService(mock_rpc, tx, balance, mock_settings)


class TestPoolState:
    def test_get_pool_state_returns_typed(self, lps):
        pool_contract = lps._pool
        pool_contract.functions.getTokenX.return_value.call.return_value = "0xWMNT"
        pool_contract.functions.getTokenY.return_value.call.return_value = "0xUSDT"
        pool_contract.functions.getBinStep.return_value.call.return_value = 25
        pool_contract.functions.getActiveId.return_value.call.return_value = 8325650
        pool_contract.functions.getReserves.return_value.call.return_value = (10**18, 10**6)
        pool_contract.functions.getProtocolFees.return_value.call.return_value = (0, 0)
        pool_contract.functions.getStaticFeeParameters.return_value.call.return_value = [0]*7
        pool_contract.functions.getVariableFeeParameters.return_value.call.return_value = [0]*4
        pool_contract.functions.getPriceFromId.return_value.call.return_value = 2**128

        lps.get_token_info = MagicMock(side_effect=[
            TokenInfo("0xWMNT", "Wrapped MNT", "WMNT", 18),
            TokenInfo("0xUSDT", "USD Coin", "USDT", 6),
        ])

        pool = lps.get_pool_state()
        assert isinstance(pool, PoolState)
        assert pool.active_bin_id == 8325650
        assert pool.bin_step == 25
        assert pool.token_x.symbol == "WMNT"
        assert pool.token_y.symbol == "USDT"


class TestPositionState:
    def test_empty_position(self, lps):
        lps._discover_bins_near_active = MagicMock(return_value=[])
        lps.get_pool_state = MagicMock(return_value=PoolState(
            pair_address="0xPOOL",
            token_x=TokenInfo("0xWMNT", "WMNT", "WMNT", 18),
            token_y=TokenInfo("0xUSDT", "USDT", "USDT", 6),
            bin_step=25, active_bin_id=101,
            price_y_per_x=Decimal("0.02"), price_y_per_x_raw_128x128=0,
            mnt_price_usdt=Decimal("0.02"),
            reserve_x_raw=0, reserve_x_normalized=Decimal(0),
            reserve_y_raw=0, reserve_y_normalized=Decimal(0),
            protocol_fee_x_raw=0, protocol_fee_y_raw=0,
        ))
        pos = lps.get_position("0xWALLET")
        assert isinstance(pos, PositionState)
        assert pos.position_exists is False
        assert pos.bin_count == 0

    def test_position_with_bins(self, lps):
        lps._discover_bins_near_active = MagicMock(return_value=[100, 101, 102])
        lps.get_pool_state = MagicMock(return_value=PoolState(
            pair_address="0xPOOL",
            token_x=TokenInfo("0xWMNT", "WMNT", "WMNT", 18),
            token_y=TokenInfo("0xUSDT", "USDT", "USDT", 6),
            bin_step=25, active_bin_id=101,
            price_y_per_x=Decimal("0.02"), price_y_per_x_raw_128x128=0,
            mnt_price_usdt=Decimal("0.02"),
            reserve_x_raw=0, reserve_x_normalized=Decimal(0),
            reserve_y_raw=0, reserve_y_normalized=Decimal(0),
            protocol_fee_x_raw=0, protocol_fee_y_raw=0,
        ))

        pool_contract = lps._pool
        # balanceOfBatch returns nonzero for bins 100, 101, 102 (above dust threshold 1M)
        pool_contract.functions.balanceOfBatch.return_value.call.return_value = [10**15, 2 * 10**15, 10**15]
        pool_contract.functions.totalSupply.return_value.call.return_value = 10000
        pool_contract.functions.getBin.return_value.call.return_value = (10**18, 10**6)

        pos = lps.get_position("0xWALLET")
        assert pos.position_exists is True
        assert pos.bin_count == 3
        assert pos.in_range is True
        assert pos.min_bin_id == 100
        assert pos.max_bin_id == 102

    def test_out_of_range(self, lps):
        lps._discover_bins_near_active = MagicMock(return_value=[100, 101])
        lps.get_pool_state = MagicMock(return_value=PoolState(
            pair_address="0xPOOL",
            token_x=TokenInfo("0xWMNT", "WMNT", "WMNT", 18),
            token_y=TokenInfo("0xUSDT", "USDT", "USDT", 6),
            bin_step=25, active_bin_id=200,  # far away
            price_y_per_x=Decimal("0.02"), price_y_per_x_raw_128x128=0,
            mnt_price_usdt=Decimal("0.02"),
            reserve_x_raw=0, reserve_x_normalized=Decimal(0),
            reserve_y_raw=0, reserve_y_normalized=Decimal(0),
            protocol_fee_x_raw=0, protocol_fee_y_raw=0,
        ))

        pool_contract = lps._pool
        pool_contract.functions.balanceOfBatch.return_value.call.return_value = [10**15, 10**15]
        pool_contract.functions.totalSupply.return_value.call.return_value = 10000
        pool_contract.functions.getBin.return_value.call.return_value = (10**18, 10**6)

        pos = lps.get_position("0xWALLET", include_inventory=False)
        assert pos.position_exists is True
        assert pos.in_range is False


class TestValidation:
    def test_valid_position_size(self, lps):
        ok, msg = lps.validate_position_size(
            amount_wmnt=Decimal(500), amount_usdt=Decimal(10),
            mnt_price_usdt=Decimal("0.02"),
        )
        assert ok is True

    def test_position_too_small(self, lps):
        ok, msg = lps.validate_position_size(
            amount_wmnt=Decimal(1), amount_usdt=Decimal("0.01"),
            mnt_price_usdt=Decimal("0.02"),
        )
        assert ok is False
        assert "below minimum" in msg

    def test_live_onesided_mode_is_allowed(self, lps):
        lps._validate_live_distribution_mode(
            distribution_plan={"active_mode": "x_only_onesided"},
            dry_run=False,
        )

    def test_reserve_estimate_error_marker(self, lps):
        assert lps._is_reserve_estimate_error(
            RuntimeError("gas fee greater than reserve for non-dipping transaction")
        ) is True
        assert lps._is_reserve_estimate_error(RuntimeError("execution reverted")) is False

    def test_estimate_position_fill_reports_subminimum_expected_fill(self, lps):
        pool_state = MagicMock()
        pool_state.token_x.address = "0xWMNT"
        pool_state.token_y.address = "0xUSDT"
        pool_state.bin_step = 25
        pool_state.active_bin_id = 101
        pool_state.mnt_price_usdt = Decimal("0.0225")
        pool_state.price_y_per_x = Decimal("0.0225")
        lps.get_pool_state = MagicMock(return_value=pool_state)
        lps.balance._token_to_raw = MagicMock(side_effect=lambda token, amount: int(amount))
        lps._lp_range_delta_ids = MagicMock(return_value=[-1, 0, 1])
        lps._pool.functions.getActiveId.return_value.call.return_value = 101
        lps._liquidity_distributions = MagicMock(return_value={
            "active_mode": "y_only",
            "x_used": Decimal("79.03"),
            "y_used": Decimal("1.81"),
        })

        estimate = lps.estimate_position_fill(
            amount_wmnt=Decimal("79.03"),
            amount_usdt=Decimal("14.48"),
            bin_count=3,
        )

        assert estimate["active_mode"] == "y_only"
        assert estimate["meets_min_fill"] is False
        assert estimate["used_value_usdt"] < estimate["min_position_size_usdt"]


class TestHelpers:
    def test_has_active_position(self, lps):
        lps.get_position = MagicMock(return_value=PositionState(
            wallet_address="0xW", candidate_bin_ids=[], active_bins=[],
            position_exists=True, in_range=True,
            min_bin_id=100, max_bin_id=102,
            estimated_token_x=None, estimated_token_y=None, inventory_included=False,
        ))
        assert lps.has_active_position("0xW") is True

    def test_is_in_range(self, lps):
        lps.get_position = MagicMock(return_value=PositionState(
            wallet_address="0xW", candidate_bin_ids=[], active_bins=[],
            position_exists=True, in_range=False,
            min_bin_id=100, max_bin_id=102,
            estimated_token_x=None, estimated_token_y=None, inventory_included=False,
        ))
        assert lps.is_in_range("0xW") is False

    def test_get_position_range(self, lps):
        lps.get_position = MagicMock(return_value=PositionState(
            wallet_address="0xW", candidate_bin_ids=[], active_bins=[],
            position_exists=True, in_range=True,
            min_bin_id=100, max_bin_id=105,
            estimated_token_x=None, estimated_token_y=None, inventory_included=False,
        ))
        assert lps.get_position_range("0xW") == (100, 105)


class TestRemovePositionPreviewFallback:
    def _mock_pool_state(self):
        return PoolState(
            pair_address="0xPOOL",
            token_x=TokenInfo("0xWMNT", "WMNT", "WMNT", 18),
            token_y=TokenInfo("0xUSDT", "USDT", "USDT", 6),
            bin_step=25,
            active_bin_id=101,
            price_y_per_x=Decimal("0.02"),
            price_y_per_x_raw_128x128=0,
            mnt_price_usdt=Decimal("0.02"),
            reserve_x_raw=0,
            reserve_x_normalized=Decimal(0),
            reserve_y_raw=0,
            reserve_y_normalized=Decimal(0),
            protocol_fee_x_raw=0,
            protocol_fee_y_raw=0,
        )

    def _mock_position(self):
        return PositionState(
            wallet_address="0xWALLET",
            candidate_bin_ids=[100, 101],
            active_bins=[
                BinState(
                    bin_id=100,
                    wallet_lb_token_balance_raw=1000,
                    bin_total_supply_raw=10000,
                    bin_reserve_x_raw=10**18,
                    bin_reserve_y_raw=10**6,
                ),
                BinState(
                    bin_id=101,
                    wallet_lb_token_balance_raw=1000,
                    bin_total_supply_raw=10000,
                    bin_reserve_x_raw=10**18,
                    bin_reserve_y_raw=10**6,
                ),
            ],
            position_exists=True,
            in_range=False,
            min_bin_id=100,
            max_bin_id=101,
            estimated_token_x=Decimal("0.2"),
            estimated_token_y=Decimal("0.2"),
            inventory_included=True,
        )

    def test_live_remove_raises_when_preview_reverts(self, lps):
        """Live removal must NOT proceed with unvalidated fallback amounts when preview reverts."""
        pool_state = self._mock_pool_state()
        position = self._mock_position()
        lps.get_position = MagicMock(return_value=position)
        lps.tx.wallet_address = "0xWALLET"
        lps.tx.ensure_pair_approval.return_value = None
        lps.tx.deadline.return_value = 999999
        lps.tx.send.return_value = MagicMock()
        lps._preview_remove_liquidity = MagicMock(return_value={"status": "reverted", "error": "rpc"})
        lps._lp_remove_preflight = MagicMock(return_value={})
        lps._router.functions.removeLiquidity.return_value = MagicMock()
        lps._deregister_positions = MagicMock()

        with pytest.raises(PreviewValidationError, match="preview reverted"):
            lps.remove_position(pool_state=pool_state, dry_run=False)

        lps.tx.send.assert_not_called()

    def test_dry_run_remove_still_fails_when_preview_reverts(self, lps):
        pool_state = self._mock_pool_state()
        position = self._mock_position()
        lps.get_position = MagicMock(return_value=position)
        lps.tx.wallet_address = "0xWALLET"
        lps.tx.ensure_pair_approval.return_value = None
        lps.tx.deadline.return_value = 999999
        lps._preview_remove_liquidity = MagicMock(return_value={"status": "reverted", "error": "rpc"})
        lps._lp_remove_preflight = MagicMock(return_value={})
        lps._router.functions.removeLiquidity.return_value = MagicMock()

        with pytest.raises(PreviewValidationError):
            lps.remove_position(pool_state=pool_state, dry_run=True)

    def test_live_remove_blocks_on_reverted_preview_before_gas_check(self, lps):
        """When preview reverts in live mode, abort immediately — don't reach gas headroom check."""
        pool_state = self._mock_pool_state()
        position = self._mock_position()
        lps.get_position = MagicMock(return_value=position)
        lps.tx.wallet_address = "0xWALLET"
        lps.tx.ensure_pair_approval.return_value = None
        lps.tx.deadline.return_value = 999999
        lps._preview_remove_liquidity = MagicMock(return_value={"status": "reverted", "error": "rpc"})
        lps._lp_remove_preflight = MagicMock(return_value={})
        lps._deregister_positions = MagicMock()

        with pytest.raises(PreviewValidationError, match="preview reverted"):
            lps.remove_position(pool_state=pool_state, dry_run=False)

        lps._deregister_positions.assert_not_called()
