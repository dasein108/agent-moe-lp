from __future__ import annotations

from typing import Any, Callable

from ..logging_config import get_logger
from ..strategy_types import StrategyIntent

logger = get_logger(__name__)


class SinglePositionIntentExecutor:
    """Execute legacy single-position enter/top-up intents.

    This is the first extraction step from FarmBot.execute_cycle(). It keeps the
    current safety and execution behavior, but moves the allocation and intent
    handling out of the cycle orchestrator.
    """

    def __init__(
        self,
        *,
        settings,
        balance,
        refresh_budget: Callable[[str], Any],
        apply_effective_gas_reserve_to_allocation: Callable[[Any, Any, str, int], Any],
        prepare_wide_entry_inventory: Callable[..., tuple[Any, dict[str, Any] | None]],
        top_up_expected_fill_is_viable: Callable[..., tuple[bool, dict[str, Any] | None]],
        create_position_with_retry: Callable[..., dict[str, Any]],
    ) -> None:
        self.settings = settings
        self.balance = balance
        self._refresh_budget = refresh_budget
        self._apply_effective_gas_reserve_to_allocation = apply_effective_gas_reserve_to_allocation
        self._prepare_wide_entry_inventory = prepare_wide_entry_inventory
        self._top_up_expected_fill_is_viable = top_up_expected_fill_is_viable
        self._create_position_with_retry = create_position_with_retry

    def execute(self, ctx, intent: StrategyIntent) -> dict[str, Any]:
        if intent.action not in {"enter", "top_up"}:
            raise ValueError(f"Unsupported single-position intent action: {intent.action}")

        strategy = intent.strategy_id
        if strategy not in {"narrow", "wide"}:
            return {"action": "hold", "timestamp": ctx.timestamp}

        budget = ctx.budget if ctx.dry_run else self._refresh_budget(ctx.wallet_address)
        params = intent.shape_plan.distribution_params if intent.shape_plan else None

        if strategy == "narrow":
            bin_count = intent.range_plan.bin_count if intent.range_plan else None
            if bin_count is None:
                return {
                    "action": "skip",
                    "strategy": strategy,
                    "reason": "too volatile for narrow",
                    "timestamp": ctx.timestamp,
                }
            alloc = self.balance.calculate_lp_allocation(
                ctx.wallet_address,
                target_pct=float(intent.capital_plan.target_pct),
                budget=budget,
                min_size_usdt=self.settings.min_position_size_usdt,
            )
            alloc = self._apply_effective_gas_reserve_to_allocation(
                alloc, budget, strategy="narrow", bin_count=bin_count,
            )
        else:
            bin_count = int(intent.range_plan.bin_count if intent.range_plan else 100)
            if intent.capital_plan and intent.capital_plan.requires_inventory_prep:
                budget, prep_result = self._prepare_wide_entry_inventory(
                    ctx.wallet_address,
                    budget,
                    bin_count=bin_count,
                    dry_run=ctx.dry_run,
                    timestamp=ctx.timestamp,
                )
                if prep_result is not None:
                    return prep_result
            alloc = self.balance.calculate_lp_allocation(
                ctx.wallet_address,
                target_pct=float(intent.capital_plan.target_pct),
                budget=budget,
                min_size_usdt=self.settings.min_position_size_usdt,
            )
            alloc = self._apply_effective_gas_reserve_to_allocation(
                alloc, budget, strategy="wide", bin_count=bin_count,
            )

        if not alloc.is_viable:
            logger.warning("Insufficient capital for %s: %s", strategy, alloc.reason)
            return {
                "action": "skip",
                "strategy": strategy,
                "reason": alloc.reason,
                "timestamp": ctx.timestamp,
            }

        if intent.action == "top_up":
            fill_ok, fill_estimate = self._top_up_expected_fill_is_viable(
                strategy=strategy,
                alloc=alloc,
                bin_count=bin_count,
                params=params,
            )
            if not fill_ok:
                return {
                    "action": "hold",
                    "strategy": strategy,
                    "reason": "top_up_expected_fill_below_minimum",
                    "lp_mode": fill_estimate.get("active_mode") if fill_estimate else None,
                    "expected_fill_value_usdt": (
                        float(fill_estimate.get("used_value_usdt") or 0)
                        if fill_estimate else None
                    ),
                    "requested_value_usdt": (
                        float(fill_estimate.get("requested_value_usdt") or 0)
                        if fill_estimate else None
                    ),
                    "timestamp": ctx.timestamp,
                }

        return self._create_position_with_retry(
            strategy=strategy,
            alloc=alloc,
            bin_count=bin_count,
            params=params,
            dry_run=ctx.dry_run,
            timestamp=ctx.timestamp,
        )
