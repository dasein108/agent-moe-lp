"""Single authority on wallet balances and balance-changing operations.

Owns: balance reads, MNT wrapping/unwrapping, swaps, portfolio rebalancing.
Composes RpcClient + TxSender.
"""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any

from .abi import LB_ROUTER_ABI, WMNT_ABI
from .config import Settings
from .logging_config import get_logger
from .models import (
    ERC20Balance,
    ExecutionResult,
    LpAllocation,
    NativeBalance,
    RebalancePlan,
    RebalanceState,
    SwapQuote,
    TokenInfo,
    WalletBalances,
)
from .rpc_client import RpcClient
from .tx_sender import TxSender
from .utils import scaled_decimal, serialize_decimal

logger = get_logger(__name__)


class BalanceManager:
    """Single authority on wallet balances and balance-changing operations."""

    def __init__(self, rpc: RpcClient, tx: TxSender, settings: Settings) -> None:
        self.rpc = rpc
        self.tx = tx
        self.settings = settings

        self._pool_address = rpc.checksum(settings.pool_address)
        self._router_address = rpc.checksum(settings.moe_router_address)
        self._wmnt_address = rpc.checksum(settings.wmnt_address)
        self._usdt_address = rpc.checksum(settings.usdt_address)

        self._router = rpc.get_contract(self._router_address, LB_ROUTER_ABI)
        self._wmnt = rpc.get_contract(self._wmnt_address, WMNT_ABI)
        self._pool = rpc.get_pair_contract(self._pool_address)

        self._token_cache: dict[str, TokenInfo] = {}

    # ── Token Info ─────────────────────────────────────────

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

    def _token_to_raw(self, token_address: str, amount: Decimal) -> int:
        token = self.get_token_info(token_address)
        return int(amount * (Decimal(10) ** token.decimals))

    def _raw_to_decimal(self, token_address: str, raw_amount: int) -> Decimal:
        token = self.get_token_info(token_address)
        return scaled_decimal(raw_amount, token.decimals)

    # ── Balance Reads ──────────────────────────────────────

    def get_native_balance(self, wallet: str) -> NativeBalance:
        def _read():
            raw = self.rpc.get_balance(wallet)
            return NativeBalance(symbol="MNT", raw=int(raw), normalized=scaled_decimal(raw, 18))
        return self.rpc.call_with_retry("get_native_balance", _read)

    def get_erc20_balance(self, wallet: str, token_address: str) -> ERC20Balance:
        def _read():
            token = self.get_token_info(token_address)
            contract = self.rpc.get_erc20_contract(token.address)
            checksum = self.rpc.checksum(wallet)
            raw = int(contract.functions.balanceOf(checksum).call())
            allowance = int(contract.functions.allowance(checksum, self._router_address).call())
            return ERC20Balance(
                token=token,
                raw=raw,
                normalized=scaled_decimal(raw, token.decimals),
                router_allowance_raw=allowance,
                router_allowance_normalized=scaled_decimal(allowance, token.decimals),
            )
        return self.rpc.call_with_retry("get_erc20_balance", _read)

    def get_wallet_balances(self, wallet: str, mnt_price: Decimal | None = None) -> WalletBalances:
        native = self.get_native_balance(wallet)
        wmnt = self.get_erc20_balance(wallet, self._wmnt_address)
        usdt = self.get_erc20_balance(wallet, self._usdt_address)
        if mnt_price is None:
            mnt_price = self._get_mnt_price()
        return WalletBalances(native_mnt=native, wmnt=wmnt, usdt=usdt, mnt_price_usdt=mnt_price)

    def _get_mnt_price(self) -> Decimal | None:
        """Get MNT/USDT price. Only 2 RPC calls (getActiveId + getPriceFromId)."""
        from .utils import price_128x128_to_decimal
        try:
            # Cache token addresses after first call
            if not hasattr(self, "_pool_token_x"):
                self._pool_token_x = self.get_token_info(self._pool.functions.getTokenX().call())
                self._pool_token_y = self.get_token_info(self._pool.functions.getTokenY().call())

            active_bin_id = int(self._pool.functions.getActiveId().call())
            price_raw = int(self._pool.functions.getPriceFromId(active_bin_id).call())
            price_y_per_x = price_128x128_to_decimal(price_raw) * (
                Decimal(10) ** (self._pool_token_x.decimals - self._pool_token_y.decimals)
            )
            x = self._pool_token_x.address.lower()
            y = self._pool_token_y.address.lower()
            if x == self._wmnt_address.lower() and y == self._usdt_address.lower():
                return price_y_per_x
            if x == self._usdt_address.lower() and y == self._wmnt_address.lower():
                return Decimal(1) / price_y_per_x if price_y_per_x else None
            return None
        except (ConnectionError, TimeoutError) as e:
            logger.debug(f"RPC error reading MNT price: {e}")
            return None

    def has_sufficient_gas(self, wallet: str, buffer_mnt: Decimal = Decimal("100")) -> bool:
        native = self.get_native_balance(wallet)
        return native.normalized >= buffer_mnt

    # ── Wrap / Unwrap ──────────────────────────────────────

    def wrap_mnt(self, amount: Decimal, *, dry_run: bool = False) -> ExecutionResult:
        raw_amount = self._token_to_raw(self._wmnt_address, amount)
        function = self._wmnt.functions.deposit()
        return self.tx.send(
            "wrap_mnt", function, value=raw_amount, dry_run=dry_run,
            details={"amount_mnt": serialize_decimal(amount), "raw_amount": str(raw_amount)},
        )

    def unwrap_wmnt(self, amount: Decimal, *, dry_run: bool = False) -> ExecutionResult:
        raw_amount = self._token_to_raw(self._wmnt_address, amount)
        function = self._wmnt.functions.withdraw(raw_amount)
        return self.tx.send(
            "unwrap_wmnt", function, dry_run=dry_run,
            details={"amount_wmnt": serialize_decimal(amount), "raw_amount": str(raw_amount)},
        )

    # ── MNT Min Balance Guard ─────────────────────────────

    def ensure_mnt_min_balance(
        self,
        wallet: str,
        *,
        dry_run: bool = False,
    ) -> list[ExecutionResult]:
        """If native MNT is below mnt_min_balance, swap USDT to top up to 2x the minimum.

        Uses mnt_min_balance from settings (falls back to gas_reserve_mnt if 0).
        """
        min_bal = Decimal(str(self.settings.mnt_min_balance or self.settings.gas_reserve_mnt))
        if min_bal <= 0:
            return []

        native = self.get_native_balance(wallet)
        native_mnt = native.normalized
        if native_mnt >= min_bal:
            return []

        target = min_bal * 2
        deficit = target - native_mnt

        # First try unwrapping WMNT if available
        wmnt_bal = self.get_erc20_balance(wallet, self._wmnt_address).normalized
        results: list[ExecutionResult] = []

        if wmnt_bal > 0:
            unwrap_amount = min(wmnt_bal, deficit)
            logger.info(
                "MNT min balance guard: native=%.2f MNT < min=%.2f MNT, "
                "unwrapping %.2f WMNT",
                float(native_mnt), float(min_bal), float(unwrap_amount),
            )
            results.append(self.unwrap_wmnt(unwrap_amount, dry_run=dry_run))
            deficit -= unwrap_amount

        if deficit <= 0:
            return results

        # Swap USDT → WMNT for remaining deficit, then unwrap
        mnt_price = self._get_mnt_price()
        if mnt_price is None or mnt_price <= 0:
            logger.warning(
                "MNT min balance guard: cannot determine MNT price, skipping swap"
            )
            return results

        usdt_needed = deficit * mnt_price * Decimal("1.02")  # 2% buffer for slippage
        usdt_bal = self.get_erc20_balance(wallet, self._usdt_address).normalized
        if usdt_bal < usdt_needed:
            logger.warning(
                "MNT min balance guard: need $%.2f USDT for swap but only have $%.2f",
                float(usdt_needed), float(usdt_bal),
            )
            usdt_needed = usdt_bal
            if usdt_needed <= 0:
                return results

        logger.info(
            "MNT min balance guard: native=%.2f MNT < min=%.2f MNT, "
            "swapping $%.2f USDT → WMNT (target=%.2f MNT)",
            float(native_mnt), float(min_bal), float(usdt_needed), float(target),
        )
        swap_results = self.swap(
            token_in=self._usdt_address,
            token_out=self._wmnt_address,
            amount_in=usdt_needed,
            dry_run=dry_run,
        )
        results.extend(swap_results)

        # Unwrap the WMNT we just received
        if not dry_run:
            new_wmnt = self.get_erc20_balance(wallet, self._wmnt_address).normalized
            if new_wmnt > 0:
                unwrap_amount = min(new_wmnt, deficit)
                results.append(self.unwrap_wmnt(unwrap_amount, dry_run=dry_run))

        return results

    # ── Swap ───────────────────────────────────────────────

    def quote_swap(self, token_in: str, token_out: str, amount_in: Decimal) -> SwapQuote:
        raw_in = self._token_to_raw(token_in, amount_in)
        token_x = self.rpc.checksum(self._pool.functions.getTokenX().call())
        swap_for_y = self.rpc.checksum(token_in) == token_x
        amount_in_left, amount_out, fee = self._router.functions.getSwapOut(
            self._pool_address, raw_in, swap_for_y,
        ).call()
        token_out_dec = self.get_token_info(token_out).decimals
        return SwapQuote(
            amount_in_raw=int(raw_in),
            amount_in_left_raw=int(amount_in_left),
            amount_out_raw=int(amount_out),
            fee_raw=int(fee),
            amount_out=scaled_decimal(int(amount_out), token_out_dec),
            swap_for_y=swap_for_y,
        )

    def swap(
        self,
        *,
        token_in: str,
        token_out: str,
        amount_in: Decimal,
        slippage_bps: int | None = None,
        dry_run: bool = False,
    ) -> list[ExecutionResult]:
        quote = self.quote_swap(token_in, token_out, amount_in)
        slippage = self.settings.slippage_bps if slippage_bps is None else slippage_bps
        amount_out_min = int(quote.amount_out_raw * (10_000 - slippage) / 10_000)

        approvals: list[ExecutionResult] = []
        approval = self.tx.ensure_erc20_approval(
            token_in, self._router_address, quote.amount_in_raw, dry_run=dry_run,
        )
        if approval is not None:
            approvals.append(approval)

        bin_step = int(self._pool.functions.getBinStep().call())
        pair_bin_steps = [bin_step]
        versions = [self.settings.pair_version]
        token_path = [self.rpc.checksum(token_in), self.rpc.checksum(token_out)]

        function = self._router.functions.swapExactTokensForTokens(
            quote.amount_in_raw, amount_out_min,
            (pair_bin_steps, versions, token_path),
            self.tx.wallet_address, self.tx.deadline(),
        )
        approvals.append(
            self.tx.send(
                "swap_exact_in", function, dry_run=dry_run,
                details={
                    "token_in": self.rpc.checksum(token_in),
                    "token_out": self.rpc.checksum(token_out),
                    "amount_in": serialize_decimal(amount_in),
                    "quote": quote.to_dict(),
                    "amount_out_min_raw": str(amount_out_min),
                    "path": {
                        "pair_bin_steps": pair_bin_steps,
                        "versions": versions,
                        "token_path": token_path,
                    },
                },
            )
        )
        return approvals

    # ── Rebalance ──────────────────────────────────────────

    def get_rebalance_state(self, wallet: str) -> RebalanceState:
        mnt_price = self._get_mnt_price()
        if mnt_price is None:
            raise RuntimeError("Unable to derive MNT price in USDT from pool state")

        native_mnt = self.get_native_balance(wallet).normalized
        wmnt = self.get_erc20_balance(wallet, self._wmnt_address).normalized
        usdt = self.get_erc20_balance(wallet, self._usdt_address).normalized

        mnt_total = native_mnt + wmnt
        mnt_value_usdt = mnt_total * mnt_price
        total_value_usdt = mnt_value_usdt + usdt
        if total_value_usdt == 0:
            mnt_weight = Decimal(0)
            usdt_weight = Decimal(0)
        else:
            mnt_weight = mnt_value_usdt / total_value_usdt
            usdt_weight = usdt / total_value_usdt

        return RebalanceState(
            wallet_address=wallet,
            mnt_native=native_mnt,
            wmnt=wmnt,
            mnt_total=mnt_total,
            usdt=usdt,
            mnt_price_usdt=mnt_price,
            mnt_value_usdt=mnt_value_usdt,
            total_value_usdt=total_value_usdt,
            mnt_weight=mnt_weight,
            usdt_weight=usdt_weight,
        )

    def plan_rebalance(
        self,
        wallet: str,
        *,
        tolerance_bps: int = 1_000,
        min_trade_usdt: Decimal = Decimal("0.10"),
        target_mnt_ratio_bps: int = 5_000,
    ) -> RebalancePlan:
        state = self.get_rebalance_state(wallet)

        target_mnt_weight = Decimal(target_mnt_ratio_bps) / Decimal(10_000)

        if state.total_value_usdt == 0:
            return RebalancePlan(
                action="none", within_tolerance=True, tolerance_bps=tolerance_bps,
                target_weight=str(target_mnt_weight),
                current_mnt_weight="0", current_usdt_weight="0",
                trade_value_usdt="0", amount_in_token="", amount_in="0",
                amount_out_token=None, quoted_amount_out=None,
                details={"reason": "wallet has zero value"},
            )

        tolerance = Decimal(tolerance_bps) / Decimal(10_000)
        lower = target_mnt_weight - tolerance
        upper = target_mnt_weight + tolerance
        target_mnt_value = state.total_value_usdt * target_mnt_weight

        if lower <= state.mnt_weight <= upper:
            return RebalancePlan(
                action="none", within_tolerance=True, tolerance_bps=tolerance_bps,
                target_weight=str(target_mnt_weight),
                current_mnt_weight=serialize_decimal(state.mnt_weight, 6),
                current_usdt_weight=serialize_decimal(state.usdt_weight, 6),
                trade_value_usdt="0", amount_in_token="", amount_in="0",
                amount_out_token=None, quoted_amount_out=None,
                details={"reason": "already within tolerance band"},
            )

        if state.mnt_value_usdt > target_mnt_value:
            trade_value_usdt = state.mnt_value_usdt - target_mnt_value
            if trade_value_usdt < min_trade_usdt:
                return RebalancePlan(
                    action="none", within_tolerance=True, tolerance_bps=tolerance_bps,
                    target_weight=str(target_mnt_weight),
                    current_mnt_weight=serialize_decimal(state.mnt_weight, 6),
                    current_usdt_weight=serialize_decimal(state.usdt_weight, 6),
                    trade_value_usdt=serialize_decimal(trade_value_usdt),
                    amount_in_token="WMNT", amount_in="0",
                    amount_out_token="USDT", quoted_amount_out=None,
                    details={"reason": "required trade below minimum threshold"},
                )
            amount_mnt_to_sell = trade_value_usdt / state.mnt_price_usdt
            quote = self.quote_swap(self._wmnt_address, self._usdt_address, amount_mnt_to_sell)
            wrap_amount = max(Decimal(0), amount_mnt_to_sell - state.wmnt)
            return RebalancePlan(
                action="sell_mnt", within_tolerance=False, tolerance_bps=tolerance_bps,
                target_weight=str(target_mnt_weight),
                current_mnt_weight=serialize_decimal(state.mnt_weight, 6),
                current_usdt_weight=serialize_decimal(state.usdt_weight, 6),
                trade_value_usdt=serialize_decimal(trade_value_usdt),
                amount_in_token="WMNT",
                amount_in=serialize_decimal(amount_mnt_to_sell),
                amount_out_token="USDT",
                quoted_amount_out=serialize_decimal(quote.amount_out),
                details={
                    "wrap_amount_mnt_first": serialize_decimal(wrap_amount),
                    "quoted_fee_raw": str(quote.fee_raw),
                },
            )

        # buy_mnt
        trade_value_usdt = target_mnt_value - state.mnt_value_usdt
        if trade_value_usdt < min_trade_usdt:
            return RebalancePlan(
                action="none", within_tolerance=True, tolerance_bps=tolerance_bps,
                target_weight=str(target_mnt_weight),
                current_mnt_weight=serialize_decimal(state.mnt_weight, 6),
                current_usdt_weight=serialize_decimal(state.usdt_weight, 6),
                trade_value_usdt=serialize_decimal(trade_value_usdt),
                amount_in_token="USDT", amount_in="0",
                amount_out_token="MNT", quoted_amount_out=None,
                details={"reason": "required trade below minimum threshold"},
            )
        quote = self.quote_swap(self._usdt_address, self._wmnt_address, trade_value_usdt)
        return RebalancePlan(
            action="buy_mnt", within_tolerance=False, tolerance_bps=tolerance_bps,
            target_weight=str(target_mnt_weight),
            current_mnt_weight=serialize_decimal(state.mnt_weight, 6),
            current_usdt_weight=serialize_decimal(state.usdt_weight, 6),
            trade_value_usdt=serialize_decimal(trade_value_usdt),
            amount_in_token="USDT",
            amount_in=serialize_decimal(trade_value_usdt),
            amount_out_token="MNT",
            quoted_amount_out=serialize_decimal(quote.amount_out),
            details={"quoted_fee_raw": str(quote.fee_raw)},
        )

    def execute_rebalance(
        self,
        wallet: str,
        plan: RebalancePlan,
        *,
        dry_run: bool = True,
        unwrap_after_buy: bool = True,
    ) -> list[ExecutionResult]:
        results: list[ExecutionResult] = []

        if plan.action == "none":
            return results

        if plan.action == "sell_mnt":
            amount_in = Decimal(plan.amount_in)
            wrap_amount = Decimal(plan.details["wrap_amount_mnt_first"])
            if wrap_amount > 0:
                logger.debug(f"wrapping {wrap_amount} MNT before swap")
                results.append(self.wrap_mnt(wrap_amount, dry_run=dry_run))
            logger.debug(f"swapping {amount_in} WMNT to USDT")
            results.extend(self.swap(
                token_in=self._wmnt_address, token_out=self._usdt_address,
                amount_in=amount_in, dry_run=dry_run,
            ))

        elif plan.action == "buy_mnt":
            amount_in = Decimal(plan.amount_in)
            logger.debug(f"swapping {amount_in} USDT to WMNT")
            results.extend(self.swap(
                token_in=self._usdt_address, token_out=self._wmnt_address,
                amount_in=amount_in, dry_run=dry_run,
            ))
            if unwrap_after_buy:
                if dry_run:
                    results.append(ExecutionResult(
                        action="unwrap_wmnt", tx_hash=None, dry_run=True,
                        details={
                            "note": "unwrap amount determined after swap in live mode",
                            "quoted_amount_out_wmnt": plan.quoted_amount_out,
                        },
                    ))
                else:
                    wmnt_balance = self.get_erc20_balance(wallet, self._wmnt_address)
                    if wmnt_balance.raw > 0:
                        wmnt_amount = self._raw_to_decimal(self._wmnt_address, wmnt_balance.raw)
                        logger.debug(f"unwrapping WMNT after buy: {serialize_decimal(wmnt_amount, 18)}")
                        results.append(self.unwrap_wmnt(wmnt_amount, dry_run=False))

        return results

    def rebalance_if_needed(
        self,
        wallet: str,
        *,
        tolerance_bps: int = 1_000,
        min_trade_usdt: Decimal = Decimal("0.10"),
        target_mnt_ratio_bps: int = 5_000,
        dry_run: bool = True,
        unwrap_after_buy: bool = True,
    ) -> dict[str, Any]:
        plan = self.plan_rebalance(
            wallet,
            tolerance_bps=tolerance_bps,
            min_trade_usdt=min_trade_usdt,
            target_mnt_ratio_bps=target_mnt_ratio_bps,
        )
        results = self.execute_rebalance(
            wallet, plan, dry_run=dry_run, unwrap_after_buy=unwrap_after_buy,
        )
        state_before = self.get_rebalance_state(wallet)
        return {
            "state_before": state_before.to_dict(),
            "plan": asdict(plan),
            "results": [{"action": r.action, "tx_hash": r.tx_hash, "dry_run": r.dry_run, "details": r.details} for r in results],
        }

    # ── Capital Budget ─────────────────────────────────────

    def get_capital_budget(
        self,
        wallet: str,
        lp_service,
        gas_reserve: Decimal | None = None,
    ):
        """Compute deployed vs free capital using wallet balances + LP position."""
        from .models import CapitalBudget

        if gas_reserve is None:
            gas_reserve = Decimal(str(self.settings.gas_reserve_mnt))

        balances = self.get_wallet_balances(wallet)
        mnt_price = balances.mnt_price_usdt or Decimal(0)

        wallet_mnt = balances.native_mnt.normalized + balances.wmnt.normalized
        wallet_usdt = balances.usdt.normalized

        # Estimate deployed capital from on-chain position
        deployed_mnt = Decimal(0)
        deployed_usdt = Decimal(0)
        try:
            position = lp_service.get_position(wallet, include_inventory=True)
            if position.position_exists and position.inventory_included:
                deployed_mnt = position.estimated_token_x or Decimal(0)
                deployed_usdt = position.estimated_token_y or Decimal(0)
        except Exception as e:
            logger.debug(f"Could not read LP position for budget: {e}")

        # Wallet balance is separate from deployed (LP tokens are locked in pool).
        # Total = wallet + deployed. Free = wallet - gas_reserve - estimate_headroom,
        # capped at max_budget_pct. The estimate_headroom covers the estimateGas
        # simulation which requires more native headroom than actual gas cost.
        total_mnt = wallet_mnt + deployed_mnt
        total_usdt = wallet_usdt + deployed_usdt
        max_budget_pct = Decimal(str(getattr(self.settings, "max_budget_pct", 0.90)))
        estimate_headroom = Decimal(str(getattr(self.settings, "native_estimate_headroom_mnt", 50)))
        total_reserve = gas_reserve + estimate_headroom
        free_mnt = min(
            max(wallet_mnt - total_reserve, Decimal(0)),
            wallet_mnt * max_budget_pct,
        )
        free_usdt = wallet_usdt * max_budget_pct

        logger.info(
            f"Budget: wallet={float(wallet_mnt):.2f} MNT + ${float(wallet_usdt):.2f} USDT | "
            f"deployed={float(deployed_mnt):.2f} MNT + ${float(deployed_usdt):.2f} USDT | "
            f"free={float(free_mnt):.2f} MNT + ${float(free_usdt):.2f} USDT "
            f"(reserve={float(total_reserve):.0f} MNT, budget_cap={float(max_budget_pct):.0%})"
        )

        return CapitalBudget(
            total_mnt=total_mnt,
            total_usdt=total_usdt,
            deployed_mnt=deployed_mnt,
            deployed_usdt=deployed_usdt,
            free_mnt=free_mnt,
            free_usdt=free_usdt,
            gas_reserve_mnt=gas_reserve,
            mnt_price_usdt=mnt_price,
        )

    # ── Allocation ─────────────────────────────────────────

    def calculate_lp_allocation(
        self,
        wallet: str,
        *,
        target_pct: float = 0.6,
        safety_margin: float = 0.95,
        min_size_usdt: float = 10.0,
        budget=None,
    ) -> LpAllocation:
        mnt_price = self._get_mnt_price()
        if mnt_price is None or mnt_price == 0:
            return LpAllocation(Decimal(0), Decimal(0), False, "Cannot determine MNT price")

        if budget is not None:
            # Budget-aware: use free capital only
            total_mnt = budget.free_mnt * Decimal(str(safety_margin))
            total_usdt = budget.free_usdt * Decimal(str(safety_margin))
        else:
            # Fallback: raw wallet balance
            native = self.get_native_balance(wallet)
            wmnt = self.get_erc20_balance(wallet, self._wmnt_address)
            usdt = self.get_erc20_balance(wallet, self._usdt_address)
            total_mnt = (native.normalized + wmnt.normalized) * Decimal(str(safety_margin))
            total_usdt = usdt.normalized * Decimal(str(safety_margin))

        portfolio_value = total_mnt * mnt_price + total_usdt

        target_value = portfolio_value * Decimal(str(target_pct))
        if target_value < Decimal(str(min_size_usdt)):
            return LpAllocation(Decimal(0), Decimal(0), False, f"Position too small: ${target_value:.2f}")

        # Pass all available tokens — the LP distribution math handles the split
        # (y_only/x_only/mixed mode). The router refunds unused tokens.
        target_mnt = min(total_mnt, target_value / mnt_price)
        target_usdt = min(total_usdt, target_value)

        return LpAllocation(target_mnt, target_usdt, True, "ok")
