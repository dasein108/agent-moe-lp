from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import CapitalBudget, PoolState, PositionState


@dataclass(frozen=True)
class CycleContext:
    """Normalized market and wallet state for one farm cycle."""

    wallet_address: str
    timestamp: str
    dry_run: bool
    pool_state: PoolState
    position: PositionState
    budget: CapitalBudget
    keltner: dict[str, Any] | None = None
    selected_strategy: str | None = None
    top_up_candidate: str | None = None
    bias_signal: dict[str, Any] | None = None
    reentry_summary: dict[str, Any] | None = None


def build_cycle_context(
    *,
    wallet_address: str,
    timestamp: str,
    dry_run: bool,
    pool_state: PoolState,
    position: PositionState,
    budget: CapitalBudget,
    keltner: dict[str, Any] | None = None,
    selected_strategy: str | None = None,
    top_up_candidate: str | None = None,
    bias_signal: dict[str, Any] | None = None,
    reentry_summary: dict[str, Any] | None = None,
) -> CycleContext:
    return CycleContext(
        wallet_address=wallet_address,
        timestamp=timestamp,
        dry_run=dry_run,
        pool_state=pool_state,
        position=position,
        budget=budget,
        keltner=keltner,
        selected_strategy=selected_strategy,
        top_up_candidate=top_up_candidate,
        bias_signal=bias_signal,
        reentry_summary=reentry_summary,
    )
