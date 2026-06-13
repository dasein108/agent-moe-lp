from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from moe_mantle_bot.models import CapitalBudget
from moe_mantle_bot.orchestration import SinglePositionCyclePlanner
from moe_mantle_bot.strategies.legacy_profile import LegacySinglePositionStrategyProfile


def _budget() -> CapitalBudget:
    return CapitalBudget(
        total_mnt=Decimal("100"),
        total_usdt=Decimal("100"),
        deployed_mnt=Decimal("10"),
        deployed_usdt=Decimal("10"),
        free_mnt=Decimal("90"),
        free_usdt=Decimal("90"),
        gas_reserve_mnt=Decimal("1"),
        mnt_price_usdt=Decimal("2"),
    )


def _position(*, exists: bool, in_range: bool):
    return SimpleNamespace(
        position_exists=exists,
        in_range=in_range,
        min_bin_id=100,
        max_bin_id=120,
        bin_count=20,
    )


def _pool_state():
    return SimpleNamespace(active_bin_id=110)


def _profile() -> LegacySinglePositionStrategyProfile:
    return LegacySinglePositionStrategyProfile(
        default_target_mnt_ratio_bps=5000,
        get_narrow_bin_count=lambda _: 12,
        get_wide_params=lambda _: {"bin_count": 88},
        target_pct_for_strategy=lambda strategy: 1.0 if strategy == "wide" else 0.9,
        resolve_reentry_distribution_params=lambda strategy, _: (None, "base_strategy_shape"),
    )


def test_cycle_planner_builds_hold_intent_without_top_up():
    planner = SinglePositionCyclePlanner(
        select_strategy=MagicMock(return_value="hold"),
        resolve_top_up_strategy=MagicMock(return_value=None),
        strategy_profile=_profile(),
    )

    decision = planner.plan(
        wallet_address="0xW",
        timestamp="2026-03-25T00:00:00+00:00",
        dry_run=True,
        pool_state=_pool_state(),
        position=_position(exists=True, in_range=True),
        budget=_budget(),
        keltner={"bounds": {"width_pct": 1.1}},
    )

    assert decision.selected_strategy == "hold"
    assert decision.top_up_candidate is None
    assert decision.intent.action == "hold"
    assert decision.cycle_context.selected_strategy == "hold"


def test_cycle_planner_resolves_top_up_before_building_intent():
    resolve_top_up = MagicMock(return_value="wide")
    planner = SinglePositionCyclePlanner(
        select_strategy=MagicMock(return_value="hold"),
        resolve_top_up_strategy=resolve_top_up,
        strategy_profile=_profile(),
    )

    decision = planner.plan(
        wallet_address="0xW",
        timestamp="2026-03-25T00:00:00+00:00",
        dry_run=False,
        pool_state=_pool_state(),
        position=_position(exists=True, in_range=True),
        budget=_budget(),
        keltner={"bounds": {"width_pct": 1.1}},
    )

    resolve_top_up.assert_called_once()
    assert decision.top_up_candidate == "wide"
    assert decision.intent.action == "top_up"
    assert decision.intent.strategy_id == "wide"


def test_cycle_planner_skips_top_up_lookup_for_direct_entry():
    resolve_top_up = MagicMock(return_value="wide")
    planner = SinglePositionCyclePlanner(
        select_strategy=MagicMock(return_value="narrow"),
        resolve_top_up_strategy=resolve_top_up,
        strategy_profile=_profile(),
    )

    decision = planner.plan(
        wallet_address="0xW",
        timestamp="2026-03-25T00:00:00+00:00",
        dry_run=True,
        pool_state=_pool_state(),
        position=_position(exists=False, in_range=False),
        budget=_budget(),
        keltner={"bounds": {"width_pct": 1.1}},
    )

    resolve_top_up.assert_not_called()
    assert decision.selected_strategy == "narrow"
    assert decision.intent.action == "enter"
    assert decision.intent.strategy_id == "narrow"
