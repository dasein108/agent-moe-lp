from __future__ import annotations

from typing import Any, Protocol

from ..orchestration.cycle_context import CycleContext
from ..strategy_types import StrategyIntent


class StrategyProfile(Protocol):
    """Strategy-kernel contract for cycle and re-entry intent assembly."""

    profile_id: str

    def build_cycle_intent(self, ctx: CycleContext) -> StrategyIntent:
        ...

    def build_reentry_intent(
        self,
        ctx: CycleContext,
        *,
        strategy: str,
        reentry_policy_result: dict[str, Any] | None,
    ) -> StrategyIntent:
        ...
