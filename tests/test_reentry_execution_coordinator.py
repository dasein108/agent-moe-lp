from __future__ import annotations

from unittest.mock import MagicMock

from moe_mantle_bot.orchestration import ReentryExecutionCoordinator
from moe_mantle_bot.strategy_types import RangePlan, StrategyIntent


def _coordinator():
    analytics = MagicMock()
    analytics.conn.execute.return_value.fetchone.return_value = {"recovered_value_usdt": 42.0}
    return ReentryExecutionCoordinator(
        analytics=analytics,
        safe_float=lambda value: float(value or 0),
    ), analytics


def test_finalize_create_result_records_successful_reentry():
    coordinator, analytics = _coordinator()
    intent = StrategyIntent(
        action="enter",
        profile_id="legacy_reentry_entry",
        strategy_id="wide",
        range_plan=RangePlan(bin_count=88),
    )

    result = coordinator.finalize_create_result(
        result={"action": "exit_and_reenter"},
        strategy="wide",
        intent=intent,
        create_result={
            "action": "enter_wide",
            "entry_value_usdt": 18.5,
            "lp_mode": "y_only",
            "expected_refund_mnt": 0.1,
            "expected_refund_usdt": 1.2,
            "fill_pct_mnt": 90.0,
            "fill_pct_usdt": 75.0,
        },
        reentry_event_id="evt-1",
        reentry_policy_result={"mode": "partial_rebalance"},
    )

    assert result["action"] == "reenter_wide"
    assert result["strategy"] == "wide"
    assert result["bin_count"] == 88
    analytics.complete_reentry_entry.assert_called_once()
    kwargs = analytics.complete_reentry_entry.call_args.kwargs
    assert kwargs["turnover_usdt"] == 60.5
    assert kwargs["selected_strategy"] == "wide"


def test_finalize_create_result_closes_exit_only_for_skipped_reentry():
    coordinator, analytics = _coordinator()
    intent = StrategyIntent(
        action="enter",
        profile_id="legacy_reentry_entry",
        strategy_id="narrow",
        range_plan=RangePlan(bin_count=12),
    )

    result = coordinator.finalize_create_result(
        result={"action": "exit_and_reenter"},
        strategy="narrow",
        intent=intent,
        create_result={
            "action": "skip_narrow",
            "reason": "expected_fill_below_minimum",
        },
        reentry_event_id="evt-2",
        reentry_policy_result={"mode": "continuation_safe"},
    )

    assert result["action"] == "exit_only"
    assert result["reason"] == "expected_fill_below_minimum"
    assert result["reenter_skip"]["action"] == "skip_narrow"
    analytics.close_reentry_event.assert_called_once()
    kwargs = analytics.close_reentry_event.call_args.kwargs
    assert kwargs["status"] == "exit_only"
    assert kwargs["selected_strategy"] == "narrow"


def test_finalize_exception_marks_error_and_closes_event():
    coordinator, analytics = _coordinator()

    result = coordinator.finalize_exception(
        {"action": "exit_and_reenter"},
        error=RuntimeError("boom"),
        reentry_event_id="evt-3",
        reentry_policy_result={"mode": "partial_rebalance"},
    )

    assert result["action"] == "exit_only"
    assert result["reenter_error"] == "boom"
    analytics.close_reentry_event.assert_called_once()
    kwargs = analytics.close_reentry_event.call_args.kwargs
    assert kwargs["status"] == "error"


def test_close_exit_only_sets_reason_and_selected_strategy():
    coordinator, analytics = _coordinator()

    result = coordinator.close_exit_only(
        {"action": "exit_and_reenter"},
        reason="ensemble_skip_reentry",
        reentry_event_id="evt-4",
        reentry_policy_result={"mode": "partial_rebalance"},
        selected_strategy="hold",
    )

    assert result["action"] == "exit_only"
    assert result["reason"] == "ensemble_skip_reentry"
    analytics.close_reentry_event.assert_called_once()
    kwargs = analytics.close_reentry_event.call_args.kwargs
    assert kwargs["selected_strategy"] == "hold"
