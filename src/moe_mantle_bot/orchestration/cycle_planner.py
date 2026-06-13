from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .cycle_context import CycleContext, build_cycle_context
from ..strategy_types import StrategyIntent


@dataclass(frozen=True)
class PlannedCycleDecision:
    """Normalized pre-execution decision for a single farm cycle."""

    selected_strategy: str
    top_up_candidate: str | None
    cycle_context: CycleContext
    intent: StrategyIntent


class SinglePositionCyclePlanner:
    """Assemble the legacy single-position cycle decision before execution."""

    def __init__(
        self,
        *,
        select_strategy: Callable[..., str],
        resolve_top_up_strategy: Callable[..., str | None],
        strategy_profile,
    ) -> None:
        self._select_strategy = select_strategy
        self._resolve_top_up_strategy = resolve_top_up_strategy
        self._strategy_profile = strategy_profile

    def plan(
        self,
        *,
        wallet_address: str,
        timestamp: str,
        dry_run: bool,
        pool_state,
        position,
        budget,
        keltner: dict[str, Any] | None = None,
    ) -> PlannedCycleDecision:
        selected_strategy = self._select_strategy(position, keltner, pool_state=pool_state, budget=budget)
        top_up_candidate = None
        if selected_strategy == "hold":
            top_up_candidate = self._resolve_top_up_strategy(
                wallet_address,
                position,
                budget,
                keltner_analysis=keltner,
            )
        cycle_context = build_cycle_context(
            wallet_address=wallet_address,
            timestamp=timestamp,
            dry_run=dry_run,
            pool_state=pool_state,
            position=position,
            budget=budget,
            keltner=keltner,
            selected_strategy=selected_strategy,
            top_up_candidate=top_up_candidate,
        )
        intent = self._strategy_profile.build_cycle_intent(cycle_context)
        return PlannedCycleDecision(
            selected_strategy=selected_strategy,
            top_up_candidate=top_up_candidate,
            cycle_context=cycle_context,
            intent=intent,
        )
