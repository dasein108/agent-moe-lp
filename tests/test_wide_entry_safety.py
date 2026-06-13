from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from moe_mantle_bot.models import CapitalBudget, ExecutionResult, LpAllocation, RebalancePlan, RebalanceState


def _make_bot():
    from moe_mantle_bot.farm_bot import FarmBot

    bot = MagicMock(spec=FarmBot)
    bot.settings = SimpleNamespace(
        gas_reserve_mnt=5.0,
        min_position_size_usdt=10.0,
        adaptive_gas_reserve_enabled=True,
        adaptive_gas_reserve_lookback=5,
        adaptive_gas_reserve_multiplier=3.0,
        adaptive_gas_reserve_default_tx_mnt=1.0,
        adaptive_gas_reserve_bin_buffer_mnt=0.05,
        wide_entry_inventory_gate_enabled=True,
        wide_entry_max_mnt_weight_bps=8500,
        wide_entry_min_usdt=10.0,
        wide_entry_rebalance_enabled=True,
        wide_entry_rebalance_target_mnt_ratio_bps=7000,
        wide_entry_rebalance_tolerance_bps=500,
        wide_entry_rebalance_min_trade_usdt=10.0,
        wide_entry_rebalance_max_swap_pct=0.35,
        reentry_skip_rebalance=False,
        reentry_max_swap_usdt=0.0,  # 0 disables the size cap
        data_dir=Path("data"),
    )
    bot.balance = MagicMock()
    bot.analytics = MagicMock()
    bot.lp = MagicMock()
    # Re-entry policy shares VWAP/cooldown guards with the wide-entry path.
    # Default to "not blocked" so rebalance proceeds; specific tests can override.
    bot.reentry_policy = MagicMock()
    bot.reentry_policy._check_vwap_guard.return_value = (False, "ok")
    bot.reentry_policy._check_cooldown.return_value = (False, "ok")
    bot._market_context = {}
    bot._gas_cost_mnt = FarmBot._gas_cost_mnt
    bot._effective_gas_reserve_mnt = FarmBot._effective_gas_reserve_mnt.__get__(bot)
    bot._apply_effective_gas_reserve_to_allocation = FarmBot._apply_effective_gas_reserve_to_allocation.__get__(bot)
    bot._get_wide_entry_inventory_status = FarmBot._get_wide_entry_inventory_status.__get__(bot)
    bot._prepare_wide_entry_inventory = FarmBot._prepare_wide_entry_inventory.__get__(bot)
    return bot


def _budget(*, free_mnt: str = "100", free_usdt: str = "25", gas_reserve_mnt: str = "5") -> CapitalBudget:
    return CapitalBudget(
        total_mnt=Decimal("100"),
        total_usdt=Decimal("25"),
        deployed_mnt=Decimal("0"),
        deployed_usdt=Decimal("0"),
        free_mnt=Decimal(free_mnt),
        free_usdt=Decimal(free_usdt),
        gas_reserve_mnt=Decimal(gas_reserve_mnt),
        mnt_price_usdt=Decimal("1"),
    )


def _state(*, total_value: str = "100", mnt_weight: str = "0.95", usdt: str = "5") -> RebalanceState:
    total_value_dec = Decimal(total_value)
    mnt_weight_dec = Decimal(mnt_weight)
    mnt_value = total_value_dec * mnt_weight_dec
    return RebalanceState(
        wallet_address="0xW",
        mnt_native=Decimal("50"),
        wmnt=Decimal("45"),
        mnt_total=Decimal("95"),
        usdt=Decimal(usdt),
        mnt_price_usdt=Decimal("1"),
        mnt_value_usdt=mnt_value,
        total_value_usdt=total_value_dec,
        mnt_weight=mnt_weight_dec,
        usdt_weight=Decimal("1") - mnt_weight_dec,
    )


def _plan(*, action: str = "sell_mnt", trade_value_usdt: str = "20") -> RebalancePlan:
    return RebalancePlan(
        action=action,
        within_tolerance=False,
        tolerance_bps=500,
        target_weight="0.7",
        current_mnt_weight="0.95",
        current_usdt_weight="0.05",
        trade_value_usdt=trade_value_usdt,
        amount_in_token="WMNT",
        amount_in=trade_value_usdt,
        amount_out_token="USDT",
        quoted_amount_out=trade_value_usdt,
        details={"reason": "rebalance"},
    )


def test_apply_effective_gas_reserve_reduces_mnt_allocation():
    bot = _make_bot()
    bot.analytics.get_recent_average_gas_mnt.return_value = 2.0

    alloc = LpAllocation(Decimal("100"), Decimal("20"), True, "ok")

    adjusted = bot._apply_effective_gas_reserve_to_allocation(
        alloc,
        _budget(),
        strategy="wide",
        bin_count=20,
    )

    assert adjusted.is_viable is True
    assert adjusted.amount_wmnt == Decimal("98")
    assert adjusted.amount_usdt == Decimal("20")


def test_prepare_wide_entry_inventory_holds_when_skewed_and_rebalance_disabled():
    bot = _make_bot()
    bot.settings.wide_entry_rebalance_enabled = False
    budget = _budget()
    bot.balance.get_rebalance_state.return_value = _state(mnt_weight="0.98", usdt="1")

    refreshed_budget, prep_result = bot._prepare_wide_entry_inventory(
        "0xW",
        budget,
        bin_count=28,
        dry_run=False,
        timestamp="2026-03-25T00:00:00+00:00",
    )

    assert refreshed_budget == budget
    assert prep_result is not None
    assert prep_result["action"] == "hold_cash_wait_rebalance"
    assert prep_result["reason"].startswith("wide_inventory_gate:")
    bot.balance.plan_rebalance.assert_not_called()


def test_prepare_wide_entry_inventory_executes_rebalance_and_refreshes_budget():
    bot = _make_bot()
    initial_budget = _budget(free_mnt="95", free_usdt="5")
    refreshed_budget = _budget(free_mnt="70", free_usdt="30")
    bot.balance.get_rebalance_state.side_effect = [
        _state(mnt_weight="0.95", usdt="5"),
        _state(mnt_weight="0.70", usdt="30"),
    ]
    bot.balance.plan_rebalance.return_value = _plan(trade_value_usdt="25")
    bot.balance.execute_rebalance.return_value = [
        ExecutionResult(action="swap_exact_in", tx_hash="0xabc", dry_run=False, details={}),
    ]
    bot.balance.get_capital_budget.return_value = refreshed_budget

    result_budget, prep_result = bot._prepare_wide_entry_inventory(
        "0xW",
        initial_budget,
        bin_count=31,
        dry_run=False,
        timestamp="2026-03-25T00:00:00+00:00",
    )

    assert result_budget == refreshed_budget
    assert prep_result is None
    bot.balance.execute_rebalance.assert_called_once()
    assert bot.balance.execute_rebalance.call_args.kwargs["unwrap_after_buy"] is False
    bot.analytics.record_operation.assert_called_once()


def test_prepare_wide_entry_inventory_ignores_below_minimum_rebalance_plan():
    bot = _make_bot()
    budget = _budget(free_mnt="35", free_usdt="1.37")
    bot.balance.get_rebalance_state.return_value = _state(total_value="36.8", mnt_weight="0.962847", usdt="1.37")
    bot.balance.plan_rebalance.return_value = RebalancePlan(
        action="none",
        within_tolerance=True,
        tolerance_bps=500,
        target_weight="0.7",
        current_mnt_weight="0.962847",
        current_usdt_weight="0.037153",
        trade_value_usdt="9.573565039747",
        amount_in_token="WMNT",
        amount_in="0",
        amount_out_token="USDT",
        quoted_amount_out=None,
        details={"reason": "required trade below minimum threshold"},
    )

    result_budget, prep_result = bot._prepare_wide_entry_inventory(
        "0xW",
        budget,
        bin_count=28,
        dry_run=False,
        timestamp="2026-03-25T00:00:00+00:00",
    )

    assert result_budget == budget
    assert prep_result is None
    bot.balance.execute_rebalance.assert_not_called()
