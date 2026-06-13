"""Tests for Phase 2: Registry hooks on LPService create/remove."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from moe_mantle_bot.lp_service import LPService
from moe_mantle_bot.models import ExecutionResult, PositionState, BinState


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
    s.distribution_shape = "uniform"
    s.slope_direction = "ascending"
    s.slope_steepness = 1.0
    s.curve_type = "exponential"
    s.curve_exponent = 2.0
    s.bin_count = 5
    s.id_slippage = 5
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
    tx.wallet_address = "0xWALLET"
    balance = MagicMock()
    return LPService(rpc, tx, balance, mock_settings)


class TestRegisterPosition:
    def test_register_on_live_create(self, lps):
        """_register_position is called on non-dry-run create_position."""
        mock_reg = MagicMock()
        lps.get_registry = MagicMock(return_value=mock_reg)

        add_result = ExecutionResult(
            action="add_liquidity",
            tx_hash="0xABC123",
            dry_run=False,
            details={},
        )

        lps._register_position(
            strategy_type="narrow",
            results=[add_result],
            active_id=8325650,
            delta_ids=[-2, -1, 0, 1, 2],
            amount_wmnt=Decimal(10),
            amount_usdt=Decimal(10),
            distribution_shape="slope",
        )

        mock_reg.add_position.assert_called_once()
        call_kwargs = mock_reg.add_position.call_args[1]
        assert call_kwargs["strategy_type"] == "narrow"
        assert call_kwargs["min_bin"] == 8325648  # 8325650 + (-2)
        assert call_kwargs["max_bin"] == 8325652  # 8325650 + 2
        assert call_kwargs["tx_hash"] == "0xABC123"
        assert call_kwargs["initial_mnt"] == 10.0
        assert call_kwargs["initial_usdt"] == 10.0
        assert call_kwargs["distribution_shape"] == "slope"

    def test_register_wide_strategy_type(self, lps):
        """Wide strategy type is preserved in registry."""
        mock_reg = MagicMock()
        lps.get_registry = MagicMock(return_value=mock_reg)

        add_result = ExecutionResult(
            action="add_liquidity",
            tx_hash="0xDEF456",
            dry_run=False,
            details={},
        )

        lps._register_position(
            strategy_type="wide",
            results=[add_result],
            active_id=100,
            delta_ids=[-50, -49, 0, 49, 50],
            amount_wmnt=Decimal(100),
            amount_usdt=Decimal(100),
        )

        call_kwargs = mock_reg.add_position.call_args[1]
        assert call_kwargs["strategy_type"] == "wide"
        assert call_kwargs["min_bin"] == 50   # 100 + (-50)
        assert call_kwargs["max_bin"] == 150  # 100 + 50


class TestDeregisterPositions:
    def test_deregister_on_live_remove(self, lps):
        """_deregister_positions removes matching positions from registry."""
        mock_pos = MagicMock()
        mock_pos.id = "narrow_123_100"
        mock_reg = MagicMock()
        mock_reg.find_positions_by_bins.return_value = [mock_pos]
        lps.get_registry = MagicMock(return_value=mock_reg)

        position = PositionState(
            wallet_address="0xWALLET",
            candidate_bin_ids=[100, 101, 102],
            active_bins=[
                BinState(bin_id=100, wallet_lb_token_balance_raw=1000,
                         bin_total_supply_raw=10000, bin_reserve_x_raw=10**18,
                         bin_reserve_y_raw=10**6),
                BinState(bin_id=101, wallet_lb_token_balance_raw=2000,
                         bin_total_supply_raw=10000, bin_reserve_x_raw=10**18,
                         bin_reserve_y_raw=10**6),
                BinState(bin_id=102, wallet_lb_token_balance_raw=1000,
                         bin_total_supply_raw=10000, bin_reserve_x_raw=10**18,
                         bin_reserve_y_raw=10**6),
            ],
            position_exists=True,
            in_range=True,
            min_bin_id=100,
            max_bin_id=102,
            estimated_token_x=None,
            estimated_token_y=None,
            inventory_included=False,
        )

        lps._deregister_positions(position)

        mock_reg.find_positions_by_bins.assert_called_once_with([100, 101, 102])
        mock_reg.remove_position.assert_called_once_with(
            "narrow_123_100", tx_hash="", final_mnt=0, final_usdt=0, fees_earned_usdt=0,
        )

    def test_deregister_no_matching_positions(self, lps):
        """No error when no registry positions match removed bins."""
        mock_reg = MagicMock()
        mock_reg.find_positions_by_bins.return_value = []
        lps.get_registry = MagicMock(return_value=mock_reg)

        position = PositionState(
            wallet_address="0xWALLET",
            candidate_bin_ids=[200],
            active_bins=[
                BinState(bin_id=200, wallet_lb_token_balance_raw=1000,
                         bin_total_supply_raw=10000, bin_reserve_x_raw=10**18,
                         bin_reserve_y_raw=10**6),
            ],
            position_exists=True,
            in_range=True,
            min_bin_id=200,
            max_bin_id=200,
            estimated_token_x=None,
            estimated_token_y=None,
            inventory_included=False,
        )

        lps._deregister_positions(position)
        mock_reg.remove_position.assert_not_called()


class TestStrategyTypeParam:
    def test_create_position_accepts_strategy_type(self, lps):
        """create_position signature includes strategy_type."""
        import inspect
        sig = inspect.signature(lps.create_position)
        assert "strategy_type" in sig.parameters
        assert sig.parameters["strategy_type"].default == "narrow"
