from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


StrategyAction = Literal[
    "hold",
    "enter",
    "top_up",
    "exit",
    "exit_and_reenter",
    "rebalance_then_enter",
]


@dataclass(frozen=True)
class RangePlan:
    """Normalized LP range selection for a strategy intent."""

    bin_count: int | None = None
    distribution_params: dict[str, Any] | None = None
    source: str = "legacy"
    reason: str | None = None


@dataclass(frozen=True)
class RatioPlan:
    """Target inventory mix associated with a strategy intent."""

    target_mnt_ratio_bps: int | None = None
    source: str = "legacy"
    reason: str | None = None


@dataclass(frozen=True)
class CapitalPlan:
    """Capital-use policy associated with a strategy intent."""

    target_pct: float | None = None
    top_up: bool = False
    requires_inventory_prep: bool = False
    source: str = "legacy"
    reason: str | None = None


@dataclass(frozen=True)
class ShapePlan:
    """LP shape or distribution bias attached to a strategy intent."""

    distribution_params: dict[str, Any] | None = None
    source: str = "legacy"
    bucket: str | None = None


@dataclass(frozen=True)
class StrategyIntent:
    """Single normalized action emitted by a strategy profile or legacy adapter."""

    action: StrategyAction
    profile_id: str
    strategy_id: str | None = None
    reason: str | None = None
    range_plan: RangePlan | None = None
    ratio_plan: RatioPlan | None = None
    capital_plan: CapitalPlan | None = None
    shape_plan: ShapePlan | None = None
    execution_notes: tuple[str, ...] = ()
    telemetry: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionOutcome:
    """Normalized result returned by the future execution core."""

    status: str
    action: str
    reason: str | None = None
    gas_mnt: float | None = None
    tx_hashes: tuple[str, ...] = ()
    telemetry: dict[str, Any] = field(default_factory=dict)
