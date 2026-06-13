from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from moe_mantle_bot.models import CapitalBudget
from moe_mantle_bot.orchestration.cycle_context import build_cycle_context
from moe_mantle_bot.strategies.legacy_profile import LegacySinglePositionStrategyProfile


def _make_profile():
    from moe_mantle_bot.farm_bot import FarmBot

    bot = MagicMock(spec=FarmBot)
    bot.settings = MagicMock()
    bot.settings.bin_count = 12
    bot.settings.target_mnt_ratio_bps = 5000
    bot._get_narrow_bin_count = FarmBot._get_narrow_bin_count.__get__(bot)
    bot._get_wide_params = FarmBot._get_wide_params.__get__(bot)
    # _target_pct_for_strategy is an instance method that reads self._market_context
    # for scale-in sizing (overbought=1/3, neutral=3/4, oversold=full). Bind it to
    # the mock bot; use an oversold context so target_pct reflects full deployment.
    bot._market_context = {"oversold": True}
    bot._target_pct_for_strategy = FarmBot._target_pct_for_strategy.__get__(bot)
    bot._resolve_reentry_distribution_params = MagicMock(
        return_value=({"distribution_shape": "slope"}, "base_strategy_shape")
    )
    bot.wide_range_manager = MagicMock()
    bot.wide_range_manager.calculate_wide_range_params.return_value = {"bin_count": 88}
    return LegacySinglePositionStrategyProfile(
        default_target_mnt_ratio_bps=bot.settings.target_mnt_ratio_bps,
        get_narrow_bin_count=bot._get_narrow_bin_count,
        get_wide_params=bot._get_wide_params,
        target_pct_for_strategy=bot._target_pct_for_strategy,
        resolve_reentry_distribution_params=bot._resolve_reentry_distribution_params,
    )


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


def test_build_cycle_context_captures_selected_strategy_and_top_up_candidate():
    ctx = build_cycle_context(
        wallet_address="0xW",
        timestamp="2026-03-25T00:00:00+00:00",
        dry_run=True,
        pool_state=_pool_state(),
        position=_position(exists=True, in_range=True),
        budget=_budget(),
        keltner={"bounds": {"width_pct": 1.5}},
        selected_strategy="hold",
        top_up_candidate="wide",
    )

    assert ctx.wallet_address == "0xW"
    assert ctx.selected_strategy == "hold"
    assert ctx.top_up_candidate == "wide"


def test_legacy_strategy_intent_holds_when_no_top_up_candidate_exists():
    profile = _make_profile()
    ctx = build_cycle_context(
        wallet_address="0xW",
        timestamp="2026-03-25T00:00:00+00:00",
        dry_run=True,
        pool_state=_pool_state(),
        position=_position(exists=True, in_range=True),
        budget=_budget(),
        selected_strategy="hold",
        top_up_candidate=None,
    )

    intent = profile.build_cycle_intent(ctx)

    assert intent.action == "hold"
    assert intent.profile_id == "legacy_single_position"
    assert intent.reason == "in_range_no_top_up"


def test_legacy_strategy_intent_builds_top_up_wide_plan():
    profile = _make_profile()
    ctx = build_cycle_context(
        wallet_address="0xW",
        timestamp="2026-03-25T00:00:00+00:00",
        dry_run=False,
        pool_state=_pool_state(),
        position=_position(exists=True, in_range=True),
        budget=_budget(),
        keltner={"bounds": {"width_pct": 1.2}},
        selected_strategy="hold",
        top_up_candidate="wide",
    )

    intent = profile.build_cycle_intent(ctx)

    assert intent.action == "top_up"
    assert intent.strategy_id == "wide"
    assert intent.range_plan.bin_count == 88
    assert intent.capital_plan.target_pct == 1.0
    assert intent.capital_plan.requires_inventory_prep is True


def test_legacy_strategy_intent_marks_narrow_as_too_volatile_when_bin_count_is_none():
    profile = _make_profile()
    ctx = build_cycle_context(
        wallet_address="0xW",
        timestamp="2026-03-25T00:00:00+00:00",
        dry_run=True,
        pool_state=_pool_state(),
        position=_position(exists=False, in_range=False),
        budget=_budget(),
        keltner={"bounds": {"width_pct": 9.1}},
        selected_strategy="narrow",
    )

    intent = profile.build_cycle_intent(ctx)

    assert intent.action == "enter"
    assert intent.strategy_id == "narrow"
    assert intent.range_plan.bin_count is None
    assert intent.range_plan.reason == "too_volatile_for_narrow"


def test_legacy_reentry_intent_builds_wide_shape_and_ratio_from_policy():
    profile = _make_profile()
    profile._resolve_reentry_distribution_params = MagicMock(
        return_value=(
            {"distribution_shape": "slope", "slope_direction": "descending"},
            "bull_wide_shape_strong",
        )
    )
    ctx = build_cycle_context(
        wallet_address="0xW",
        timestamp="2026-03-25T00:00:00+00:00",
        dry_run=False,
        pool_state=_pool_state(),
        position=_position(exists=False, in_range=False),
        budget=_budget(),
        keltner={"bounds": {"width_pct": 1.2}},
        selected_strategy="wide",
    )

    intent = profile.build_reentry_intent(
        ctx,
        strategy="wide",
        reentry_policy_result={
            "mode": "partial_rebalance",
            "ensemble_decision": "swap_to_30_70",
            "resolved_target_mnt_ratio_bps": 6000,
            "bias_signal": {"direction": "BULL"},
        },
    )

    assert intent.action == "enter"
    assert intent.profile_id == "legacy_reentry_entry"
    assert intent.strategy_id == "wide"
    assert intent.range_plan.bin_count == 88
    assert intent.ratio_plan.target_mnt_ratio_bps == 6000
    assert intent.shape_plan.bucket == "bull_wide_shape_strong"
    assert intent.shape_plan.distribution_params["distribution_shape"] == "slope"


def test_legacy_reentry_intent_marks_narrow_too_volatile_when_bin_count_is_none():
    profile = _make_profile()
    ctx = build_cycle_context(
        wallet_address="0xW",
        timestamp="2026-03-25T00:00:00+00:00",
        dry_run=False,
        pool_state=_pool_state(),
        position=_position(exists=False, in_range=False),
        budget=_budget(),
        keltner={"bounds": {"width_pct": 9.1}},
        selected_strategy="narrow",
    )

    intent = profile.build_reentry_intent(
        ctx,
        strategy="narrow",
        reentry_policy_result={"mode": "continuation_safe", "bias_signal": None},
    )

    assert intent.strategy_id == "narrow"
    assert intent.range_plan.bin_count is None
    assert intent.range_plan.reason == "too_volatile_for_narrow"
