"""Tests for BalanceManager — business logic only, mocked RPC."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from moe_mantle_bot.balance_manager import BalanceManager
from moe_mantle_bot.models import (
    ERC20Balance,
    LpAllocation,
    NativeBalance,
    RebalancePlan,
    RebalanceState,
    SwapQuote,
    TokenInfo,
)


@pytest.fixture
def mock_settings():
    s = MagicMock()
    s.pool_address = "0x5afd3ec861f6104af26e8755abcc1f876de77620"
    s.moe_router_address = "0x18556DA13313f3532c54711497A8FedAC273220E"
    s.wmnt_address = "0x3bd359c1119da7da1d913d1c4d2b7c461115433a"
    s.usdt_address = "0x754704bc059f8c67012fed69bc8a327a5aafb603"
    s.slippage_bps = 100
    s.pair_version = 2
    s.tx_deadline_seconds = 300
    s.target_mnt_ratio_bps = 5000
    return s


@pytest.fixture
def mock_rpc():
    rpc = MagicMock()
    rpc.checksum.side_effect = lambda addr: addr
    rpc.get_balance.return_value = 1000 * 10**18  # 1000 MNT
    rpc.get_contract.return_value = MagicMock()
    rpc.get_erc20_contract.return_value = MagicMock()
    rpc.get_pair_contract.return_value = MagicMock()
    rpc.call_with_retry.side_effect = lambda name, fn, *a, **kw: fn()
    return rpc


@pytest.fixture
def mock_tx():
    return MagicMock()


@pytest.fixture
def bm(mock_rpc, mock_tx, mock_settings):
    return BalanceManager(mock_rpc, mock_tx, mock_settings)


class TestNativeBalance:
    def test_returns_native_balance(self, bm, mock_rpc):
        mock_rpc.get_balance.return_value = 500 * 10**18
        result = bm.get_native_balance("0xWALLET")
        assert isinstance(result, NativeBalance)
        assert result.symbol == "MNT"
        assert result.normalized == Decimal(500)

    def test_zero_balance(self, bm, mock_rpc):
        mock_rpc.get_balance.return_value = 0
        result = bm.get_native_balance("0xWALLET")
        assert result.raw == 0
        assert result.normalized == Decimal(0)


class TestRebalanceState:
    def test_balanced_portfolio(self, bm):
        """50/50 portfolio should have equal weights."""
        # Mock balance reads
        bm.get_native_balance = MagicMock(return_value=NativeBalance("MNT", 100 * 10**18, Decimal(100)))
        bm.get_erc20_balance = MagicMock(side_effect=[
            ERC20Balance(TokenInfo("0xWMNT", "WMNT", "WMNT", 18), 0, Decimal(0), 0, Decimal(0)),
            ERC20Balance(TokenInfo("0xUSDT", "USDT", "USDT", 6), 200_000_000, Decimal(200), 0, Decimal(0)),
        ])
        bm._get_mnt_price = MagicMock(return_value=Decimal(2))

        state = bm.get_rebalance_state("0xWALLET")
        assert isinstance(state, RebalanceState)
        assert state.mnt_total == Decimal(100)
        assert state.usdt == Decimal(200)
        assert state.mnt_value_usdt == Decimal(200)  # 100 MNT * $2
        assert state.total_value_usdt == Decimal(400)
        assert state.mnt_weight == Decimal("0.5")
        assert state.usdt_weight == Decimal("0.5")

    def test_zero_portfolio(self, bm):
        bm.get_native_balance = MagicMock(return_value=NativeBalance("MNT", 0, Decimal(0)))
        bm.get_erc20_balance = MagicMock(return_value=ERC20Balance(
            TokenInfo("0x", "", "", 18), 0, Decimal(0), 0, Decimal(0),
        ))
        bm._get_mnt_price = MagicMock(return_value=Decimal("0.02"))

        state = bm.get_rebalance_state("0xWALLET")
        assert state.mnt_weight == Decimal(0)
        assert state.usdt_weight == Decimal(0)


class TestRebalancePlan:
    def test_within_tolerance_no_action(self, bm):
        """Already balanced portfolio should return action='none'."""
        bm.get_rebalance_state = MagicMock(return_value=RebalanceState(
            wallet_address="0xW", mnt_native=Decimal(100), wmnt=Decimal(0),
            mnt_total=Decimal(100), usdt=Decimal(200),
            mnt_price_usdt=Decimal(2), mnt_value_usdt=Decimal(200),
            total_value_usdt=Decimal(400), mnt_weight=Decimal("0.5"), usdt_weight=Decimal("0.5"),
        ))
        plan = bm.plan_rebalance("0xW")
        assert plan.action == "none"
        assert plan.within_tolerance is True

    def test_sell_mnt_when_overweight(self, bm):
        """MNT-heavy portfolio should plan to sell MNT."""
        bm.get_rebalance_state = MagicMock(return_value=RebalanceState(
            wallet_address="0xW", mnt_native=Decimal(500), wmnt=Decimal(0),
            mnt_total=Decimal(500), usdt=Decimal(10),
            mnt_price_usdt=Decimal("0.02"), mnt_value_usdt=Decimal(10),
            total_value_usdt=Decimal(20), mnt_weight=Decimal("0.5"), usdt_weight=Decimal("0.5"),
        ))
        # With 50/50, this is balanced. Let's make it unbalanced:
        bm.get_rebalance_state = MagicMock(return_value=RebalanceState(
            wallet_address="0xW", mnt_native=Decimal(900), wmnt=Decimal(0),
            mnt_total=Decimal(900), usdt=Decimal(2),
            mnt_price_usdt=Decimal("0.02"), mnt_value_usdt=Decimal(18),
            total_value_usdt=Decimal(20), mnt_weight=Decimal("0.9"), usdt_weight=Decimal("0.1"),
        ))
        bm.quote_swap = MagicMock(return_value=SwapQuote(
            amount_in_raw=400 * 10**18, amount_in_left_raw=0,
            amount_out_raw=8_000_000, fee_raw=1000,
            amount_out=Decimal(8), swap_for_y=True,
        ))
        plan = bm.plan_rebalance("0xW")
        assert plan.action == "sell_mnt"
        assert plan.within_tolerance is False

    def test_buy_mnt_when_underweight(self, bm):
        """USDT-heavy portfolio should plan to buy MNT."""
        bm.get_rebalance_state = MagicMock(return_value=RebalanceState(
            wallet_address="0xW", mnt_native=Decimal(10), wmnt=Decimal(0),
            mnt_total=Decimal(10), usdt=Decimal(18),
            mnt_price_usdt=Decimal("0.02"), mnt_value_usdt=Decimal("0.2"),
            total_value_usdt=Decimal("18.2"), mnt_weight=Decimal("0.011"), usdt_weight=Decimal("0.989"),
        ))
        bm.quote_swap = MagicMock(return_value=SwapQuote(
            amount_in_raw=9_000_000, amount_in_left_raw=0,
            amount_out_raw=400 * 10**18, fee_raw=500,
            amount_out=Decimal(400), swap_for_y=False,
        ))
        plan = bm.plan_rebalance("0xW")
        assert plan.action == "buy_mnt"


class TestLpAllocation:
    def test_viable_allocation(self, bm):
        bm._get_mnt_price = MagicMock(return_value=Decimal("0.02"))
        bm.get_native_balance = MagicMock(return_value=NativeBalance("MNT", 1000 * 10**18, Decimal(1000)))
        bm.get_erc20_balance = MagicMock(side_effect=[
            ERC20Balance(TokenInfo("0xWMNT", "WMNT", "WMNT", 18), 0, Decimal(0), 0, Decimal(0)),
            ERC20Balance(TokenInfo("0xUSDT", "USDT", "USDT", 6), 20_000_000, Decimal(20), 0, Decimal(0)),
        ])
        alloc = bm.calculate_lp_allocation("0xW")
        assert isinstance(alloc, LpAllocation)
        assert alloc.is_viable is True
        assert alloc.amount_wmnt > 0
        assert alloc.amount_usdt > 0

    def test_insufficient_balance(self, bm):
        bm._get_mnt_price = MagicMock(return_value=Decimal("0.02"))
        bm.get_native_balance = MagicMock(return_value=NativeBalance("MNT", 100, Decimal("0.0000001")))
        bm.get_erc20_balance = MagicMock(return_value=ERC20Balance(
            TokenInfo("0x", "", "", 6), 100, Decimal("0.0001"), 0, Decimal(0),
        ))
        alloc = bm.calculate_lp_allocation("0xW")
        assert alloc.is_viable is False


class TestSwapQuote:
    def test_quote_returns_typed(self, bm):
        """quote_swap should return SwapQuote object."""
        # Mock the pool and router
        bm._pool.functions.getTokenX.return_value.call.return_value = "0xWMNT"
        bm._router.functions.getSwapOut.return_value.call.return_value = (0, 5_000_000, 1000)
        bm.get_token_info = MagicMock(return_value=TokenInfo("0xUSDT", "USDT", "USDT", 6))
        bm._token_to_raw = MagicMock(return_value=100 * 10**18)
        bm.rpc.checksum = lambda x: x

        quote = bm.quote_swap("0xWMNT", "0xUSDT", Decimal(100))
        assert isinstance(quote, SwapQuote)
        assert quote.amount_out_raw == 5_000_000
        assert quote.fee_raw == 1000
