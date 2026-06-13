"""Cross-module data models for the Merchant Moe (Mantle) farming bot.

All dataclasses that cross module boundaries live here.
Internal/temporary structures stay in their respective modules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from .utils import serialize_decimal


# ── Token & Pool ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class TokenInfo:
    address: str
    name: str
    symbol: str
    decimals: int


@dataclass(frozen=True)
class PoolState:
    pair_address: str
    token_x: TokenInfo
    token_y: TokenInfo
    bin_step: int
    active_bin_id: int
    price_y_per_x: Decimal
    price_y_per_x_raw_128x128: int
    mnt_price_usdt: Decimal | None
    reserve_x_raw: int
    reserve_x_normalized: Decimal
    reserve_y_raw: int
    reserve_y_normalized: Decimal
    # Fee parameters — kept as dicts (rarely accessed, not worth typing)
    protocol_fee_x_raw: int
    protocol_fee_y_raw: int
    static_fee_parameters: dict[str, int] = field(default_factory=dict)
    variable_fee_parameters: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict (matches original dict format)."""
        return {
            "pair_address": self.pair_address,
            "token_x": asdict(self.token_x),
            "token_y": asdict(self.token_y),
            "bin_step": self.bin_step,
            "active_bin_id": self.active_bin_id,
            "price_y_per_x": serialize_decimal(self.price_y_per_x),
            "price_y_per_x_raw_128x128": str(self.price_y_per_x_raw_128x128),
            "mnt_price_usdt": serialize_decimal(self.mnt_price_usdt) if self.mnt_price_usdt else None,
            "reserve_x_raw": str(self.reserve_x_raw),
            "reserve_x_normalized": serialize_decimal(self.reserve_x_normalized),
            "reserve_y_raw": str(self.reserve_y_raw),
            "reserve_y_normalized": serialize_decimal(self.reserve_y_normalized),
            "protocol_fee_x_raw": str(self.protocol_fee_x_raw),
            "protocol_fee_y_raw": str(self.protocol_fee_y_raw),
            "static_fee_parameters": self.static_fee_parameters,
            "variable_fee_parameters": self.variable_fee_parameters,
        }


# ── Position ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BinState:
    bin_id: int
    wallet_lb_token_balance_raw: int
    # Inventory fields — None when include_inventory=False
    bin_total_supply_raw: int | None = None
    bin_reserve_x_raw: int | None = None
    bin_reserve_y_raw: int | None = None
    estimated_token_x: Decimal | None = None
    estimated_token_y: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "bin_id": self.bin_id,
            "wallet_lb_token_balance_raw": str(self.wallet_lb_token_balance_raw),
        }
        if self.bin_total_supply_raw is not None:
            d["bin_total_supply_raw"] = str(self.bin_total_supply_raw)
            d["bin_reserve_x_raw"] = str(self.bin_reserve_x_raw)
            d["bin_reserve_y_raw"] = str(self.bin_reserve_y_raw)
            d["estimated_token_x"] = serialize_decimal(self.estimated_token_x)
            d["estimated_token_y"] = serialize_decimal(self.estimated_token_y)
        return d


@dataclass(frozen=True)
class PositionState:
    wallet_address: str
    candidate_bin_ids: list[int]
    active_bins: list[BinState]
    position_exists: bool
    in_range: bool
    min_bin_id: int | None
    max_bin_id: int | None
    estimated_token_x: Decimal | None  # total across bins
    estimated_token_y: Decimal | None
    inventory_included: bool = True

    @property
    def bin_count(self) -> int:
        return len(self.active_bins)

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet_address": self.wallet_address,
            "candidate_bin_ids": self.candidate_bin_ids,
            "active_bins": [b.to_dict() for b in self.active_bins],
            "position_exists": self.position_exists,
            "in_range": self.in_range,
            "min_bin_id": self.min_bin_id,
            "max_bin_id": self.max_bin_id,
            "estimated_token_x": serialize_decimal(self.estimated_token_x) if self.estimated_token_x is not None else "0",
            "estimated_token_y": serialize_decimal(self.estimated_token_y) if self.estimated_token_y is not None else "0",
            "inventory_included": self.inventory_included,
        }


# ── Balances ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NativeBalance:
    symbol: str  # "MNT"
    raw: int
    normalized: Decimal

    def to_dict(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "raw": str(self.raw),
            "normalized": serialize_decimal(self.normalized),
        }


@dataclass(frozen=True)
class ERC20Balance:
    token: TokenInfo
    raw: int
    normalized: Decimal
    router_allowance_raw: int
    router_allowance_normalized: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": asdict(self.token),
            "raw": str(self.raw),
            "normalized": serialize_decimal(self.normalized),
            "router_allowance_raw": str(self.router_allowance_raw),
            "router_allowance_normalized": serialize_decimal(self.router_allowance_normalized),
        }


@dataclass(frozen=True)
class WalletBalances:
    """Combined wallet state — MNT + WMNT + USDT."""
    native_mnt: NativeBalance
    wmnt: ERC20Balance
    usdt: ERC20Balance
    mnt_price_usdt: Decimal | None

    @property
    def total_mnt_equivalent(self) -> Decimal:
        return self.native_mnt.normalized + self.wmnt.normalized

    @property
    def total_value_usdt(self) -> Decimal | None:
        if self.mnt_price_usdt is None:
            return None
        return self.total_mnt_equivalent * self.mnt_price_usdt + self.usdt.normalized

    def to_dict(self) -> dict[str, Any]:
        return {
            "native_mnt": self.native_mnt.to_dict(),
            "wmnt": self.wmnt.to_dict(),
            "usdt": self.usdt.to_dict(),
            "estimated_total_value_usdt": (
                serialize_decimal(self.total_value_usdt)
                if self.total_value_usdt is not None
                else None
            ),
        }


# ── Swap ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SwapQuote:
    amount_in_raw: int
    amount_in_left_raw: int
    amount_out_raw: int
    fee_raw: int
    amount_out: Decimal  # normalized
    swap_for_y: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount_in_raw": str(self.amount_in_raw),
            "amount_in_left_raw": str(self.amount_in_left_raw),
            "amount_out_raw": str(self.amount_out_raw),
            "fee_raw": str(self.fee_raw),
            "amount_out": serialize_decimal(self.amount_out),
            "swap_for_y": self.swap_for_y,
        }


# ── Execution ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExecutionResult:
    action: str
    tx_hash: str | None
    dry_run: bool
    details: dict[str, Any]


@dataclass(frozen=True)
class TransactionGasReport:
    gas_used: int | None = None
    gas_expected: int | None = None
    gas_limit: int | None = None
    note: str | None = None


# ── Capital Budget ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class CapitalBudget:
    """Tracks deployed vs free capital."""
    total_mnt: Decimal
    total_usdt: Decimal
    deployed_mnt: Decimal
    deployed_usdt: Decimal
    free_mnt: Decimal
    free_usdt: Decimal
    gas_reserve_mnt: Decimal
    mnt_price_usdt: Decimal

    @property
    def free_value_usdt(self) -> Decimal:
        return self.free_mnt * self.mnt_price_usdt + self.free_usdt

    @property
    def deployed_value_usdt(self) -> Decimal:
        return self.deployed_mnt * self.mnt_price_usdt + self.deployed_usdt

    @property
    def total_value_usdt(self) -> Decimal:
        return self.total_mnt * self.mnt_price_usdt + self.total_usdt

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_mnt": str(self.total_mnt),
            "total_usdt": str(self.total_usdt),
            "deployed_mnt": str(self.deployed_mnt),
            "deployed_usdt": str(self.deployed_usdt),
            "free_mnt": str(self.free_mnt),
            "free_usdt": str(self.free_usdt),
            "gas_reserve_mnt": str(self.gas_reserve_mnt),
            "mnt_price_usdt": str(self.mnt_price_usdt),
            "free_value_usdt": str(self.free_value_usdt),
            "deployed_value_usdt": str(self.deployed_value_usdt),
            "total_value_usdt": str(self.total_value_usdt),
        }


# ── Rebalance ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RebalanceState:
    """Current portfolio balance state."""
    wallet_address: str
    mnt_native: Decimal
    wmnt: Decimal
    mnt_total: Decimal
    usdt: Decimal
    mnt_price_usdt: Decimal
    mnt_value_usdt: Decimal
    total_value_usdt: Decimal
    mnt_weight: Decimal
    usdt_weight: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet_address": self.wallet_address,
            "mnt_native": serialize_decimal(self.mnt_native),
            "wmnt": serialize_decimal(self.wmnt),
            "mnt_total": serialize_decimal(self.mnt_total),
            "usdt": serialize_decimal(self.usdt),
            "mnt_price_usdt": serialize_decimal(self.mnt_price_usdt),
            "mnt_value_usdt": serialize_decimal(self.mnt_value_usdt),
            "total_value_usdt": serialize_decimal(self.total_value_usdt),
            "mnt_weight": serialize_decimal(self.mnt_weight, 6),
            "usdt_weight": serialize_decimal(self.usdt_weight, 6),
        }


@dataclass(frozen=True)
class RebalancePlan:
    """What swap to execute to reach target ratio."""
    action: str  # "sell_mnt" | "buy_mnt" | "none"
    within_tolerance: bool
    tolerance_bps: int
    target_weight: str
    current_mnt_weight: str
    current_usdt_weight: str
    trade_value_usdt: str
    amount_in_token: str
    amount_in: str
    amount_out_token: str | None
    quoted_amount_out: str | None
    details: dict[str, str]


@dataclass(frozen=True)
class LpAllocation:
    """How much to allocate to LP creation."""
    amount_wmnt: Decimal
    amount_usdt: Decimal
    is_viable: bool
    reason: str
