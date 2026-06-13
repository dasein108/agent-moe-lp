"""Single authority on LP positions.

Owns: pool state, position state, bin discovery, add/remove liquidity,
position tracking (registry), reconciliation, validation.

Composes RpcClient + TxSender + BalanceManager. No inheritance.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError, Timeout
from web3.exceptions import ContractLogicError, Web3RPCError

from .abi import LB_FACTORY_ABI, LB_ROUTER_ABI, WMNT_ABI
from .balance_manager import BalanceManager
from .config import Settings
from .logging_config import get_logger
from .lp_shapes import apply_shape_to_allocations, calculate_slope_weights
from .models import (
    BinState,
    ExecutionResult,
    PoolState,
    PositionState,
    TokenInfo,
)
from .rpc_client import RpcClient
from .tx_sender import PreviewValidationError, TxSender
from .utils import price_128x128_to_decimal, price_from_bin_id, scaled_decimal, serialize_decimal

ONE = 10**18

logger = get_logger(__name__)


def resolve_pool_token_roles(rpc: RpcClient, settings: Settings) -> Settings:
    """Resolve the cash/quote token for an arbitrary LB pool from on-chain data.

    Reads ``tokenX``/``tokenY`` from the configured pool. Keeps ``wmnt_address``
    as the native wrapped token (required for native wrap/unwrap and gas
    replenishment) and sets ``usdt_address`` to the *paired* token — the
    cash/quote side the bot holds and rebalances into.

    For the default WMNT/USDT pool this resolves to USDT unchanged, so existing
    behavior is preserved exactly. For any WMNT/<token> pool it adapts the cash
    token automatically. Pools where neither token is the native wrapped token
    are not yet supported and raise explicitly (we cannot wrap/unwrap for gas).
    """
    pool = rpc.get_pair_contract(rpc.checksum(settings.pool_address))
    token_x = rpc.checksum(pool.functions.getTokenX().call())
    token_y = rpc.checksum(pool.functions.getTokenY().call())
    wmnt = rpc.checksum(settings.wmnt_address).lower()

    if token_x.lower() == wmnt:
        quote = token_y
    elif token_y.lower() == wmnt:
        quote = token_x
    else:
        raise RuntimeError(
            f"Pool {settings.pool_address} pairs {token_x}/{token_y}; neither is the "
            f"native wrapped token {settings.wmnt_address}. Non-WMNT pools are not yet "
            f"supported — set WMNT_ADDRESS for this chain or choose a WMNT-paired pool."
        )

    if rpc.checksum(settings.usdt_address) == quote:
        return settings  # already correct (default pool) — no change
    logger.info(
        "Resolved pool %s quote token → %s (was %s)",
        settings.pool_address, quote, settings.usdt_address,
    )
    return replace(settings, usdt_address=quote)


class LPService:
    """Single authority on LP positions."""

    DEFAULT_FALLBACK_LOG_LOOKBACK = 5_000_000
    ACTIVE_BIN_SEARCH_CHUNK = 100
    # Dust bins have ~34k LBToken balance — residue from prior positions that
    # can't be removed on-chain. Excluded from range calculation and removal.
    # Threshold to distinguish real LP bins from residual dust left by partial fills.
    # Real bins typically have balances >1e18; partial-fill residue is ~1e6.
    # A threshold of 1e12 cleanly separates them without filtering real positions.
    DUST_LB_TOKEN_THRESHOLD = 1_000_000_000_000  # 1e12

    def __init__(
        self,
        rpc: RpcClient,
        tx: TxSender,
        balance: BalanceManager,
        settings: Settings,
    ) -> None:
        self.rpc = rpc
        self.tx = tx
        self.balance = balance
        self.settings = settings

        self._pool_address = rpc.checksum(settings.pool_address)
        self._router_address = rpc.checksum(settings.moe_router_address)
        self._wmnt_address = rpc.checksum(settings.wmnt_address)
        self._usdt_address = rpc.checksum(settings.usdt_address)

        self._pool = rpc.get_pair_contract(self._pool_address)
        self._router = rpc.get_contract(self._router_address, LB_ROUTER_ABI)
        self._factory = rpc.get_contract(rpc.checksum(settings.moe_factory_address), LB_FACTORY_ABI)
        self._wmnt_contract = rpc.get_contract(self._wmnt_address, WMNT_ABI)
        self._token_cache: dict[str, TokenInfo] = {}
        self._deployment_block_cache: int | None = None

    @classmethod
    def read_only(cls, rpc: RpcClient, settings: Settings) -> LPService:
        """Create a read-only LPService (no TxSender/BalanceManager).

        Safe for pool state, position reads, bin discovery.
        Calling create_position/remove_position will raise AttributeError.
        """
        instance = cls.__new__(cls)
        instance.rpc = rpc
        instance.settings = settings
        instance._pool_address = rpc.checksum(settings.pool_address)
        instance._router_address = rpc.checksum(settings.moe_router_address)
        instance._wmnt_address = rpc.checksum(settings.wmnt_address)
        instance._usdt_address = rpc.checksum(settings.usdt_address)
        instance._pool = rpc.get_pair_contract(instance._pool_address)
        instance._router = rpc.get_contract(instance._router_address, LB_ROUTER_ABI)
        instance._factory = rpc.get_contract(rpc.checksum(settings.moe_factory_address), LB_FACTORY_ABI)
        instance._wmnt_contract = rpc.get_contract(instance._wmnt_address, WMNT_ABI)
        instance._token_cache = {}
        instance._deployment_block_cache = None
        return instance

    # ── Token Helpers ──────────────────────────────────────

    def get_token_info(self, address: str) -> TokenInfo:
        checksum = self.rpc.checksum(address)
        if checksum in self._token_cache:
            return self._token_cache[checksum]
        contract = self.rpc.get_erc20_contract(checksum)
        token = TokenInfo(
            address=checksum,
            name=contract.functions.name().call(),
            symbol=contract.functions.symbol().call(),
            decimals=int(contract.functions.decimals().call()),
        )
        self._token_cache[checksum] = token
        return token

    # ── Pool State ─────────────────────────────────────────

    def get_pool_state(self) -> PoolState:
        def _read():
            token_x_address = self._pool.functions.getTokenX().call()
            token_y_address = self._pool.functions.getTokenY().call()
            token_x = self.get_token_info(token_x_address)
            token_y = self.get_token_info(token_y_address)

            bin_step = int(self._pool.functions.getBinStep().call())
            active_bin_id = int(self._pool.functions.getActiveId().call())
            reserve_x_raw, reserve_y_raw = self._pool.functions.getReserves().call()
            fee_x_raw, fee_y_raw = self._pool.functions.getProtocolFees().call()
            static_fees = self._pool.functions.getStaticFeeParameters().call()
            variable_fees = self._pool.functions.getVariableFeeParameters().call()
            price_raw = int(self._pool.functions.getPriceFromId(active_bin_id).call())
            price_y_per_x = price_128x128_to_decimal(price_raw) * (
                Decimal(10) ** (token_x.decimals - token_y.decimals)
            )
            mnt_price_usdt = self._infer_mnt_price(token_x, token_y, price_y_per_x)

            return PoolState(
                pair_address=self._pool_address,
                token_x=token_x,
                token_y=token_y,
                bin_step=bin_step,
                active_bin_id=active_bin_id,
                price_y_per_x=price_y_per_x,
                price_y_per_x_raw_128x128=price_raw,
                mnt_price_usdt=mnt_price_usdt,
                reserve_x_raw=int(reserve_x_raw),
                reserve_x_normalized=scaled_decimal(reserve_x_raw, token_x.decimals),
                reserve_y_raw=int(reserve_y_raw),
                reserve_y_normalized=scaled_decimal(reserve_y_raw, token_y.decimals),
                protocol_fee_x_raw=int(fee_x_raw),
                protocol_fee_y_raw=int(fee_y_raw),
                static_fee_parameters={
                    "base_factor": static_fees[0],
                    "filter_period": static_fees[1],
                    "decay_period": static_fees[2],
                    "reduction_factor": static_fees[3],
                    "variable_fee_control": static_fees[4],
                    "protocol_share": static_fees[5],
                    "max_volatility_accumulator": static_fees[6],
                },
                variable_fee_parameters={
                    "volatility_accumulator": variable_fees[0],
                    "volatility_reference": variable_fees[1],
                    "id_reference": variable_fees[2],
                    "time_of_last_update": variable_fees[3],
                },
            )

        return self.rpc.call_with_retry("get_pool_state", _read)

    def _infer_mnt_price(
        self, token_x: TokenInfo, token_y: TokenInfo, price_y_per_x: Decimal,
    ) -> Decimal | None:
        x = token_x.address.lower()
        y = token_y.address.lower()
        if x == self._wmnt_address.lower() and y == self._usdt_address.lower():
            return price_y_per_x
        if x == self._usdt_address.lower() and y == self._wmnt_address.lower():
            return Decimal(1) / price_y_per_x if price_y_per_x else None
        return None

    # ── Position State ─────────────────────────────────────

    def get_position(
        self,
        wallet: str,
        *,
        pool_state: PoolState | None = None,
        deep_search: bool = False,
        include_inventory: bool = True,
    ) -> PositionState:
        checksum = self.rpc.checksum(wallet)
        pool = pool_state or self.get_pool_state()
        token_x = pool.token_x
        token_y = pool.token_y

        candidate_ids = self._discover_bins_near_active(checksum)
        if not candidate_ids and deep_search:
            candidate_ids = self._discover_bins_from_logs(checksum)

        _empty = PositionState(
            wallet_address=checksum,
            candidate_bin_ids=candidate_ids,
            active_bins=[],
            position_exists=False,
            in_range=False,
            min_bin_id=None,
            max_bin_id=None,
            estimated_token_x=Decimal(0),
            estimated_token_y=Decimal(0),
            inventory_included=include_inventory,
        )

        if not candidate_ids:
            return _empty

        balances = self._pool.functions.balanceOfBatch(
            [checksum] * len(candidate_ids), candidate_ids,
        ).call()

        active_bins: list[BinState] = []
        total_x = Decimal(0)
        total_y = Decimal(0)

        for bin_id, user_balance in zip(candidate_ids, balances, strict=True):
            user_balance = int(user_balance)
            if user_balance == 0:
                continue
            if include_inventory:
                total_supply = int(self._pool.functions.totalSupply(bin_id).call())
                rx_raw, ry_raw = self._pool.functions.getBin(bin_id).call()
                share = Decimal(user_balance) / Decimal(total_supply) if total_supply else Decimal(0)
                user_x = scaled_decimal(int(rx_raw), token_x.decimals) * share
                user_y = scaled_decimal(int(ry_raw), token_y.decimals) * share
                total_x += user_x
                total_y += user_y
                active_bins.append(BinState(
                    bin_id=int(bin_id), wallet_lb_token_balance_raw=user_balance,
                    bin_total_supply_raw=total_supply, bin_reserve_x_raw=int(rx_raw),
                    bin_reserve_y_raw=int(ry_raw), estimated_token_x=user_x, estimated_token_y=user_y,
                ))
            else:
                active_bins.append(BinState(bin_id=int(bin_id), wallet_lb_token_balance_raw=user_balance))

        if not active_bins:
            return _empty

        # Separate real bins from dust for range calculation.
        # Dust bins (~34k LBToken) are leftover residue that can't be removed.
        # They must not affect in_range, min_bin_id, max_bin_id, or bin_count.
        real_bins = [b for b in active_bins if b.wallet_lb_token_balance_raw >= self.DUST_LB_TOKEN_THRESHOLD]
        if not real_bins:
            # All bins are dust — treat as no position
            return _empty

        real_bin_ids = [b.bin_id for b in real_bins]
        return PositionState(
            wallet_address=checksum,
            candidate_bin_ids=candidate_ids,
            active_bins=real_bins,
            position_exists=True,
            in_range=pool.active_bin_id in set(real_bin_ids),
            min_bin_id=min(real_bin_ids),
            max_bin_id=max(real_bin_ids),
            estimated_token_x=total_x if include_inventory else None,
            estimated_token_y=total_y if include_inventory else None,
            inventory_included=include_inventory,
        )

    def has_active_position(self, wallet: str) -> bool:
        pos = self.get_position(wallet, include_inventory=False)
        return pos.position_exists

    def is_in_range(self, wallet: str) -> bool:
        pos = self.get_position(wallet, include_inventory=False)
        return pos.in_range

    def get_position_range(self, wallet: str) -> tuple[int | None, int | None]:
        pos = self.get_position(wallet, include_inventory=False)
        return pos.min_bin_id, pos.max_bin_id

    # ── Bin Discovery ──────────────────────────────────────

    def _discover_bins_near_active(self, wallet: str) -> list[int]:
        checksum = self.rpc.checksum(wallet)
        active_id = int(self._pool.functions.getActiveId().call())

        seed_ids: list[int] = []
        for width in (20, 50, 100, 200):
            start = max(0, active_id - width)
            ids = list(range(start, active_id + width + 1))
            balances = self._pool.functions.balanceOfBatch([checksum] * len(ids), ids).call()
            nonzero = [bid for bid, bal in zip(ids, balances, strict=True) if int(bal) > 0]
            if not nonzero:
                continue
            seed_ids = nonzero
            if nonzero[0] != ids[0] and nonzero[-1] != ids[-1]:
                return nonzero

        if not seed_ids:
            return []

        min_bin = min(seed_ids)
        max_bin = max(seed_ids)

        while min_bin > 0:
            chunk_start = max(0, min_bin - self.ACTIVE_BIN_SEARCH_CHUNK)
            ids = list(range(chunk_start, min_bin))
            if not ids:
                break
            balances = self._pool.functions.balanceOfBatch([checksum] * len(ids), ids).call()
            nonzero = [bid for bid, bal in zip(ids, balances, strict=True) if int(bal) > 0]
            if not nonzero:
                break
            min_bin = min(nonzero)

        while True:
            ids = list(range(max_bin + 1, max_bin + 1 + self.ACTIVE_BIN_SEARCH_CHUNK))
            balances = self._pool.functions.balanceOfBatch([checksum] * len(ids), ids).call()
            nonzero = [bid for bid, bal in zip(ids, balances, strict=True) if int(bal) > 0]
            if not nonzero:
                break
            max_bin = max(nonzero)

        return list(range(min_bin, max_bin + 1))

    def _discover_bins_from_logs(self, wallet: str) -> list[int]:
        checksum = self.rpc.checksum(wallet)
        latest_block = self.rpc.block_number
        from_block = (
            self.settings.log_scan_start_block
            if self.settings.log_scan_start_block > 0
            else self._get_deployment_block()
        )
        wallet_topic = RpcClient.wallet_topic(checksum)
        received = self.rpc.scan_transfer_logs(
            pair_contract=self._pool, pool_address=self._pool_address,
            indexed_topic_position=3, wallet_topic=wallet_topic,
            from_block=from_block, to_block=latest_block,
            chunk_size=max(1, self.settings.log_scan_chunk_size),
        )
        sent = self.rpc.scan_transfer_logs(
            pair_contract=self._pool, pool_address=self._pool_address,
            indexed_topic_position=2, wallet_topic=wallet_topic,
            from_block=from_block, to_block=latest_block,
            chunk_size=max(1, self.settings.log_scan_chunk_size),
        )
        return sorted(received | sent)

    def _get_deployment_block(self) -> int:
        if self._deployment_block_cache is not None:
            return self._deployment_block_cache
        latest = self.rpc.block_number
        try:
            code = self.rpc.get_code(self._pool_address, block=latest)
            if code in (b"\x00", b""):
                raise RuntimeError(f"No bytecode for pool {self._pool_address}")
            low, high = 0, latest
            while low < high:
                mid = (low + high) // 2
                c = self.rpc.get_code(self._pool_address, block=mid)
                if c not in (b"\x00", b""):
                    high = mid
                else:
                    low = mid + 1
            self._deployment_block_cache = low
            return low
        except Web3RPCError:
            fallback = max(0, latest - self.DEFAULT_FALLBACK_LOG_LOOKBACK)
            self._deployment_block_cache = fallback
            return fallback

    def get_bin_balances(self, wallet: str, bin_ids: list[int]) -> list[int]:
        def _read():
            checksum = self.rpc.checksum(wallet)
            if not bin_ids:
                return []
            balances = self._pool.functions.balanceOfBatch(
                [checksum] * len(bin_ids), bin_ids,
            ).call()
            return [int(b) for b in balances]
        return self.rpc.call_with_retry("get_bin_balances", _read)

    # ── LP Create / Remove ───────────────────────────────

    def create_position(
        self,
        *,
        amount_wmnt: Decimal,
        amount_usdt: Decimal,
        bin_count: int | None = None,
        slippage_bps: int | None = None,
        distribution_params: dict[str, Any] | None = None,
        strategy_type: str = "narrow",
        skip_preview_native_conversion: bool = False,
        dry_run: bool = True,
    ) -> list[ExecutionResult]:
        # Read pool state fresh to avoid stale active_bin causing WrongAmounts
        pool_state = self.get_pool_state()
        token_x = self.rpc.checksum(pool_state.token_x.address)
        token_y = self.rpc.checksum(pool_state.token_y.address)
        if {token_x.lower(), token_y.lower()} != {self._wmnt_address.lower(), self._usdt_address.lower()}:
            raise RuntimeError("Pool token set does not match configured WMNT/USDT")

        raw_wmnt = self.balance._token_to_raw(self._wmnt_address, amount_wmnt)
        raw_usdt = self.balance._token_to_raw(self._usdt_address, amount_usdt)
        amount_x = raw_wmnt if token_x.lower() == self._wmnt_address.lower() else raw_usdt
        amount_y = raw_usdt if token_y.lower() == self._usdt_address.lower() else raw_wmnt
        slippage = self.settings.slippage_bps if slippage_bps is None else slippage_bps
        use_bin_count = self.settings.bin_count if bin_count is None else bin_count
        delta_ids = self._lp_range_delta_ids(use_bin_count, self.settings.position_upside_pct)

        # ── Phase 1: Setup (wrap MNT, approve, pre-flight) ──
        native_lp = self._native_lp_support(token_x=token_x, token_y=token_y, amount_x=amount_x, amount_y=amount_y)
        wallet = self.tx.wallet_address

        # Wrap native MNT → WMNT for ERC20 addLiquidity path (value=0).
        # With value=0, only gas_reserve native is needed for estimateGas.
        if not native_lp["enabled"] and not dry_run and self.balance is not None:
            wmnt_is_token_x = token_x.lower() == self._wmnt_address.lower()
            wmnt_amount_raw = amount_x if wmnt_is_token_x else amount_y
            if wmnt_amount_raw > 0:
                wmnt_balance = self.balance.get_erc20_balance(wallet, self._wmnt_address)
                if wmnt_balance.raw < wmnt_amount_raw:
                    shortfall_raw = wmnt_amount_raw - wmnt_balance.raw
                    # Keep only gas_reserve as native (value=0 needs minimal headroom)
                    native_keep_raw = int(
                        Decimal(str(self.settings.gas_reserve_mnt)) * Decimal(10**18)
                    )
                    native_balance = self.balance.get_native_balance(wallet)
                    available_raw = max(0, native_balance.raw - native_keep_raw)
                    wrap_raw = min(shortfall_raw, available_raw)
                    if wrap_raw > 0:
                        wrap_amount = self.balance._raw_to_decimal(self._wmnt_address, wrap_raw)
                        logger.info(
                            "Wrapping %.2f MNT → WMNT for addLiquidity "
                            "(keeping %.0f MNT native for gas)",
                            float(wrap_amount),
                            float(self.settings.gas_reserve_mnt),
                        )
                        self.balance.wrap_mnt(wrap_amount, dry_run=False)
                    # After wrap, clamp amount to actual WMNT available
                    refreshed_wmnt = self.balance.get_erc20_balance(wallet, self._wmnt_address)
                    if refreshed_wmnt.raw < wmnt_amount_raw:
                        clamped = refreshed_wmnt.raw
                        logger.info(
                            "Clamping WMNT amount: requested=%s actual=%s",
                            float(Decimal(wmnt_amount_raw) / Decimal(10**18)),
                            float(refreshed_wmnt.normalized),
                        )
                        if wmnt_is_token_x:
                            amount_x = clamped
                        else:
                            amount_y = clamped

        # Cap native LP value to prevent addLiquidityNATIVE reverts on large msg.value.
        # Large native msg.value can revert addLiquidityNATIVE; cap it defensively.
        if native_lp["enabled"]:
            max_native_mnt = getattr(self.settings, "max_native_lp_value_mnt", 500.0)
            max_native_raw = int(Decimal(str(max_native_mnt)) * Decimal(10**18))
            if int(native_lp["native_value"]) > max_native_raw:
                logger.info(
                    "Capping native LP value: %.0f MNT → %.0f MNT (max_native_lp_value_mnt)",
                    float(Decimal(int(native_lp["native_value"])) / Decimal(10**18)),
                    max_native_mnt,
                )
                native_lp["native_value"] = max_native_raw
                if native_lp["native_token"] == "token_x":
                    amount_x = max_native_raw
                else:
                    amount_y = max_native_raw

        if native_lp["enabled"] and not dry_run and self.balance is not None:
            native_needed = int(native_lp["native_value"])
            gas_buffer = int(Decimal(str(self.settings.gas_reserve_mnt)) * Decimal(10**18))
            estimate_headroom = int(
                Decimal(str(getattr(self.settings, "native_estimate_headroom_mnt", 50)))
                * Decimal(10**18)
            )
            native_balance = self.balance.get_native_balance(wallet)
            total_needed = native_needed + gas_buffer + estimate_headroom
            logger.info(f"Native MNT: have={float(native_balance.normalized):.2f}, "
                        f"need={float(Decimal(native_needed) / Decimal(10**18)):.2f} + "
                        f"{float(self.settings.gas_reserve_mnt):.0f} gas reserve + "
                        f"{float(Decimal(estimate_headroom) / Decimal(10**18)):.0f} estimate headroom")
            self._ensure_native_mnt_headroom(
                wallet=wallet,
                target_native_raw=total_needed,
                dry_run=False,
                reason="add_liquidity native value + gas reserve + estimate headroom",
            )

        # Approvals — only for the non-native token (native is sent via msg.value)
        approvals: list[ExecutionResult] = []
        approval_x = None if native_lp["native_token"] == "token_x" else self.tx.ensure_erc20_approval(token_x, self._router_address, amount_x, dry_run=dry_run)
        approval_y = None if native_lp["native_token"] == "token_y" else self.tx.ensure_erc20_approval(token_y, self._router_address, amount_y, dry_run=dry_run)
        approvals_required: list[str] = []
        if approval_x is not None:
            approvals.append(approval_x)
            approvals_required.append(token_x)
        if approval_y is not None:
            approvals.append(approval_y)
            approvals_required.append(token_y)

        # ── Phase 2: Compute distributions ──
        # Read fresh active_id right before computing distributions.
        active_id = int(self._pool.functions.getActiveId().call())
        if active_id != pool_state.active_bin_id:
            logger.info(f"Active bin: pool_state={pool_state.active_bin_id}, "
                        f"current={active_id} (delta={active_id - pool_state.active_bin_id})")

        distribution_plan = self._liquidity_distributions(
            active_id=active_id, bin_step=pool_state.bin_step,
            token_x=token_x, token_y=token_y,
            amount_x_raw=amount_x, amount_y_raw=amount_y,
            delta_ids=delta_ids,
            **(distribution_params or {}),
        )
        self._validate_live_distribution_mode(distribution_plan=distribution_plan, dry_run=dry_run)
        self._validate_distribution_plan_fill(
            distribution_plan=distribution_plan,
            pool_state=pool_state,
            token_x=token_x,
            token_y=token_y,
            requested_amount_wmnt=amount_wmnt,
            requested_amount_usdt=amount_usdt,
        )
        distribution_x = distribution_plan["distribution_x"]
        distribution_y = distribution_plan["distribution_y"]

        # Zero out unused token amounts to match distributions.
        # Router reverts with WrongAmounts if amount_y > 0 but distribution_y is all zeros.
        active_mode = distribution_plan.get("active_mode", "")
        if active_mode.startswith("x_only") and all(d == 0 for d in distribution_y):
            amount_y = 0
        elif active_mode.startswith("y_only") and all(d == 0 for d in distribution_x):
            amount_x = 0
            if native_lp["enabled"] and native_lp["native_token"] == "token_x":
                native_lp["native_value"] = 0

        deadline = self.tx.deadline()
        logger.info(f"addLiquidity: active_id={active_id}, bins={len(delta_ids)}, "
                     f"mode={active_mode}")

        # Preview only for dry-run (to show what would happen).
        # For live execution, skip preview — it's unreliable due to
        # active bin movement between getActiveId() and the static call.
        # The actual tx uses id_slippage to tolerate small movements.
        preview = None
        if dry_run:
            if skip_preview_native_conversion:
                preview = self._skip_preview_native(amount_wmnt)
            elif approvals_required:
                preview = self._skip_preview_approvals(approvals_required)
            else:
                preview = self._preview_add_liquidity(
                    token_x=token_x, token_y=token_y, bin_step=pool_state.bin_step,
                    amount_x=amount_x, amount_y=amount_y, active_id=active_id,
                    delta_ids=delta_ids, distribution_x=distribution_x, distribution_y=distribution_y,
                    deadline=deadline, use_native=bool(native_lp["enabled"]),
                    native_value=int(native_lp["native_value"]),
                    id_slippage=self.settings.id_slippage,
                )

        if preview and preview.get("status") == "ok":
            preview_amount_x = int(preview["amount_x_added_raw"])
            preview_amount_y = int(preview["amount_y_added_raw"])
        else:
            preview_amount_x, preview_amount_y = amount_x, amount_y

        # Use generous slippage for min amounts (active bin may shift by id_slippage bins)
        amount_x_min = 0
        amount_y_min = 0

        factory_pair_info = self._factory_pair_information(
            token_x=token_x, token_y=token_y, pool_bin_step=pool_state.bin_step,
        )

        preflight = self._lp_add_preflight(
            pool_state=pool_state, amount_wmnt=amount_wmnt, amount_usdt=amount_usdt,
            amount_x=amount_x, amount_y=amount_y, amount_x_min=amount_x_min, amount_y_min=amount_y_min,
            active_id=active_id, delta_ids=delta_ids, distribution_x=distribution_x, distribution_y=distribution_y,
            deadline=deadline, preview=preview,
            distribution_details=self._distribution_details(distribution_plan),
            factory_pair_info=factory_pair_info,
        )

        # Validate factory pair (catches misconfigured pool address)
        if factory_pair_info.get("status") == "ok":
            mismatch = (
                not factory_pair_info.get("matches_configured_pool", False)
                or not factory_pair_info.get("bin_step_matches_pool", False)
                or factory_pair_info.get("ignored_for_routing", False)
            )
            if mismatch:
                raise PreviewValidationError(
                    action="add_liquidity",
                    message="Configured pool not compatible with factory view.",
                    preview={"status": "factory_pair_mismatch", "factory_pair_info": factory_pair_info},
                    context={"preflight": preflight},
                )
        # For dry-run: block on preview failure
        if dry_run and preview and preview.get("status") == "reverted":
            raise PreviewValidationError(
                action="add_liquidity",
                message="add_liquidity preview reverted (dry-run).",
                preview=preview, context={"preflight": preflight},
            )

        liquidity_params = (
            token_x, token_y, pool_state.bin_step,
            amount_x, amount_y, amount_x_min, amount_y_min,
            active_id, self.settings.id_slippage,
            delta_ids, distribution_x, distribution_y,
            wallet, wallet, deadline,
        )
        function = (
            self._router.functions.addLiquidityNATIVE(liquidity_params)
            if native_lp["enabled"]
            else self._router.functions.addLiquidity(liquidity_params)
        )

        # Snapshot LBToken balances before add (for per-bin tracking)
        target_bin_ids = [active_id + d for d in delta_ids]
        pre_balances: dict[int, int] = {}
        if not dry_run:
            try:
                raw_balances = self._pool.functions.balanceOfBatch(
                    [wallet] * len(target_bin_ids), target_bin_ids,
                ).call()
                pre_balances = dict(zip(target_bin_ids, (int(b) for b in raw_balances)))
            except Exception as e:
                logger.warning(f"Failed to snapshot pre-add balances: {e}")

        # Try gas estimation up to 3 times (it can fail if active bin moves).
        # If all attempts fail, fall back to a generous fixed limit.
        n_bins = len(delta_ids)
        lp_gas_limit = None
        last_gas_error: Exception | None = None
        # Skip estimateGas entirely — use a fixed gas limit.
        # The chain's estimateGas can be unreliable for addLiquidityNATIVE:
        # 1. With gasPrice: returns false "reserve for non-dipping transaction" errors
        # 2. Without gasPrice: returns WrongAmounts on volatile pairs (active bin moves
        #    between distribution computation and estimation)
        # The "last-second active bin check" below catches stale distributions before
        # broadcast, and idSlippage provides on-chain tolerance.
        if not dry_run:
            lp_gas_limit = max(8_000_000, 8_000_000 + (n_bins - 10) * 100_000)
            logger.info(f"Using fixed gas limit: {lp_gas_limit:,} (skipping estimateGas)")

            # ── Last-second active bin check ──
            # Between gas estimation and tx broadcast, the active bin may have
            # moved.  If it did, the distribution arrays (which encode which bin
            # is mixed/x_only/y_only) become invalid and the tx will revert
            # on-chain with WrongAmounts.  Re-read and recompute if needed.
            final_active_id = int(self._pool.functions.getActiveId().call())
            if final_active_id != active_id:
                logger.info(f"Active bin shifted {active_id} -> {final_active_id} before send, "
                            f"recomputing distributions")
                original_mode = distribution_plan.get("active_mode")
                safe_mode = self._resolve_recompute_prefer_mode(
                    original_mode=original_mode,
                    initial_active_id=active_id,
                    final_active_id=final_active_id,
                )
                logger.info(
                    "Recompute mode selection: original_mode=%s prefer_mode=%s",
                    original_mode,
                    safe_mode,
                )
                active_id = final_active_id
                dist_params = dict(distribution_params or {})
                dist_params["prefer_mode"] = safe_mode
                distribution_plan = self._liquidity_distributions(
                    active_id=active_id, bin_step=pool_state.bin_step,
                    token_x=token_x, token_y=token_y,
                    amount_x_raw=amount_x, amount_y_raw=amount_y,
                    delta_ids=delta_ids,
                    **dist_params,
                )
                self._validate_live_distribution_mode(distribution_plan=distribution_plan, dry_run=dry_run)
                self._validate_distribution_plan_fill(
                    distribution_plan=distribution_plan,
                    pool_state=pool_state,
                    token_x=token_x,
                    token_y=token_y,
                    requested_amount_wmnt=amount_wmnt,
                    requested_amount_usdt=amount_usdt,
                )
                distribution_x = distribution_plan["distribution_x"]
                distribution_y = distribution_plan["distribution_y"]
                deadline = self.tx.deadline()
                logger.info(f"Recomputed: active_id={active_id}, "
                            f"mode={distribution_plan.get('active_mode')}")
                preflight = self._lp_add_preflight(
                    pool_state=pool_state, amount_wmnt=amount_wmnt, amount_usdt=amount_usdt,
                    amount_x=amount_x, amount_y=amount_y, amount_x_min=amount_x_min, amount_y_min=amount_y_min,
                    active_id=active_id, delta_ids=delta_ids, distribution_x=distribution_x, distribution_y=distribution_y,
                    deadline=deadline, preview=preview,
                    distribution_details=self._distribution_details(distribution_plan),
                    factory_pair_info=factory_pair_info,
                )
                liquidity_params = (
                    token_x, token_y, pool_state.bin_step,
                    amount_x, amount_y, amount_x_min, amount_y_min,
                    active_id, self.settings.id_slippage,
                    delta_ids, distribution_x, distribution_y,
                    wallet, wallet, deadline,
                )
                function = (
                    self._router.functions.addLiquidityNATIVE(liquidity_params)
                    if native_lp["enabled"]
                    else self._router.functions.addLiquidity(liquidity_params)
                )
                target_bin_ids = [active_id + d for d in delta_ids]
                try:
                    raw_balances = self._pool.functions.balanceOfBatch(
                        [wallet] * len(target_bin_ids), target_bin_ids,
                    ).call()
                    pre_balances = dict(zip(target_bin_ids, (int(b) for b in raw_balances)))
                except Exception as e:
                    logger.warning(f"Failed to re-snapshot pre-add balances: {e}")

        logger.info(f"Gas limit: {lp_gas_limit} ({n_bins} bins)")
        approvals.append(self.tx.send(
            "add_liquidity", function, dry_run=dry_run,
            value=int(native_lp["native_value"]),
            gas_limit=lp_gas_limit,
            details={
                "preflight": preflight,
                "amount_wmnt": serialize_decimal(amount_wmnt),
                "amount_usdt": serialize_decimal(amount_usdt),
                "bin_count": use_bin_count, "delta_ids": delta_ids,
                "distribution_x": distribution_x, "distribution_y": distribution_y,
                "distribution_details": self._distribution_details(distribution_plan),
                "active_id": active_id,
                "router_method": native_lp["router_method"],
                "native_value_raw": str(native_lp["native_value"]),
            },
        ))

        # Capture per-bin LBToken amounts minted (post - pre)
        bin_amounts: dict[int, int] | None = None
        if not dry_run:
            try:
                raw_balances = self._pool.functions.balanceOfBatch(
                    [wallet] * len(target_bin_ids), target_bin_ids,
                ).call()
                post_balances = dict(zip(target_bin_ids, (int(b) for b in raw_balances)))
                bin_amounts = {}
                for bid in target_bin_ids:
                    delta = post_balances.get(bid, 0) - pre_balances.get(bid, 0)
                    if delta > 0:
                        bin_amounts[bid] = delta
                logger.info(f"Captured LBToken amounts for {len(bin_amounts)} bins")
            except Exception as e:
                logger.warning(f"Failed to capture post-add balances: {e}")

        # Register position in LP registry on successful live execution
        if not dry_run:
            self._register_position(
                strategy_type=strategy_type,
                results=approvals,
                active_id=active_id,
                delta_ids=delta_ids,
                amount_wmnt=amount_wmnt,
                amount_usdt=amount_usdt,
                distribution_shape=distribution_plan.get("distribution_shape"),
                bin_amounts=bin_amounts,
            )

        return approvals

    @staticmethod
    def _distribution_details(distribution_plan: dict[str, Any]) -> dict[str, Any]:
        return {
            k: v for k, v in distribution_plan.items()
            if k not in {"distribution_x", "distribution_y"}
        }

    @staticmethod
    def _is_reserve_estimate_error(exc: Exception | None) -> bool:
        return exc is not None and "gas fee greater than reserve for non-dipping transaction" in str(exc).lower()

    def _raise_native_gas_headroom_error(
        self,
        *,
        action: str,
        wallet: str,
        native_value_raw: int,
        gas_limit: int,
        last_gas_error: Exception,
        preflight: dict[str, Any],
    ) -> None:
        native_balance = self.balance.get_native_balance(wallet) if self.balance is not None else None
        native_balance_raw = int(getattr(native_balance, "raw", 0) or 0)
        native_balance_mnt = (
            getattr(native_balance, "normalized", Decimal(0))
            if native_balance is not None else Decimal(0)
        )
        gas_price_wei = int(self.tx.gas_price_params().get("gasPrice", 0))
        gas_needed_wei = int(gas_limit) * gas_price_wei
        total_needed_wei = int(native_value_raw) + gas_needed_wei
        shortfall_wei = max(0, total_needed_wei - native_balance_raw)
        try:
            gas_reserve_mnt = Decimal(str(getattr(self.settings, "gas_reserve_mnt", 0) or 0))
        except Exception:
            gas_reserve_mnt = Decimal(0)

        raise PreviewValidationError(
            action=action,
            message=(
                f"Skipping live {action.replace('_', ' ')}: native MNT headroom is too tight "
                "for tx value + gas."
            ),
            preview={
                "status": "native_gas_headroom_too_low",
                "phase": action,
                "message": str(last_gas_error),
                "native_value_raw": str(native_value_raw),
                "native_needed_mnt": serialize_decimal(Decimal(native_value_raw) / Decimal(10**18), 6),
                "gas_limit": gas_limit,
                "gas_price_wei": str(gas_price_wei),
                "gas_needed_mnt": serialize_decimal(Decimal(gas_needed_wei) / Decimal(10**18), 6),
                "native_balance_mnt": serialize_decimal(native_balance_mnt, 6),
                "total_needed_mnt": serialize_decimal(Decimal(total_needed_wei) / Decimal(10**18), 6),
                "shortfall_mnt": serialize_decimal(Decimal(shortfall_wei) / Decimal(10**18), 6),
                "gas_reserve_mnt": serialize_decimal(gas_reserve_mnt, 6),
            },
            context={"preflight": preflight},
        )

    def _ensure_native_mnt_headroom(
        self,
        *,
        wallet: str,
        target_native_raw: int,
        dry_run: bool,
        reason: str,
    ) -> bool:
        if dry_run or self.balance is None or target_native_raw <= 0:
            return False

        native_balance = self.balance.get_native_balance(wallet)
        if native_balance.raw >= target_native_raw:
            return False

        shortfall_raw = target_native_raw - native_balance.raw
        wmnt_balance = self.balance.get_erc20_balance(wallet, self._wmnt_address)
        if wmnt_balance.raw < shortfall_raw:
            return False

        shortfall = self.balance._raw_to_decimal(self._wmnt_address, shortfall_raw)
        logger.info(
            "Unwrapping %.6f WMNT -> native MNT for %s",
            float(shortfall),
            reason,
        )
        self.balance.unwrap_wmnt(shortfall, dry_run=False)
        return True

    @staticmethod
    def _validate_live_distribution_mode(*, distribution_plan: dict[str, Any], dry_run: bool) -> None:
        if dry_run:
            return
        mode = str(distribution_plan.get("active_mode") or "")
        if mode.endswith("_onesided"):
            logger.info("Live add proceeding with one-sided LP mode: %s", mode)

    @staticmethod
    def _resolve_recompute_prefer_mode(
        *,
        original_mode: str | None,
        initial_active_id: int,
        final_active_id: int,
    ) -> str:
        if original_mode in {"x_only", "x_only_onesided", "y_only", "y_only_onesided"}:
            return original_mode
        return "y_only" if final_active_id < initial_active_id else "x_only"

    def _validate_distribution_plan_fill(
        self,
        *,
        distribution_plan: dict[str, Any],
        pool_state: PoolState,
        token_x: str,
        token_y: str,
        requested_amount_wmnt: Decimal,
        requested_amount_usdt: Decimal,
    ) -> None:
        mnt_used, usdt_used = self._distribution_plan_usage(
            distribution_plan=distribution_plan,
            token_x=token_x,
            token_y=token_y,
        )
        spot = pool_state.mnt_price_usdt or pool_state.price_y_per_x or Decimal(0)
        used_value_usdt = mnt_used * spot + usdt_used if spot > 0 else usdt_used
        requested_value_usdt = (
            requested_amount_wmnt * spot + requested_amount_usdt if spot > 0 else requested_amount_usdt
        )
        min_value_usdt = Decimal(str(self.settings.min_position_size_usdt))

        if requested_value_usdt >= min_value_usdt and used_value_usdt < min_value_usdt:
            logger.error(
                "Rejecting LP add: mode=%s would deploy only %.6f MNT + %.6f USDT "
                "(~$%.4f) from requested %.6f MNT + %.6f USDT (~$%.4f)",
                distribution_plan.get("active_mode"),
                float(mnt_used),
                float(usdt_used),
                float(used_value_usdt),
                float(requested_amount_wmnt),
                float(requested_amount_usdt),
                float(requested_value_usdt),
            )
            raise PreviewValidationError(
                action="add_liquidity",
                message="Rejected LP add because planned fill is below minimum position size.",
                preview={
                    "status": "insufficient_expected_fill",
                    "active_mode": distribution_plan.get("active_mode"),
                    "used_mnt": serialize_decimal(mnt_used, 8),
                    "used_usdt": serialize_decimal(usdt_used, 8),
                    "used_value_usdt": serialize_decimal(used_value_usdt, 8),
                    "requested_mnt": serialize_decimal(requested_amount_wmnt, 8),
                    "requested_usdt": serialize_decimal(requested_amount_usdt, 8),
                    "requested_value_usdt": serialize_decimal(requested_value_usdt, 8),
                    "min_position_size_usdt": serialize_decimal(min_value_usdt, 8),
                },
            )

    def _distribution_plan_usage(
        self,
        *,
        distribution_plan: dict[str, Any],
        token_x: str,
        token_y: str,
    ) -> tuple[Decimal, Decimal]:
        x_used = distribution_plan.get("x_used")
        y_used = distribution_plan.get("y_used")
        x_dec = x_used if isinstance(x_used, Decimal) else Decimal(str(x_used or 0))
        y_dec = y_used if isinstance(y_used, Decimal) else Decimal(str(y_used or 0))
        if token_x.lower() == self._wmnt_address.lower():
            return x_dec, y_dec
        if token_y.lower() == self._wmnt_address.lower():
            return y_dec, x_dec
        return x_dec, y_dec

    def estimate_position_fill(
        self,
        *,
        amount_wmnt: Decimal,
        amount_usdt: Decimal,
        bin_count: int | None = None,
        distribution_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Estimate deployable LP fill without touching approvals or tx execution."""
        pool_state = self.get_pool_state()
        token_x = self.rpc.checksum(pool_state.token_x.address)
        token_y = self.rpc.checksum(pool_state.token_y.address)
        raw_wmnt = self.balance._token_to_raw(self._wmnt_address, amount_wmnt)
        raw_usdt = self.balance._token_to_raw(self._usdt_address, amount_usdt)
        amount_x = raw_wmnt if token_x.lower() == self._wmnt_address.lower() else raw_usdt
        amount_y = raw_usdt if token_y.lower() == self._usdt_address.lower() else raw_wmnt
        use_bin_count = self.settings.bin_count if bin_count is None else bin_count
        delta_ids = self._lp_range_delta_ids(use_bin_count, self.settings.position_upside_pct)
        active_id = int(self._pool.functions.getActiveId().call())
        distribution_plan = self._liquidity_distributions(
            active_id=active_id,
            bin_step=pool_state.bin_step,
            token_x=token_x,
            token_y=token_y,
            amount_x_raw=amount_x,
            amount_y_raw=amount_y,
            delta_ids=delta_ids,
            **(distribution_params or {}),
        )
        mnt_used, usdt_used = self._distribution_plan_usage(
            distribution_plan=distribution_plan,
            token_x=token_x,
            token_y=token_y,
        )
        spot = pool_state.mnt_price_usdt or pool_state.price_y_per_x or Decimal(0)
        used_value_usdt = mnt_used * spot + usdt_used if spot > 0 else usdt_used
        requested_value_usdt = (
            amount_wmnt * spot + amount_usdt if spot > 0 else amount_usdt
        )
        min_value_usdt = Decimal(str(self.settings.min_position_size_usdt))
        return {
            "active_mode": distribution_plan.get("active_mode"),
            "used_mnt": mnt_used,
            "used_usdt": usdt_used,
            "used_value_usdt": used_value_usdt,
            "requested_mnt": amount_wmnt,
            "requested_usdt": amount_usdt,
            "requested_value_usdt": requested_value_usdt,
            "min_position_size_usdt": min_value_usdt,
            "meets_min_fill": not (
                requested_value_usdt >= min_value_usdt and used_value_usdt < min_value_usdt
            ),
        }

    def remove_position(
        self,
        *,
        pool_state: PoolState | None = None,
        slippage_bps: int | None = None,
        dry_run: bool = True,
        max_bins_per_tx: int = 50,
    ) -> list[ExecutionResult]:
        wallet = self.tx.wallet_address
        pool_state = pool_state or self.get_pool_state()
        position = self.get_position(wallet, pool_state=pool_state, include_inventory=True)
        if not position.position_exists:
            raise RuntimeError("No LP position found for current wallet")
        logger.info(
            f"remove_position: discovered {position.bin_count} active bins "
            f"[{position.min_bin_id}-{position.max_bin_id}] "
            f"in_range={position.in_range} active_bin={pool_state.active_bin_id}"
        )

        approvals: list[ExecutionResult] = []
        pair_approval = self.tx.ensure_pair_approval(self._pool, self._router_address, dry_run=dry_run)
        pair_approval_required = pair_approval is not None
        if pair_approval is not None:
            approvals.append(pair_approval)

        slippage = self.settings.slippage_bps if slippage_bps is None else slippage_bps

        # Filter out dust bins where burning would yield 0 tokens (causes contract revert)
        non_dust_bins = []
        dust_count = 0
        for b in position.active_bins:
            total_supply = b.bin_total_supply_raw or 0
            if total_supply == 0:
                dust_count += 1
                continue
            share = Decimal(b.wallet_lb_token_balance_raw) / Decimal(total_supply)
            expected_x = int(Decimal(b.bin_reserve_x_raw or 0) * share)
            expected_y = int(Decimal(b.bin_reserve_y_raw or 0) * share)
            if expected_x == 0 and expected_y == 0:
                dust_count += 1
                logger.debug(
                    "remove_position: skipping dust bin %d (balance=%d, share=%.2e)",
                    b.bin_id, b.wallet_lb_token_balance_raw, float(share),
                )
                continue
            non_dust_bins.append(b)
        if dust_count > 0:
            logger.info(
                "remove_position: filtered %d dust bins (would revert on zero-amount burn), "
                "%d bins remaining",
                dust_count, len(non_dust_bins),
            )
        if not non_dust_bins:
            logger.info("remove_position: all bins are dust — nothing to remove")
            return approvals

        active_bins = non_dust_bins
        for start in range(0, len(active_bins), max_bins_per_tx):
            chunk = active_bins[start : start + max_bins_per_tx]
            ids = [b.bin_id for b in chunk]
            amounts = [b.wallet_lb_token_balance_raw for b in chunk]
            chunk_no = start // max_bins_per_tx + 1
            total_chunks = (len(active_bins) + max_bins_per_tx - 1) // max_bins_per_tx
            logger.info(
                f"remove_position: chunk {chunk_no}/{total_chunks} bins={len(chunk)} "
                f"range=[{ids[0]}-{ids[-1]}]"
            )

            raw_x = Decimal(0)
            raw_y = Decimal(0)
            for b in chunk:
                total_supply = b.bin_total_supply_raw or 0
                if total_supply == 0:
                    continue
                share = Decimal(b.wallet_lb_token_balance_raw) / Decimal(total_supply)
                raw_x += Decimal(b.bin_reserve_x_raw or 0) * share
                raw_y += Decimal(b.bin_reserve_y_raw or 0) * share

            deadline = self.tx.deadline()
            token_x = self.rpc.checksum(pool_state.token_x.address)
            token_y = self.rpc.checksum(pool_state.token_y.address)
            if dry_run and pair_approval_required:
                preview: dict[str, Any] | None = {
                    "required_pair_approval": {"pair_address": self._pool_address},
                    "reason": "dry_run has pending pair approval",
                    "status": "skipped_pending_approvals",
                }
                preview_amount_x, preview_amount_y = int(raw_x), int(raw_y)
            else:
                preview = self._preview_remove_liquidity(
                    token_x=token_x, token_y=token_y, bin_step=pool_state.bin_step,
                    ids=ids, amounts=amounts, deadline=deadline,
                )
                if preview and preview.get("status") == "ok":
                    logger.info(
                        f"remove_position: preview ok for chunk {chunk_no}/{total_chunks}"
                    )
                    preview_amount_x = int(preview["amount_x_raw"])
                    preview_amount_y = int(preview["amount_y_raw"])
                    preview["token_x_summary"] = self._preview_remove_amount(token_x, preview_amount_x)
                    preview["token_y_summary"] = self._preview_remove_amount(token_y, preview_amount_y)
                else:
                    if not dry_run and preview and preview.get("status") == "reverted":
                        logger.error(
                            "remove_liquidity preview reverted in live mode — "
                            "aborting removal to avoid on-chain revert with unvalidated amounts. "
                            f"chunk={chunk_no}/{total_chunks} bins={len(ids)} "
                            f"error={preview.get('error', 'unknown')}"
                        )
                        raise PreviewValidationError(
                            action="remove_liquidity",
                            message="remove_liquidity preview reverted — cannot proceed with unvalidated reserve estimates",
                            preview=preview,
                            context={"chunk": chunk_no, "total_chunks": total_chunks, "bins": len(ids)},
                        )
                    elif preview:
                        logger.warning(
                            f"remove_position: preview status={preview.get('status')} "
                            f"for chunk {chunk_no}/{total_chunks}; using reserve estimate"
                        )
                    preview_amount_x, preview_amount_y = int(raw_x), int(raw_y)
            amount_x_min = int(preview_amount_x * Decimal(10_000 - slippage) / Decimal(10_000))
            amount_y_min = int(preview_amount_y * Decimal(10_000 - slippage) / Decimal(10_000))
            logger.info(
                f"remove_position: chunk {chunk_no}/{total_chunks} "
                f"preview_x={preview_amount_x} preview_y={preview_amount_y} "
                f"min_x={amount_x_min} min_y={amount_y_min} slippage_bps={slippage}"
            )

            function = self._router.functions.removeLiquidity(
                token_x, token_y, pool_state.bin_step,
                amount_x_min, amount_y_min, ids, amounts, wallet, deadline,
            )
            preflight = self._lp_remove_preflight(
                pool_state=pool_state, position=position,
                ids=ids, amounts=amounts, amount_x_min=amount_x_min, amount_y_min=amount_y_min,
                deadline=deadline, preview=preview,
            )
            if dry_run and preview and preview.get("status") == "reverted":
                raise PreviewValidationError(
                    action="remove_liquidity",
                    message="remove_liquidity preview reverted.",
                    preview=preview, context={"preflight": preflight},
                )
            logger.info(
                f"remove_position: sending chunk {chunk_no}/{total_chunks} "
                f"with {len(ids)} ids and {len(amounts)} liquidity amounts"
            )

            # Gas estimation retry (mirrors add_liquidity pattern).
            # The RPC can reject estimate_gas with "gas fee greater than
            # reserve for non-dipping transaction" when native balance is tight.
            n_chunk_bins = len(ids)
            remove_gas_limit: int | None = None
            attempted_native_topup = False
            if not dry_run:
                last_remove_gas_error: Exception | None = None
                for gas_attempt in range(1, 4):
                    try:
                        tx_for_estimate = function.build_transaction({
                            "from": wallet, "chainId": self.settings.chain_id,
                            "nonce": self.rpc.w3.eth.get_transaction_count(
                                wallet, block_identifier="pending",
                            ),
                            "value": 0, "gas": 0, **self.tx.gas_price_params(),
                        })
                        estimate = self.rpc.w3.eth.estimate_gas(
                            {k: v for k, v in tx_for_estimate.items() if k not in ("gas", "gasPrice")}
                        )
                        remove_gas_limit = int(estimate * 1.3)
                        logger.info(
                            f"remove_position: gas estimated: {estimate:,} "
                            f"(limit: {remove_gas_limit:,})"
                        )
                        break
                    except Exception as e:
                        last_remove_gas_error = e
                        if self._is_reserve_estimate_error(e) and not attempted_native_topup:
                            fallback_remove_gas_limit = max(
                                8_000_000, 8_000_000 + (n_chunk_bins - 10) * 100_000,
                            )
                            gas_price_wei = int(self.tx.gas_price_params().get("gasPrice", 0))
                            required_native_raw = fallback_remove_gas_limit * gas_price_wei
                            if self._ensure_native_mnt_headroom(
                                wallet=wallet,
                                target_native_raw=required_native_raw,
                                dry_run=False,
                                reason="remove_liquidity gas headroom",
                            ):
                                attempted_native_topup = True
                                logger.info(
                                    "remove_position: restored native MNT from WMNT; retrying gas estimation"
                                )
                                continue
                        logger.debug(
                            f"remove_position: gas estimation attempt "
                            f"{gas_attempt}/3 failed: {e}"
                        )
                if remove_gas_limit is None:
                    fallback_remove_gas_limit = max(
                        8_000_000, 8_000_000 + (n_chunk_bins - 10) * 100_000,
                    )
                    if self._is_reserve_estimate_error(last_remove_gas_error):
                        self._raise_native_gas_headroom_error(
                            action="remove_liquidity",
                            wallet=wallet,
                            native_value_raw=0,
                            gas_limit=fallback_remove_gas_limit,
                            last_gas_error=last_remove_gas_error,
                            preflight=preflight,
                        )
                    remove_gas_limit = max(
                        8_000_000, 8_000_000 + (n_chunk_bins - 10) * 100_000,
                    )
                    logger.info(
                        f"remove_position: gas estimation failed — "
                        f"using fallback: {remove_gas_limit:,} "
                        f"(last error: {last_remove_gas_error})"
                    )

            approvals.append(self.tx.send(
                "remove_liquidity", function, dry_run=dry_run,
                gas_limit=remove_gas_limit,
                details={
                    "preflight": preflight, "ids": ids,
                    "amounts": [str(a) for a in amounts],
                    "amount_x_min_raw": str(amount_x_min), "amount_y_min_raw": str(amount_y_min),
                },
            ))

        # Deregister positions from LP registry on successful live removal
        if not dry_run:
            self._deregister_positions(position)

        return approvals

    def get_strategy_position(
        self,
        wallet: str,
        strategy_type: str,
        *,
        pool_state: PoolState | None = None,
    ) -> PositionState:
        """Get position state filtered to bins belonging to a specific strategy.

        Uses registry bin_amounts to identify which bins belong to the strategy,
        then queries on-chain balances for those bins only.
        """
        checksum = self.rpc.checksum(wallet)
        pool = pool_state or self.get_pool_state()
        reg = self.get_registry(wallet)

        positions = reg.get_narrow_positions() if strategy_type == "narrow" else reg.get_wide_positions()
        _empty = PositionState(
            wallet_address=checksum, candidate_bin_ids=[], active_bins=[],
            position_exists=False, in_range=False, min_bin_id=None, max_bin_id=None,
            estimated_token_x=Decimal(0), estimated_token_y=Decimal(0),
        )
        if not positions:
            return _empty

        pos = positions[0]
        # Use bin_amounts keys if available, otherwise fall back to range
        candidate_ids = sorted(pos.bin_amounts.keys()) if pos.bin_amounts else pos.get_all_bins()

        balances = self._pool.functions.balanceOfBatch(
            [checksum] * len(candidate_ids), candidate_ids,
        ).call()

        token_x = pool.token_x
        token_y = pool.token_y
        active_bins: list[BinState] = []
        total_x = Decimal(0)
        total_y = Decimal(0)

        for bin_id, user_balance in zip(candidate_ids, balances):
            user_balance = int(user_balance)
            if user_balance == 0:
                continue
            total_supply = int(self._pool.functions.totalSupply(bin_id).call())
            rx_raw, ry_raw = self._pool.functions.getBin(bin_id).call()
            share = Decimal(user_balance) / Decimal(total_supply) if total_supply else Decimal(0)
            user_x = scaled_decimal(int(rx_raw), token_x.decimals) * share
            user_y = scaled_decimal(int(ry_raw), token_y.decimals) * share
            total_x += user_x
            total_y += user_y
            active_bins.append(BinState(
                bin_id=int(bin_id), wallet_lb_token_balance_raw=user_balance,
                bin_total_supply_raw=total_supply, bin_reserve_x_raw=int(rx_raw),
                bin_reserve_y_raw=int(ry_raw), estimated_token_x=user_x, estimated_token_y=user_y,
            ))

        if not active_bins:
            return _empty

        # Filter dust bins from range calculation (same as primary get_position)
        real_bins = [b for b in active_bins if b.wallet_lb_token_balance_raw >= self.DUST_LB_TOKEN_THRESHOLD]
        if not real_bins:
            return _empty

        real_bin_ids = [b.bin_id for b in real_bins]
        # Recompute totals from real bins only
        total_x = sum((b.estimated_token_x or Decimal(0)) for b in real_bins)
        total_y = sum((b.estimated_token_y or Decimal(0)) for b in real_bins)
        return PositionState(
            wallet_address=checksum, candidate_bin_ids=candidate_ids, active_bins=real_bins,
            position_exists=True,
            in_range=pool.active_bin_id in set(real_bin_ids),
            min_bin_id=min(real_bin_ids), max_bin_id=max(real_bin_ids),
            estimated_token_x=total_x, estimated_token_y=total_y,
        )

    # ── LP Helpers (distribution, preview, preflight) ──────

    @staticmethod
    @staticmethod
    def _lp_range_delta_ids(bin_count: int, upside_pct: float = 0.5) -> list[int]:
        """Generate delta IDs for LP range. upside_pct controls asymmetry.
        0.5 = centered, 0.65 = 65% bins above active (bullish offset).
        """
        upside_pct = max(0.1, min(0.9, upside_pct))
        right = int(bin_count * upside_pct)
        left = bin_count - 1 - right
        return [*range(-left, right + 1)]

    def _sdk_spot_distribution(self, delta_ids: list[int]) -> dict[str, Any] | None:
        if delta_ids != [-1, 0, 1]:
            return None
        distribution_x = [0, ONE // 2, ONE - (ONE // 2)]
        distribution_y = [(2 * ONE) // 3, ONE // 3, 0]
        distribution_y[1] += ONE - sum(distribution_y)
        return {
            "active_mode": "sdk_spot",
            "distribution_x": distribution_x, "distribution_y": distribution_y,
            "prices_y_per_x": [], "x_allocations": [], "y_allocations": [],
            "x_used": None, "y_used": None, "x_refund": None, "y_refund": None,
            "liquidity_per_bin_y": None,
        }

    @staticmethod
    def _distribution_from_allocations(allocations: list[Decimal], total_amount: Decimal) -> list[int]:
        if total_amount <= 0:
            return [0 for _ in allocations]
        raw = [int((a / total_amount) * Decimal(ONE)) for a in allocations]
        target = int((sum(allocations) / total_amount) * Decimal(ONE))
        delta = target - sum(raw)
        if delta == 0:
            return raw
        candidates = [i for i, a in enumerate(allocations) if a > 0]
        if candidates:
            raw[candidates[-1]] += delta
        return raw

    def _candidate_uniform_allocations(
        self, *, amount_x_dec: Decimal, amount_y_dec: Decimal,
        prices: list[Decimal], delta_ids: list[int], active_mode: str,
    ) -> dict[str, Any] | None:
        positive_indices = [i for i, d in enumerate(delta_ids) if d > 0]
        negative_indices = [i for i, d in enumerate(delta_ids) if d < 0]
        active_index = next((i for i, d in enumerate(delta_ids) if d == 0), None)
        if active_index is None:
            return None

        sum_inv_pos = sum(Decimal(1) / prices[i] for i in positive_indices)
        neg_count = Decimal(len(negative_indices))
        active_price = prices[active_index]

        liquidity: Decimal | None = None
        active_x = Decimal(0)
        active_y = Decimal(0)

        if active_mode == "mixed":
            denom = Decimal(1) + (active_price * sum_inv_pos) + neg_count
            if denom <= 0:
                return None
            liquidity = ((active_price * amount_x_dec) + amount_y_dec) / denom
            active_x = amount_x_dec - (liquidity * sum_inv_pos)
            active_y = amount_y_dec - (liquidity * neg_count)
            if active_x < 0 or active_y < 0:
                return None
        elif active_mode == "x_only":
            dx = sum_inv_pos + (Decimal(1) / active_price)
            dy = neg_count
            limits = []
            if dx > 0: limits.append(amount_x_dec / dx)
            if dy > 0: limits.append(amount_y_dec / dy)
            if not limits: return None
            liquidity = min(limits)
            active_x = liquidity / active_price
        elif active_mode == "y_only":
            dx = sum_inv_pos
            dy = neg_count + Decimal(1)
            limits = []
            if dx > 0: limits.append(amount_x_dec / dx)
            if dy > 0: limits.append(amount_y_dec / dy)
            if not limits: return None
            liquidity = min(limits)
            active_y = liquidity
        else:
            raise ValueError(f"Unknown active_mode {active_mode}")

        x_alloc = [Decimal(0)] * len(delta_ids)
        y_alloc = [Decimal(0)] * len(delta_ids)
        for i in positive_indices: x_alloc[i] = liquidity / prices[i]
        for i in negative_indices: y_alloc[i] = liquidity
        x_alloc[active_index] = active_x
        y_alloc[active_index] = active_y
        x_used, y_used = sum(x_alloc), sum(y_alloc)
        if x_used < 0 or y_used < 0:
            return None
        return {
            "active_mode": active_mode, "liquidity_per_bin_y": liquidity,
            "x_allocations": x_alloc, "y_allocations": y_alloc,
            "x_used": x_used, "y_used": y_used,
            "x_refund": max(amount_x_dec - x_used, Decimal(0)),
            "y_refund": max(amount_y_dec - y_used, Decimal(0)),
        }

    @staticmethod
    def _one_sided_y_candidate(
        *, amount_y_dec: Decimal, prices: list[Decimal], delta_ids: list[int],
    ) -> dict[str, Any] | None:
        """Distribute only token_y across y-eligible bins (active + below active).

        Used when the portfolio is >90% token_y.  Skips x-only bins entirely
        so the abundant y-token is not bottlenecked by scarce x-token.
        """
        y_bins = [i for i, d in enumerate(delta_ids) if d <= 0]  # active + below
        if not y_bins:
            return None
        per_bin = amount_y_dec / Decimal(len(y_bins))
        x_alloc = [Decimal(0)] * len(delta_ids)
        y_alloc = [Decimal(0)] * len(delta_ids)
        for i in y_bins:
            y_alloc[i] = per_bin
        y_used = sum(y_alloc)
        return {
            "active_mode": "y_only_onesided",
            "liquidity_per_bin_y": per_bin,
            "x_allocations": x_alloc, "y_allocations": y_alloc,
            "x_used": Decimal(0), "y_used": y_used,
            "x_refund": Decimal(0), "y_refund": max(amount_y_dec - y_used, Decimal(0)),
        }

    @staticmethod
    def _one_sided_x_candidate(
        *, amount_x_dec: Decimal, prices: list[Decimal], delta_ids: list[int],
    ) -> dict[str, Any] | None:
        """Distribute only token_x across x-only bins (strictly above active).

        Used when the portfolio is >90% token_x.  Skips y-only bins entirely
        so the abundant x-token is not bottlenecked by scarce y-token.
        Excludes active bin (delta_id=0) because the active bin requires both tokens.
        """
        x_bins = [i for i, d in enumerate(delta_ids) if d > 0]  # above active only
        if not x_bins:
            return None
        per_bin_x = amount_x_dec / Decimal(len(x_bins))
        x_alloc = [Decimal(0)] * len(delta_ids)
        y_alloc = [Decimal(0)] * len(delta_ids)
        for i in x_bins:
            x_alloc[i] = per_bin_x
        x_used = sum(x_alloc)
        # liquidity_per_bin_y: convert x value to y terms for comparison
        active_price = prices[delta_ids.index(0)]
        liquidity_y_equiv = per_bin_x * active_price
        return {
            "active_mode": "x_only_onesided",
            "liquidity_per_bin_y": liquidity_y_equiv,
            "x_allocations": x_alloc, "y_allocations": y_alloc,
            "x_used": x_used, "y_used": Decimal(0),
            "x_refund": max(amount_x_dec - x_used, Decimal(0)), "y_refund": Decimal(0),
        }

    @staticmethod
    def _apply_shape_weights(
        weights: list[float],
        x_allocs: list[Decimal],
        y_allocs: list[Decimal],
        delta_ids: list[int],
    ) -> tuple[list[Decimal], list[Decimal]]:
        """Apply shape weights while preserving bin-type constraints.

        Liquidity Book rules: bins above active are x-only, bins below are y-only,
        active bin is mixed. Shape weights are applied independently to
        each token's valid bins, keeping zero allocations at zero.
        """
        x_indices = [i for i in range(len(delta_ids)) if x_allocs[i] > 0]
        y_indices = [i for i in range(len(delta_ids)) if y_allocs[i] > 0]

        def _reweight(indices: list[int], allocs: list[Decimal]) -> list[Decimal]:
            if not indices:
                return allocs[:]
            total = sum(allocs[i] for i in indices)
            w_sum = sum(Decimal(str(weights[i])) for i in indices)
            if total <= 0 or w_sum <= 0:
                return allocs[:]
            result = allocs[:]
            for i in indices:
                result[i] = total * Decimal(str(weights[i])) / w_sum
            return result

        return _reweight(x_indices, x_allocs), _reweight(y_indices, y_allocs)

    def _liquidity_distributions(
        self, *, active_id: int, bin_step: int, token_x: str, token_y: str,
        amount_x_raw: int, amount_y_raw: int, delta_ids: list[int],
        distribution_shape: str | None = None,
        slope_direction: str | None = None,
        slope_steepness: float | None = None,
        curve_type: str | None = None,
        curve_exponent: float | None = None,
        prefer_mode: str | None = None,
    ) -> dict[str, Any]:
        # Resolve shape params: explicit overrides > global settings
        shape = distribution_shape or self.settings.distribution_shape
        s_direction = slope_direction or self.settings.slope_direction
        s_steepness = slope_steepness if slope_steepness is not None else self.settings.slope_steepness
        c_type = curve_type or self.settings.curve_type
        c_exponent = curve_exponent if curve_exponent is not None else self.settings.curve_exponent

        sdk_spot = self._sdk_spot_distribution(delta_ids)
        if sdk_spot is not None and shape == "uniform":
            return sdk_spot

        token_x_info = self.get_token_info(token_x)
        token_y_info = self.get_token_info(token_y)
        amount_x_dec = self.balance._raw_to_decimal(token_x, amount_x_raw)
        amount_y_dec = self.balance._raw_to_decimal(token_y, amount_y_raw)
        prices = [price_from_bin_id(active_id + d, bin_step, token_x_info.decimals, token_y_info.decimals) for d in delta_ids]

        shape_weights = None
        if shape == "slope":
            shape_weights = calculate_slope_weights(len(delta_ids), direction=s_direction, steepness=s_steepness)
        elif shape == "curve":
            from .lp_shapes import calculate_curve_weights
            shape_weights = calculate_curve_weights(len(delta_ids), curve_type=c_type, exponent=c_exponent)

        candidates = []
        for mode in ("mixed", "x_only", "y_only"):
            c = self._candidate_uniform_allocations(
                amount_x_dec=amount_x_dec, amount_y_dec=amount_y_dec,
                prices=prices, delta_ids=delta_ids, active_mode=mode,
            )
            if c is not None:
                # Only apply shape weights in mixed mode. In x_only/y_only modes,
                # shape weights distort the single-token distribution in ways the
                # router rejects (WrongAmounts). The router cross-checks x/y
                # amounts for active bin composition, and skewed shapes break this.
                if shape_weights is not None and mode == "mixed":
                    c["x_allocations"], c["y_allocations"] = self._apply_shape_weights(
                        shape_weights, c["x_allocations"], c["y_allocations"], delta_ids,
                    )
                candidates.append(c)

        # ── One-sided candidate for skewed portfolios ──
        # When the portfolio is heavily skewed (>90% in one token by value),
        # the uniform-liquidity constraint wastes the abundant token because
        # the scarce token bottlenecks every bin. Generate an additional
        # candidate that deploys ONLY to bins using the dominant token,
        # concentrating capital on one side of the active bin.
        active_price = prices[delta_ids.index(0)]
        x_value = amount_x_dec * active_price
        y_value = amount_y_dec
        total_value = x_value + y_value
        if total_value > 0:
            x_pct = x_value / total_value
            y_pct = y_value / total_value
            SKEW_THRESHOLD = Decimal("0.90")
            if y_pct > SKEW_THRESHOLD:
                # Mostly token_y (USDT) — deploy to y-only bins below active + active bin
                onesided = self._one_sided_y_candidate(
                    amount_y_dec=amount_y_dec, prices=prices, delta_ids=delta_ids,
                )
                if onesided is not None:
                    logger.info(f"Portfolio skewed to Y ({float(y_pct)*100:.0f}%) — "
                                f"adding one-sided Y candidate "
                                f"(${float(onesided['y_used']):.2f} USDT across "
                                f"{sum(1 for a in onesided['y_allocations'] if a > 0)} bins)")
                    candidates.append(onesided)
            elif x_pct > SKEW_THRESHOLD:
                # Mostly token_x (MNT) — log skew but don't add x_only_onesided candidate.
                # The x_only_onesided mode causes on-chain reverts (WrongAmounts) because
                # it creates distribution arrays incompatible with addLiquidityNATIVE when
                # amount_y > 0.  Instead, fall through to the normal x_only candidate from
                # the standard mixed/x_only/y_only loop above.
                logger.info(f"Portfolio skewed to X ({float(x_pct)*100:.0f}%) — "
                            f"using standard x_only mode (onesided disabled)")

        if not candidates:
            raise RuntimeError("Unable to construct Liquidity Book distributions")

        # When prefer_mode is set (e.g., during volatile conditions), prefer that
        # mode over mixed.  Mixed mode is fragile: if the active bin moves even 1
        # bin between distribution computation and tx mining, the router rejects
        # the distribution with WrongAmounts.  x_only / y_only distributions are
        # robust to continued movement in the same direction.
        if prefer_mode:
            preferred = [c for c in candidates if c["active_mode"] == prefer_mode]
            if preferred:
                candidates = preferred

        best = max(candidates, key=lambda c: (c["liquidity_per_bin_y"], prices[delta_ids.index(0)] * c["x_used"] + c["y_used"]))
        dist_x = self._distribution_from_allocations(best["x_allocations"], amount_x_dec)
        dist_y = self._distribution_from_allocations(best["y_allocations"], amount_y_dec)

        # Ensure no bin has both dist_x=0 AND dist_y=0 (empty bins cause WrongAmounts).
        # This can happen with steep shape weights that push edge bins to zero.
        MIN_DIST = 1  # 1 wei of distribution — effectively dust but non-zero
        for i in range(len(delta_ids)):
            if dist_x[i] == 0 and dist_y[i] == 0:
                # Assign minimum to the token this bin should have
                if delta_ids[i] > 0:
                    dist_x[i] = MIN_DIST
                else:
                    dist_y[i] = MIN_DIST

        return {
            "active_mode": best["active_mode"],
            "distribution_x": dist_x,
            "distribution_y": dist_y,
            "prices_y_per_x": [serialize_decimal(p, 12) for p in prices],
            "x_allocations": [serialize_decimal(a, 12) for a in best["x_allocations"]],
            "y_allocations": [serialize_decimal(a, 12) for a in best["y_allocations"]],
            "x_used": serialize_decimal(best["x_used"], 12),
            "y_used": serialize_decimal(best["y_used"], 12),
            "x_refund": serialize_decimal(best["x_refund"], 12),
            "y_refund": serialize_decimal(best["y_refund"], 12),
            "liquidity_per_bin_y": serialize_decimal(best["liquidity_per_bin_y"], 12),
            "distribution_shape": shape,
        }

    def _native_lp_support(self, *, token_x: str, token_y: str, amount_x: int, amount_y: int) -> dict[str, Any]:
        if token_x.lower() == self._wmnt_address.lower():
            return {"enabled": True, "native_token": "token_x", "native_value": amount_x, "router_method": "addLiquidityNATIVE"}
        if token_y.lower() == self._wmnt_address.lower():
            return {"enabled": True, "native_token": "token_y", "native_value": amount_y, "router_method": "addLiquidityNATIVE"}
        return {"enabled": False, "native_token": None, "native_value": 0, "router_method": "addLiquidity"}

    def _factory_pair_information(self, *, token_x: str, token_y: str, pool_bin_step: int) -> dict[str, Any]:
        try:
            info = self._factory.functions.getLBPairInformation(token_x, token_y, int(pool_bin_step)).call()
        except (Web3RPCError, ContractLogicError, ConnectionError, TimeoutError) as exc:
            return {"error": str(exc), "status": "error"}
        pair_info = info
        if isinstance(info, (list, tuple)) and len(info) == 1 and isinstance(info[0], (list, tuple)):
            pair_info = info[0]
        if not isinstance(pair_info, (list, tuple)) or len(pair_info) < 4:
            return {"error": f"Unexpected shape: {info!r}", "status": "error"}
        bs, lb_pair, created, ignored = int(pair_info[0]), self.rpc.checksum(pair_info[1]), bool(pair_info[2]), bool(pair_info[3])
        return {
            "bin_step": bs, "bin_step_matches_pool": bs == int(pool_bin_step),
            "created_by_owner": created, "ignored_for_routing": ignored,
            "lb_pair": lb_pair, "matches_configured_pool": lb_pair.lower() == self._pool_address.lower(),
            "status": "ok",
        }

    def _preview_add_liquidity(self, *, token_x: str, token_y: str, bin_step: int,
                               amount_x: int, amount_y: int, active_id: int,
                               delta_ids: list[int], distribution_x: list[int], distribution_y: list[int],
                               deadline: int, use_native: bool = False, native_value: int = 0,
                               id_slippage: int | None = None) -> dict[str, Any] | None:
        wallet = self.tx.wallet_address
        use_id_slippage = id_slippage if id_slippage is not None else self.settings.id_slippage
        params = (token_x, token_y, bin_step, amount_x, amount_y, 0, 0, active_id,
                  use_id_slippage, delta_ids, distribution_x, distribution_y, wallet, wallet, deadline)
        fn = self._router.functions.addLiquidityNATIVE(params) if use_native else self._router.functions.addLiquidity(params)
        try:
            call_tx: dict[str, Any] = {"from": wallet}
            if use_native: call_tx["value"] = native_value

            result = fn.call(call_tx)
        except (Web3RPCError, ContractLogicError, ConnectionError, TimeoutError) as exc:
            logger.error(f"Preview revert: {type(exc).__name__}: {exc}")
            return {"error": str(exc), "status": "reverted"}
        if isinstance(result, (list, tuple)) and len(result) == 6:
            ax, ay, _, _, dep_ids, liq = result
        elif isinstance(result, (list, tuple)) and len(result) == 4:
            ax, ay, dep_ids, liq = result
        else:
            return {"error": f"unexpected shape: {result!r}", "status": "decode_error"}
        return {"amount_x_added_raw": str(ax), "amount_y_added_raw": str(ay),
                "deposit_ids": [str(i) for i in dep_ids], "liquidity_minted": [str(i) for i in liq], "status": "ok"}

    def _preview_remove_liquidity(self, *, token_x: str, token_y: str, bin_step: int,
                                  ids: list[int], amounts: list[int], deadline: int) -> dict[str, Any] | None:
        wallet = self.tx.wallet_address
        fn = self._router.functions.removeLiquidity(token_x, token_y, bin_step, 0, 0, ids, amounts, wallet, deadline)
        try:
            ax, ay = fn.call({"from": wallet})
        except (Web3RPCError, ContractLogicError, ConnectionError, TimeoutError) as exc:
            return {"error": str(exc), "status": "reverted"}
        return {"amount_x_raw": str(ax), "amount_y_raw": str(ay), "status": "ok"}

    def _preview_requested_vs_added(self, token_address: str, requested_raw: int, added_raw: int) -> dict[str, Any]:
        req, added = int(requested_raw), int(added_raw)
        refund = max(req - added, 0)
        fill_pct = Decimal(0) if req <= 0 else Decimal(added) * Decimal(100) / Decimal(req)
        return {
            "requested_raw": str(req), "requested_normalized": serialize_decimal(self.balance._raw_to_decimal(token_address, req)),
            "expected_added_raw": str(added), "expected_added_normalized": serialize_decimal(self.balance._raw_to_decimal(token_address, added)),
            "expected_refund_raw": str(refund), "expected_refund_normalized": serialize_decimal(self.balance._raw_to_decimal(token_address, refund)),
            "expected_fill_pct": serialize_decimal(fill_pct, 4),
        }

    def _preview_remove_amount(self, token_address: str, amount_raw: int) -> dict[str, Any]:
        return {"expected_out_raw": str(amount_raw), "expected_out_normalized": serialize_decimal(self.balance._raw_to_decimal(token_address, amount_raw))}

    def _skip_preview_approvals(self, token_addresses: list[str]) -> dict[str, Any]:
        details = [{"address": t.address, "symbol": t.symbol, "decimals": t.decimals} for t in (self.get_token_info(a) for a in token_addresses)]
        return {"required_approvals": details, "reason": "dry_run has pending approvals", "status": "skipped_pending_approvals"}

    def _skip_preview_native(self, amount_mnt: Decimal) -> dict[str, Any]:
        return {"amount_mnt": serialize_decimal(amount_mnt), "reason": "dry_run has pending unwrap", "status": "skipped_pending_native_conversion"}

    def _lp_add_preflight(self, *, pool_state: PoolState, amount_wmnt: Decimal, amount_usdt: Decimal,
                          amount_x: int, amount_y: int, amount_x_min: int, amount_y_min: int,
                          active_id: int, delta_ids: list[int], distribution_x: list[int], distribution_y: list[int],
                          deadline: int, preview=None, distribution_details=None, factory_pair_info=None) -> dict[str, Any]:
        wallet = self.tx.wallet_address
        wmnt_bal = self.balance.get_erc20_balance(wallet, self._wmnt_address)
        usdt_bal = self.balance.get_erc20_balance(wallet, self._usdt_address)
        mnt_bal = self.balance.get_native_balance(wallet)
        spot = pool_state.mnt_price_usdt or pool_state.price_y_per_x or Decimal(0)
        wmnt_val = amount_wmnt * spot if spot > 0 else Decimal(0)
        side = "balanced"
        if spot > 0:
            side = "usdt" if wmnt_val > amount_usdt else ("wmnt" if wmnt_val < amount_usdt else "balanced")
        return {
            "wallet": wallet,
            "pool": {"active_id": active_id, "bin_step": pool_state.bin_step, "pair_address": self._pool_address,
                     "price_y_per_x": serialize_decimal(pool_state.price_y_per_x),
                     "spot_price_mnt_usdt": serialize_decimal(pool_state.mnt_price_usdt) if pool_state.mnt_price_usdt else None,
                     "token_x": pool_state.token_x.address, "token_y": pool_state.token_y.address, "factory_pair_info": factory_pair_info},
            "wallet_balances": {
                "mnt_native": mnt_bal.to_dict(),
                "usdt": {"allowance_normalized": serialize_decimal(usdt_bal.router_allowance_normalized), "balance_normalized": serialize_decimal(usdt_bal.normalized)},
                "wmnt": {"allowance_normalized": serialize_decimal(wmnt_bal.router_allowance_normalized), "balance_normalized": serialize_decimal(wmnt_bal.normalized)},
            },
            "liquidity_parameters": {
                "active_id_desired": active_id, "amount_usdt": serialize_decimal(amount_usdt), "amount_wmnt": serialize_decimal(amount_wmnt),
                "amount_x_raw": str(amount_x), "amount_x_min_raw": str(amount_x_min), "amount_y_raw": str(amount_y), "amount_y_min_raw": str(amount_y_min),
                "bin_count": len(delta_ids), "deadline": deadline, "delta_ids": delta_ids,
                "distribution_x": distribution_x, "distribution_y": distribution_y,
                "distribution_x_sum": str(sum(distribution_x)), "distribution_y_sum": str(sum(distribution_y)),
                "distribution_details": distribution_details, "id_slippage": self.settings.id_slippage,
                "limiting_side_at_spot": side, "preview": preview,
                "spot_value_wmnt_in_usdt": serialize_decimal(wmnt_val) if spot > 0 else None,
                "spot_value_gap_usdt": serialize_decimal(amount_usdt - wmnt_val) if spot > 0 else None,
            },
        }

    def _lp_remove_preflight(self, *, pool_state: PoolState, position: PositionState,
                             ids: list[int], amounts: list[int], amount_x_min: int, amount_y_min: int,
                             deadline: int, preview=None) -> dict[str, Any]:
        return {
            "wallet": self.tx.wallet_address,
            "pool": {"active_id": pool_state.active_bin_id, "bin_step": pool_state.bin_step, "pair_address": self._pool_address,
                     "price_y_per_x": serialize_decimal(pool_state.price_y_per_x),
                     "spot_price_mnt_usdt": serialize_decimal(pool_state.mnt_price_usdt) if pool_state.mnt_price_usdt else None,
                     "token_x": pool_state.token_x.address, "token_y": pool_state.token_y.address},
            "position": {"active_bins_total": len(position.active_bins), "in_range": position.in_range,
                         "max_bin_id": position.max_bin_id, "min_bin_id": position.min_bin_id},
            "remove_parameters": {"amount_x_min_raw": str(amount_x_min), "amount_y_min_raw": str(amount_y_min),
                                  "deadline": deadline, "ids": ids, "lb_amounts_raw": [str(a) for a in amounts], "preview": preview},
        }

    # ── Validation ─────────────────────────────────────────

    def validate_position_size(
        self,
        amount_wmnt: Decimal,
        amount_usdt: Decimal,
        mnt_price_usdt: Decimal,
        min_size_usdt: Decimal | None = None,
    ) -> tuple[bool, str]:
        min_size = min_size_usdt or Decimal(str(self.settings.min_position_size_usdt))
        total_value = amount_wmnt * mnt_price_usdt + amount_usdt
        if total_value < min_size:
            return False, f"Position value ${total_value:.2f} below minimum ${min_size:.2f}"
        return True, "ok"

    # ── Registry (position tracking) ──────────────────────

    def _register_position(
        self, *, strategy_type: str, results: list[ExecutionResult],
        active_id: int, delta_ids: list[int],
        amount_wmnt: Decimal, amount_usdt: Decimal,
        distribution_shape: str | None = None,
        bin_amounts: dict[int, int] | None = None,
    ) -> None:
        """Register a newly created position in the LP registry."""
        try:
            # Find the add_liquidity result with a tx_hash
            tx_hash = ""
            for r in results:
                if r.action == "add_liquidity" and r.tx_hash:
                    tx_hash = r.tx_hash
                    break

            min_bin = active_id + min(delta_ids)
            max_bin = active_id + max(delta_ids)

            reg = self.get_registry(self.tx.wallet_address)
            reg.add_position(
                strategy_type=strategy_type,
                min_bin=min_bin,
                max_bin=max_bin,
                tx_hash=tx_hash,
                initial_mnt=float(amount_wmnt),
                initial_usdt=float(amount_usdt),
                distribution_shape=distribution_shape,
                bin_amounts=bin_amounts,
            )
            logger.info(f"Registered {strategy_type} position in LP registry (bins {min_bin}-{max_bin})")
        except Exception as e:
            logger.error(f"Failed to register position in LP registry: {e}")

    def _deregister_positions(
        self, position: PositionState,
        final_mnt: float = 0, final_usdt: float = 0,
        fees_earned_usdt: float = 0,
    ) -> None:
        """Deregister removed positions from the LP registry."""
        try:
            wallet = self.tx.wallet_address
            reg = self.get_registry(wallet)
            active_bin_ids = [b.bin_id for b in position.active_bins]
            if not active_bin_ids:
                return

            affected = reg.find_positions_by_bins(active_bin_ids)
            for pos in affected:
                reg.remove_position(
                    pos.id, tx_hash="",
                    final_mnt=final_mnt, final_usdt=final_usdt,
                    fees_earned_usdt=fees_earned_usdt,
                )
                logger.info(
                    f"Deregistered position {pos.id} from LP registry "
                    f"(recovered {final_mnt:.2f} MNT + ${final_usdt:.2f}, fees=${fees_earned_usdt:.2f})"
                )
        except Exception as e:
            logger.error(f"Failed to deregister positions from LP registry: {e}")

    def get_registry(self, wallet: str):
        """Get or create LPRegistry for wallet. Lazy-loaded."""
        from ._lp_registry import LPRegistry
        if not hasattr(self, "_registry") or self._registry is None:
            self._registry = LPRegistry(wallet)
            self._registry.load()
        return self._registry

    def get_tracked_positions(self, wallet: str, strategy_type: str | None = None):
        reg = self.get_registry(wallet)
        if strategy_type == "narrow":
            return reg.get_narrow_positions()
        if strategy_type == "wide":
            return reg.get_wide_positions()
        return reg.get_all_active_positions()

    def get_all_active_bins(self, wallet: str) -> list[int]:
        return self.get_registry(wallet).get_all_active_bins()

    # ── Reconciliation ─────────────────────────────────────

    def discover_onchain_bins(self, wallet: str) -> list[int]:
        """Discover all onchain bins by probing around active bin."""
        checksum = self.rpc.checksum(wallet)
        pool_state = self.get_pool_state()
        active_bin = pool_state.active_bin_id
        probe_range = 200
        nonzero_bins: list[int] = []

        for start in range(active_bin - probe_range, active_bin + probe_range + 1, 50):
            end = min(start + 50, active_bin + probe_range + 1)
            ids = list(range(start, end))
            if not ids:
                continue
            balances = self._pool.functions.balanceOfBatch(
                [checksum] * len(ids), ids,
            ).call()
            nonzero_bins.extend(
                bid for bid, bal in zip(ids, balances, strict=True) if int(bal) > 0
            )

        return sorted(set(nonzero_bins))

    def reconcile(self, wallet: str, *, dry_run: bool = True) -> dict[str, Any]:
        """Compare registry with onchain state. Returns diff — caller decides action.

        Cleans stale registry entries (bins gone from onchain) when not dry_run.
        """
        reg = self.get_registry(wallet)
        onchain_bins = self.discover_onchain_bins(wallet)
        registry_bins = reg.get_all_active_bins()

        onchain_set = set(onchain_bins)
        registry_set = set(registry_bins)
        unauthorized = sorted(onchain_set - registry_set)
        missing_onchain = sorted(registry_set - onchain_set)
        matched = sorted(onchain_set & registry_set)

        # Clean stale registry entries whose bins are entirely gone from onchain
        if missing_onchain and not dry_run:
            affected = reg.find_positions_by_bins(missing_onchain)
            for pos in affected:
                pos_bins = set(pos.get_all_bins())
                if pos_bins.issubset(set(missing_onchain)):
                    logger.info(f"Removing stale registry position {pos.id} "
                                f"(bins {pos.min_bin}-{pos.max_bin} no longer onchain)")
                    reg.remove_position(pos.id, tx_hash="reconcile_cleanup")

        return {
            "action": "synced" if not unauthorized else "unauthorized",
            "reason": f"{len(matched)} bins matched" if not unauthorized
                      else f"{len(unauthorized)} onchain bins not in registry",
            "registry_bins": registry_bins,
            "onchain_bins": onchain_bins,
            "matched": matched,
            "unauthorized": unauthorized,
            "missing_onchain": missing_onchain,
            "dry_run": dry_run,
        }
