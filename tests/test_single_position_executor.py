from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from moe_mantle_bot.execution.executor import SinglePositionIntentExecutor
from moe_mantle_bot.orchestration.cycle_context import build_cycle_context
from moe_mantle_bot.strategy_types import CapitalPlan, RangePlan, ShapePlan, StrategyIntent


def _settings():
    settings = MagicMock()
    settings.min_position_size_usdt = 10.0
    return settings


def _budget():
    return SimpleNamespace(
        free_value_usdt=Decimal("50"),
        total_value_usdt=Decimal("100"),
        mnt_price_usdt=Decimal("2"),
    )


def _context(*, dry_run: bool = True):
    return build_cycle_context(
        wallet_address="0xW",
        timestamp="2026-03-25T00:00:00+00:00",
        dry_run=dry_run,
        pool_state=SimpleNamespace(active_bin_id=100),
        position=SimpleNamespace(position_exists=False, in_range=False, min_bin_id=None, max_bin_id=None, bin_count=0),
        budget=_budget(),
        selected_strategy="wide",
    )


def _intent(*, action: str = "enter", strategy_id: str = "wide", bin_count: int = 60):
    return StrategyIntent(
        action=action,
        profile_id="legacy_single_position",
        strategy_id=strategy_id,
        range_plan=RangePlan(bin_count=bin_count),
        capital_plan=CapitalPlan(
            target_pct=1.0 if strategy_id == "wide" else 0.9,
            top_up=action == "top_up",
            requires_inventory_prep=strategy_id == "wide",
        ),
        shape_plan=ShapePlan(distribution_params=None),
    )


def _executor():
    balance = MagicMock()
    alloc = SimpleNamespace(
        amount_wmnt=Decimal("10"),
        amount_usdt=Decimal("20"),
        is_viable=True,
        reason="ok",
    )
    balance.calculate_lp_allocation.return_value = alloc
    return (
        SinglePositionIntentExecutor(
            settings=_settings(),
            balance=balance,
            refresh_budget=MagicMock(return_value=_budget()),
            apply_effective_gas_reserve_to_allocation=MagicMock(return_value=alloc),
            prepare_wide_entry_inventory=MagicMock(return_value=(_budget(), None)),
            top_up_expected_fill_is_viable=MagicMock(return_value=(True, None)),
            create_position_with_retry=MagicMock(return_value={"action": "enter_wide"}),
        ),
        balance,
    )


def test_executor_runs_enter_intent_through_create_position():
    executor, balance = _executor()

    result = executor.execute(_context(dry_run=True), _intent(action="enter", strategy_id="wide"))

    assert result == {"action": "enter_wide"}
    balance.calculate_lp_allocation.assert_called_once()


def test_executor_returns_hold_when_top_up_fill_is_below_minimum():
    executor, _ = _executor()
    executor._top_up_expected_fill_is_viable = MagicMock(
        return_value=(
            False,
            {
                "active_mode": "y_only",
                "used_value_usdt": Decimal("3.8"),
                "requested_value_usdt": Decimal("16.3"),
            },
        )
    )

    result = executor.execute(_context(dry_run=True), _intent(action="top_up", strategy_id="wide"))

    assert result["action"] == "hold"
    assert result["reason"] == "top_up_expected_fill_below_minimum"
    assert result["lp_mode"] == "y_only"


def test_executor_returns_prep_result_when_wide_inventory_gate_blocks_entry():
    executor, _ = _executor()
    executor._prepare_wide_entry_inventory = MagicMock(
        return_value=(_budget(), {"action": "hold", "reason": "wide_entry_inventory_gate"})
    )

    result = executor.execute(_context(dry_run=True), _intent(action="enter", strategy_id="wide"))

    assert result == {"action": "hold", "reason": "wide_entry_inventory_gate"}
