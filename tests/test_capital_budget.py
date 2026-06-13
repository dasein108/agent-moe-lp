"""Tests for Phase 3: Capital Budget tracking."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest

from moe_mantle_bot.models import CapitalBudget, PositionState, NativeBalance, ERC20Balance, WalletBalances, TokenInfo


class TestCapitalBudgetModel:
    def test_free_value_usdt(self):
        budget = CapitalBudget(
            total_mnt=Decimal(1000), total_usdt=Decimal(20),
            deployed_mnt=Decimal(600), deployed_usdt=Decimal(12),
            free_mnt=Decimal(300), free_usdt=Decimal(8),
            gas_reserve_mnt=Decimal(100), mnt_price_usdt=Decimal("0.04"),
        )
        assert budget.free_value_usdt == Decimal(300) * Decimal("0.04") + Decimal(8)
        assert budget.free_value_usdt == Decimal("20.00")

    def test_deployed_value_usdt(self):
        budget = CapitalBudget(
            total_mnt=Decimal(1000), total_usdt=Decimal(20),
            deployed_mnt=Decimal(600), deployed_usdt=Decimal(12),
            free_mnt=Decimal(300), free_usdt=Decimal(8),
            gas_reserve_mnt=Decimal(100), mnt_price_usdt=Decimal("0.04"),
        )
        assert budget.deployed_value_usdt == Decimal(600) * Decimal("0.04") + Decimal(12)

    def test_total_value_usdt(self):
        budget = CapitalBudget(
            total_mnt=Decimal(1000), total_usdt=Decimal(20),
            deployed_mnt=Decimal(0), deployed_usdt=Decimal(0),
            free_mnt=Decimal(900), free_usdt=Decimal(20),
            gas_reserve_mnt=Decimal(100), mnt_price_usdt=Decimal("0.04"),
        )
        assert budget.total_value_usdt == Decimal(1000) * Decimal("0.04") + Decimal(20)

    def test_to_dict(self):
        budget = CapitalBudget(
            total_mnt=Decimal(100), total_usdt=Decimal(5),
            deployed_mnt=Decimal(0), deployed_usdt=Decimal(0),
            free_mnt=Decimal(0), free_usdt=Decimal(5),
            gas_reserve_mnt=Decimal(100), mnt_price_usdt=Decimal("0.02"),
        )
        d = budget.to_dict()
        assert "free_value_usdt" in d
        assert "deployed_value_usdt" in d
        assert "total_value_usdt" in d

    def test_frozen(self):
        budget = CapitalBudget(
            total_mnt=Decimal(100), total_usdt=Decimal(5),
            deployed_mnt=Decimal(0), deployed_usdt=Decimal(0),
            free_mnt=Decimal(0), free_usdt=Decimal(5),
            gas_reserve_mnt=Decimal(100), mnt_price_usdt=Decimal("0.02"),
        )
        with pytest.raises(AttributeError):
            budget.total_mnt = Decimal(999)


class TestGetCapitalBudget:
    def test_no_position_all_free(self):
        from moe_mantle_bot.balance_manager import BalanceManager
        bm = MagicMock(spec=BalanceManager)
        bm.settings = SimpleNamespace(max_budget_pct=0.90, native_estimate_headroom_mnt=200)
        bm.get_wallet_balances = MagicMock(return_value=WalletBalances(
            native_mnt=NativeBalance(symbol="MNT", raw=500 * 10**18, normalized=Decimal(500)),
            wmnt=ERC20Balance(token=TokenInfo("0xWMNT", "WMNT", "WMNT", 18), raw=0, normalized=Decimal(0), router_allowance_raw=0, router_allowance_normalized=Decimal(0)),
            usdt=ERC20Balance(token=TokenInfo("0xUSDT", "USDT", "USDT", 6), raw=10 * 10**6, normalized=Decimal(10), router_allowance_raw=0, router_allowance_normalized=Decimal(0)),
            mnt_price_usdt=Decimal("0.04"),
        ))

        lp = MagicMock()
        pos = MagicMock()
        pos.position_exists = False
        lp.get_position.return_value = pos

        # Call real method
        budget = BalanceManager.get_capital_budget(bm, "0xWALLET", lp, gas_reserve=Decimal(100))

        assert budget.total_mnt == Decimal(500)
        assert budget.deployed_mnt == Decimal(0)
        # free_mnt = min(500 - 100 - 200, 500*0.9) = min(200, 450) = 200
        assert budget.free_mnt == Decimal(200)
        # free_usdt = 10 * 0.9 = 9
        assert budget.free_usdt == Decimal(9)

    def test_with_deployed_position(self):
        from moe_mantle_bot.balance_manager import BalanceManager
        bm = MagicMock(spec=BalanceManager)
        bm.get_wallet_balances = MagicMock(return_value=WalletBalances(
            native_mnt=NativeBalance(symbol="MNT", raw=200 * 10**18, normalized=Decimal(200)),
            wmnt=ERC20Balance(token=TokenInfo("0xWMNT", "WMNT", "WMNT", 18), raw=0, normalized=Decimal(0), router_allowance_raw=0, router_allowance_normalized=Decimal(0)),
            usdt=ERC20Balance(token=TokenInfo("0xUSDT", "USDT", "USDT", 6), raw=5 * 10**6, normalized=Decimal(5), router_allowance_raw=0, router_allowance_normalized=Decimal(0)),
            mnt_price_usdt=Decimal("0.04"),
        ))

        lp = MagicMock()
        pos = MagicMock()
        pos.position_exists = True
        pos.inventory_included = True
        pos.estimated_token_x = Decimal(300)
        pos.estimated_token_y = Decimal(6)
        lp.get_position.return_value = pos

        bm.settings = SimpleNamespace(max_budget_pct=0.90, native_estimate_headroom_mnt=200)
        budget = BalanceManager.get_capital_budget(bm, "0xWALLET", lp, gas_reserve=Decimal(50))

        assert budget.deployed_mnt == Decimal(300)
        assert budget.deployed_usdt == Decimal(6)
        # total = wallet + deployed: 200 + 300 = 500 MNT, 5 + 6 = 11 USDT
        assert budget.total_mnt == Decimal(500)
        assert budget.total_usdt == Decimal(11)
        # free_mnt = min(200 - 50 - 150, 200*0.9) = min(0, 180) = 0
        assert budget.free_mnt == Decimal(0)
        # free_usdt = 5 * 0.9 = 4.5
        assert budget.free_usdt == Decimal("4.5")


class TestBudgetAwareAllocation:
    def test_allocation_uses_budget_free_capital(self):
        from moe_mantle_bot.balance_manager import BalanceManager
        bm = MagicMock(spec=BalanceManager)
        bm._get_mnt_price = MagicMock(return_value=Decimal("0.04"))

        budget = CapitalBudget(
            total_mnt=Decimal(1000), total_usdt=Decimal(20),
            deployed_mnt=Decimal(600), deployed_usdt=Decimal(12),
            free_mnt=Decimal(300), free_usdt=Decimal(8),
            gas_reserve_mnt=Decimal(100), mnt_price_usdt=Decimal("0.04"),
        )

        alloc = BalanceManager.calculate_lp_allocation(
            bm, "0xWALLET", target_pct=0.6, budget=budget,
        )
        assert alloc.is_viable
        # Allocation uses all available tokens (not 50/50 capped)
        # MNT capped by free_mnt * safety_margin, USDT by free_usdt * safety_margin
        assert alloc.amount_wmnt <= Decimal(300) * Decimal("0.95")
        assert alloc.amount_usdt <= Decimal(8) * Decimal("0.95")
