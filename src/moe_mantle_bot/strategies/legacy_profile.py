from __future__ import annotations

from typing import Any, Callable

from ..orchestration.cycle_context import CycleContext
from ..strategy_types import CapitalPlan, RangePlan, RatioPlan, ShapePlan, StrategyIntent


class LegacySinglePositionStrategyProfile:
    """Adapter that preserves current single-position strategy behavior."""

    profile_id = "legacy_single_position"
    reentry_profile_id = "legacy_reentry_entry"

    def __init__(
        self,
        *,
        default_target_mnt_ratio_bps: int,
        get_narrow_bin_count: Callable[[dict[str, Any] | None], int | None],
        get_wide_params: Callable[[dict[str, Any] | None], dict[str, Any]],
        target_pct_for_strategy: Callable[[str], float],
        resolve_reentry_distribution_params: Callable[
            [str, dict[str, Any] | None], tuple[dict[str, Any] | None, str]
        ],
    ) -> None:
        self._default_target_mnt_ratio_bps = int(default_target_mnt_ratio_bps)
        self._get_narrow_bin_count = get_narrow_bin_count
        self._get_wide_params = get_wide_params
        self._target_pct_for_strategy = target_pct_for_strategy
        self._resolve_reentry_distribution_params = resolve_reentry_distribution_params

    def build_cycle_intent(self, ctx: CycleContext) -> StrategyIntent:
        selected_strategy = ctx.selected_strategy or "hold"
        if selected_strategy == "hold" and ctx.top_up_candidate is None:
            return StrategyIntent(
                action="hold",
                profile_id=self.profile_id,
                reason="in_range_no_top_up",
                telemetry={
                    "selected_strategy": selected_strategy,
                    "free_value_usdt": float(ctx.budget.free_value_usdt),
                },
            )

        if selected_strategy == "exit_and_reenter":
            return StrategyIntent(
                action="exit_and_reenter",
                profile_id=self.profile_id,
                strategy_id="cash",
                reason="position_exit_or_rebalance",
                telemetry={
                    "selected_strategy": selected_strategy,
                    "position_in_range": bool(ctx.position.in_range),
                },
            )

        strategy = ctx.top_up_candidate or selected_strategy
        action = "top_up" if ctx.top_up_candidate is not None else "enter"
        ratio_plan = self._default_ratio_plan(source="settings.target_mnt_ratio_bps")
        if strategy == "narrow":
            bin_count = self._get_narrow_bin_count(ctx.keltner)
            return StrategyIntent(
                action=action,
                profile_id=self.profile_id,
                strategy_id="narrow",
                reason="top_up_existing_position" if action == "top_up" else "selected_entry_strategy",
                range_plan=RangePlan(
                    bin_count=bin_count,
                    source="keltner_half_width" if ctx.keltner is not None else "settings.bin_count",
                    reason="too_volatile_for_narrow" if bin_count is None else None,
                ),
                ratio_plan=ratio_plan,
                capital_plan=CapitalPlan(
                    target_pct=self._target_pct_for_strategy("narrow"),
                    top_up=action == "top_up",
                    requires_inventory_prep=False,
                    source="legacy_target_pct",
                ),
                shape_plan=ShapePlan(
                    distribution_params=None,
                    source="global_default",
                    bucket="base_strategy_shape",
                ),
                execution_notes=("legacy_single_position_adapter",),
                telemetry={
                    "selected_strategy": selected_strategy,
                    "top_up_candidate": ctx.top_up_candidate,
                },
            )

        if strategy == "wide":
            wide_params = self._get_wide_params(ctx.keltner)
            return StrategyIntent(
                action=action,
                profile_id=self.profile_id,
                strategy_id="wide",
                reason="top_up_existing_position" if action == "top_up" else "selected_entry_strategy",
                range_plan=RangePlan(
                    bin_count=int(wide_params.get("bin_count", 100)),
                    source="wide_range_manager",
                ),
                ratio_plan=ratio_plan,
                capital_plan=CapitalPlan(
                    target_pct=self._target_pct_for_strategy("wide"),
                    top_up=action == "top_up",
                    requires_inventory_prep=True,
                    source="legacy_target_pct",
                ),
                shape_plan=ShapePlan(
                    distribution_params=None,
                    source="global_default",
                    bucket="base_strategy_shape",
                ),
                execution_notes=("legacy_single_position_adapter",),
                telemetry={
                    "selected_strategy": selected_strategy,
                    "top_up_candidate": ctx.top_up_candidate,
                    "wide_range_bin_count": int(wide_params.get("bin_count", 100)),
                },
            )

        return StrategyIntent(
            action="hold",
            profile_id=self.profile_id,
            reason=f"unsupported_strategy:{strategy}",
            telemetry={"selected_strategy": selected_strategy},
        )

    def build_reentry_intent(
        self,
        ctx: CycleContext,
        *,
        strategy: str,
        reentry_policy_result: dict[str, Any] | None,
    ) -> StrategyIntent:
        bias_signal = reentry_policy_result.get("bias_signal") if reentry_policy_result else None
        target_ratio = (
            reentry_policy_result.get("resolved_target_mnt_ratio_bps")
            if reentry_policy_result else None
        )
        if strategy == "narrow":
            params, shape_bucket = self._resolve_reentry_distribution_params("narrow", bias_signal)
            bin_count = self._get_narrow_bin_count(ctx.keltner)
            return StrategyIntent(
                action="enter",
                profile_id=self.reentry_profile_id,
                strategy_id="narrow",
                reason="reenter_after_exit",
                range_plan=RangePlan(
                    bin_count=bin_count,
                    source="keltner_half_width" if ctx.keltner is not None else "settings.bin_count",
                    reason="too_volatile_for_narrow" if bin_count is None else None,
                ),
                ratio_plan=self._resolved_reentry_ratio_plan(target_ratio),
                capital_plan=CapitalPlan(
                    target_pct=self._target_pct_for_strategy("narrow"),
                    top_up=False,
                    requires_inventory_prep=False,
                    source="legacy_target_pct",
                ),
                shape_plan=ShapePlan(
                    distribution_params=params,
                    source="reentry_distribution_policy",
                    bucket=shape_bucket,
                ),
                execution_notes=("legacy_reentry_entry_adapter",),
                telemetry={
                    "policy_mode": reentry_policy_result.get("mode") if reentry_policy_result else None,
                    "ensemble_decision": (
                        reentry_policy_result.get("ensemble_decision")
                        if reentry_policy_result else None
                    ),
                },
            )

        if strategy == "wide":
            params, shape_bucket = self._resolve_reentry_distribution_params("wide", bias_signal)
            wide_params = self._get_wide_params(ctx.keltner)
            # Skip inventory prep when reentry_policy said continuation_safe.
            # The wide_entry gate would force a swap (buy/sell to 50%) that conflicts
            # with the policy's decision to keep one-sided inventory, causing whipsaw
            # losses (sell low on exit-down, buy high on exit-up).
            reentry_skipped = (
                reentry_policy_result
                and reentry_policy_result.get("reason") == "continuation_safe"
            )
            return StrategyIntent(
                action="enter",
                profile_id=self.reentry_profile_id,
                strategy_id="wide",
                reason="reenter_after_exit",
                range_plan=RangePlan(
                    bin_count=int(wide_params.get("bin_count", 100)),
                    source="wide_range_manager",
                ),
                ratio_plan=self._resolved_reentry_ratio_plan(target_ratio),
                capital_plan=CapitalPlan(
                    target_pct=self._target_pct_for_strategy("wide"),
                    top_up=False,
                    requires_inventory_prep=not reentry_skipped,
                    source="legacy_target_pct",
                ),
                shape_plan=ShapePlan(
                    distribution_params=params,
                    source="reentry_distribution_policy",
                    bucket=shape_bucket,
                ),
                execution_notes=("legacy_reentry_entry_adapter",),
                telemetry={
                    "policy_mode": reentry_policy_result.get("mode") if reentry_policy_result else None,
                    "ensemble_decision": (
                        reentry_policy_result.get("ensemble_decision")
                        if reentry_policy_result else None
                    ),
                    "wide_range_bin_count": int(wide_params.get("bin_count", 100)),
                },
            )

        return StrategyIntent(
            action="hold",
            profile_id=self.reentry_profile_id,
            reason=f"unsupported_reentry_strategy:{strategy}",
            telemetry={"strategy": strategy},
        )

    def _default_ratio_plan(self, *, source: str) -> RatioPlan:
        return RatioPlan(
            target_mnt_ratio_bps=self._default_target_mnt_ratio_bps,
            source=source,
        )

    def _resolved_reentry_ratio_plan(self, target_ratio: Any) -> RatioPlan:
        return RatioPlan(
            target_mnt_ratio_bps=(
                int(target_ratio)
                if target_ratio is not None else self._default_target_mnt_ratio_bps
            ),
            source="reentry_policy",
        )
